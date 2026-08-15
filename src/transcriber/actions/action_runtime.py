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
    """Own action-request persistence and processing services for one process run."""

    def __init__(self, store: SQLiteActionRequestStore) -> None:
        """Compose one canonical store with its transport-neutral service.

        Args:
            store: Run-scoped SQLite repository shared by every action adapter.

        """
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

    def create_processor(self, bundle_cache: BundleCache) -> ActionProcessor:
        """Create an action processor that works with the current bundle state."""
        return ActionProcessor(self.store, BundleActionExecutor(bundle_cache))

    def process_external_requests(self, bundle_cache: BundleCache) -> list[ActionResult]:
        """Execute pending HTTP requests.

        Command requests are skipped because `RunCommandsJob` must execute them
        and write their result back to the originating bundle.
        """
        if self.store.dry_run:
            return []

        processor = self.create_processor(bundle_cache)
        results: list[ActionResult] = []

        # Repeating an action interrupted in `running` state may be unsafe.
        if interrupted := processor.block_interrupted_request():
            results.append(interrupted)

        for request in self.store.list_pending():
            # Command requests must return through RunCommandsJob so their
            # terminal result is also written to the bundle's command file.
            if isinstance(request.origin, CommandActionOrigin):
                continue
            if result := processor.process(request.request_id):
                results.append(result)

        self.service.prune_expired()
        return results
