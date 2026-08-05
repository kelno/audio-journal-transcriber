"""Typed user intent supported by the action-request boundary."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from transcriber.bundle_id import BundleId
from transcriber.bundle_title import RequestedBundleTitle


class ActionModel(BaseModel):
    """Immutable action value that rejects fields outside its schema."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class PreviousBundleTarget(ActionModel):
    """Resolve the previous bundle according to merge policy at execution time."""

    type: Literal["previous"] = "previous"


class BundleTarget(ActionModel):
    """Target a bundle by its stable persistent identity."""

    type: Literal["bundle_id"] = "bundle_id"
    bundle_id: BundleId


type MergeTarget = Annotated[
    PreviousBundleTarget | BundleTarget,
    Field(discriminator="type"),
]


class MergeAction(ActionModel):
    """Merge a source bundle into a deferred or explicit target."""

    type: Literal["merge"] = "merge"
    source_bundle_id: BundleId
    target: MergeTarget


class DeleteAction(ActionModel):
    """Delete one bundle from the managed store."""

    type: Literal["delete"] = "delete"
    bundle_id: BundleId


class SetTitleAction(ActionModel):
    """Request a manual title that the bundle mutation layer will normalize."""

    type: Literal["set_title"] = "set_title"
    bundle_id: BundleId
    title: RequestedBundleTitle


type Action = Annotated[
    MergeAction | DeleteAction | SetTitleAction,
    Field(discriminator="type"),
]

_ACTION_ADAPTER = TypeAdapter(Action)


def parse_action(value: object) -> Action:
    """Validate an untyped value as one of the supported actions."""
    return _ACTION_ADAPTER.validate_python(value)
