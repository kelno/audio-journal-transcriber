"""Tests for what happens to a bundle's source audio once it is transcribed.

These tests use real files in a temporary directory, because the age rule reads
file modification times, which the in-memory fake file system does not model.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.fake_audio_service import FakeAudioService
from transcriber.bundle_id import new_bundle_id
from transcriber.bundle_title import BundleTitleState
from transcriber.config import TranscribeConfig
from transcriber.constants import TRANSCRIPT_FILENAME
from transcriber.files.file_system import RealFileSystemService
from transcriber.files.metadata import AudioFileMeta, MetadataFile
from transcriber.transcribe_bundle import AudioRetentionBlocker, TranscribeBundle

RETENTION_DAYS = 30


@pytest.fixture
def disk_config(fake_config: TranscribeConfig, tmp_path: Path) -> TranscribeConfig:
    """Return a configuration whose store is a real temporary directory."""
    general = fake_config.general.model_copy(
        update={"store_dir": tmp_path / "store", "delete_source_audio_after_days": RETENTION_DAYS},
    )
    return fake_config.model_copy(update={"general": general})


def _set_age(path: Path, days: float) -> None:
    """Set a file's modification time to the given number of days ago."""
    timestamp = (datetime.now().astimezone() - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def _write_bundle(
    config: TranscribeConfig,
    *,
    listed_audio: list[str],
    present_audio: list[str],
    bundle_age_days: float,
    audio_age_days: dict[str, float] | None = None,
    transcript: bool = True,
    keep_forever: bool = False,
) -> TranscribeBundle:
    """Write a bundle directory to disk and load it the way the transcriber does."""
    bundle_date = datetime.now(config.general.timezone) - timedelta(days=bundle_age_days)
    bundle_dir = config.general.store_dir / f"{bundle_date:%Y-%m-%d %H.%M} Test bundle"
    bundle_dir.mkdir(parents=True)
    fs_service = RealFileSystemService(config)
    MetadataFile(
        bundle_id=new_bundle_id(),
        audio_files=[AudioFileMeta(filename=name) for name in listed_audio],
        bundle_date=bundle_date,
        bundle_title_state=BundleTitleState.GENERATED,
        keep_forever=keep_forever,
    ).write(bundle_dir, fs_service)
    for name in present_audio:
        (bundle_dir / name).write_bytes(b"audio " + name.encode())
        _set_age(bundle_dir / name, (audio_age_days or {}).get(name, bundle_age_days))
    if transcript:
        (bundle_dir / TRANSCRIPT_FILENAME).write_text("transcript text", encoding="utf-8")
    return TranscribeBundle.from_existing_directory(bundle_dir, config, fs_service, FakeAudioService())


class TestAudioAge:
    """The age of a bundle's audio is the most recent of its recording date and its audio files."""

    def test_old_bundle_with_old_audio(self, disk_config: TranscribeConfig) -> None:
        """A bundle recorded and last changed 40 days ago is 40 days old."""
        bundle = _write_bundle(disk_config, listed_audio=["a.m4a"], present_audio=["a.m4a"], bundle_age_days=40)

        assert bundle.get_audio_age_days() == 40

    def test_recently_added_audio_makes_the_bundle_recent(self, disk_config: TranscribeConfig) -> None:
        """Audio merged in 5 days ago keeps an old bundle's audio recent."""
        bundle = _write_bundle(
            disk_config,
            listed_audio=["a.m4a", "b.m4a"],
            present_audio=["a.m4a", "b.m4a"],
            bundle_age_days=40,
            audio_age_days={"b.m4a": 5},
        )

        assert bundle.get_audio_age_days() == 5


class TestAudioRetentionBlocker:
    """One rule decides whether a bundle's audio may leave the bundle."""

    def test_old_transcribed_audio_has_no_blocker(self, disk_config: TranscribeConfig) -> None:
        """Transcribed audio older than the threshold may leave."""
        bundle = _write_bundle(disk_config, listed_audio=["a.m4a"], present_audio=["a.m4a"], bundle_age_days=40)

        assert bundle.find_audio_retention_blocker(older_than_days=RETENTION_DAYS) is None

    @pytest.mark.parametrize(
        ("bundle_kwargs", "expected"),
        [
            ({"present_audio": [], "bundle_age_days": 40}, AudioRetentionBlocker.NO_AUDIO),
            ({"present_audio": ["a.m4a"], "bundle_age_days": 40, "transcript": False}, AudioRetentionBlocker.NO_TRANSCRIPT),
            ({"present_audio": ["a.m4a"], "bundle_age_days": 40, "keep_forever": True}, AudioRetentionBlocker.KEEP_FOREVER),
            ({"present_audio": ["a.m4a"], "bundle_age_days": 10}, AudioRetentionBlocker.TOO_RECENT),
        ],
    )
    def test_blockers(
        self,
        disk_config: TranscribeConfig,
        bundle_kwargs: dict[str, object],
        expected: AudioRetentionBlocker,
    ) -> None:
        """Each condition that keeps audio in the bundle is reported."""
        bundle = _write_bundle(disk_config, listed_audio=["a.m4a"], **bundle_kwargs)  # pyright: ignore[reportArgumentType]

        assert bundle.find_audio_retention_blocker(older_than_days=RETENTION_DAYS) is expected


class TestAudioSourceNeedsRemoval:
    """The transcriber's own retention setting, `delete_source_audio_after_days`."""

    def test_removes_audio_older_than_the_setting(self, disk_config: TranscribeConfig) -> None:
        """Audio older than the setting is due for removal."""
        bundle = _write_bundle(disk_config, listed_audio=["a.m4a"], present_audio=["a.m4a"], bundle_age_days=40)

        assert bundle.audio_source_needs_removal(disk_config.general.store_dir)

    def test_keeps_audio_younger_than_the_setting(self, disk_config: TranscribeConfig) -> None:
        """Audio younger than the setting stays."""
        bundle = _write_bundle(disk_config, listed_audio=["a.m4a"], present_audio=["a.m4a"], bundle_age_days=10)

        assert not bundle.audio_source_needs_removal(disk_config.general.store_dir)

    def test_disabled_setting_keeps_audio(self, disk_config: TranscribeConfig) -> None:
        """A setting of 0 never removes audio."""
        general = disk_config.general.model_copy(update={"delete_source_audio_after_days": 0})
        config = disk_config.model_copy(update={"general": general})
        bundle = _write_bundle(config, listed_audio=["a.m4a"], present_audio=["a.m4a"], bundle_age_days=400)

        assert not bundle.audio_source_needs_removal(config.general.store_dir)
