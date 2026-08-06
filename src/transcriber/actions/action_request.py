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

    # Value objects must not gain transport-specific fields or change after
    # validation because they are compared as durable request data.
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class CommandActionOrigin(ImmutableRequestModel):
    """Identify the command whose interpretation submitted an action."""

    # Discriminator used when serializing and parsing the ActionOrigin union.
    type: Literal["command"] = "command"
    # Bundle that owned the command at submission time; a merge may later move
    # the command receipt while this original identity remains unchanged.
    bundle_id: BundleId
    # Stable command identity used to find and update its durable receipt.
    command_id: str = Field(min_length=1)


class HttpActionOrigin(ImmutableRequestModel):
    """Identify an action submitted through the local HTTP API."""

    # Discriminator used when serializing and parsing the ActionOrigin union.
    type: Literal["http"] = "http"


type ActionOrigin = Annotated[
    CommandActionOrigin | HttpActionOrigin,
    Field(discriminator="type"),
]


class ActionError(ImmutableRequestModel):
    """Bounded diagnostic information safe to expose to request clients."""

    # Stable machine-readable category suitable for client decisions.
    code: str = Field(min_length=1, max_length=ACTION_ERROR_CODE_MAX_LENGTH)
    # Human-readable explanation safe to return through the public API.
    message: str = Field(min_length=1, max_length=ACTION_ERROR_MESSAGE_MAX_LENGTH)
    # Optional bounded context that must not contain internal exception details.
    details: str | None = Field(default=None, max_length=ACTION_ERROR_DETAILS_MAX_LENGTH)


class ActionEffects(ImmutableRequestModel):
    """Bundle identities whose queued work must be reconsidered after execution."""

    # Existing bundles whose derived jobs must be recalculated from current state.
    changed_bundle_ids: tuple[BundleId, ...] = ()
    # Deleted bundles that must be removed from scheduler state and queued work.
    removed_bundle_ids: tuple[BundleId, ...] = ()


class ActionResultModel(ImmutableRequestModel):
    """Common scheduler effects returned by every terminal outcome."""

    # Scheduler-visible state changes produced even by failed or blocked actions.
    effects: ActionEffects = Field(default_factory=ActionEffects)


class ActionSucceeded(ActionResultModel):
    """Successful execution and its scheduler-visible effects."""

    # Discriminator identifying a successful terminal result.
    status: Literal["succeeded"] = "succeeded"


class ActionFailed(ActionResultModel):
    """Terminal execution failure that is safe to present to a user."""

    # Discriminator identifying an expected, non-retryable failure.
    status: Literal["failed"] = "failed"
    # Bounded explanation of why the action could not be completed.
    error: ActionError


class ActionBlocked(ActionResultModel):
    """Unsafe-to-repeat outcome requiring inspection or intervention."""

    # Discriminator identifying an outcome that requires human inspection.
    status: Literal["blocked"] = "blocked"
    # Bounded explanation of why automatic repetition would be unsafe.
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

    # Reject unknown persistence fields while allowing validated lifecycle updates;
    # immutable identity and intent fields are frozen individually below.
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", validate_assignment=True)

    # Version of the durable request representation stored in SQLite.
    schema_version: Literal[1] = ACTION_REQUEST_SCHEMA_VERSION
    # Immutable identity used for idempotent submission and status lookup.
    request_id: ActionRequestId = Field(default_factory=new_action_request_id, frozen=True)
    # Immutable operation intent that the executor must apply exactly once.
    action: Action = Field(frozen=True)
    # Immutable submitter identity used to route or reconcile the terminal receipt.
    origin: ActionOrigin = Field(frozen=True)
    # Current lifecycle phase, independent from origin acknowledgement.
    status: ActionRequestStatus = "pending"
    # Number of times execution actually entered the running phase.
    attempt_count: int = Field(default=0, ge=0)
    # Durable acceptance time used for oldest-first request ordering.
    created_at: AwareDatetime = Field(default_factory=_utc_now, frozen=True)
    # Time the execution attempt entered the running phase.
    started_at: AwareDatetime | None = None
    # Time the request reached a terminal status.
    finished_at: AwareDatetime | None = None
    # Earliest time a terminal request becomes eligible for retention cleanup.
    expires_at: AwareDatetime | None = None
    # Client-safe failure information, present only for failed or blocked requests.
    error: ActionError | None = None
    # Set only after the terminal outcome is reflected in the origin's own
    # durable state; it does not indicate when action execution completed.
    acknowledged_at: AwareDatetime | None = None
