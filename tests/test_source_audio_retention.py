"""Tests for what happens to a bundle's source audio once it is transcribed.

These tests use real files in a temporary directory, because the age rule reads
file modification times, which the in-memory fake file system does not model.
"""

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.fake_ai_manager import FakeAIManager
from tests.fake_audio_service import FakeAudioService
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.bundle_id import new_bundle_id
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command_resolution import MigratedCommandResolution
from transcriber.config import TranscribeConfig
from transcriber.constants import DELETED_DIR_NAME, SUMMARY_FILENAME, TRANSCRIPT_FILENAME
from transcriber.files.commands_file import CommandsFile
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


class TestMissingAudioKeepsTranscript:
    """Audio that left a bundle (retention or archiving) must not cost the bundle its transcript."""

    def test_some_listed_audio_missing_keeps_transcript(self, disk_config: TranscribeConfig) -> None:
        """A bundle whose listed audio is only partly present keeps its transcript.

        This is the state another machine sees while a synchronisation tool applies
        the removal of a multi-recording bundle's audio one file at a time.
        """
        bundle = _write_bundle(
            disk_config,
            listed_audio=["a.m4a", "b.m4a"],
            present_audio=["a.m4a"],
            bundle_age_days=40,
        )

        bundle.cleanup_inconsistencies(dry_run=False)

        assert bundle.transcript is not None
        assert (bundle.get_bundle_dir() / TRANSCRIPT_FILENAME).is_file()

    def test_unlisted_audio_still_refreshes_bundle(self, disk_config: TranscribeConfig) -> None:
        """Audio the metadata does not list is new content, so the transcript is redone."""
        bundle = _write_bundle(
            disk_config,
            listed_audio=["a.m4a"],
            present_audio=["a.m4a", "b.m4a"],
            bundle_age_days=40,
        )

        bundle.cleanup_inconsistencies(dry_run=False)

        assert bundle.transcript is None
        assert not (bundle.get_bundle_dir() / TRANSCRIPT_FILENAME).exists()
        assert (disk_config.general.store_dir / DELETED_DIR_NAME / bundle.bundle_name / TRANSCRIPT_FILENAME).is_file()


def _finish_processing(bundle: TranscribeBundle) -> None:
    """Write what a fully processed bundle holds besides its transcript: summary, context hash, handled commands."""
    bundle_dir = bundle.get_bundle_dir()
    (bundle_dir / SUMMARY_FILENAME).write_text("summary text", encoding="utf-8")
    bundle.init_custom_context()
    commands = CommandsFile.from_command_list(["merge with previous"])
    for command in commands.commands:
        command.resolution = MigratedCommandResolution()
    commands.write(bundle_dir, bundle.fs_service)


class TestArchivedBundleGetsNoJobs:
    """The transcriber's reaction to a bundle whose audio left the store: nothing redoes it."""

    @pytest.mark.parametrize("present_audio", [[], ["b.m4a"]], ids=["all audio gone", "half the audio gone"])
    def test_no_job_after_the_cleanup_pass(
        self,
        disk_config: TranscribeConfig,
        fake_ai_manager: FakeAIManager,
        present_audio: list[str],
    ) -> None:
        """After the load-and-clean pass the daemon runs, the bundle gets no job and keeps its transcript.

        "Half the audio gone" is what another machine sees while a sync tool applies
        the removal of a multi-recording bundle's audio one file at a time.
        """
        general = disk_config.general.model_copy(update={"delete_source_audio_after_days": 0})
        config = disk_config.model_copy(update={"general": general})
        store_dir = config.general.store_dir
        _finish_processing(_write_bundle(config, listed_audio=["a.m4a", "b.m4a"], present_audio=present_audio, bundle_age_days=40))
        fs_service = RealFileSystemService(config)
        transcriber = AudioTranscriber(
            dry_run=True,
            ai_manager=fake_ai_manager,
            config=config,
            fs_service=fs_service,
            audio_service=FakeAudioService(),
        )

        bundles = TranscribeBundle.gather_existing_bundles(
            store_dir=store_dir,
            dry_run=False,
            cleanup_bundle=True,
            config=config,
            fs_service=fs_service,
            audio_service=FakeAudioService(),
        )
        [bundle] = bundles.values()
        jobs = transcriber.gather_bundle_jobs(bundle, store_dir, dry_run=True, config=config)

        assert jobs == []
        assert (bundle.get_bundle_dir() / TRANSCRIPT_FILENAME).is_file()
