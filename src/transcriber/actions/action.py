"""Typed user intent supported by the action-request boundary."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from transcriber.bundle_id import BundleId
from transcriber.bundle_title import RequestedBundleTitle


class ActionModel(BaseModel):
    """Immutable action value that rejects fields outside its schema."""

    # Actions are durable intent, so neither their fields nor their schema may
    # be changed after validation.
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class PreviousBundleTarget(ActionModel):
    """Resolve the previous bundle according to merge policy at execution time."""

    # Discriminator for a target selected from bundle chronology at execution.
    type: Literal["previous"] = "previous"


class BundleTarget(ActionModel):
    """Target a bundle by its stable persistent identity."""

    # Discriminator for an explicitly identified merge target.
    type: Literal["bundle_id"] = "bundle_id"
    # Persistent identity of the bundle that should receive the merge.
    bundle_id: BundleId


type MergeTarget = Annotated[
    PreviousBundleTarget | BundleTarget,
    Field(discriminator="type"),
]


class MergeAction(ActionModel):
    """Merge a source bundle into a deferred or explicit target."""

    # Discriminator used when parsing the Action union.
    type: Literal["merge"] = "merge"
    # Persistent identity of the bundle whose contents will be moved.
    source_bundle_id: BundleId
    # Rule or explicit identity used to select the receiving bundle.
    target: MergeTarget


class DeleteAction(ActionModel):
    """Delete one bundle from the managed store."""

    # Discriminator used when parsing the Action union.
    type: Literal["delete"] = "delete"
    # Persistent identity of the bundle to remove.
    bundle_id: BundleId


class SetTitleAction(ActionModel):
    """Request a manual title that the bundle mutation layer will normalize."""

    # Discriminator used when parsing the Action union.
    type: Literal["set_title"] = "set_title"
    # Persistent identity of the bundle whose title should change.
    bundle_id: BundleId
    # Requested text preserved until the shared title mutation logic normalizes it.
    title: RequestedBundleTitle


type Action = Annotated[
    MergeAction | DeleteAction | SetTitleAction,
    Field(discriminator="type"),
]

_ACTION_ADAPTER = TypeAdapter(Action)


def parse_action(value: object) -> Action:
    """Validate an untyped value as one of the supported actions."""
    return _ACTION_ADAPTER.validate_python(value)
