"""
Poker Analyzer — local offline HTTP app (Python stdlib only).

Offline: bind 127.0.0.1. Online: set POKER_MODE=online (+ POKER_ACCESS_PASSWORD).
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from poker.config import browse_job_status, format_data_dir, load_data_dir, resolve_data_dir, start_browse_job
from poker.filters import FilterSpec
from poker.online.settings import is_online_mode, load_settings
from poker.service import get_service

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATE_PATH = BASE_DIR / "templates" / "index.html"

HOST = "127.0.0.1"
PORT = 8000

_ONLINE_APP = None


def _get_online_app():
    global _ONLINE_APP
    if _ONLINE_APP is None:
        from poker.online.http_api import OnlineApp

        _ONLINE_APP = OnlineApp(load_settings())
    return _ONLINE_APP


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes, str]:
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    return status, body, "application/json; charset=utf-8"


def _error(message: str, status: int) -> tuple[int, bytes, str]:
    return _json_bytes({"detail": message}, status=status)


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


def _handle_api(method: str, path: str, handler: BaseHTTPRequestHandler) -> tuple[int, bytes, str]:
    svc = get_service()

    if path == "/api/summary" and method == "GET":
        try:
            return _json_bytes(svc.summary())
        except FileNotFoundError as exc:
            payload = svc.dir_info()
            payload["error"] = str(exc)
            return _json_bytes(payload)

    if path == "/api/load" and method == "POST":
        try:
            svc.ensure_loaded()
            return _json_bytes(svc.summary())
        except FileNotFoundError as exc:
            payload = svc.dir_info()
            payload["error"] = str(exc)
            return _json_bytes(payload, status=HTTPStatus.BAD_REQUEST)

    if path == "/api/reload" and method == "POST":
        svc.reload()
        return _json_bytes(svc.summary())

    if path == "/api/data-dir" and method == "GET":
        return _json_bytes(
            {
                "data_dir": format_data_dir(svc.data_dir),
                "data_dir_resolved": str(svc.data_dir),
                "default": format_data_dir(load_data_dir()),
            }
        )

    if path == "/api/data-dir" and method == "POST":
        body = _read_json(handler)
        raw_path = str(body.get("path") or "").strip()
        if not raw_path:
            return _error("path is required", HTTPStatus.BAD_REQUEST)
        target = resolve_data_dir(raw_path)
        if not target.exists() or not target.is_dir():
            return _error(f"目录不存在: {target}", HTTPStatus.BAD_REQUEST)
        svc.set_data_dir(target)
        try:
            summary = svc.summary()
        except Exception as exc:  # noqa: BLE001 — surface load errors to UI
            return _json_bytes(
                {
                    "ok": True,
                    "data_dir": format_data_dir(svc.data_dir),
                    "data_dir_resolved": str(svc.data_dir),
                    "warning": str(exc),
                    "summary": svc.dir_info(),
                }
            )
        return _json_bytes(
            {
                "ok": True,
                "data_dir": format_data_dir(svc.data_dir),
                "data_dir_resolved": str(svc.data_dir),
                "summary": summary,
            }
        )

    if path == "/api/browse-dir" and method == "POST":
        body = _read_json(handler)
        initial = body.get("initial") or str(svc.data_dir)
        try:
            return _json_bytes(start_browse_job(initial))
        except Exception as exc:  # noqa: BLE001
            return _error(f"打开文件夹对话框失败: {exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    if path == "/api/browse-dir/status" and method == "GET":
        return _json_bytes(browse_job_status())

    if path == "/api/metrics" and method == "GET":
        from poker.metrics.base import list_metrics

        return _json_bytes({"metrics": list_metrics()})

    metric_match = re.fullmatch(r"/api/metrics/([^/]+)", path)
    if metric_match:
        metric_id = unquote(metric_match.group(1))
        try:
            if method == "GET":
                return _json_bytes(svc.compute_metric(metric_id))
            if method == "POST":
                body = _read_json(handler)
                return _json_bytes(
                    svc.compute_metric(
                        metric_id,
                        _spec_from_body(body),
                        options=_options_from_body(body),
                    )
                )
        except KeyError as exc:
            return _error(str(exc), HTTPStatus.NOT_FOUND)
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
                svc.replay_hand(
                    source,
                    index,
                    _spec_from_body(body),
                    _options_from_body(body),
                )
            )
        except ValueError as exc:
            return _error(str(exc), HTTPStatus.BAD_REQUEST)

    if path in ("/api/analyze/profit_curve", "/api/profit/curve"):
        try:
            if method == "GET":
                return _json_bytes(svc.compute_metric("profit_curve"))
            if method == "POST":
                body = _read_json(handler)
                return _json_bytes(
                    svc.compute_metric(
                        "profit_curve",
                        _spec_from_body(body),
                        options=_options_from_body(body),
                    )
                )
        except KeyError as exc:
            return _error(str(exc), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return _error(str(exc), HTTPStatus.BAD_REQUEST)

    analyze_match = re.fullmatch(r"/api/analyze/([^/]+)", path)
    if analyze_match and method == "POST":
        metric_id = unquote(analyze_match.group(1))
        try:
            body = _read_json(handler)
            return _json_bytes(
                svc.compute_metric(
                    metric_id,
                    _spec_from_body(body),
                    options=_options_from_body(body),
                )
            )
        except KeyError as exc:
            return _error(str(exc), HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return _error(str(exc), HTTPStatus.BAD_REQUEST)

    if path == "/api/tools/equity" and method == "POST":
        try:
            from poker.equity import monte_carlo_equity

            body = _read_json(handler)
            player1 = str(body.get("player1") or "").strip()
            player2 = str(body.get("player2") or "").strip()
            board = str(body.get("board") or "").strip()
            samples = int(body.get("samples") or 20000)
            if not player1 or not player2:
                return _error("player1 与 player2 均不能为空", HTTPStatus.BAD_REQUEST)
            return _json_bytes(
                monte_carlo_equity(player1, player2, board or None, samples=samples)
            )
        except ValueError as exc:
            return _error(str(exc), HTTPStatus.BAD_REQUEST)

    return _error("Not found", HTTPStatus.NOT_FOUND)


def _serve_static(rel: str) -> tuple[int, bytes, str] | None:
    # Prevent path traversal
    candidate = (STATIC_DIR / rel).resolve()
    try:
        candidate.relative_to(STATIC_DIR.resolve())
    except ValueError:
        return HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain; charset=utf-8"
    if not candidate.is_file():
        return None
    data = candidate.read_bytes()
    mime, _ = mimetypes.guess_type(str(candidate))
    return HTTPStatus.OK, data, mime or "application/octet-stream"


def _serve_index(*, online: bool = False) -> tuple[int, bytes, str]:
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    title = "Poker Analyzer Online" if online else "Poker Analyzer"
    html = html.replace("{{ title }}", title)
    if online:
        html = html.replace("Local Offline", "Online Cloud")
        html = html.replace('data-mode="local"', 'data-mode="online"')
        if 'data-mode="' not in html:
            html = html.replace("<body>", '<body data-mode="online">', 1)
    else:
        if 'data-mode="' not in html:
            html = html.replace("<body>", '<body data-mode="local">', 1)
    return HTTPStatus.OK, html.encode("utf-8"), "text/html; charset=utf-8"


class LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    online = False

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        try:
            if path.startswith("/api/"):
                if self.online:
                    status, body, ctype, headers = _get_online_app().handle(method, path, self)
                    self._send(status, body, ctype, headers)
                else:
                    status, body, ctype = _handle_api(method, path, self)
                    self._send(status, body, ctype)
                return

            if path in ("/", "/index.html"):
                status, body, ctype = _serve_index(online=self.online)
                self._send(status, body, ctype)
                return

            if path.startswith("/static/"):
                rel = path[len("/static/") :]
                result = _serve_static(rel)
                if result is None:
                    self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")
                    return
                self._send(*result)
                return

            self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            status, body, ctype = _error(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
            try:
                self._send(status, body, ctype)
            except OSError:
                pass

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")


def main() -> int:
    if not (STATIC_DIR / "js" / "chart.umd.min.js").is_file():
        print("[ERROR] Missing local chart.js: static/js/chart.umd.min.js", file=sys.stderr)
        return 1

    online = is_online_mode()
    host, port = HOST, PORT
    if online:
        settings = load_settings()
        host, port = settings.host, settings.port
        if not settings.access_password:
            print(
                "[ERROR] Online mode requires POKER_ACCESS_PASSWORD",
                file=sys.stderr,
            )
            return 1
        _get_online_app()  # init early
        LocalHandler.online = True

    try:
        server = ThreadingHTTPServer((host, port), LocalHandler)
    except OSError as exc:
        print(f"[ERROR] Cannot bind {host}:{port} — {exc}", file=sys.stderr)
        print("Close the old Poker Analyzer window (or free the port) and retry.", file=sys.stderr)
        return 1

    url = f"http://{host}:{port}"
    if online:
        settings = load_settings()
        print("Poker Analyzer (online)")
        print(f"URL: {url}")
        print(f"Data root: {settings.data_root}")
        print(f"Max hands / user: {settings.max_hands}")
        print(f"Idle TTL: {settings.idle_ttl_sec}s · cached users: {settings.max_cached_users}")
        print("Press Ctrl+C to stop.")
    else:
        print("Poker Analyzer (offline)")
        print(f"URL: {url}")
        print(f"Data: {get_service().data_dir}")
        print("Keep this window open. Press Ctrl+C to stop.")
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
