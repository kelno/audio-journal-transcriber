"""Integration tests for Markdown action-request submission."""

from datetime import datetime, timedelta
from pathlib import Path

import yaml

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_file_system import FakeFileSystemService
from transcriber.actions.filesystem_action_requests import ACTION_REQUESTS_DIRECTORY_NAME, FilesystemActionRequestAdapter
from transcriber.config import TranscribeConfig


def _frontmatter(content: str) -> dict[str, object]:
    """Decode the frontmatter projection written by the adapter."""
    loaded = yaml.safe_load(content.split("---", 2)[1])
    assert isinstance(loaded, dict)
    return loaded


def test_manual_merge_round_trips_through_canonical_request(
    tmp_path: Path,
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    transcribe_bundle_factory: TranscribeBundleFactory,
) -> None:
    """A request file receives an ID and terminal status from the shared executor."""
    fake_config.general.store_dir = tmp_path
    target_date = datetime(2026, 8, 6, 9, tzinfo=fake_config.general.timezone)
    target = transcribe_bundle_factory(
        bundle_name="target",
        audio_filename="target.mp3",
        bundle_date=target_date,
    )
    source = transcribe_bundle_factory(
        bundle_name="source",
        audio_filename="source.mp3",
        bundle_date=target_date + timedelta(hours=1),
    )
    bundle_cache = {target.bundle_id: target, source.bundle_id: source}
    request_path = tmp_path / ACTION_REQUESTS_DIRECTORY_NAME / "merge.md"
    fake_fs.write_file(
        request_path,
        f"""---
schema_version: 1
action:
  type: merge
  source_bundle_id: {source.bundle_id}
  target:
    type: bundle_id
    bundle_id: {target.bundle_id}
---
Keep this note.
""",
    )
    adapter = FilesystemActionRequestAdapter(fake_config, fake_fs)

    adapter.process_all(bundle_cache, dry_run=False)

    projection = _frontmatter(fake_fs.read_file(request_path))
    assert isinstance(projection["request_id"], str)
    assert projection["status"] == "succeeded"
    assert source.bundle_id not in bundle_cache
    assert target.bundle_id in bundle_cache
    assert "Keep this note." in fake_fs.read_file(request_path)


def test_existing_request_id_recovers_submission_without_duplicate_execution(
    tmp_path: Path,
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
) -> None:
    """A draft ID persisted before SQLite insertion is reused on the next pass."""
    fake_config.general.store_dir = tmp_path
    request_id = "a" * 32
    request_path = tmp_path / ACTION_REQUESTS_DIRECTORY_NAME / "missing-source.md"
    fake_fs.write_file(
        request_path,
        f"""---
schema_version: 1
request_id: {request_id}
action:
  type: merge
  source_bundle_id: {'b' * 32}
  target:
    type: previous
---
""",
    )
    adapter = FilesystemActionRequestAdapter(fake_config, fake_fs)

    adapter.process_all({}, dry_run=False)
    first_projection = fake_fs.read_file(request_path)
    writes_after_first_pass = len(fake_fs.get_operations("write"))
    adapter.process_all({}, dry_run=False)

    projection = _frontmatter(first_projection)
    assert projection["request_id"] == request_id
    assert projection["status"] == "failed"
    assert len(fake_fs.get_operations("write")) == writes_after_first_pass


def test_dry_run_does_not_create_or_rewrite_request_directory(
    tmp_path: Path,
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
) -> None:
    """Manual submissions remain untouched when mutations are disabled."""
    fake_config.general.store_dir = tmp_path
    adapter = FilesystemActionRequestAdapter(fake_config, fake_fs)

    adapter.process_all({}, dry_run=True)

    assert not fake_fs.directory_exists(adapter.request_directory)
