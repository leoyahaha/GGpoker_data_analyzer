from __future__ import annotations

import json
import pickle
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from poker.filters import FilterSpec, apply_filter, filter_options, filter_options_from_directory, hand_file_date
from poker.metrics.base import get_metric, list_metrics, load_builtin_metrics
from poker.models import HandDataset
from poker.online.limits import BusyError, ResourceGate
from poker.online.settings import OnlineSettings
from poker.sources import LocalDirectorySource


@dataclass
class CachedDataset:
    user_id: str
    dataset: HandDataset
    last_access: float
    loaded_at: float


class WorkspaceStore:
    """Per-user upload dir + pickle cache + in-memory dataset with TTL eviction."""

    def __init__(self, settings: OnlineSettings, gate: ResourceGate) -> None:
        load_builtin_metrics()
        self.settings = settings
        self.gate = gate
        self.root = settings.data_root
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cache: dict[str, CachedDataset] = {}
        self._import_status: dict[str, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._janitor = threading.Thread(target=self._janitor_loop, daemon=True)
        self._janitor.start()

    def user_dir(self, user_id: str) -> Path:
        path = self.root / "users" / user_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def uploads_dir(self, user_id: str) -> Path:
        path = self.user_dir(user_id) / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pickle_path(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "dataset.pkl"

    def meta_path(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "meta.json"

    def touch(self, user_id: str) -> None:
        with self._lock:
            entry = self._cache.get(user_id)
            if entry:
                entry.last_access = time.time()

    def import_status(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._import_status.get(user_id) or {"status": "idle"})

    def dir_info(self, user_id: str) -> dict[str, Any]:
        uploads = self.uploads_dir(user_id)
        meta = self._read_meta(user_id)
        file_count = sum(1 for p in uploads.glob("*.txt") if p.is_file())
        loaded = user_id in self._cache or self.pickle_path(user_id).exists()
        if meta and meta.get("hand_count"):
            return {
                "data_dir": f"workspace:{user_id}",
                "data_dir_resolved": str(uploads),
                "source": meta.get("source"),
                "hand_count": meta.get("hand_count", 0),
                "file_count": meta.get("file_count", file_count),
                "raw_hand_count": meta.get("raw_hand_count", meta.get("hand_count", 0)),
                "duplicate_hands_removed": meta.get("duplicate_hands_removed", 0),
                "duplicate_files_skipped": meta.get("duplicate_files_skipped", 0),
                "date_range": meta.get("date_range") or {"start": None, "end": None},
                "filter": meta.get("filter") or filter_options_from_directory(uploads),
                "metrics": list_metrics(),
                "loaded": bool(meta.get("hand_count")),
                "online": True,
                "max_hands": self.settings.max_hands,
            }
        return {
            "data_dir": f"workspace:{user_id}",
            "data_dir_resolved": str(uploads),
            "source": None,
            "hand_count": 0,
            "file_count": file_count,
            "date_range": {"start": None, "end": None},
            "filter": filter_options_from_directory(uploads),
            "metrics": list_metrics(),
            "loaded": False,
            "online": True,
            "max_hands": self.settings.max_hands,
        }

    def summary(self, user_id: str) -> dict[str, Any]:
        ds = self._try_get_cached(user_id)
        if ds is None:
            return self.dir_info(user_id)
        return self._summary_from_dataset(user_id, ds)

    def ensure_loaded(self, user_id: str) -> HandDataset:
        with self.gate.slot("heavy"):
            return self._load_dataset(user_id)

    def ensure_loaded_for_metric(self, user_id: str) -> HandDataset:
        with self.gate.slot("heavy"):
            return self._load_dataset(user_id)

    def _load_dataset(self, user_id: str) -> HandDataset:
        cached = self._try_get_cached(user_id)
        if cached is not None:
            return cached
        if self.pickle_path(user_id).exists():
            ds = self._load_pickle(user_id)
            self._put_cache(user_id, ds)
            return ds
        uploads = self.uploads_dir(user_id)
        if not any(uploads.glob("*.txt")):
            raise FileNotFoundError("请先上传牌谱文件（.txt 或 .zip）")
        ds = self._parse_uploads(user_id)
        self._persist(user_id, ds)
        self._put_cache(user_id, ds)
        return ds

    def compute_metric(
        self,
        user_id: str,
        metric_id: str,
        spec: FilterSpec | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective = spec or FilterSpec()
        if not effective.table_format:
            raise ValueError("请先选择桌型（6-max 或 9-max）")
        ds = self.ensure_loaded_for_metric(user_id)
        metric = get_metric(metric_id)
        opts = dict(options or {})
        if metric_id == "profit_curve" and "max_points" not in opts:
            opts["max_points"] = self.settings.profit_max_points
        filtered = apply_filter(ds, spec)
        result = metric.compute(filtered, options=opts)
        result["filter"] = effective.to_dict()
        result["filtered_hand_count"] = len(filtered.hands)
        result["total_hand_count"] = len(ds.hands)
        self.touch(user_id)
        return result

    def replay_hand(
        self,
        user_id: str,
        source: str,
        index: int,
        spec: FilterSpec | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from poker.replay import get_replay

        effective = spec or FilterSpec()
        if not effective.table_format:
            raise ValueError("请先选择桌型（6-max 或 9-max）")
        ds = self.ensure_loaded_for_metric(user_id)
        filtered = apply_filter(ds, spec)
        result = get_replay(filtered, source, index, options)
        result["source"] = source
        result["filter"] = effective.to_dict()
        self.touch(user_id)
        return result

    def save_upload(self, user_id: str, filename: str, data: bytes) -> dict[str, Any]:
        name = Path(filename).name
        if not name:
            raise ValueError("文件名无效")
        lower = name.lower()
        uploads = self.uploads_dir(user_id)
        if lower.endswith(".zip"):
            return self._save_zip(user_id, uploads, name, data)
        if not lower.endswith(".txt"):
            raise ValueError("仅支持 .txt 或 .zip")
        target = uploads / name
        target.write_bytes(data)
        return {"saved": [name], "txt_count": 1}

    def _save_zip(self, user_id: str, uploads: Path, name: str, data: bytes) -> dict[str, Any]:
        zip_path = self.user_dir(user_id) / "incoming" / name
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        zip_path.write_bytes(data)
        saved: list[str] = []
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    base = Path(info.filename).name
                    if not base.lower().endswith(".txt"):
                        continue
                    # Prevent zip-slip
                    out = (uploads / base).resolve()
                    if not str(out).startswith(str(uploads.resolve())):
                        continue
                    out.write_bytes(zf.read(info))
                    saved.append(base)
        finally:
            try:
                zip_path.unlink(missing_ok=True)
            except OSError:
                pass
        if not saved:
            raise ValueError("压缩包内没有 .txt 牌谱")
        return {"saved": saved, "txt_count": len(saved)}

    def start_import(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            cur = self._import_status.get(user_id) or {}
            if cur.get("status") == "running":
                return dict(cur)
            job = {
                "status": "queued",
                "message": "等待导入…",
                "started": time.time(),
                "error": None,
                "hand_count": 0,
            }
            self._import_status[user_id] = job

        def worker() -> None:
            try:
                with self.gate.slot("import"):
                    with self._lock:
                        self._import_status[user_id] = {
                            "status": "running",
                            "message": "正在解析牌谱…",
                            "started": time.time(),
                            "error": None,
                            "hand_count": 0,
                        }
                    ds = self._parse_uploads(user_id)
                    self._persist(user_id, ds)
                    self._put_cache(user_id, ds)
                    with self._lock:
                        self._import_status[user_id] = {
                            "status": "done",
                            "message": f"导入完成：{len(ds.hands)} 手",
                            "started": time.time(),
                            "error": None,
                            "hand_count": len(ds.hands),
                            "summary": self._summary_from_dataset(user_id, ds),
                        }
            except BusyError as exc:
                with self._lock:
                    self._import_status[user_id] = {
                        "status": "error",
                        "message": str(exc),
                        "retry_after": exc.retry_after,
                        "error": str(exc),
                        "hand_count": 0,
                    }
            except Exception as exc:  # noqa: BLE001
                with self._lock:
                    self._import_status[user_id] = {
                        "status": "error",
                        "message": str(exc),
                        "error": str(exc),
                        "hand_count": 0,
                    }

        threading.Thread(target=worker, daemon=True).start()
        return dict(job)

    def clear_uploads(self, user_id: str) -> None:
        # Hand files are permanent operator-owned data — never delete from disk.
        self.unload(user_id)

    def unload(self, user_id: str) -> None:
        with self._lock:
            self._cache.pop(user_id, None)

    def _parse_uploads(self, user_id: str) -> HandDataset:
        uploads = self.uploads_dir(user_id)
        txts = [p for p in uploads.glob("*.txt") if p.is_file()]
        if not txts:
            raise FileNotFoundError("请先上传牌谱文件（.txt 或 .zip）")
        source = LocalDirectorySource(uploads)
        ds = source.load()
        if len(ds.hands) > self.settings.max_hands:
            raise ValueError(
                f"手牌数 {len(ds.hands)} 超过服务器上限 {self.settings.max_hands}。"
                f"请减少上传量，或联系管理员提高 POKER_MAX_HANDS。"
            )
        if not ds.hands:
            raise ValueError("未能解析出任何手牌，请检查文件格式")
        return ds

    def _persist(self, user_id: str, ds: HandDataset) -> None:
        pkl = self.pickle_path(user_id)
        tmp = pkl.with_suffix(".tmp")
        with tmp.open("wb") as f:
            pickle.dump(ds, f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(pkl)
        meta = self._summary_from_dataset(user_id, ds)
        self.meta_path(user_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )

    def _load_pickle(self, user_id: str) -> HandDataset:
        with self.pickle_path(user_id).open("rb") as f:
            ds = pickle.load(f)
        if not isinstance(ds, HandDataset):
            raise ValueError("缓存损坏，请重新导入")
        if len(ds.hands) > self.settings.max_hands:
            raise ValueError(
                f"缓存手牌数 {len(ds.hands)} 超过上限 {self.settings.max_hands}"
            )
        return ds

    def _summary_from_dataset(self, user_id: str, ds: HandDataset) -> dict[str, Any]:
        stats = ds.load_stats or {}
        hand_count = len(ds.hands)
        file_dates = sorted({d for h in ds.hands if (d := hand_file_date(h))})
        return {
            "data_dir": f"workspace:{user_id}",
            "data_dir_resolved": str(self.uploads_dir(user_id)),
            "source": ds.source_label,
            "hand_count": hand_count,
            "file_count": stats.get("file_count", len({h.source_file for h in ds.hands})),
            "raw_hand_count": stats.get("raw_hand_count", hand_count),
            "duplicate_hands_removed": stats.get("duplicate_hands_removed", 0),
            "duplicate_files_skipped": stats.get("duplicate_files_skipped", 0),
            "date_range": {
                "start": file_dates[0].isoformat() if file_dates else None,
                "end": file_dates[-1].isoformat() if file_dates else None,
            },
            "filter": filter_options(ds),
            "metrics": list_metrics(),
            "loaded": True,
            "online": True,
            "max_hands": self.settings.max_hands,
        }

    def _read_meta(self, user_id: str) -> dict[str, Any] | None:
        path = self.meta_path(user_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _try_get_cached(self, user_id: str) -> HandDataset | None:
        with self._lock:
            entry = self._cache.get(user_id)
            if entry is None:
                return None
            entry.last_access = time.time()
            return entry.dataset

    def _put_cache(self, user_id: str, ds: HandDataset) -> None:
        now = time.time()
        with self._lock:
            self._cache[user_id] = CachedDataset(user_id, ds, now, now)
            self._evict_locked(now)

    def _evict_locked(self, now: float | None = None) -> None:
        now = now or time.time()
        ttl = self.settings.idle_ttl_sec
        stale = [uid for uid, e in self._cache.items() if now - e.last_access > ttl]
        for uid in stale:
            self._cache.pop(uid, None)
        while len(self._cache) > self.settings.max_cached_users:
            oldest = min(self._cache.values(), key=lambda e: e.last_access)
            self._cache.pop(oldest.user_id, None)

    def _janitor_loop(self) -> None:
        while not self._stop.wait(60):
            with self._lock:
                self._evict_locked()

    def disk_usage_hint(self) -> dict[str, Any]:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
        return {"data_root": str(self.root), "bytes": total}
