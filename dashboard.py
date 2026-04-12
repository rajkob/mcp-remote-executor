"""
dashboard.py — ASGI mini-app serving the web dashboard.

Routes:
  GET /dashboard          → serves dashboard.html
  GET /api/status         → JSON metrics for all hosts
  GET /api/status?refresh=1  → force-refresh (bypass cache)

Mounts alongside the FastMCP SSE app via a simple path router.
"""
import json
import hmac
import os
from pathlib import Path

import exec_log
import monitor

STATIC_DIR = Path(__file__).parent / "static"
API_KEY = os.getenv("MCP_API_KEY", "").strip()


def _json_response(data, status=200):
    body = json.dumps(data, default=str).encode()
    return status, [(b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"access-control-allow-origin", b"*")], body


def _html_response(html: str, status=200):
    body = html.encode()
    return status, [(b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(body)).encode())], body


def _auth_ok(scope) -> bool:
    """Allow request if auth is disabled or key matches."""
    if not API_KEY:
        return True
    headers = {k.lower(): v for k, v in scope.get("headers", [])}
    provided = headers.get(b"x-mcp-key", b"").decode()
    if not provided:
        query = scope.get("query_string", b"").decode()
        for param in query.split("&"):
            if param.startswith("api_key="):
                provided = param[8:]
                break
    return provided == API_KEY if not API_KEY else hmac.compare_digest(provided, API_KEY)


class DashboardApp:
    """Lightweight ASGI app handling dashboard routes."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        if method != "GET":
            await self._send(send, *_json_response({"error": "method not allowed"}, 405))
            return

        if path in ("/dashboard", "/dashboard/"):
            await self._serve_html(send)
        elif path == "/api/status":
            if not _auth_ok(scope):
                await self._send(send, *_json_response({"error": "unauthorized"}, 401))
                return
            query = scope.get("query_string", b"").decode()
            force = "refresh=1" in query
            metrics = monitor.get_all_metrics(force=force)
            await self._send(send, *_json_response(metrics))
        elif path == "/api/logs" or path.startswith("/api/logs/"):
            if not _auth_ok(scope):
                await self._send(send, *_json_response({"error": "unauthorized"}, 401))
                return
            # /api/logs          → last 200 entries for all hosts
            # /api/logs/{alias}  → filtered to one host
            alias = path[len("/api/logs/"):] or None
            query = scope.get("query_string", b"").decode()
            n = 200
            for param in query.split("&"):
                if param.startswith("n="):
                    try:
                        n = max(1, min(int(param[2:]), 1000))
                    except ValueError:
                        pass
            entries = exec_log.read(n)
            if alias:
                entries = [e for e in entries if e.get("alias") == alias]
            await self._send(send, *_json_response(entries))
        else:
            await self._send(send, *_json_response({"error": "not found"}, 404))

    async def _serve_html(self, send):
        html_file = STATIC_DIR / "dashboard.html"
        if html_file.exists():
            html = html_file.read_text(encoding="utf-8")
        else:
            html = "<h1>Dashboard not found</h1><p>static/dashboard.html missing.</p>"
        await self._send(send, *_html_response(html))

    @staticmethod
    async def _send(send, status, headers, body):
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


class RouterApp:
    """
    Top-level ASGI router — sends dashboard routes to DashboardApp,
    everything else to the wrapped MCP app.
    """
    DASHBOARD_PATHS = {"/dashboard", "/dashboard/", "/api/status"}

    def __init__(self, mcp_app):
        self.mcp_app = mcp_app
        self.dashboard = DashboardApp()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path in self.DASHBOARD_PATHS or path.startswith("/api/"):
            await self.dashboard(scope, receive, send)
        else:
            await self.mcp_app(scope, receive, send)
