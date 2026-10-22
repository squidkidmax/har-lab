"""Local, fake-account application for exercising HAR capture end to end.

All accounts are invented. The HTTP server listens only on loopback, stores its
sessions in memory, and suppresses access logs so credentials never reach stdout.
"""

from __future__ import annotations

from contextlib import contextmanager
from html import escape
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
from threading import Lock, Thread
from typing import Iterator
from urllib.parse import parse_qs, urlsplit


DEMO_ACCOUNTS = {
    f"profile_{index:02d}": {
        "password": f"DemoPass-{index:02d}!",
        "name": f"Demo Person {index:02d}",
        "email": f"profile_{index:02d}@example.test",
    }
    for index in range(1, 6)
}


def _html(title: str, content: str, script: str = "") -> bytes:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)} — HAR capture demo</title>"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>body{font:18px system-ui;max-width:680px;margin:48px auto;"
        "padding:0 20px;color:#243040}label{display:block;margin-top:18px}"
        "input,button{font:inherit;padding:10px;margin:8px 0}"
        "button{cursor:pointer}small{display:block;color:#566273}</style>"
        f"</head><body><main>{content}</main>{script}</body></html>"
    ).encode("utf-8")


class _DemoServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int):
        self.sessions: dict[str, str] = {}
        self.state_lock = Lock()
        self.request_number = 0
        super().__init__(("127.0.0.1", port), _DemoHandler)


class _DemoHandler(BaseHTTPRequestHandler):
    server: _DemoServer
    server_version = "HARCaptureDemo/1.0"

    def log_message(self, format: str, *args: object) -> None:
        # Default HTTP logs would expose URL query values from test workflows.
        return

    def _respond(
        self,
        status: int,
        body: bytes = b"",
        *,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
    ) -> None:
        with self.server.state_lock:
            self.server.request_number += 1
            request_number = self.server.request_number
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", f"demo-request-{request_number:06d}")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _session(self) -> tuple[str | None, str | None]:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            item = cookie.get("demo_session")
            token = item.value if item else None
        except CookieError:
            return None, None
        with self.server.state_lock:
            username = self.server.sessions.get(token) if token else None
        return token, username

    def _redirect(self, location: str, cookie: str | None = None) -> None:
        headers = {"Location": location}
        if cookie:
            headers["Set-Cookie"] = cookie
        self._respond(HTTPStatus.SEE_OTHER, headers=headers)

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        _, username = self._session()

        if parsed.path in {"/", "/login"}:
            content = (
                "<h1>Log in</h1><p>Local application with invented test accounts.</p>"
                '<form method="post" action="/login">'
                '<label for="username">Username</label>'
                '<input id="username" name="username" autocomplete="username" required>'
                '<label for="password">Password</label>'
                '<input id="password" name="password" type="password" '
                'autocomplete="current-password" required>'
                '<div><button type="submit">Log in</button></div></form>'
            )
            self._respond(HTTPStatus.OK, _html("Log in", content))
            return

        if parsed.path == "/favicon.ico":
            self._respond(HTTPStatus.NO_CONTENT, content_type="image/x-icon")
            return

        if parsed.path not in {"/dashboard", "/search", "/api/profile"}:
            self._respond(HTTPStatus.NOT_FOUND, _html("Not found", "<h1>Not found</h1>"))
            return

        if username is None:
            if parsed.path == "/api/profile":
                self._respond(
                    HTTPStatus.UNAUTHORIZED,
                    b'{"error":"authentication_required"}',
                    content_type="application/json; charset=utf-8",
                )
            else:
                self._redirect("/login")
            return

        account = DEMO_ACCOUNTS[username]
        if parsed.path == "/api/profile":
            payload = {
                "username": username,
                "name": account["name"],
                "email": account["email"],
                "access_token": f"demo-api-token-{username}",
                "settings": {"theme": "light", "notifications": False},
            }
            self._respond(
                HTTPStatus.OK,
                json.dumps(payload).encode("utf-8"),
                content_type="application/json; charset=utf-8",
            )
            return

        logout = '<form method="post" action="/logout"><button>Log out</button></form>'
        if parsed.path == "/dashboard":
            content = (
                f"<h1>Dashboard</h1><p>Welcome, {escape(account['name'])}.</p>"
                '<p id="profile-ready" data-loaded="false">Loading profile…</p>'
                '<pre id="profile-data"></pre>'
                '<form method="get" action="/search">'
                '<label for="search">Search</label>'
                '<input id="search" name="q" required>'
                '<button type="submit">Search</button></form>'
                + logout
            )
            script = """<script>
fetch('/api/profile', {credentials: 'same-origin'})
  .then(response => {
    if (!response.ok) throw new Error('Profile request failed');
    return response.json();
  })
  .then(profile => {
    document.querySelector('#profile-data').textContent = JSON.stringify(profile, null, 2);
    const marker = document.querySelector('#profile-ready');
    marker.textContent = 'Profile loaded';
    marker.dataset.loaded = 'true';
  })
  .catch(() => {document.querySelector('#profile-ready').textContent = 'Profile failed';});
</script>"""
            self._respond(HTTPStatus.OK, _html("Dashboard", content, script))
            return

        query = parse_qs(parsed.query).get("q", [""])[0]
        content = (
            f"<h1>Search results</h1><p>Results for: <span id=\"search-query\">{escape(query)}</span></p>"
            '<ul><li>Example troubleshooting guide</li><li>Example account guide</li></ul>'
            '<a href="/dashboard">Back to dashboard</a>'
            + logout
        )
        self._respond(HTTPStatus.OK, _html("Search results", content))

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in {"/login", "/logout"}:
            self._respond(HTTPStatus.NOT_FOUND, _html("Not found", "<h1>Not found</h1>"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(HTTPStatus.BAD_REQUEST)
            return
        if length < 0 or length > 16_384:
            self._respond(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        raw_body = self.rfile.read(length)
        if path == "/logout":
            token, _ = self._session()
            with self.server.state_lock:
                self.server.sessions.pop(token, None)
            self._redirect("/login", "demo_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            return
        try:
            form = parse_qs(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._respond(HTTPStatus.BAD_REQUEST)
            return
        username = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        account = DEMO_ACCOUNTS.get(username)
        if account is None or not secrets.compare_digest(
            password.encode("utf-8"), account["password"].encode("utf-8")
        ):
            self._respond(
                HTTPStatus.UNAUTHORIZED,
                _html("Login failed", '<h1>Login failed</h1><a href="/login">Try again</a>'),
            )
            return
        session = f"demo-session-{secrets.token_urlsafe(24)}"
        with self.server.state_lock:
            self.server.sessions[session] = username
        self._redirect("/dashboard", f"demo_session={session}; Path=/; HttpOnly; SameSite=Lax")


@contextmanager
def demo_server(port: int = 0) -> Iterator[str]:
    """Serve invented accounts on loopback; yield the live origin and clean up."""
    server = _DemoServer(port)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
