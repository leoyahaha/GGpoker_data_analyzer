from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

from poker.online.settings import OnlineSettings

COOKIE_NAME = "pa_session"


@dataclass
class Session:
    token: str
    user_id: str
    created: float
    last_seen: float


class AuthManager:
    """Password gate + opaque session cookie → per-user workspace id."""

    def __init__(self, settings: OnlineSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._path = settings.data_root / "_sessions.json"
        settings.data_root.mkdir(parents=True, exist_ok=True)
        self._load()

    @property
    def password_configured(self) -> bool:
        return bool(self.settings.access_password)

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        now = time.time()
        with self._lock:
            for item in raw.get("sessions") or []:
                token = str(item.get("token") or "")
                user_id = str(item.get("user_id") or "")
                if not token or not user_id:
                    continue
                created = float(item.get("created") or now)
                last_seen = float(item.get("last_seen") or created)
                if now - last_seen > self.settings.session_ttl_sec:
                    continue
                self._sessions[token] = Session(token, user_id, created, last_seen)

    def _save(self) -> None:
        with self._lock:
            payload = {
                "sessions": [
                    {
                        "token": s.token,
                        "user_id": s.user_id,
                        "created": s.created,
                        "last_seen": s.last_seen,
                    }
                    for s in self._sessions.values()
                ]
            }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def login(self, password: str, workspace: str | None = None) -> Session:
        if not self.password_configured:
            raise ValueError("服务器未配置 POKER_ACCESS_PASSWORD")
        if not hmac.compare_digest(password, self.settings.access_password):
            raise ValueError("访问密码错误")
        now = time.time()
        token = secrets.token_urlsafe(32)
        user_id = self._workspace_id(workspace)
        session = Session(token=token, user_id=user_id, created=now, last_seen=now)
        with self._lock:
            self._sessions[token] = session
        self._save()
        return session

    def _workspace_id(self, workspace: str | None) -> str:
        name = (workspace or "").strip()
        if not name:
            return secrets.token_hex(8)
        digest = hashlib.sha256(
            f"{name}\0{self.settings.access_password}".encode("utf-8")
        ).hexdigest()
        return digest[:16]

    def resolve(self, cookie_header: str | None) -> Session | None:
        token = self._token_from_cookie(cookie_header)
        if not token:
            return None
        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            now = time.time()
            if now - session.last_seen > self.settings.session_ttl_sec:
                del self._sessions[token]
                session = None
            else:
                session.last_seen = now
        if session is None:
            self._save()
            return None
        self._save()
        return session

    def logout(self, cookie_header: str | None) -> None:
        token = self._token_from_cookie(cookie_header)
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)
        self._save()

    @staticmethod
    def _token_from_cookie(cookie_header: str | None) -> str | None:
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
        return morsel.value or None

    def set_cookie_header(self, session: Session) -> str:
        # Not HttpOnly-only for simplicity with static fetch; still Path=/; SameSite=Lax
        max_age = self.settings.session_ttl_sec
        return (
            f"{COOKIE_NAME}={session.token}; Path=/; Max-Age={max_age}; "
            f"HttpOnly; SameSite=Lax"
        )

    def clear_cookie_header(self) -> str:
        return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

    def status(self, cookie_header: str | None) -> dict[str, Any]:
        session = self.resolve(cookie_header)
        return {
            "configured": self.password_configured,
            "authenticated": session is not None,
            "user_id": session.user_id if session else None,
        }
