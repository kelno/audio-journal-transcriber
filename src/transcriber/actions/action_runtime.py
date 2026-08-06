"""Run-scoped composition of action-request persistence and processing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcriber.actions.action_executors import BundleActionExecutor
from transcriber.actions.action_request import CommandActionOrigin
from transcriber.actions.action_request_store import SQLiteActionRequestStore, default_action_request_database_path
from transcriber.actions.action_service import ActionProcessor, ActionService

if TYPE_CHECKING:
    from transcriber.actions.action_request import ActionResult
    from transcriber.config import TranscribeConfig
    from transcriber.transcribe_bundle import BundleCache


class ActionRuntime:
    """Own the action store and application services for one process run."""

    def __init__(self, store: SQLiteActionRequestStore) -> None:
        self.store: SQLiteActionRequestStore = store
        self.service: ActionService = ActionService(store)

    @classmethod
    def from_config(cls, config: TranscribeConfig, *, dry_run: bool) -> ActionRuntime:
        """Create the runtime at the configured daemon-owned database path."""
        return cls(
            SQLiteActionRequestStore(
                default_action_request_database_path(config.general.store_dir),
                dry_run=dry_run,
            ),
        )

    def processor(self, bundle_cache: BundleCache) -> ActionProcessor:
        """Bind lifecycle processing to the current loaded bundle state."""
        return ActionProcessor(self.store, BundleActionExecutor(bundle_cache))

    def process_external_requests(self, bundle_cache: BundleCache) -> list[ActionResult]:
        """Recover interrupted work and execute pending non-command requests oldest first."""
        if self.store.dry_run:
            return []

        processor = self.processor(bundle_cache)
        results: list[ActionResult] = []
        if interrupted := processor.block_interrupted_request():
            results.append(interrupted)

        for request in self.store.list_pending():
            if isinstance(request.origin, CommandActionOrigin):
                continue
            if result := processor.process(request.request_id):
                results.append(result)

        self.service.prune_expired()
        return results
