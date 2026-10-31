from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT.parent / "all_hand"
SETTINGS_PATH = PROJECT_ROOT / "local_settings.json"
BROWSE_SCRIPT = Path(__file__).resolve().parent / "browse_folder.py"

# Background browse job (single-user local app).
_browse_lock = threading.Lock()
_browse_job: dict | None = None


def resolve_data_dir(path: Path | str) -> Path:
    """Resolve a data directory path; relative paths are from poker_analyzer/."""
    raw = Path(str(path).strip()).expanduser()
    if not str(raw):
        return default_data_dir()
    if raw.is_absolute():
        return raw.resolve()
    return (PROJECT_ROOT / raw).resolve()


def format_data_dir(path: Path | str) -> str:
    """Prefer a project-relative path for display and persistence."""
    resolved = resolve_data_dir(path)
    try:
        rel = resolved.relative_to(PROJECT_ROOT)
        return rel.as_posix()
    except ValueError:
        pass
    try:
        rel = resolved.relative_to(PROJECT_ROOT.parent)
        return Path("..", *rel.parts).as_posix()
    except ValueError:
        return str(resolved)


def default_data_dir() -> Path:
    return DEFAULT_DATA_DIR.resolve()


def load_data_dir() -> Path:
    """Return the configured hand-history directory (persisted locally)."""
    fallback = default_data_dir()
    if SETTINGS_PATH.exists():
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            path_str = str(raw.get("data_dir", "")).strip()
            if path_str:
                resolved = resolve_data_dir(path_str)
                if resolved.exists() and resolved.is_dir():
                    return resolved
                save_data_dir(fallback)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return fallback


def save_data_dir(path: Path | str) -> Path:
    resolved = resolve_data_dir(path)
    payload = {"data_dir": format_data_dir(resolved)}
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return resolved


def _resolve_start(initial: Path | str | None) -> Path:
    start = resolve_data_dir(initial) if initial else load_data_dir()
    if start.exists():
        return start if start.is_dir() else start.parent
    return Path.home()


UPLOAD_DIR = PROJECT_ROOT / "uploads"


def gui_browse_available() -> bool:
    """Native folder dialog needs a display (except Windows)."""
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def ingest_client_uploads(files: list[tuple[str, bytes]]) -> dict:
    """Save browser-uploaded .txt/.zip into uploads/ and return saved names + path."""
    import io
    import zipfile

    from poker.online.workspace import WorkspaceStore

    if not files:
        raise ValueError("未找到上传文件")

    upload_dir = UPLOAD_DIR
    upload_dir.mkdir(parents=True, exist_ok=True)
    for old in upload_dir.iterdir():
        if old.is_file():
            old.unlink()

    saved: list[str] = []
    for name, data in files:
        base = Path(name).name
        WorkspaceStore.validate_upload(base, data)
        if base.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    member = Path(info.filename).name
                    if not member.lower().endswith(".txt"):
                        continue
                    raw = zf.read(info)
                    try:
                        WorkspaceStore.validate_upload(member, raw)
                    except ValueError:
                        continue
                    out = (upload_dir / member).resolve()
                    if not str(out).startswith(str(upload_dir.resolve())):
                        continue
                    out.write_bytes(raw)
                    saved.append(member)
        else:
            (upload_dir / base).write_bytes(data)
            saved.append(base)

    if not saved:
        raise ValueError("没有可用的 .txt 牌谱")
    return {"saved": saved, "path": str(upload_dir.resolve())}


def _python_for_gui() -> str:
    """Prefer pythonw.exe on Windows so only the folder dialog appears."""
    exe = Path(sys.executable)
    if sys.platform == "win32" and exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return str(exe)


def _run_browse_process(initial: Path, out_file: Path) -> None:
    flags = 0
    gui_exe = _python_for_gui()
    # python.exe needs a console to own the dialog; pythonw does not.
    if sys.platform == "win32" and Path(gui_exe).name.lower() == "python.exe":
        flags = subprocess.CREATE_NEW_CONSOLE  # type: ignore[attr-defined]

    subprocess.run(
        [gui_exe, str(BROWSE_SCRIPT), str(out_file), str(initial)],
        timeout=600,
        stdin=subprocess.DEVNULL,
        # Do not pipe stdout/stderr — GUI dialogs hang or stay invisible.
        creationflags=flags,
    )


def browse_directory(initial: Path | str | None = None) -> Path | None:
    """Blocking folder dialog via a child process. Returns None if cancelled."""
    start = _resolve_start(initial)
    fd, name = tempfile.mkstemp(prefix="poker_browse_", suffix=".txt")
    os.close(fd)
    out_file = Path(name)
    try:
        if out_file.exists():
            out_file.write_text("", encoding="utf-8")
        _run_browse_process(start, out_file)
        text = out_file.read_text(encoding="utf-8").strip() if out_file.exists() else ""
        if text.startswith("__ERROR__:"):
            raise RuntimeError(text[len("__ERROR__:") :])
        if not text:
            return None
        return Path(text).resolve()
    finally:
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass


def start_browse_job(initial: Path | str | None = None) -> dict:
    """Start folder dialog in a background thread; returns immediately."""
    global _browse_job
    start = _resolve_start(initial)

    if not gui_browse_available():
        return {
            "status": "client_upload",
            "message": "请从你的电脑选择牌谱文件夹（文件会上传到分析服务）。",
        }

    with _browse_lock:
        if _browse_job and _browse_job.get("status") == "pending":
            return {"status": "pending", "message": "已有选择窗口打开，请先完成或关闭它。"}

        job: dict = {"status": "pending", "path": None, "error": None, "started": time.time()}
        _browse_job = job

    def worker() -> None:
        global _browse_job
        try:
            chosen = browse_directory(start)
            with _browse_lock:
                if chosen is None:
                    job["status"] = "cancelled"
                    job["path"] = None
                else:
                    job["status"] = "done"
                    job["path"] = str(chosen)
        except Exception as exc:  # noqa: BLE001
            with _browse_lock:
                job["status"] = "error"
                job["error"] = str(exc)
        finally:
            with _browse_lock:
                _browse_job = job

    threading.Thread(target=worker, daemon=True).start()
    return {"status": "pending", "message": "请在弹出的窗口中选择文件夹。"}


def browse_job_status() -> dict:
    with _browse_lock:
        if not _browse_job:
            return {"status": "idle"}
        return {
            "status": _browse_job.get("status", "idle"),
            "path": _browse_job.get("path"),
            "error": _browse_job.get("error"),
            "message": _browse_job.get("message"),
        }
