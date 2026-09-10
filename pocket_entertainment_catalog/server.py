"""Threaded local HTTP server and static application delivery."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import mimetypes
from pathlib import Path
import socket
import threading
from urllib.parse import urlsplit

from . import __version__
from .api import APIResponse, CatalogAPI, MAX_JSON_BODY, RequestError
from .config import Settings
from .git_sync import GitSync
from .lease import EditorLease
from .service import CatalogService
from .storage import CatalogStore


STATIC_ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/assets/app.js": "app.js",
    "/assets/styles.css": "styles.css",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/icon.svg": "icon.svg",
}


class CatalogHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class PocketCatalogServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = CatalogStore(settings.catalog_path)
        self.lease = EditorLease()
        self.git_sync = GitSync(settings.repository_path, settings.catalog_path)
        self.service = CatalogService(self.store, self.lease, self.git_sync)
        self.public_host = settings.public_host or _discover_lan_ipv4() or "127.0.0.1"
        allowed_hosts = {settings.host, self.public_host}
        self.api = CatalogAPI(self.service, allowed_hosts=allowed_hosts)
        self._server = CatalogHTTPServer(
            (settings.host, settings.port),
            self._handler(),
        )
        self._serving_thread_id: int | None = None

    @property
    def url(self) -> str:
        return f"http://{self.public_host}:{self._server.server_address[1]}/"

    def serve_forever(self) -> None:
        self._serving_thread_id = threading.get_ident()
        try:
            self._server.serve_forever(poll_interval=0.25)
        finally:
            self._serving_thread_id = None

    def close(self) -> None:
        # BaseServer.shutdown() deadlocks when called by the thread currently
        # running serve_forever (notably after Ctrl+C). Only request shutdown
        # when another thread is responsible for the serving loop.
        if (
            self._serving_thread_id is not None
            and self._serving_thread_id != threading.get_ident()
        ):
            self._server.shutdown()
        self._server.server_close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        api = self.api
        static_root = self.settings.static_path

        class Handler(BaseHTTPRequestHandler):
            server_version = "PocketCatalog"
            sys_version = ""
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802
                if self._reject_nonlocal():
                    return
                if urlsplit(self.path).path.startswith("/api/"):
                    self._send(api.handle("GET", self.path, self.headers, client_address=self.client_address[0]))
                else:
                    self._send_static(static_root)

            def do_POST(self) -> None:  # noqa: N802
                self._mutation("POST")

            def do_PATCH(self) -> None:  # noqa: N802
                self._mutation("PATCH")

            def do_PUT(self) -> None:  # noqa: N802
                self._mutation("PUT")

            def do_DELETE(self) -> None:  # noqa: N802
                self._mutation("DELETE")

            def do_OPTIONS(self) -> None:  # noqa: N802
                self._send(api._error(405, "method_not_allowed", "This method is not allowed."))

            def _mutation(self, method: str) -> None:
                if self._reject_nonlocal():
                    return
                raw_length = self.headers.get("Content-Length", "0")
                try:
                    length = int(raw_length)
                except ValueError:
                    self._send(api._error(400, "bad_request", "Content-Length is invalid."))
                    return
                if length < 0 or length > MAX_JSON_BODY:
                    self._send(api._error(413, "request_too_large", "Request body is too large."))
                    return
                body = self.rfile.read(length)
                self._send(
                    api.handle(
                        method,
                        self.path,
                        self.headers,
                        body,
                        client_address=self.client_address[0],
                    )
                )

            def _send_static(self, root: Path) -> None:
                try:
                    api.validate_request(self.headers, mutating=False)
                except RequestError as error:
                    self._send(api._exception(error))
                    return
                path = urlsplit(self.path).path
                filename = STATIC_ROUTES.get(path)
                if filename is None:
                    self._send(api._error(404, "not_found", "The requested asset does not exist."))
                    return
                source = root / filename
                if source.is_symlink() or not source.is_file():
                    self._send(api._error(500, "asset_error", "The web application asset is unavailable."))
                    return
                try:
                    body = source.read_bytes()
                except OSError:
                    self._send(api._error(500, "asset_error", "The web application asset could not be read."))
                    return
                mime = {
                    ".js": "text/javascript; charset=utf-8",
                    ".css": "text/css; charset=utf-8",
                    ".html": "text/html; charset=utf-8",
                    ".webmanifest": "application/manifest+json",
                    ".svg": "image/svg+xml",
                }.get(source.suffix.lower()) or mimetypes.guess_type(source.name)[0] or "application/octet-stream"
                self._send(
                    APIResponse(
                        200,
                        (
                            ("Content-Type", mime),
                            ("Cache-Control", "no-cache" if source.suffix in {".html", ".js", ".css"} else "public, max-age=3600"),
                        ),
                        body,
                    )
                )

            def _reject_nonlocal(self) -> bool:
                try:
                    api.validate_peer(str(self.client_address[0]))
                except RequestError as error:
                    self._send(api._exception(error))
                    return True
                return False

            def _send(self, response: APIResponse) -> None:
                try:
                    self.send_response(response.status)
                    for name, value in response.headers:
                        self.send_header(name, value)
                    self.send_header("Content-Length", str(len(response.body)))
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                        "script-src 'self'; connect-src 'self'; object-src 'none'; "
                        "base-uri 'none'; frame-ancestors 'none'",
                    )
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("X-Frame-Options", "DENY")
                    self.end_headers()
                    if response.body:
                        self.wfile.write(response.body)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler


def _discover_lan_ipv4() -> str | None:
    candidates: list[str] = []
    connection: socket.socket | None = None
    try:
        connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        connection.connect(("192.0.2.1", 9))
        candidates.append(connection.getsockname()[0])
    except OSError:
        pass
    finally:
        if connection is not None:
            connection.close()
    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    for candidate in candidates:
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and address.is_private and not address.is_loopback:
            return str(address)
    return None


def run(settings: Settings) -> None:
    server = PocketCatalogServer(settings)
    print(f"Pocket Entertainment Catalog {__version__}")
    print(f"Catalog: {settings.catalog_path}")
    print(f"Open: {server.url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
