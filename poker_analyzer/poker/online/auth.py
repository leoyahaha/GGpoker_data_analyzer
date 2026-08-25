from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any

from poker.online.settings import OnlineSettings

COOKIE_NAME = "pa_uid"
# Random id like "a3f9c1e2b8d0476a" — also used as on-disk directory name.
_UID_RE = re.compile(r"^[a-z0-9]{12,32}$")


@dataclass(frozen=True)
class Session:
    user_id: str
    created: float


class AuthManager:
    """Anonymous cookie identity: cookie value == workspace directory name."""

    def __init__(self, settings: OnlineSettings) -> None:
        self.settings = settings
        settings.data_root.mkdir(parents=True, exist_ok=True)

    def ensure(self, cookie_header: str | None) -> tuple[Session, str | None]:
        """Return (session, Set-Cookie header or None if cookie already valid)."""
        existing = self._uid_from_cookie(cookie_header)
        if existing and _UID_RE.fullmatch(existing):
            return Session(user_id=existing, created=time.time()), None
        user_id = secrets.token_hex(8)  # 16 hex chars
        return Session(user_id=user_id, created=time.time()), self.set_cookie_header(user_id)

    def resolve(self, cookie_header: str | None) -> Session | None:
        uid = self._uid_from_cookie(cookie_header)
        if not uid or not _UID_RE.fullmatch(uid):
            return None
        return Session(user_id=uid, created=time.time())

    @staticmethod
    def _uid_from_cookie(cookie_header: str | None) -> str | None:
        if not cookie_header:
            return None
        jar = SimpleCookie()
        try:
            jar.load(cookie_header)
        except Exception:  # noqa: BLE001
            return None
        morsel = jar.get(COOKIE_NAME)
        if not morsel:
            return None
        return (morsel.value or "").strip().lower() or None

    def set_cookie_header(self, user_id: str) -> str:
        max_age = self.settings.session_ttl_sec
        return (
            f"{COOKIE_NAME}={user_id}; Path=/; Max-Age={max_age}; "
            f"HttpOnly; SameSite=Lax"
        )

    def clear_cookie_header(self) -> str:
        return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

    def status(self, cookie_header: str | None) -> dict[str, Any]:
        session, _ = self.ensure(cookie_header)
        return {"authenticated": True, "user_id": session.user_id, "online": True}
