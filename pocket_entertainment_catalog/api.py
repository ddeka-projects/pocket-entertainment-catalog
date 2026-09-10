"""Framework-independent HTTP API and trusted-LAN request policy."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
import re
import socket
from typing import Any, Mapping
from urllib.parse import parse_qs, unquote, urlsplit

from .lease import LeaseConflict, LeaseError, LeaseExpired
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
    ) -> None:
        self.service = service
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

            if method == "GET" and segments == ["api", "state"]:
                return self._json(200, self.service.catalog_state())

            if segments[:2] == ["api", "editor"]:
                return self._editor_route(method, segments, headers, body)

            if method == "GET" and segments == ["api", "catalog"]:
                query = parse_qs(target_parts.query, keep_blank_values=True)
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
                self._authorize_editor(headers)
                return self._json(201, self.service.create(self._json_body(headers, body)))

            is_entry = len(segments) >= 3 and segments[:2] == ["api", "entries"]
            if is_entry:
                self._authorize_editor(headers)
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
                self._authorize_editor(headers)
                payload = self._json_body(headers, body)
                if payload:
                    raise RequestError("Sync retry body must be empty.")
                return self._json(200, self.service.retry_sync())

            return self._error(404, "not_found", "The requested API route does not exist.")
        except Exception as error:
            return self._exception(error)

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

    def _editor_route(
        self,
        method: str,
        segments: list[str],
        headers: Mapping[str, str],
        body: bytes,
    ) -> APIResponse:
        if method != "POST" or len(segments) != 3:
            return self._error(404, "not_found", "The requested API route does not exist.")
        payload = self._json_body(headers, body)
        if set(payload) != {"client_id", "page_id"}:
            raise RequestError("Editor request requires exactly client_id and page_id.")
        client_id = self._identity(payload.get("client_id"), "client_id")
        page_id = self._identity(payload.get("page_id"), "page_id")
        action = segments[2]
        if action == "claim":
            snapshot = self.service.editor_lease.claim(client_id, page_id)
        elif action == "heartbeat":
            snapshot = self.service.editor_lease.heartbeat(client_id, page_id)
        elif action == "release":
            self.service.editor_lease.release(client_id, page_id)
            snapshot = self.service.editor_lease.snapshot()
        else:
            return self._error(404, "not_found", "The requested API route does not exist.")
        return self._json(
            200,
            {"editor": {"claimed": snapshot.claimed, "expires_at": snapshot.expires_at}},
        )

    def _authorize_editor(self, headers: Mapping[str, str]) -> None:
        self.service.editor_lease.authorize(
            self._required_header(headers, "X-Catalog-Client"),
            self._required_header(headers, "X-Catalog-Page"),
        )

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
        if isinstance(error, (RequestError, InvalidRequest, ModelError, LeaseError)):
            status = getattr(error, "status", 400)
            if isinstance(error, LeaseConflict):
                status = 423
            elif isinstance(error, LeaseExpired):
                status = 409
            return self._error(status, getattr(error, "code", "bad_request"), str(error))
        if isinstance(error, CatalogNotFound):
            return self._error(404, error.code, str(error))
        if isinstance(error, CatalogConflict):
            return self._error(412, error.code, str(error))
        if isinstance(error, (DeletedEntry, InvalidTransition, CatalogChangedExternally, CatalogBusy)):
            return self._error(409, getattr(error, "code", "conflict"), str(error))
        if isinstance(error, StorageError):
            return self._error(500, error.code, "The catalog could not be saved safely.")
        return self._error(500, "internal_error", "The server could not complete the request.")

    @staticmethod
    def _json(status: int, payload: Mapping[str, Any]) -> APIResponse:
        body = json.dumps({"ok": True, **payload}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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
