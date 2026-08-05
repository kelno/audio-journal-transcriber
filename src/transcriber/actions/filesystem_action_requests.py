"""Markdown filesystem transport for manual action-request submission."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from transcriber.actions.action import Action  # noqa: TC001 - Pydantic resolves it at runtime.
from transcriber.actions.action_executors import BundleActionExecutor
from transcriber.actions.action_request import ActionRequest, ActionRequestId, FilesystemActionOrigin, new_action_request_id
from transcriber.actions.action_request_store import SQLiteActionRequestStore, default_action_request_database_path
from transcriber.actions.action_service import ActionProcessor, ActionService
from transcriber.logger import logger

if TYPE_CHECKING:
    from pathlib import Path

    from transcriber.config import TranscribeConfig
    from transcriber.files.file_system import FileSystemService
    from transcriber.transcribe_bundle import BundleCache

ACTION_REQUESTS_DIRECTORY_NAME = "_requests"


class _FilesystemSubmission(BaseModel):
    """Validate only user-owned submission fields from request frontmatter."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    request_id: ActionRequestId | None = None
    action: Action = Field(discriminator="type")


class FilesystemActionRequestAdapter:
    """Reconcile Markdown request drafts with canonical SQLite request rows."""

    def __init__(self, config: TranscribeConfig, fs_service: FileSystemService) -> None:
        self._config: TranscribeConfig = config
        self._fs_service: FileSystemService = fs_service
        self.request_directory: Path = config.general.store_dir / ACTION_REQUESTS_DIRECTORY_NAME

    def process_all(self, bundle_cache: BundleCache, *, dry_run: bool) -> None:
        """Submit drafts, process canonical requests, and refresh their projections."""
        if dry_run:
            return
        self._fs_service.ensure_directory_exists(self.request_directory)
        store = SQLiteActionRequestStore(default_action_request_database_path(self._config.general.store_dir))
        service = ActionService(store)
        processor = ActionProcessor(store, BundleActionExecutor(bundle_cache))
        processor.block_interrupted_request()

        request_files = [
            path
            for path in self._fs_service.list_directory(self.request_directory)
            if path.suffix.casefold() == ".md" and self._fs_service.file_exists(path)
        ]
        submitted_files = [
            (request_file, request_id)
            for request_file in request_files
            if (request_id := self._submit_file(request_file, service)) is not None
        ]

        # SQLite creation time, not directory enumeration, is the canonical
        # ordering for accepted requests. Command-origin requests are reconciled
        # by their owning command job because only that adapter can write the
        # terminal receipt back to the command file.
        for request in store.list_pending():
            if isinstance(request.origin, FilesystemActionOrigin):
                processor.process(request.request_id)

        for request_file, request_id in submitted_files:
            self._project_file(request_file, request_id, service)

        deleted_ids = set(service.prune_expired())
        if deleted_ids:
            for request_file in request_files:
                request_id = self._read_request_id(request_file)
                if request_id in deleted_ids and self._fs_service.file_exists(request_file):
                    self._fs_service.delete_file(request_file)

    def _submit_file(self, path: Path, service: ActionService) -> ActionRequestId | None:
        """Validate one draft and ensure its canonical row exists."""
        original = self._fs_service.read_file(path)
        try:
            frontmatter, body = self._parse_document(original)
            submission = _FilesystemSubmission.model_validate(frontmatter)
        except (TypeError, ValueError, yaml.YAMLError, ValidationError) as error:
            logger.warning(f"Invalid action request file {path}: {error}")
            return None

        request_id = submission.request_id or new_action_request_id()
        if submission.request_id is None:
            frontmatter["request_id"] = request_id
            self._write_if_changed(path, self._render_document(frontmatter, body), original)

        service.submit(
            submission.action,
            FilesystemActionOrigin(),
            request_id=request_id,
        )
        return request_id

    def _project_file(self, path: Path, request_id: ActionRequestId, service: ActionService) -> None:
        """Refresh one valid draft from its canonical request state."""
        original = self._fs_service.read_file(path)
        try:
            _frontmatter, body = self._parse_document(original)
        except (TypeError, ValueError, yaml.YAMLError) as error:
            logger.warning(f"Could not project action request file {path}: {error}")
            return
        request = service.get_request(request_id)
        assert request is not None

        projection = self._request_projection(request)
        self._write_if_changed(path, self._render_document(projection, body), original)

    def _read_request_id(self, path: Path) -> ActionRequestId | None:
        try:
            frontmatter, _body = self._parse_document(self._fs_service.read_file(path))
            return _FilesystemSubmission.model_validate(frontmatter).request_id
        except (TypeError, ValueError, yaml.YAMLError, ValidationError):
            return None

    @staticmethod
    def _parse_document(content: str) -> tuple[dict[str, object], str]:
        if not content.startswith("---"):
            msg = "Request document must begin with YAML frontmatter"
            raise ValueError(msg)
        parts = content.split("---", 2)
        if len(parts) != 3:
            msg = "Request document is missing the closing frontmatter delimiter"
            raise ValueError(msg)
        loaded = yaml.safe_load(parts[1])
        if not isinstance(loaded, dict):
            msg = "Request frontmatter must be a mapping"
            raise TypeError(msg)
        return loaded, parts[2].lstrip("\r\n")

    @staticmethod
    def _request_projection(request: ActionRequest) -> dict[str, object]:
        projection = request.model_dump(mode="json")
        projection.pop("origin", None)
        projection.pop("acknowledged_at", None)
        return projection

    @staticmethod
    def _render_document(frontmatter: dict[str, object], body: str) -> str:
        yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
        body_text = f"\n{body}" if body else ""
        return f"---\n{yaml_text}\n---{body_text}\n"

    def _write_if_changed(self, path: Path, content: str, previous: str) -> None:
        if content != previous:
            self._fs_service.write_file(path, content)
