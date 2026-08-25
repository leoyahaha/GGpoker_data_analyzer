from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# Outside the git repo by default — uploads are permanent operator-owned data.
_DEFAULT_DATA_ROOT = Path.home() / "poker_data"


@dataclass(frozen=True)
class OnlineSettings:
    host: str = "0.0.0.0"
    port: int = 8000
    data_root: Path = _DEFAULT_DATA_ROOT
    idle_ttl_sec: int = 1800
    max_cached_users: int = 2
    max_hands: int = 250_000
    import_slots: int = 1
    heavy_slots: int = 2
    light_slots: int = 8
    min_free_mb: int = 350
    profit_max_points: int = 2500
    session_ttl_sec: int = 60 * 60 * 24 * 30


def load_settings() -> OnlineSettings:
    root = os.environ.get("POKER_DATA_ROOT", "").strip()
    data_root = Path(root).expanduser() if root else _DEFAULT_DATA_ROOT
    if not data_root.is_absolute():
        data_root = (Path.cwd() / data_root).resolve()
    else:
        data_root = data_root.resolve()

    return OnlineSettings(
        host=os.environ.get("POKER_HOST", "0.0.0.0").strip() or "0.0.0.0",
        port=int(os.environ.get("POKER_PORT", "8000") or 8000),
        data_root=data_root,
        idle_ttl_sec=int(os.environ.get("POKER_IDLE_TTL_SEC", "1800") or 1800),
        max_cached_users=int(os.environ.get("POKER_MAX_CACHED_USERS", "2") or 2),
        max_hands=int(os.environ.get("POKER_MAX_HANDS", "250000") or 250_000),
        import_slots=int(os.environ.get("POKER_IMPORT_SLOTS", "1") or 1),
        heavy_slots=int(os.environ.get("POKER_HEAVY_SLOTS", "2") or 2),
        light_slots=int(os.environ.get("POKER_LIGHT_SLOTS", "8") or 8),
        min_free_mb=int(os.environ.get("POKER_MIN_FREE_MB", "350") or 350),
        profit_max_points=int(os.environ.get("POKER_PROFIT_MAX_POINTS", "2500") or 2500),
        session_ttl_sec=int(os.environ.get("POKER_SESSION_TTL_SEC", str(60 * 60 * 24 * 30)) or 2_592_000),
    )


def is_online_mode() -> bool:
    return os.environ.get("POKER_MODE", "").strip().lower() in {"online", "cloud", "1", "true"}
