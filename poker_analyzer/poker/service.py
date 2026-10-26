from __future__ import annotations

from pathlib import Path
from typing import Any

from poker.config import format_data_dir, load_data_dir, resolve_data_dir, save_data_dir
from poker.filters import FilterSpec, apply_filter, filter_options, hand_file_date
from poker.metrics.base import get_metric, list_metrics, load_builtin_metrics
from poker.models import HandDataset
from poker.sources import LocalDirectorySource


class AnalysisService:
    """Orchestrates data loading and metric computation."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        load_builtin_metrics()
        self.data_dir = resolve_data_dir(data_dir) if data_dir else load_data_dir()
        self._dataset: HandDataset | None = None
        self._filter_cache_key: tuple[Any, ...] | None = None
        self._filter_cache: HandDataset | None = None

    def _clear_filter_cache(self) -> None:
        self._filter_cache_key = None
        self._filter_cache = None

    def set_data_dir(self, data_dir: Path | str, *, persist: bool = True) -> Path:
        path = resolve_data_dir(data_dir)
        if persist:
            save_data_dir(path)
        self.data_dir = path
        self._dataset = None
        self._clear_filter_cache()
        return path

    def reload(self) -> HandDataset:
        source = LocalDirectorySource(self.data_dir)
        self._dataset = source.load()
        self._clear_filter_cache()
        return self._dataset

    @property
    def is_loaded(self) -> bool:
        return self._dataset is not None

    @property
    def dataset(self) -> HandDataset:
        if self._dataset is None:
            self.reload()
        assert self._dataset is not None
        return self._dataset

    def _count_source_files(self) -> int:
        if not self.data_dir.exists():
            return 0
        return sum(1 for p in self.data_dir.glob("*.txt") if p.is_file())

    def dir_info(self) -> dict[str, Any]:
        from poker.filters import filter_options_from_directory

        return {
            "data_dir": format_data_dir(self.data_dir),
            "data_dir_resolved": str(self.data_dir),
            "source": None,
            "hand_count": 0,
            "file_count": self._count_source_files(),
            "date_range": {"start": None, "end": None},
            "filter": filter_options_from_directory(self.data_dir),
            "metrics": list_metrics(),
            "loaded": False,
        }

    def ensure_loaded(self) -> HandDataset:
        return self.dataset

    def _load_stats(self, ds: HandDataset) -> dict[str, int]:
        stats = ds.load_stats
        hand_count = len(ds.hands)
        return {
            "raw_hand_count": stats.get("raw_hand_count", hand_count),
            "duplicate_hands_removed": stats.get("duplicate_hands_removed", 0),
            "duplicate_files_skipped": stats.get("duplicate_files_skipped", 0),
            "file_count": stats.get("file_count", len({h.source_file for h in ds.hands})),
        }

    def summary(self) -> dict[str, Any]:
        if self._dataset is None:
            return self.dir_info()
        ds = self._dataset
        load_stats = self._load_stats(ds)
        file_dates = sorted({d for h in ds.hands if (d := hand_file_date(h))})
        return {
            "data_dir": format_data_dir(self.data_dir),
            "data_dir_resolved": str(self.data_dir),
            "source": ds.source_label,
            "hand_count": len(ds.hands),
            "file_count": load_stats["file_count"],
            "raw_hand_count": load_stats["raw_hand_count"],
            "duplicate_hands_removed": load_stats["duplicate_hands_removed"],
            "duplicate_files_skipped": load_stats["duplicate_files_skipped"],
            "date_range": {
                "start": file_dates[0].isoformat() if file_dates else None,
                "end": file_dates[-1].isoformat() if file_dates else None,
            },
            "filter": filter_options(ds),
            "metrics": list_metrics(),
            "loaded": True,
        }

    def filtered_dataset(self, spec: FilterSpec | None = None) -> HandDataset:
        # Reuse last filter result across sequential metric requests (same analyze click).
        key: tuple[Any, ...]
        if spec is None:
            key = ()
        else:
            d = spec.to_dict()
            key = (
                d.get("date_from"),
                d.get("date_to"),
                tuple(d.get("stakes") or ()),
                tuple(d.get("game_types") or ()),
                d.get("table_format"),
            )
        if key == self._filter_cache_key and self._filter_cache is not None:
            return self._filter_cache
        filtered = apply_filter(self.dataset, spec)
        self._filter_cache_key = key
        self._filter_cache = filtered
        return filtered

    def compute_metric(
        self,
        metric_id: str,
        spec: FilterSpec | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective = spec or FilterSpec()
        if not effective.table_format:
            raise ValueError("请先选择桌型（6-max 或 9-max）")
        metric = get_metric(metric_id)
        filtered = self.filtered_dataset(spec)
        result = metric.compute(filtered, options=options)
        result["filter"] = effective.to_dict()
        result["filtered_hand_count"] = len(filtered.hands)
        result["total_hand_count"] = len(self.dataset.hands)
        return result

    def replay_hand(
        self,
        source: str,
        index: int,
        spec: FilterSpec | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from poker.replay import get_replay

        filtered = self.filtered_dataset(spec)
        effective = spec or FilterSpec()
        if not effective.table_format:
            raise ValueError("请先选择桌型（6-max 或 9-max）")
        result = get_replay(filtered, source, index, options)
        result["source"] = source
        result["filter"] = effective.to_dict()
        return result


_service: AnalysisService | None = None


def get_service() -> AnalysisService:
    global _service
    if _service is None:
        _service = AnalysisService()
    return _service


def reset_service(data_dir: Path | str | None = None) -> AnalysisService:
    global _service
    if data_dir is None:
        _service = AnalysisService()
    else:
        _service = AnalysisService(data_dir)
        save_data_dir(_service.data_dir)
    return _service
