"""Action executors that apply typed intent to the loaded bundle cache."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcriber.actions.action import BundleTarget, DeleteAction, MergeAction, SetTitleAction
from transcriber.actions.action_request import ActionEffects, ActionError, ActionFailed, ActionResult, ActionSucceeded
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command_handlers import delete_bundle, merge_bundles
from transcriber.logger import logger

if TYPE_CHECKING:
    from transcriber.actions.action import Action
    from transcriber.transcribe_bundle import BundleCache, TranscribeBundle


class BundleActionExecutor:
    """Apply one action to the currently loaded bundles and return its result."""

    def __init__(self, bundle_cache: BundleCache) -> None:
        """Bind action execution to the currently loaded bundle state.

        Args:
            bundle_cache: Mutable bundles indexed by persistent identity.

        """
        self._bundle_cache: BundleCache = bundle_cache

    def execute(self, action: Action, /) -> ActionResult:
        """Dispatch a supported action to its sole mutation implementation.

        Args:
            action: Immutable intent to apply to the loaded bundle state.

        Returns:
            The terminal outcome and scheduler-visible bundle effects.

        """
        match action:
            case MergeAction():
                return self._execute_merge(action)
            case DeleteAction():
                return self._execute_delete(action)
            case SetTitleAction():
                return self._execute_set_title(action)

    def _execute_merge(self, action: MergeAction) -> ActionResult:
        """Resolve and merge two bundles without recreating command logic.

        Args:
            action: Source identity and target-selection rule to apply.

        Returns:
            Success with changed/removed bundle effects, or a bounded failure
            when the source or an eligible target no longer exists.

        """
        source = self._bundle_cache.get(action.source_bundle_id)
        if source is None:
            return ActionFailed(
                error=ActionError(code="source_not_found", message="The source bundle no longer exists."),
            )

        target = self._resolve_merge_target(source, action)
        if target is None:
            return ActionFailed(
                error=ActionError(code="target_not_found", message="No eligible merge target was found."),
            )

        gap_hours = (source.get_bundle_date() - target.get_bundle_date()).total_seconds() / 3600
        logger.info(
            f"{source}: Merge target selected -> {target}, gap = {gap_hours:.1f}h "
            f"(merge window: {source.config.general.merge_max_hours:.1f}h)",
        )
        merge_bundles(source=source, target=target, bundles_cache=self._bundle_cache)
        return ActionSucceeded(
            effects=ActionEffects(
                changed_bundle_ids=(target.bundle_id,),
                removed_bundle_ids=(source.bundle_id,),
            ),
        )

    def _resolve_merge_target(self, source: TranscribeBundle, action: MergeAction) -> TranscribeBundle | None:
        """Resolve an explicit or chronology-based target for a merge.

        Args:
            source: Loaded bundle that will be merged into the target.
            action: Merge intent containing the target-selection rule.

        Returns:
            A distinct loaded target, or ``None`` when no eligible target exists.

        """
        if isinstance(action.target, BundleTarget):
            target = self._bundle_cache.get(action.target.bundle_id)
            return target if target is not None and target.bundle_id != source.bundle_id else None
        return source.find_previous_bundle(source, self._bundle_cache.values())

    def _execute_delete(self, action: DeleteAction) -> ActionResult:
        """Delete the identified bundle through the shared mutation function.

        Args:
            action: Persistent identity of the bundle to remove.

        Returns:
            Success with a removal effect, or a bounded not-found failure.

        """
        bundle = self._bundle_cache.get(action.bundle_id)
        if bundle is None:
            return ActionFailed(
                error=ActionError(code="bundle_not_found", message="The bundle no longer exists."),
            )
        delete_bundle(bundle, self._bundle_cache)
        return ActionSucceeded(effects=ActionEffects(removed_bundle_ids=(action.bundle_id,)))

    def _execute_set_title(self, action: SetTitleAction) -> ActionResult:
        """Apply a requested manual title through the shared title logic.

        Args:
            action: Bundle identity and validated requested title.

        Returns:
            Success with a changed-bundle effect, or a bounded not-found failure.

        """
        bundle = self._bundle_cache.get(action.bundle_id)
        if bundle is None:
            return ActionFailed(
                error=ActionError(code="bundle_not_found", message="The bundle no longer exists."),
            )
        bundle.set_and_write_bundle_title(action.title, title_state=BundleTitleState.MANUAL)
        return ActionSucceeded(effects=ActionEffects(changed_bundle_ids=(action.bundle_id,)))
