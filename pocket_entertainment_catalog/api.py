"""Framework-independent HTTP API and trusted-LAN request policy."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import logging
import re
import socket
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from .controller import ControllerError
from .model import ModelError
from .service import CatalogService, DeletedEntry, InvalidRequest, InvalidTransition
from .storage import (
    CatalogBusy,
    CatalogChangedExternally,
    CatalogConflict,
    CatalogNotFound,
    StorageError,
)


MAX_JSON_BODY = 128 * 1024
_HOSTNAME = re.compile(r"^[A-Za-z0-9.-]{1,253}$")


@dataclass(frozen=True)
class APIResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


class RequestError(RuntimeError):
    code = "bad_request"
    status = 400


class ForbiddenRequest(RequestError):
    code = "forbidden_request"
    status = 403


class UnsupportedMediaType(RequestError):
    code = "unsupported_media_type"
    status = 415


class CatalogAPI:
    def __init__(
        self,
        service: CatalogService,
        *,
        allowed_hosts: set[str] | None = None,
        work_service=None,
    ) -> None:
        self.service = service
        self.work_service = work_service
        self._allowed_hosts = {
            host.casefold().rstrip(".") for host in (allowed_hosts or set()) if host
        }
        self._allowed_hosts.update({"localhost", "127.0.0.1", "::1"})
        try:
            self._allowed_hosts.update(
                {socket.gethostname().casefold(), socket.getfqdn().casefold()}
            )
        except OSError:
            pass

    def handle(
        self,
        method: str,
        target: str,
        headers: Mapping[str, str],
        body: bytes = b"",
        *,
        client_address: str | None = None,
    ) -> APIResponse:
        try:
            if client_address is not None:
                self.validate_peer(client_address)
            method = method.upper()
            mutating = method in {"POST", "PATCH", "PUT", "DELETE"}
            self.validate_request(headers, mutating=mutating)
            target_parts = urlsplit(target)
            segments = self._segments(target_parts.path)

            if method == "GET" and segments == ["api", "status"]:
                return self._json(200, {"server": "available"})
            if segments[:2] == ["api", "controller"]:
                return self._controller_route(method, segments, headers, body)
            with self.service.controller.operation(
                self._header(headers, "X-Page-Id"),
                self._header(headers, "X-Controller-Token"),
            ):
                return self._dispatch(method, segments, target_parts.query, headers, body)
        except Exception as error:
            return self._exception(error)

    def _dispatch(self, method, segments, query, headers, body) -> APIResponse:
        if segments[:2] == ["api", "work"] and self.work_service is not None:
            return self._work_route(method, segments[2:], query, headers, body)

        if method == "GET" and segments == ["api", "state"]:
            return self._json(200, self.service.catalog_state())

        if method == "GET" and segments == ["api", "catalog"]:
            query = parse_qs(query, keep_blank_values=True)
            unknown = set(query) - {"deleted"}
            if unknown:
                raise RequestError("Catalog query contains unknown parameters.")
            deleted = query.get("deleted", ["exclude"])
            if len(deleted) != 1 or deleted[0] not in {"exclude", "include"}:
                raise RequestError("deleted must be exclude or include.")
            return self._json(
                200,
                self.service.catalog(include_deleted=deleted[0] == "include"),
            )

        if method == "GET" and len(segments) == 3 and segments[:2] == ["api", "entries"]:
            return self._json(200, self.service.entry(segments[2]))

        if method == "GET" and segments == ["api", "sync"]:
            return self._json(200, {"sync": self.service.git_sync.snapshot().as_dict()})

        if method == "POST" and segments == ["api", "entries"]:
            return self._json(201, self.service.create(self._json_body(headers, body)))

        is_entry = len(segments) >= 3 and segments[:2] == ["api", "entries"]
        if is_entry:
            record_id = segments[2]
            expected_etag = self._required_header(headers, "If-Match")
            payload = self._json_body(headers, body) if method in {"POST", "PATCH", "PUT"} else {}
            if method == "PATCH" and len(segments) == 3:
                return self._json(
                    200,
                    self.service.update(record_id, payload, expected_etag=expected_etag),
                )
            if method == "DELETE" and len(segments) == 3:
                if body:
                    raise RequestError("DELETE request must not contain a body.")
                return self._json(
                    200,
                    self.service.delete(record_id, expected_etag=expected_etag),
                )
            if method == "POST" and segments[3:] == ["transition"]:
                return self._json(
                    200,
                    self.service.transition(record_id, payload, expected_etag=expected_etag),
                )
            if method == "PUT" and segments[3:] == ["history"]:
                return self._json(
                    200,
                    self.service.correct_history(record_id, payload, expected_etag=expected_etag),
                )
            if method == "POST" and segments[3:] == ["restore"]:
                if payload:
                    raise RequestError("Restore request body must be empty.")
                return self._json(
                    200,
                    self.service.restore(record_id, expected_etag=expected_etag),
                )

        if method == "POST" and segments == ["api", "sync", "retry"]:
            payload = self._json_body(headers, body)
            if payload:
                raise RequestError("Sync retry body must be empty.")
            return self._json(200, self.service.retry_sync())

        return self._error(404, "not_found", "The requested API route does not exist.")

    def _work_route(self, method, segments, query, headers, body) -> APIResponse:
        work = self.work_service
        if method == "GET" and segments == ["catalog"]:
            values = parse_qs(query, keep_blank_values=True)
            if any(len(v) != 1 for v in values.values()):
                raise RequestError("Work filters must not be repeated.")
            return self._json(200, work.catalog({k: v[0] for k, v in values.items()}))
        if method == "GET" and segments == ["state"]:
            return self._json(200, work.store.state())
        if method == "GET" and segments == ["categories"]:
            with work.store.connection() as db:
                return self._json(200, {"categories": work.store.categories(db)})
        if method == "GET" and segments == ["backup"]:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "editing.sqlite3"
                work.store.backup(destination)
                return APIResponse(
                    200,
                    (
                        ("Content-Type", "application/vnd.sqlite3"),
                        ("Content-Disposition", 'attachment; filename="editing-backup.sqlite3"'),
                        ("Cache-Control", "no-store"),
                    ),
                    destination.read_bytes(),
                )
        if method == "GET" and segments == ["export"]:
            return APIResponse(
                200,
                (
                    ("Content-Type", "application/json; charset=utf-8"),
                    ("Content-Disposition", 'attachment; filename="editing-export.json"'),
                    ("Cache-Control", "no-store"),
                ),
                json.dumps(work.store.export(), ensure_ascii=False, indent=2).encode("utf-8"),
            )
        if method == "GET" and len(segments) == 2 and segments[0] == "entries":
            return self._json(200, work.entry(segments[1]))
        if method not in {"POST", "PATCH", "PUT", "DELETE"}:
            return self._error(404, "not_found", "The requested Work route does not exist.")
        if method == "POST" and segments == ["entries"]:
            return self._json(201, work.create(self._json_body(headers, body)))
        if method == "POST" and segments == ["legacy"]:
            return self._json(201, work.create_legacy(self._json_body(headers, body)))
        if segments and segments[0] == "categories":
            if method == "POST" and len(segments) == 1:
                payload = self._json_body(headers, body)
                if set(payload) != {"name"}:
                    raise RequestError("Category requests require exactly a name.")
                return self._json(201, work.store.change_category(payload["name"]))
            if len(segments) == 2 and method in {"PATCH", "DELETE"}:
                etag = self._required_header(headers, "If-Match")
                if method == "DELETE":
                    if body:
                        raise RequestError("DELETE must not contain a body.")
                    return self._json(
                        200,
                        work.store.change_category(
                            category_id=segments[1], expected_etag=etag, delete=True
                        ),
                    )
                payload = self._json_body(headers, body)
                if set(payload) != {"name"}:
                    raise RequestError("Category requests require exactly a name.")
                return self._json(
                    200,
                    work.store.change_category(
                        payload["name"], category_id=segments[1], expected_etag=etag
                    ),
                )
        if len(segments) >= 2 and segments[0] == "entries":
            etag = self._required_header(headers, "If-Match")
            record_id = segments[1]
            if method == "DELETE" and len(segments) == 2:
                if body:
                    raise RequestError("DELETE must not contain a body.")
                return self._json(200, work.delete(record_id, expected_etag=etag))
            operation = None
            if method == "PATCH" and len(segments) == 2:
                operation = work.update
            elif method == "POST" and segments[2:] == ["transition"]:
                operation = work.transition
            elif method == "PUT" and segments[2:] == ["history"]:
                operation = work.correct_history
            elif method == "POST" and segments[2:] == ["restore"]:
                if self._json_body(headers, body):
                    raise RequestError("Restore request body must be empty.")
                return self._json(200, work.restore(record_id, expected_etag=etag))
            if operation:
                return self._json(
                    200, operation(record_id, self._json_body(headers, body), expected_etag=etag)
                )
        return self._error(404, "not_found", "The requested Work route does not exist.")

    def validate_peer(self, client_address: str) -> None:
        try:
            address = ipaddress.ip_address(client_address.split("%", 1)[0])
        except ValueError as error:
            raise ForbiddenRequest("Client address is invalid.") from error
        if not (address.is_private or address.is_loopback):
            raise ForbiddenRequest("Only private local-network clients are allowed.")

    def validate_request(self, headers: Mapping[str, str], *, mutating: bool) -> None:
        host_header = self._required_header(headers, "Host")
        hostname = self._hostname(host_header)
        if hostname.casefold().rstrip(".") not in self._allowed_hosts:
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError as error:
                raise ForbiddenRequest("Host is not allowed.") from error
            if not (address.is_private or address.is_loopback):
                raise ForbiddenRequest("Host is not allowed.")

        if mutating:
            origin = self._header(headers, "Origin")
            if origin:
                parsed = urlsplit(origin)
                if parsed.scheme != "http" or not parsed.netloc:
                    raise ForbiddenRequest("Origin is not allowed.")
                if self._authority(parsed.netloc) != self._authority(host_header):
                    raise ForbiddenRequest("Cross-origin mutations are not allowed.")

    def _controller_route(self, method, segments, headers, body) -> APIResponse:
        if method != "POST" or len(segments) != 3:
            return self._error(404, "not_found", "The requested API route does not exist.")
        payload = self._json_body(headers, body)
        action = segments[2]
        expected = {"page_id", "token"} if action == "release" else {"page_id"}
        if set(payload) != expected:
            raise RequestError("Controller request fields are invalid.")
        page_id = payload.get("page_id")
        controller = self.service.controller
        if action == "claim":
            result = controller.claim(page_id)
        elif action == "takeover":
            result = controller.takeover(page_id)
        elif action == "release":
            controller.release(page_id, payload.get("token"))
            result = controller.snapshot()
        else:
            return self._error(404, "not_found", "The requested API route does not exist.")
        return self._json(200, result)

    def _json_body(self, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
        content_type = self._header(headers, "Content-Type").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise UnsupportedMediaType("State-changing requests require application/json.")
        if len(body) > MAX_JSON_BODY:
            raise RequestError("Request body is too large.")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise RequestError("Request body is not valid JSON.") from error
        if not isinstance(value, dict):
            raise RequestError("Request body must be an object.")
        return value

    @staticmethod
    def _segments(path: str) -> list[str]:
        try:
            segments = [unquote(part, errors="strict") for part in path.split("/") if part]
        except UnicodeError as error:
            raise RequestError("Request path is invalid.") from error
        if any(part in {".", ".."} or "/" in part or "\\" in part for part in segments):
            raise RequestError("Request path is unsafe.")
        return segments

    @staticmethod
    def _identity(value: Any, name: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{1,128}", value):
            raise RequestError(f"{name} is invalid.")
        return value

    @classmethod
    def _required_header(cls, headers: Mapping[str, str], name: str) -> str:
        value = cls._header(headers, name)
        if not value:
            raise RequestError(f"{name} header is required.")
        return value

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> str:
        target = name.casefold()
        for key, value in headers.items():
            if key.casefold() == target:
                return str(value).strip()
        return ""

    @staticmethod
    def _hostname(host_header: str) -> str:
        try:
            parsed = urlsplit(f"//{host_header}")
        except ValueError as error:
            raise ForbiddenRequest("Host header is invalid.") from error
        hostname = parsed.hostname
        if not hostname or (not _HOSTNAME.fullmatch(hostname) and ":" not in hostname):
            raise ForbiddenRequest("Host header is invalid.")
        return hostname

    @classmethod
    def _authority(cls, value: str) -> tuple[str, int]:
        parsed = urlsplit(f"//{value}")
        if not parsed.hostname:
            raise ForbiddenRequest("Request authority is invalid.")
        try:
            port = parsed.port or 80
        except ValueError as error:
            raise ForbiddenRequest("Request authority is invalid.") from error
        return parsed.hostname.casefold().rstrip("."), port

    def _exception(self, error: Exception) -> APIResponse:
        if isinstance(error, (RequestError, InvalidRequest, ModelError, ControllerError)):
            return self._error(
                getattr(error, "status", 400), getattr(error, "code", "bad_request"), str(error)
            )
        if isinstance(error, CatalogNotFound):
            return self._error(404, error.code, str(error))
        if isinstance(error, CatalogConflict):
            return self._error(412, error.code, str(error))
        if isinstance(
            error, (DeletedEntry, InvalidTransition, CatalogChangedExternally, CatalogBusy)
        ):
            return self._error(409, getattr(error, "code", "conflict"), str(error))
        if isinstance(error, StorageError):
            logging.getLogger(__name__).exception("A storage operation failed", exc_info=error)
            return self._error(500, error.code, "The catalog could not be saved safely.")
        logging.getLogger(__name__).exception("An API operation failed", exc_info=error)
        return self._error(500, "internal_error", "The server could not complete the request.")

    @staticmethod
    def _json(status: int, payload: Mapping[str, Any]) -> APIResponse:
        body = json.dumps(
            {"ok": True, **payload}, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return APIResponse(
            status,
            (("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")),
            body,
        )

    @staticmethod
    def _error(status: int, code: str, message: str) -> APIResponse:
        body = json.dumps(
            {"ok": False, "error": {"code": code, "message": message}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return APIResponse(
            status,
            (("Content-Type", "application/json; charset=utf-8"), ("Cache-Control", "no-store")),
            body,
        )
