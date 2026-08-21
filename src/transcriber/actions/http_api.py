"""FastAPI transport for durable action requests."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, ClassVar, final

import uvicorn
from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from transcriber.actions.action import Action  # noqa: TC001 - discriminator resolution needs runtime types.
from transcriber.actions.action_request import ActionRequest, ActionRequestId, HttpActionOrigin
from transcriber.actions.action_request_store import ActionRequestAlreadyExistsError, ActionRequestStoreError

if TYPE_CHECKING:
    from collections.abc import Callable

    from transcriber.actions.action_service import ActionService

_SERVER_START_TIMEOUT_SECONDS = 5.0
_SERVER_STOP_TIMEOUT_SECONDS = 5.0
_SERVER_FORCE_STOP_TIMEOUT_SECONDS = 1.0
_LOGGER = logging.getLogger(__name__)


class HttpActionSubmission(BaseModel):
    """Validated HTTP body for idempotent request submission."""

    # Reject transport fields outside the public submission contract.
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    # Optional caller-generated identity that makes an HTTP retry idempotent.
    request_id: ActionRequestId | None = None
    # Typed immutable action selected through its serialized discriminator.
    action: Action = Field(discriminator="type")


def create_action_http_app(
    service: ActionService,
    on_submission: Callable[[], None],
) -> FastAPI:
    """Create the HTTP application around transport-neutral request operations.

    Args:
        service: Durable action-request submission and lookup boundary.
        on_submission: Callback that wakes the single action-processing loop.

    Returns:
        A configured FastAPI application that never executes actions itself.

    """
    app = FastAPI(title="Transcriber Action API", version="1")

    async def get_health() -> dict[str, str]:
        """Return a minimal response showing that the HTTP process is ready."""
        return {"status": "ok"}

    async def get_request(request_id: ActionRequestId) -> ActionRequest | Response:
        """Return canonical durable state for one request ID."""
        try:
            action_request = service.get_request(request_id)
        except ActionRequestStoreError:
            _LOGGER.exception("Could not read action request through HTTP")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "request_store_error"},
            )
        if action_request is None:
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"error": "request_not_found"},
            )
        return action_request

    async def submit_request(
        submission: HttpActionSubmission,
        response: Response,
    ) -> ActionRequest | Response:
        """Durably submit one action, then wake the separate processing loop."""
        try:
            action_request = service.submit(
                submission.action,
                HttpActionOrigin(),
                request_id=submission.request_id,
            )
        except ActionRequestAlreadyExistsError as error:
            return JSONResponse(
                status_code=status.HTTP_409_CONFLICT,
                content={"error": "request_id_conflict", "message": str(error)},
            )
        except ActionRequestStoreError:
            _LOGGER.exception("Could not submit action request through HTTP")
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "request_store_error"},
            )
        response.headers["Location"] = f"/requests/{action_request.request_id}"

        # Submission only records durable intent. The daemon remains the sole
        # action executor, so wake it after the transaction has completed.
        on_submission()
        return action_request

    app.add_api_route("/health", get_health, methods=["GET"])
    app.add_api_route(
        "/requests/{request_id}",
        get_request,
        methods=["GET"],
        response_model=ActionRequest,
    )
    app.add_api_route(
        "/requests",
        submit_request,
        methods=["POST"],
        response_model=ActionRequest,
        status_code=status.HTTP_202_ACCEPTED,
    )

    return app


@final
class ActionHttpServer:
    """Run the FastAPI action transport beside the synchronous daemon loop."""

    def __init__(
        self,
        host: str,
        port: int,
        service: ActionService,
        on_submission: Callable[[], None],
    ) -> None:
        """Configure an embedded Uvicorn server without starting its thread.

        Args:
            host: Network interface to bind.
            port: Listening port, or zero to let the OS select one for tests.
            service: Transport-neutral action-request service.
            on_submission: Callback that wakes the single processing loop.

        """
        app = create_action_http_app(service, on_submission)
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            access_log=False,
            log_config=None,
        )
        # Requested address retained until Uvicorn exposes its bound socket.
        self._configured_address = (host, port)
        # Embedded ASGI server controlled by the daemon lifecycle.
        self._server = uvicorn.Server(config)
        # Background transport thread; action execution stays on the daemon thread.
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        """Return the bound address, including an OS-selected test port."""
        if not self._server.started:
            return self._configured_address
        for running_server in self._server.servers:
            if running_server.sockets:
                host, port, *_ = running_server.sockets[0].getsockname()
                return str(host), int(port)
        return self._configured_address

    def start(self) -> None:
        """Start Uvicorn and wait until its listening socket is ready."""
        if self._thread is not None:
            return

        self._server.should_exit = False
        self._server.force_exit = False
        self._thread = threading.Thread(
            target=self._server.run,
            name="action-http-server",
            daemon=True,
        )
        self._thread.start()

        deadline = time.monotonic() + _SERVER_START_TIMEOUT_SECONDS
        while not self._server.started:
            if not self._thread.is_alive():
                self._thread = None
                msg = "Action request HTTP server stopped during startup"
                raise RuntimeError(msg)
            if time.monotonic() >= deadline:
                self.stop()
                msg = "Action request HTTP server did not start in time"
                raise TimeoutError(msg)
            time.sleep(0.01)

        host, port = self.address
        _LOGGER.info("Action request HTTP API listening on http://%s:%s", host, port)

    def stop(self) -> None:
        """Ask Uvicorn to finish active requests and stop its server thread."""
        if self._thread is None:
            return

        self._server.should_exit = True
        self._thread.join(timeout=_SERVER_STOP_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            self._server.force_exit = True
            self._thread.join(timeout=_SERVER_FORCE_STOP_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            _LOGGER.error("Action request HTTP server did not stop cleanly")
        self._thread = None
