"""Small loopback HTTP transport for durable action requests."""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import TYPE_CHECKING, ClassVar, cast, final, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from transcriber.actions.action import Action  # noqa: TC001 - discriminator resolution needs runtime types.
from transcriber.actions.action_request import ActionRequestId, HttpActionOrigin
from transcriber.actions.action_request_store import ActionRequestAlreadyExistsError, ActionRequestStoreError

if TYPE_CHECKING:
    from collections.abc import Callable

    from transcriber.actions.action_service import ActionService

MAX_REQUEST_BODY_BYTES = 64 * 1024
_LOGGER = logging.getLogger(__name__)


class HttpActionSubmission(BaseModel):
    """Validated HTTP body for idempotent request submission."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    request_id: ActionRequestId | None = None
    action: Action = Field(discriminator="type")


@final
class _ActionRequestHttpServer(HTTPServer):
    """HTTP server carrying transport dependencies for its request handler."""

    def __init__(
        self,
        address: tuple[str, int],
        service: ActionService,
        on_submission: Callable[[], None],
    ) -> None:
        self.service = service
        self.on_submission = on_submission
        super().__init__(address, _ActionRequestHandler)


@final
class _ActionRequestHandler(BaseHTTPRequestHandler):
    """Translate HTTP messages into transport-neutral service calls."""

    server_version: str = "TranscriberActionAPI/1"

    def do_GET(self) -> None:
        """Return health or one canonical request."""
        if self.path == "/health":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return

        prefix = "/requests/"
        if not self.path.startswith(prefix):
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        request_id = self.path.removeprefix(prefix)
        try:
            request = self._action_server.service.get_request(request_id)
        except ActionRequestStoreError:
            _LOGGER.exception("Could not read action request through HTTP")
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "request_store_error"})
            return
        if request is None:
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "request_not_found"})
            return
        self._write_json(HTTPStatus.OK, request.model_dump(mode="json"))

    def do_POST(self) -> None:
        """Validate and durably submit one action request."""
        if self.path != "/requests":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return

        submission = self._read_submission()
        if submission is not None:
            self._submit(submission)

    def _read_submission(self) -> HttpActionSubmission | None:
        """Read and validate one bounded JSON submission body."""
        if self.headers.get_content_type() != "application/json":
            self._write_json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application_json_required"})
            return None

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content_length"})
            return None
        if content_length <= 0 or content_length > MAX_REQUEST_BODY_BYTES:
            self._write_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "invalid_body_size"})
            return None

        try:
            payload = json.loads(self.rfile.read(content_length))
            return HttpActionSubmission.model_validate(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_json(HTTPStatus.BAD_REQUEST, {"error": "invalid_json"})
            return None
        except ValidationError as error:
            self._write_json(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                {"error": "invalid_request", "details": error.errors(include_url=False, include_context=False)},
            )
            return None

    def _submit(self, submission: HttpActionSubmission) -> None:
        """Submit validated intent and return its canonical request state."""
        try:
            request = self._action_server.service.submit(
                submission.action,
                HttpActionOrigin(),
                request_id=submission.request_id,
            )
        except ActionRequestAlreadyExistsError as error:
            self._write_json(HTTPStatus.CONFLICT, {"error": "request_id_conflict", "message": str(error)})
            return
        except ActionRequestStoreError:
            _LOGGER.exception("Could not submit action request through HTTP")
            self._write_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "request_store_error"})
            return

        # request has been submitted, notify the action server so it can work on it
        self._action_server.on_submission()
        self._write_json(
            HTTPStatus.ACCEPTED,
            request.model_dump(mode="json"),
            extra_headers={"Location": f"/requests/{request.request_id}"},
        )

    @property
    def _action_server(self) -> _ActionRequestHttpServer:
        return cast("_ActionRequestHttpServer", self.server)

    def _write_json(
        self,
        status: HTTPStatus,
        payload: object,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    @override
    def log_message(self, format: str, *args: object) -> None:
        """Route HTTP access messages through application logging."""
        _LOGGER.debug("HTTP %s - %s", self.address_string(), format % args)


@final
class ActionHttpServer:
    """Lifecycle wrapper for the loopback action-request HTTP server."""

    def __init__(
        self,
        host: str,
        port: int,
        service: ActionService,
        on_submission: Callable[[], None],
    ) -> None:
        self._server = _ActionRequestHttpServer((host, port), service, on_submission)
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        """Return the actual bound address, including an OS-selected test port."""
        address = self._server.server_address
        return str(address[0]), int(address[1])

    def start(self) -> None:
        """Start accepting HTTP requests on one transport-only thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="action-http-server",
            daemon=True,
        )
        self._thread.start()
        host, port = self.address
        _LOGGER.info("Action request HTTP API listening on http://%s:%s", host, port)

    def stop(self) -> None:
        """Stop accepting requests and release the listening socket."""
        if self._thread is None:
            self._server.server_close()
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._thread = None
