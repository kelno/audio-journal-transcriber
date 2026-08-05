"""Durable action-request identity, lifecycle, origin, and result models."""

from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from transcriber.actions.action import Action
from transcriber.bundle_id import BundleId

ACTION_REQUEST_SCHEMA_VERSION = 1
ACTION_REQUEST_ID_PATTERN = r"^[0-9a-f]{32}$"
ACTION_ERROR_CODE_MAX_LENGTH = 100
ACTION_ERROR_MESSAGE_MAX_LENGTH = 1_000
ACTION_ERROR_DETAILS_MAX_LENGTH = 4_000

type ActionRequestId = Annotated[str, StringConstraints(pattern=ACTION_REQUEST_ID_PATTERN)]
type ActionRequestStatus = Literal[
    # The request is durably accepted but its executor has not started.
    "pending",
    # The synchronous processor has started an execution attempt.
    "running",
    # The action completed successfully.
    "succeeded",
    # The attempt ended unsuccessfully and will not be retried automatically.
    "failed",
    # Repeating the action may be unsafe or requires human intervention.
    "blocked",
]


def new_action_request_id() -> ActionRequestId:
    """Create a lowercase UUID4 hex identifier for a new action request."""
    return uuid4().hex


def _utc_now() -> datetime:
    """Return an aware UTC timestamp for a newly submitted request."""
    return datetime.now(UTC)


class ImmutableRequestModel(BaseModel):
    """Immutable request value that rejects fields outside its schema."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CommandActionOrigin(ImmutableRequestModel):
    """Identify the command whose interpretation submitted an action."""

    type: Literal["command"] = "command"
    bundle_id: BundleId
    command_id: str = Field(min_length=1)


class FilesystemActionOrigin(ImmutableRequestModel):
    """Identify an action submitted through the global filesystem inbox."""

    type: Literal["filesystem"] = "filesystem"


type ActionOrigin = Annotated[
    CommandActionOrigin | FilesystemActionOrigin,
    Field(discriminator="type"),
]


class ActionError(ImmutableRequestModel):
    """Bounded diagnostic information safe to expose to request clients."""

    code: str = Field(min_length=1, max_length=ACTION_ERROR_CODE_MAX_LENGTH)
    message: str = Field(min_length=1, max_length=ACTION_ERROR_MESSAGE_MAX_LENGTH)
    details: str | None = Field(default=None, max_length=ACTION_ERROR_DETAILS_MAX_LENGTH)


class ActionEffects(ImmutableRequestModel):
    """Bundle identities whose queued work must be reconsidered after execution."""

    changed_bundle_ids: tuple[BundleId, ...] = ()
    removed_bundle_ids: tuple[BundleId, ...] = ()


class ActionResultModel(ImmutableRequestModel):
    """Common scheduler effects returned by every terminal outcome."""

    effects: ActionEffects = Field(default_factory=ActionEffects)


class ActionSucceeded(ActionResultModel):
    """Successful execution and its scheduler-visible effects."""

    status: Literal["succeeded"] = "succeeded"


class ActionFailed(ActionResultModel):
    """Terminal execution failure that is safe to present to a user."""

    status: Literal["failed"] = "failed"
    error: ActionError


class ActionBlocked(ActionResultModel):
    """Unsafe-to-repeat outcome requiring inspection or intervention."""

    status: Literal["blocked"] = "blocked"
    error: ActionError


type ActionResult = Annotated[
    ActionSucceeded | ActionFailed | ActionBlocked,
    Field(discriminator="status"),
]

_ACTION_RESULT_ADAPTER = TypeAdapter(ActionResult)


def parse_action_result(value: object) -> ActionResult:
    """Validate an untyped value as a terminal executor result."""
    return _ACTION_RESULT_ADAPTER.validate_python(value)


class ActionRequest(BaseModel):
    """Durable execution envelope for one typed action."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", validate_assignment=True)

    schema_version: Literal[1] = ACTION_REQUEST_SCHEMA_VERSION
    request_id: ActionRequestId = Field(default_factory=new_action_request_id, frozen=True)
    action: Action = Field(frozen=True)
    origin: ActionOrigin = Field(frozen=True)
    status: ActionRequestStatus = "pending"
    attempt_count: int = Field(default=0, ge=0)
    created_at: AwareDatetime = Field(default_factory=_utc_now, frozen=True)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    error: ActionError | None = None
    acknowledged_at: AwareDatetime | None = None
