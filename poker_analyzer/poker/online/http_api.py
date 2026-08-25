from __future__ import annotations

import json
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import unquote

from poker.filters import FilterSpec
from poker.online.auth import AuthManager
from poker.online.limits import BusyError, ResourceGate
from poker.online.settings import OnlineSettings
from poker.online.workspace import WorkspaceStore


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str, list[tuple[str, str]]]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8", []


def _error(message: str, status: int, *, retry_after: int | None = None) -> tuple[int, bytes, str, list[tuple[str, str]]]:
    headers: list[tuple[str, str]] = []
    payload: dict[str, Any] = {"detail": message}
    if retry_after is not None:
        payload["retry_after"] = retry_after
        headers.append(("Retry-After", str(retry_after)))
    status_i, body, ctype, _ = _json_bytes(payload, status=status)
    return status_i, body, ctype, headers


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    data = json.loads(raw.decode("utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("JSON body must be an object")
    return data


def _spec_from_body(body: dict[str, Any] | None) -> FilterSpec:
    return FilterSpec.from_payload(body)


def _options_from_body(body: dict[str, Any] | None) -> dict[str, Any] | None:
    if not body:
        return None
    raw = body.get("options")
    if isinstance(raw, dict):
        return raw
    return None


def _parse_multipart(handler: BaseHTTPRequestHandler) -> list[tuple[str, bytes]]:
    """Return list of (filename, content) from multipart/form-data."""
    ctype = handler.headers.get("Content-Type") or ""
    if "multipart/form-data" not in ctype:
        raise ValueError("需要 multipart/form-data 上传")
    m = re.search(r"boundary=([^;]+)", ctype, flags=re.I)
    if not m:
        raise ValueError("缺少 multipart boundary")
    boundary = m.group(1).strip().strip('"').encode("ascii", errors="ignore")
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        raise ValueError("空上传")
    if length > 200 * 1024 * 1024:
        raise ValueError("单次上传不能超过 200MB")
    raw = handler.rfile.read(length)
    sep = b"--" + boundary
    parts = raw.split(sep)
    files: list[tuple[str, bytes]] = []
    for part in parts:
        if not part or part in (b"--", b"--\r\n", b"\r\n"):
            continue
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        header_blob, _, body = part.partition(b"\r\n\r\n")
        if not body:
            continue
        if body.endswith(b"\r\n"):
            body = body[:-2]
        headers = header_blob.decode("utf-8", errors="replace")
        name_m = re.search(r'filename="([^"]+)"', headers, flags=re.I)
        if not name_m:
            continue
        filename = name_m.group(1)
        files.append((filename, body))
    if not files:
        raise ValueError("未找到上传文件")
    return files


class OnlineApp:
    def __init__(self, settings: OnlineSettings) -> None:
        self.settings = settings
        self.auth = AuthManager(settings)
        self.gate = ResourceGate(settings)
        self.store = WorkspaceStore(settings, self.gate)

    def require_user(self, handler: BaseHTTPRequestHandler) -> str | tuple[int, bytes, str, list[tuple[str, str]]]:
        if not self.auth.password_configured:
            return _error(
                "服务器未配置 POKER_ACCESS_PASSWORD，拒绝启动在线服务",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        session = self.auth.resolve(handler.headers.get("Cookie"))
        if session is None:
            return _error("未登录", HTTPStatus.UNAUTHORIZED)
        return session.user_id

    def handle(self, method: str, path: str, handler: BaseHTTPRequestHandler) -> tuple[int, bytes, str, list[tuple[str, str]]]:
        if path == "/api/auth/status" and method == "GET":
            # resolve updates last_seen; status endpoint should not force auth
            cookie = handler.headers.get("Cookie")
            token_ok = self.auth._token_from_cookie(cookie)  # noqa: SLF001
            authenticated = False
            user_id = None
            if token_ok:
                session = self.auth.resolve(cookie)
                if session:
                    authenticated = True
                    user_id = session.user_id
            return _json_bytes(
                {
                    "configured": self.auth.password_configured,
                    "authenticated": authenticated,
                    "user_id": user_id,
                    "online": True,
                }
            )

        if path == "/api/auth/login" and method == "POST":
            try:
                body = _read_json(handler)
                password = str(body.get("password") or "")
                workspace = str(body.get("workspace") or "").strip() or None
                session = self.auth.login(password, workspace)
                status, body_b, ctype, headers = _json_bytes(
                    {"ok": True, "user_id": session.user_id, "authenticated": True}
                )
                headers = list(headers) + [("Set-Cookie", self.auth.set_cookie_header(session))]
                return status, body_b, ctype, headers
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.UNAUTHORIZED)

        if path == "/api/auth/logout" and method == "POST":
            self.auth.logout(handler.headers.get("Cookie"))
            status, body_b, ctype, headers = _json_bytes({"ok": True})
            headers = list(headers) + [("Set-Cookie", self.auth.clear_cookie_header())]
            return status, body_b, ctype, headers

        user = self.require_user(handler)
        if not isinstance(user, str):
            return user

        if path == "/api/summary" and method == "GET":
            return _json_bytes(self.store.summary(user))

        if path == "/api/load" and method == "POST":
            try:
                # Prefer existing pickle/cache; else kick import if uploads present
                info = self.store.dir_info(user)
                if info.get("loaded") and self.store.pickle_path(user).exists():
                    self.store.ensure_loaded(user)
                    return _json_bytes(self.store.summary(user))
                job = self.store.start_import(user)
                return _json_bytes({"ok": True, "import": job, **self.store.dir_info(user)})
            except FileNotFoundError as exc:
                payload = self.store.dir_info(user)
                payload["error"] = str(exc)
                return _json_bytes(payload, status=HTTPStatus.BAD_REQUEST)
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        if path == "/api/reload" and method == "POST":
            try:
                job = self.store.start_import(user)
                return _json_bytes({"ok": True, "import": job})
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)

        if path == "/api/import/status" and method == "GET":
            return _json_bytes(self.store.import_status(user))

        if path == "/api/upload" and method == "POST":
            try:
                files = _parse_multipart(handler)
                saved_all: list[str] = []
                for name, data in files:
                    result = self.store.save_upload(user, name, data)
                    saved_all.extend(result.get("saved") or [])
                return _json_bytes(
                    {
                        "ok": True,
                        "saved": saved_all,
                        "summary": self.store.dir_info(user),
                    }
                )
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        if path == "/api/upload/clear" and method == "POST":
            self.store.clear_uploads(user)
            return _json_bytes({"ok": True, "summary": self.store.dir_info(user)})

        if path == "/api/unload" and method == "POST":
            self.store.unload(user)
            return _json_bytes({"ok": True})

        # Disabled local-only endpoints
        if path in ("/api/data-dir", "/api/browse-dir", "/api/browse-dir/status"):
            return _error("在线版不支持本地目录选择", HTTPStatus.BAD_REQUEST)

        if path == "/api/metrics" and method == "GET":
            from poker.metrics.base import list_metrics

            return _json_bytes({"metrics": list_metrics()})

        metric_match = re.fullmatch(r"/api/metrics/([^/]+)", path)
        if metric_match:
            metric_id = unquote(metric_match.group(1))
            try:
                if method == "GET":
                    return _json_bytes(self.store.compute_metric(user, metric_id))
                if method == "POST":
                    body = _read_json(handler)
                    return _json_bytes(
                        self.store.compute_metric(
                            user,
                            metric_id,
                            _spec_from_body(body),
                            options=_options_from_body(body),
                        )
                    )
            except KeyError as exc:
                return _error(str(exc), HTTPStatus.NOT_FOUND)
            except FileNotFoundError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        if path == "/api/replay/hand" and method == "POST":
            try:
                body = _read_json(handler)
                source = str(body.get("source") or "").strip()
                if not source:
                    return _error("source is required", HTTPStatus.BAD_REQUEST)
                index = body.get("index", 0)
                return _json_bytes(
                    self.store.replay_hand(
                        user,
                        source,
                        index,
                        _spec_from_body(body),
                        _options_from_body(body),
                    )
                )
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)
            except FileNotFoundError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        if path in ("/api/analyze/profit_curve", "/api/profit/curve"):
            try:
                if method == "GET":
                    return _json_bytes(self.store.compute_metric(user, "profit_curve"))
                if method == "POST":
                    body = _read_json(handler)
                    return _json_bytes(
                        self.store.compute_metric(
                            user,
                            "profit_curve",
                            _spec_from_body(body),
                            options=_options_from_body(body),
                        )
                    )
            except KeyError as exc:
                return _error(str(exc), HTTPStatus.NOT_FOUND)
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)
            except FileNotFoundError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        analyze_match = re.fullmatch(r"/api/analyze/([^/]+)", path)
        if analyze_match and method == "POST":
            metric_id = unquote(analyze_match.group(1))
            try:
                body = _read_json(handler)
                return _json_bytes(
                    self.store.compute_metric(
                        user,
                        metric_id,
                        _spec_from_body(body),
                        options=_options_from_body(body),
                    )
                )
            except KeyError as exc:
                return _error(str(exc), HTTPStatus.NOT_FOUND)
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)
            except FileNotFoundError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        if path == "/api/tools/equity" and method == "POST":
            try:
                from poker.equity import monte_carlo_equity

                body = _read_json(handler)
                player1 = str(body.get("player1") or "").strip()
                player2 = str(body.get("player2") or "").strip()
                board = str(body.get("board") or "").strip()
                samples = int(body.get("samples") or 20000)
                samples = max(1000, min(samples, 50_000))
                if not player1 or not player2:
                    return _error("player1 与 player2 均不能为空", HTTPStatus.BAD_REQUEST)
                with self.gate.slot("heavy"):
                    result = monte_carlo_equity(player1, player2, board or None, samples=samples)
                return _json_bytes(result)
            except BusyError as exc:
                return _error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, retry_after=exc.retry_after)
            except ValueError as exc:
                return _error(str(exc), HTTPStatus.BAD_REQUEST)

        return _error("Not found", HTTPStatus.NOT_FOUND)
