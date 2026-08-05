"""Tests for typed actions and action-request lifecycle models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from transcriber.actions.action import (
    BundleTarget,
    DeleteAction,
    MergeAction,
    PreviousBundleTarget,
    SetTitleAction,
    parse_action,
)
from transcriber.actions.action_request import (
    ActionBlocked,
    ActionEffects,
    ActionError,
    ActionFailed,
    ActionRequest,
    ActionSucceeded,
    CommandActionOrigin,
    FilesystemActionOrigin,
    parse_action_result,
)

SOURCE_BUNDLE_ID = "a" * 32
TARGET_BUNDLE_ID = "b" * 32
REQUEST_ID = "c" * 32
COMMAND_ID = "d" * 32


class TestActionSerialization:
    """Actions retain their concrete types across portable serialization."""

    def test_merge_with_previous_target_round_trip(self) -> None:
        """A deferred previous target remains a typed merge target."""
        action = MergeAction(
            source_bundle_id=SOURCE_BUNDLE_ID,
            target=PreviousBundleTarget(),
        )

        loaded = parse_action(action.model_dump(mode="json"))

        assert isinstance(loaded, MergeAction)
        assert isinstance(loaded.target, PreviousBundleTarget)
        assert loaded == action

    def test_merge_with_bundle_target_round_trip(self) -> None:
        """An explicit merge target retains its stable bundle ID."""
        action = parse_action(
            {
                "type": "merge",
                "source_bundle_id": SOURCE_BUNDLE_ID,
                "target": {"type": "bundle_id", "bundle_id": TARGET_BUNDLE_ID},
            },
        )

        assert isinstance(action, MergeAction)
        assert isinstance(action.target, BundleTarget)
        assert action.target.bundle_id == TARGET_BUNDLE_ID

    @pytest.mark.parametrize(
        ("payload", "expected_type"),
        [
            ({"type": "delete", "bundle_id": SOURCE_BUNDLE_ID}, DeleteAction),
            (
                {"type": "set_title", "bundle_id": SOURCE_BUNDLE_ID, "title": "Quarterly planning"},
                SetTitleAction,
            ),
        ],
    )
    def test_action_discriminator_selects_concrete_type(
        self,
        payload: dict[str, object],
        expected_type: type[DeleteAction] | type[SetTitleAction],
    ) -> None:
        """The action tag selects the corresponding strict model."""
        assert isinstance(parse_action(payload), expected_type)

    def test_set_title_preserves_text_for_shared_bundle_normalization(self) -> None:
        """Action validation leaves filesystem normalization to bundle mutation."""
        action = SetTitleAction(
            bundle_id=SOURCE_BUNDLE_ID,
            title="  Quarterly / Planning  ",
        )

        assert action.title == "  Quarterly / Planning  "

    @pytest.mark.parametrize(
        "payload",
        [
            {"type": "unknown", "bundle_id": SOURCE_BUNDLE_ID},
            {"type": "delete", "bundle_id": SOURCE_BUNDLE_ID, "unexpected": True},
            {"type": "set_title", "bundle_id": SOURCE_BUNDLE_ID, "title": "   "},
            {
                "type": "merge",
                "source_bundle_id": SOURCE_BUNDLE_ID,
                "target": {"type": "previous", "bundle_id": TARGET_BUNDLE_ID},
            },
        ],
    )
    def test_invalid_action_payload_is_rejected(self, payload: dict[str, object]) -> None:
        """Unknown tags, extra fields, and invalid values fail validation."""
        with pytest.raises(ValidationError):
            parse_action(payload)


class TestActionRequestSerialization:
    """Requests preserve actions, origins, lifecycle state, and diagnostics."""

    def test_request_defaults(self) -> None:
        """A newly submitted request starts pending with no attempts."""
        request = ActionRequest(
            action=DeleteAction(bundle_id=SOURCE_BUNDLE_ID),
            origin=FilesystemActionOrigin(),
        )

        assert len(request.request_id) == 32
        assert request.status == "pending"
        assert request.attempt_count == 0
        assert request.created_at.tzinfo is UTC
        assert request.started_at is None
        assert request.finished_at is None
        assert request.expires_at is None
        assert request.error is None
        assert request.acknowledged_at is None

    def test_request_round_trip_selects_nested_types(self) -> None:
        """A database-shaped JSON dump loads without losing union branches."""
        created_at = datetime(2026, 8, 5, 15, 29, tzinfo=UTC)
        request = ActionRequest(
            request_id=REQUEST_ID,
            action=MergeAction(
                source_bundle_id=SOURCE_BUNDLE_ID,
                target=BundleTarget(bundle_id=TARGET_BUNDLE_ID),
            ),
            origin=CommandActionOrigin(bundle_id=SOURCE_BUNDLE_ID, command_id=COMMAND_ID),
            created_at=created_at,
        )

        loaded = ActionRequest.model_validate(request.model_dump(mode="json"))

        assert loaded == request
        assert isinstance(loaded.action, MergeAction)
        assert isinstance(loaded.action.target, BundleTarget)
        assert isinstance(loaded.origin, CommandActionOrigin)

    def test_request_rejects_negative_attempt_count(self) -> None:
        """Attempt counts cannot move below their initial value."""
        with pytest.raises(ValidationError):
            ActionRequest(
                action=DeleteAction(bundle_id=SOURCE_BUNDLE_ID),
                origin=FilesystemActionOrigin(),
                attempt_count=-1,
            )

    def test_request_rejects_naive_timestamps(self) -> None:
        """Durable lifecycle timestamps must identify an unambiguous instant."""
        with pytest.raises(ValidationError):
            ActionRequest(
                action=DeleteAction(bundle_id=SOURCE_BUNDLE_ID),
                origin=FilesystemActionOrigin(),
                created_at=datetime(2026, 8, 5, 10),  # noqa: DTZ001 - deliberately invalid input
            )


class TestActionResultSerialization:
    """Terminal executor results use outcome-specific schemas."""

    def test_success_round_trip_preserves_scheduler_effects(self) -> None:
        """Successful merge effects identify changed and removed bundles."""
        result = ActionSucceeded(
            effects=ActionEffects(
                changed_bundle_ids=(TARGET_BUNDLE_ID,),
                removed_bundle_ids=(SOURCE_BUNDLE_ID,),
            ),
        )

        loaded = parse_action_result(result.model_dump(mode="json"))

        assert isinstance(loaded, ActionSucceeded)
        assert loaded == result

    @pytest.mark.parametrize("status", ["failed", "blocked"])
    def test_unsuccessful_result_requires_structured_error(self, status: str) -> None:
        """Failure and blocked outcomes preserve safe user-facing diagnostics."""
        result = parse_action_result(
            {
                "status": status,
                "effects": {"changed_bundle_ids": [SOURCE_BUNDLE_ID]},
                "error": {
                    "code": "merge_failed",
                    "message": "The bundles could not be merged.",
                    "details": "A merge failure marker was created.",
                },
            },
        )

        assert isinstance(result, (ActionFailed, ActionBlocked))
        assert result.effects.changed_bundle_ids == (SOURCE_BUNDLE_ID,)
        assert result.error == ActionError(
            code="merge_failed",
            message="The bundles could not be merged.",
            details="A merge failure marker was created.",
        )

    def test_failed_result_without_error_is_rejected(self) -> None:
        """An unsuccessful result cannot omit its user-facing explanation."""
        with pytest.raises(ValidationError):
            parse_action_result({"status": "failed"})
