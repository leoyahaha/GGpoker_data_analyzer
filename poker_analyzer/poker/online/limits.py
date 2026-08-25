from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from poker.online.settings import OnlineSettings


class BusyError(RuntimeError):
    def __init__(self, message: str, *, retry_after: int = 30) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def available_memory_mb() -> float | None:
    """Best-effort free RAM in MB (Linux /proc; None if unknown)."""
    try:
        meminfo = PathMeminfo.read()
        if meminfo is None:
            return None
        # Prefer MemAvailable; fall back to MemFree + Cached.
        if "MemAvailable" in meminfo:
            return meminfo["MemAvailable"] / 1024.0
        free = meminfo.get("MemFree", 0) + meminfo.get("Cached", 0) + meminfo.get("Buffers", 0)
        return free / 1024.0
    except OSError:
        return None


class PathMeminfo:
    @staticmethod
    def read() -> dict[str, int] | None:
        path = "/proc/meminfo"
        try:
            text = open(path, encoding="utf-8").read()
        except OSError:
            return None
        out: dict[str, int] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, rest = line.split(":", 1)
            parts = rest.strip().split()
            if not parts:
                continue
            try:
                out[key] = int(parts[0])
            except ValueError:
                continue
        return out


class ResourceGate:
    """Simple process-local concurrency + free-memory gate."""

    def __init__(self, settings: OnlineSettings) -> None:
        self.settings = settings
        self._import = threading.BoundedSemaphore(max(1, settings.import_slots))
        self._heavy = threading.BoundedSemaphore(max(1, settings.heavy_slots))
        self._light = threading.BoundedSemaphore(max(1, settings.light_slots))

    def _check_memory(self) -> None:
        free = available_memory_mb()
        if free is None:
            return
        if free < self.settings.min_free_mb:
            raise BusyError(
                f"服务器内存不足（可用约 {free:.0f} MB），请稍后再试",
                retry_after=60,
            )

    @contextmanager
    def slot(self, kind: str) -> Iterator[None]:
        sem = {
            "import": self._import,
            "heavy": self._heavy,
            "light": self._light,
        }.get(kind, self._light)
        acquired = sem.acquire(blocking=False)
        if not acquired:
            raise BusyError("服务器繁忙，请稍后再试", retry_after=30)
        try:
            if kind in ("import", "heavy"):
                self._check_memory()
            yield
        finally:
            sem.release()
