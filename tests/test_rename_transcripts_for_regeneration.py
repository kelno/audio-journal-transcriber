"""Tests for preparing retained audio bundles for a new transcript model."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.rename_transcripts_for_regeneration import prepare_transcript_regeneration
from tests.fake_file_system import FakeFileSystemService
from transcriber.bundle_id import new_bundle_id
from transcriber.bundle_title import BundleTitleState
from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME, TRANSCRIPT_FILENAME
from transcriber.files.metadata import AudioFileMeta, MetadataFile


def _create_bundle(
    store_dir: Path,
    bundle_name: str,
    audio_models: dict[str, list[str]],
    fs_service: FakeFileSystemService,
    *,
    transcript: str | None = "existing transcript",
) -> Path:
    """Create a persisted bundle containing the requested audio metadata."""
    bundle_dir = store_dir / bundle_name
    fs_service.create_directory(bundle_dir)
    metadata = MetadataFile(
        bundle_id=new_bundle_id(),
        audio_files=[
            AudioFileMeta(filename=filename, transcript_model_used=models)
            for filename, models in audio_models.items()
        ],
        bundle_date=datetime(2026, 8, 5, tzinfo=UTC),
        bundle_title_state=BundleTitleState.PENDING,
    )
    metadata.write(bundle_dir, fs_service)
    for filename in audio_models:
        fs_service.write_file(bundle_dir / filename, "audio")
    if transcript is not None:
        fs_service.write_file(bundle_dir / TRANSCRIPT_FILENAME, transcript)
    return bundle_dir


def test_dry_run_selects_when_any_retained_audio_has_not_used_target(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A mixed-model bundle needs one complete regeneration, without dry-run writes."""
    store_dir = fake_config.general.store_dir
    mixed_dir = _create_bundle(
        store_dir,
        "mixed",
        {
            "one.mp3": ["new-model"],
            "two.wav": ["org/old:model"],
        },
        fake_fs,
    )
    _create_bundle(
        store_dir,
        "already-current",
        {"recording.m4a": ["new-model"]},
        fake_fs,
    )
    no_audio_dir = _create_bundle(
        store_dir,
        "without-audio",
        {"deleted.mp3": ["old-model"]},
        fake_fs,
    )
    fake_fs.delete_file(no_audio_dir / "deleted.mp3")

    result = prepare_transcript_regeneration(
        store_dir,
        target_model="new-model",
        apply=False,
        fs_service=fake_fs,
    )

    assert result.eligible == 1
    assert result.already_current == 1
    assert result.without_audio == 1
    assert fake_fs.file_exists(mixed_dir / TRANSCRIPT_FILENAME)
    assert not fake_fs.file_exists(mixed_dir / "transcript_new-model+org_old_model.md")
    assert "transcript_new-model+org_old_model.md" in capsys.readouterr().out


def test_apply_uses_numbered_archive_instead_of_overwriting(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
) -> None:
    """Existing model-labelled archives are preserved by choosing a numbered name."""
    store_dir = fake_config.general.store_dir
    bundle_dir = _create_bundle(
        store_dir,
        "eligible",
        {"recording.flac": ["old/model"]},
        fake_fs,
    )
    first_archive = bundle_dir / "transcript_old_model.md"
    fake_fs.write_file(first_archive, "older archived transcript")

    result = prepare_transcript_regeneration(
        store_dir,
        target_model="new-model",
        apply=True,
        fs_service=fake_fs,
    )

    assert result.eligible == 1
    assert not fake_fs.file_exists(bundle_dir / TRANSCRIPT_FILENAME)
    assert fake_fs.read_file(first_archive) == "older archived transcript"
    assert fake_fs.read_file(bundle_dir / "transcript_old_model_2.md") == "existing transcript"


def test_missing_transcript_is_reported_as_already_pending(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
) -> None:
    """A retained-audio bundle with no transcript already awaits regeneration."""
    store_dir = fake_config.general.store_dir
    _create_bundle(
        store_dir,
        "pending",
        {"recording.ogg": ["old-model"]},
        fake_fs,
        transcript=None,
    )

    result = prepare_transcript_regeneration(
        store_dir,
        target_model="new-model",
        apply=True,
        fs_service=fake_fs,
    )

    assert result.eligible == 0
    assert result.already_pending == 1
    assert fake_fs.file_exists(store_dir / "pending" / METADATA_FILENAME)


def test_empty_target_model_is_rejected(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
) -> None:
    """An empty model cannot produce a meaningful regeneration decision."""
    store_dir = fake_config.general.store_dir

    with pytest.raises(ValueError, match="Target transcript model cannot be empty"):
        prepare_transcript_regeneration(
            store_dir,
            target_model="  ",
            apply=False,
            fs_service=fake_fs,
        )


def test_invalid_bundle_prevents_all_planned_renames(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
) -> None:
    """Preflight finds invalid metadata before apply mode can rename any transcript."""
    store_dir = fake_config.general.store_dir
    valid_dir = _create_bundle(
        store_dir,
        "a-valid",
        {"recording.mp3": ["old-model"]},
        fake_fs,
    )
    invalid_dir = store_dir / "z-missing-metadata"
    fake_fs.create_directory(invalid_dir)
    fake_fs.write_file(invalid_dir / "recording.wav", "audio")

    with pytest.raises(FileNotFoundError, match=METADATA_FILENAME):
        prepare_transcript_regeneration(
            store_dir,
            target_model="new-model",
            apply=True,
            fs_service=fake_fs,
        )

    assert fake_fs.file_exists(valid_dir / TRANSCRIPT_FILENAME)
    assert fake_fs.get_operations("move") == []
