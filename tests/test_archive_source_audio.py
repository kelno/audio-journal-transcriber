"""Tests for moving old source audio out of the bundle store into an archive.

Every test builds a throwaway store and archive under pytest's temporary
directory, with real files, since copying, hashing and file times are the point.
"""

import errno
import io
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pydantic
import pytest
import yaml

from transcriber import archive_source_audio as archive
from transcriber.bundle_id import BundleId, new_bundle_id
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command_resolution import MigratedCommandResolution
from transcriber.config import TranscribeConfig
from transcriber.constants import (
    ARCHIVED_AUDIO_FILENAME,
    MERGE_FAILED_FILENAME,
    METADATA_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.files.commands_file import CommandsFile
from transcriber.files.file_system import RealFileSystemService
from transcriber.files.metadata import AudioFileMeta, MetadataFile

OLD = 40
RECENT = 5
OLDER_THAN_DAYS = 30


@pytest.fixture
def disk_config(fake_config: TranscribeConfig, tmp_path: Path) -> TranscribeConfig:
    """Return a configuration whose store is a real temporary directory."""
    general = fake_config.general.model_copy(update={"store_dir": tmp_path / "vault" / "Store"})
    general.store_dir.mkdir(parents=True)
    return fake_config.model_copy(update={"general": general})


@pytest.fixture
def store_dir(disk_config: TranscribeConfig) -> Path:
    """Return the temporary bundle store."""
    return disk_config.general.store_dir


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    """Return the temporary archive location (not created: the script creates it)."""
    return tmp_path / "archive"


def _set_age(path: Path, days: float) -> None:
    timestamp = (datetime.now().astimezone() - timedelta(days=days)).timestamp()
    os.utime(path, (timestamp, timestamp))


def _make_bundle(
    config: TranscribeConfig,
    name: str,
    audio: dict[str, bytes],
    *,
    age_days: float = OLD,
    listed_audio: list[str] | None = None,
    transcript: bool = True,
    keep_forever: bool = False,
    pending_command: bool = False,
    extracted_commands: bool = True,
    bundle_id: BundleId | None = None,
) -> Path:
    """Write one bundle directory with the given audio files into the store."""
    bundle_dir = config.general.store_dir / name
    bundle_dir.mkdir(parents=True)
    fs_service = RealFileSystemService(config)
    MetadataFile(
        bundle_id=bundle_id or new_bundle_id(),
        audio_files=[AudioFileMeta(filename=filename) for filename in (listed_audio if listed_audio is not None else list(audio))],
        bundle_date=datetime.now(config.general.timezone) - timedelta(days=age_days),
        bundle_title_state=BundleTitleState.GENERATED,
        keep_forever=keep_forever,
    ).write(bundle_dir, fs_service)
    for filename, content in audio.items():
        (bundle_dir / filename).write_bytes(content)
        _set_age(bundle_dir / filename, age_days)
    if transcript:
        (bundle_dir / TRANSCRIPT_FILENAME).write_text("what was said", encoding="utf-8")
        (bundle_dir / SUMMARY_FILENAME).write_text("summary", encoding="utf-8")
    commands = CommandsFile.from_command_list(["merge with previous"])
    if pending_command:
        commands.write(bundle_dir, fs_service)
    elif transcript and extracted_commands:
        for command in commands.commands:
            command.resolution = MigratedCommandResolution()
        commands.write(bundle_dir, fs_service)
    return bundle_dir


def _run(
    config: TranscribeConfig,
    archive_root: Path,
    *,
    apply: bool,
    include_multi_audio: bool = False,
) -> archive.ArchiveReport:
    return archive.archive_source_audio(
        config.general.store_dir,
        archive_root,
        older_than_days=OLDER_THAN_DAYS,
        include_multi_audio=include_multi_audio,
        apply=apply,
        config=config,
    )


def _tree(root: Path) -> dict[str, bytes]:
    """Return every file under root with its content, to compare before and after."""
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def _marker_archives(bundle_dir: Path) -> list[archive.ArchiveRecord]:
    """Return the archive entries listed in a bundle's _archived_audio.md."""
    marker_text = (bundle_dir / ARCHIVED_AUDIO_FILENAME).read_text(encoding="utf-8")
    entries: object = yaml.safe_load(marker_text.split("---")[1])[archive.MARKER_ARCHIVES_KEY]
    return pydantic.TypeAdapter(list[archive.ArchiveRecord]).validate_python(entries)


def _seed_archive_folder(archive_dir: Path, bundle_id: BundleId) -> Path:
    """Create an archive folder whose record says it belongs to the given bundle, holding no files yet."""
    archive_dir.mkdir(parents=True)
    record = archive.ArchiveRecord(
        bundle_id=bundle_id,
        bundle_name=archive_dir.name,
        archived_at="2026-10-01T10:00:00+02:00",
        archive_dir=str(archive_dir),
        files=[],
    )
    (archive_dir / archive.ARCHIVE_RECORD_FILENAME).write_text(json.dumps(asdict(record)), encoding="utf-8")
    return archive_dir


def _archived_names(record_dir: Path) -> list[str]:
    """Return the file names an archive folder's archive.json lists."""
    record = json.loads((record_dir / archive.ARCHIVE_RECORD_FILENAME).read_text(encoding="utf-8"))
    return [archived["name"] for archived in record["files"]]


class TestDryRun:
    def test_dry_run_plans_and_changes_nothing(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Without --apply the store and the archive location stay exactly as they were."""
        _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        before = _tree(store_dir)

        report = _run(disk_config, archive_root, apply=False)
        archive.print_report(report, apply=False)

        assert [planned.bundle_name for planned in report.planned] == ["2026-09-01 10.00 Walk"]
        assert report.archived == []
        assert _tree(store_dir) == before
        assert not archive_root.exists()
        out = capsys.readouterr().out
        assert "2026-09-01 10.00 Walk" in out
        assert "--apply" in out


class TestApply:
    def test_moves_audio_and_keeps_everything_else(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """The audio lands in the archive, byte for byte with its date; the bundle keeps its text files."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        original_mtime = (bundle_dir / "rec.m4a").stat().st_mtime

        report = _run(disk_config, archive_root, apply=True)

        archived_file = archive_root / "2026-09-01 10.00 Walk" / "rec.m4a"
        assert report.errors == []
        assert [planned.bundle_name for planned in report.archived] == ["2026-09-01 10.00 Walk"]
        assert archived_file.read_bytes() == b"walk audio"
        assert archived_file.stat().st_mtime == pytest.approx(original_mtime, abs=1)
        assert not (bundle_dir / "rec.m4a").exists()
        for kept in (TRANSCRIPT_FILENAME, SUMMARY_FILENAME, METADATA_FILENAME):
            assert (bundle_dir / kept).is_file()
        assert not list(archive_root.rglob("*.partial"))

    def test_records_where_the_audio_went(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """The archive folder describes its files, and the bundle points to the archive folder."""
        bundle_id = new_bundle_id()
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"}, bundle_id=bundle_id)

        _run(disk_config, archive_root, apply=True)

        record = json.loads((archive_root / "2026-09-01 10.00 Walk" / archive.ARCHIVE_RECORD_FILENAME).read_text(encoding="utf-8"))
        assert record["bundle_id"] == bundle_id
        assert record["files"][0]["name"] == "rec.m4a"
        assert len(record["files"][0]["sha256"]) == 64
        marker_text = (bundle_dir / ARCHIVED_AUDIO_FILENAME).read_text(encoding="utf-8")
        [entry] = _marker_archives(bundle_dir)
        assert Path(entry.archive_dir) == archive_root / "2026-09-01 10.00 Walk"
        # tmp_path is long, so a folded YAML value would split the path over two lines.
        assert f"archive_dir: {archive_root / '2026-09-01 10.00 Walk'}\n" in marker_text
        assert entry.files[0].sha256 == record["files"][0]["sha256"]

    def test_bundle_with_several_recordings_moves_all_of_them(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """When multi-recording bundles are allowed, every recording leaves together."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Talk", {"a.m4a": b"first", "b.m4a": b"second"})

        report = _run(disk_config, archive_root, apply=True, include_multi_audio=True)

        assert report.errors == []
        assert _tree(archive_root / "2026-09-01 10.00 Talk").keys() >= {"a.m4a", "b.m4a"}
        assert not list(bundle_dir.glob("*.m4a"))


class TestSkips:
    def test_each_reason_is_counted_and_nothing_moves(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
    ) -> None:
        """Bundles that must keep their audio are counted by reason and left untouched."""
        _make_bundle(disk_config, "a no transcript", {"a.m4a": b"a"}, transcript=False)
        _make_bundle(disk_config, "b recent", {"b.m4a": b"b"}, age_days=RECENT)
        _make_bundle(disk_config, "c keep", {"c.m4a": b"c"}, keep_forever=True)
        _make_bundle(disk_config, "d no audio", {}, listed_audio=["d.m4a"])
        _make_bundle(disk_config, "e command pending", {"e.m4a": b"e"}, pending_command=True)
        _make_bundle(disk_config, "f unlisted audio", {"f.m4a": b"f", "g.m4a": b"g"}, listed_audio=["f.m4a"])
        _make_bundle(disk_config, "h several", {"h1.m4a": b"h1", "h2.m4a": b"h2"})
        merge_failed_dir = _make_bundle(disk_config, "i merge failed", {"i.m4a": b"i"})
        (merge_failed_dir / MERGE_FAILED_FILENAME).write_text("HUMAN ACTION REQUIRED", encoding="utf-8")
        _make_bundle(disk_config, "j commands not extracted", {"j.m4a": b"j"}, extracted_commands=False)
        (store_dir / "_deleted").mkdir()
        before = _tree(store_dir)

        report = _run(disk_config, archive_root, apply=True)

        assert report.planned == []
        assert report.errors == []
        assert report.skipped == {
            archive.SkipReason.NO_TRANSCRIPT: 1,
            archive.SkipReason.TOO_RECENT: 1,
            archive.SkipReason.KEEP_FOREVER: 1,
            archive.SkipReason.NO_AUDIO: 1,
            archive.SkipReason.COMMANDS_PENDING: 1,
            archive.SkipReason.UNLISTED_AUDIO: 1,
            archive.SkipReason.SEVERAL_RECORDINGS: 1,
            archive.SkipReason.MERGE_FAILED: 1,
            archive.SkipReason.COMMANDS_NOT_EXTRACTED: 1,
        }
        assert _tree(store_dir) == before

    def test_several_recordings_message_names_the_flag(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The summary tells the reader what unlocks the skipped multi-recording bundles."""
        _make_bundle(disk_config, "h several", {"h1.m4a": b"h1", "h2.m4a": b"h2"})

        report = _run(disk_config, archive_root, apply=False)
        archive.print_report(report, apply=False)

        assert "--include-multi-audio" in capsys.readouterr().out


class TestRerunAfterFailure:
    """A run that fails or stops part way leaves a state the next run finishes, in the same folder."""

    def test_failed_copies_then_rerun_uses_the_same_folder(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two runs failing on a full disk, then one that succeeds, leave one archive folder."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})

        def full_disk(_source: Path, _target: Path) -> str:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(archive, "_copy_and_hash", full_disk)
        failed_runs = [_run(disk_config, archive_root, apply=True) for _ in range(2)]
        monkeypatch.undo()
        report = _run(disk_config, archive_root, apply=True)

        for failed in failed_runs:
            [error] = failed.errors
            assert "run again" in error.next_step
        assert report.errors == []
        assert [path.name for path in archive_root.iterdir()] == ["2026-09-01 10.00 Walk"]
        assert (archive_root / "2026-09-01 10.00 Walk" / "rec.m4a").read_bytes() == b"walk audio"
        assert not (bundle_dir / "rec.m4a").exists()

    def test_interrupted_after_copying_then_rerun_reuses_the_copy(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A run stopped (Ctrl-C) between the copy and the final record is finished by the next one."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})

        def interrupted(*_args: object) -> list[archive.ArchivedFile]:
            raise KeyboardInterrupt

        monkeypatch.setattr(archive, "_merge_files", interrupted)
        with pytest.raises(KeyboardInterrupt):
            _run(disk_config, archive_root, apply=True)
        assert (bundle_dir / "rec.m4a").exists()
        monkeypatch.undo()
        report = _run(disk_config, archive_root, apply=True)

        assert report.errors == []
        assert [path.name for path in archive_root.iterdir()] == ["2026-09-01 10.00 Walk"]
        assert _archived_names(archive_root / "2026-09-01 10.00 Walk") == ["rec.m4a"]
        assert not (bundle_dir / "rec.m4a").exists()

    def test_folder_without_a_record_holding_only_this_bundles_files_is_its_own(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """A folder holding nothing but this bundle's audio and partial files is taken as this bundle's."""
        _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        archive_dir = archive_root / "2026-09-01 10.00 Walk"
        archive_dir.mkdir(parents=True)
        (archive_dir / "rec.m4a").write_bytes(b"walk audio")
        (archive_dir / f"{archive.ARCHIVE_RECORD_FILENAME}{archive.PARTIAL_SUFFIX}").write_text("{", encoding="utf-8")

        report = _run(disk_config, archive_root, apply=True)

        assert report.errors == []
        assert [planned.archive_dir for planned in report.archived] == [archive_dir]

    def test_audio_left_by_a_failed_removal_is_removed_on_rerun_and_listed_once(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """A file the store would not let go is reported as partly archived, and a rerun completes the records."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Talk", {"a.m4a": b"first", "b.m4a": b"second"})
        real_unlink = Path.unlink

        def locked(path: Path, *, missing_ok: bool = False) -> None:
            if path.parent == bundle_dir and path.name == "b.m4a":
                raise PermissionError(errno.EACCES, "file in use", str(path))
            real_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(Path, "unlink", locked)
        first = _run(disk_config, archive_root, apply=True, include_multi_audio=True)
        archive.print_report(first, apply=True)
        monkeypatch.undo()
        second = _run(disk_config, archive_root, apply=True, include_multi_audio=True)

        [error] = first.errors
        assert error.message.startswith("SourceRemovalError: Partly archived: removed a.m4a")
        assert "close whatever holds the file" in error.next_step
        assert "kept their audio" not in capsys.readouterr().out
        assert second.errors == []
        assert not list(bundle_dir.glob("*.m4a"))
        assert _archived_names(archive_root / "2026-09-01 10.00 Talk") == ["a.m4a", "b.m4a"]
        [entry] = _marker_archives(bundle_dir)
        assert [archived.name for archived in entry.files] == ["a.m4a", "b.m4a"]


class TestMarkerKeepsEarlierArchives:
    """A bundle's _archived_audio.md is added to, never replaced."""

    def test_entry_for_another_archive_folder_is_kept(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """A marker brought in by a merge, or left by an earlier archive under another name, keeps its entry."""
        earlier_dir = _make_bundle(disk_config, "2026-08-30 09.00 Earlier", {"old.m4a": b"old audio"})
        _run(disk_config, archive_root, apply=True)
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        # What a merge of the earlier bundle into this one does with its marker.
        (earlier_dir / ARCHIVED_AUDIO_FILENAME).rename(bundle_dir / ARCHIVED_AUDIO_FILENAME)

        report = _run(disk_config, archive_root, apply=True)

        assert report.errors == []
        assert [Path(entry.archive_dir).name for entry in _marker_archives(bundle_dir)] == [
            "2026-08-30 09.00 Earlier",
            "2026-09-01 10.00 Walk",
        ]

    def test_marker_in_another_format_is_an_error_and_keeps_the_audio(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """A marker this script cannot read is never overwritten; the dry run already reports it."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        foreign = "---\narchive_dir: D:/elsewhere\n---\nwritten by hand\n"
        (bundle_dir / ARCHIVED_AUDIO_FILENAME).write_text(foreign, encoding="utf-8")

        dry_run = _run(disk_config, archive_root, apply=False)
        report = _run(disk_config, archive_root, apply=True)

        assert [error.bundle_name for error in dry_run.errors] == ["2026-09-01 10.00 Walk"]
        [error] = report.errors
        assert "not in the format this script writes" in error.message
        assert (bundle_dir / "rec.m4a").exists()
        assert (bundle_dir / ARCHIVED_AUDIO_FILENAME).read_text(encoding="utf-8") == foreign


class TestChangesDuringTheRun:
    def test_bundle_marked_keep_forever_while_another_is_copied_keeps_its_audio(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each bundle is inspected right before its audio moves, not once at the start of the run."""
        _make_bundle(disk_config, "2026-09-01 10.00 First", {"first.m4a": b"first"})
        later_dir = _make_bundle(disk_config, "2026-09-02 10.00 Later", {"later.m4a": b"later"})
        real_copystat = shutil.copystat
        fs_service = RealFileSystemService(disk_config)

        def copy_then_mark_later(source: Path, target: Path) -> None:
            real_copystat(source, target)
            if source.name == "first.m4a":
                later_metadata = MetadataFile.from_file(later_dir / METADATA_FILENAME, fs_service)
                later_metadata.keep_forever = True
                later_metadata.write(later_dir, fs_service)

        monkeypatch.setattr(shutil, "copystat", copy_then_mark_later)
        report = _run(disk_config, archive_root, apply=True)

        assert [planned.bundle_name for planned in report.archived] == ["2026-09-01 10.00 First"]
        assert report.skipped == {archive.SkipReason.KEEP_FOREVER: 1}
        assert (later_dir / "later.m4a").exists()

    def test_audio_replaced_after_copying_is_not_removed(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A newer version a sync tool puts in place after the copy is left in the store, never deleted unarchived."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        real_safe_dump = yaml.safe_dump

        def dump_then_replace_audio(*args: object, **kwargs: object) -> str:
            # Runs while the marker is written: after the copy and its check, before removal.
            newer = bundle_dir / "rec.m4a.syncthing.tmp"
            newer.write_bytes(b"newer walk audio")
            newer.replace(bundle_dir / "rec.m4a")
            return real_safe_dump(*args, **kwargs)  # pyright: ignore[reportArgumentType, reportCallIssue] - passes the real call through

        monkeypatch.setattr(yaml, "safe_dump", dump_then_replace_audio)
        report = _run(disk_config, archive_root, apply=True)
        monkeypatch.undo()

        [error] = report.errors
        assert error.message.startswith("SourceChangedError: rec.m4a changed in the store after being copied")
        assert error.next_step.startswith("run again")
        assert (bundle_dir / "rec.m4a").read_bytes() == b"newer walk audio"
        assert (archive_root / "2026-09-01 10.00 Walk" / "rec.m4a").read_bytes() == b"walk audio"

    def test_audio_already_gone_at_removal_is_a_note_not_an_error(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Audio something else removed after its verified copy leaves the bundle archived, with a note."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        real_safe_dump = yaml.safe_dump

        def dump_then_remove_audio(*args: object, **kwargs: object) -> str:
            (bundle_dir / "rec.m4a").unlink(missing_ok=True)
            return real_safe_dump(*args, **kwargs)  # pyright: ignore[reportArgumentType, reportCallIssue] - passes the real call through

        monkeypatch.setattr(yaml, "safe_dump", dump_then_remove_audio)
        report = _run(disk_config, archive_root, apply=True)
        monkeypatch.undo()
        archive.print_report(report, apply=True)

        assert report.errors == []
        assert [planned.bundle_name for planned in report.archived] == ["2026-09-01 10.00 Walk"]
        assert "[note] 2026-09-01 10.00 Walk: already gone from the store" in capsys.readouterr().out


class TestFullDisk:
    def test_full_archive_drive_stops_the_run(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The first full-disk failure ends the run, rather than every following bundle failing the same way."""
        _make_bundle(disk_config, "2026-09-01 10.00 First", {"first.m4a": b"first"})
        second_dir = _make_bundle(disk_config, "2026-09-02 10.00 Second", {"second.m4a": b"second"})

        def full_disk(_source: Path, _target: Path) -> str:
            raise OSError(errno.ENOSPC, "No space left on device")

        monkeypatch.setattr(archive, "_copy_and_hash", full_disk)
        report = _run(disk_config, archive_root, apply=True)
        archive.print_report(report, apply=True)

        [error] = report.errors
        assert error.next_step.startswith("free space on the archive drive")
        assert report.stopped_early is not None
        assert "The archive drive is full, so the run stopped there" in capsys.readouterr().out
        assert [path.name for path in archive_root.iterdir()] == ["2026-09-01 10.00 First"]
        assert (second_dir / "second.m4a").exists()


class TestArchiveFolderConflicts:
    def test_different_file_with_same_name_is_an_error_and_keeps_the_audio(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """An archive file that differs from the store's is never overwritten; other bundles still go."""
        bundle_id = new_bundle_id()
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"}, bundle_id=bundle_id)
        other_dir = _make_bundle(disk_config, "2026-09-02 10.00 Run", {"run.m4a": b"run audio"})
        archive_dir = _seed_archive_folder(archive_root / "2026-09-01 10.00 Walk", bundle_id)
        (archive_dir / "rec.m4a").write_bytes(b"something else")

        report = _run(disk_config, archive_root, apply=True)

        [error] = report.errors
        assert error.bundle_name == "2026-09-01 10.00 Walk"
        assert error.next_step.startswith("look at the folder or file named above")
        assert (bundle_dir / "rec.m4a").read_bytes() == b"walk audio"
        assert (archive_dir / "rec.m4a").read_bytes() == b"something else"
        assert not (bundle_dir / ARCHIVED_AUDIO_FILENAME).exists()
        assert not (other_dir / "run.m4a").exists()

    def test_folder_name_taken_by_another_bundle_gets_the_id_suffix(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
    ) -> None:
        """Two bundles that had the same name at different times get separate archive folders."""
        bundle_id = new_bundle_id()
        _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"}, bundle_id=bundle_id)
        _seed_archive_folder(archive_root / "2026-09-01 10.00 Walk", new_bundle_id())

        report = _run(disk_config, archive_root, apply=True)

        assert report.errors == []
        assert (archive_root / f"2026-09-01 10.00 Walk ~{bundle_id[:8]}" / "rec.m4a").read_bytes() == b"walk audio"

    def test_failed_verification_keeps_the_audio_and_cleans_up(
        self,
        disk_config: TranscribeConfig,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A copy whose content does not match is removed, the store keeps its audio, and a rerun archives it."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})

        def corrupt_hash(_path: Path) -> str:
            return "0" * 64

        monkeypatch.setattr(archive, "_sha256_of_file", corrupt_hash)
        report = _run(disk_config, archive_root, apply=True)

        [error] = report.errors
        assert error.bundle_name == "2026-09-01 10.00 Walk"
        assert error.next_step.startswith("run again")
        assert (bundle_dir / "rec.m4a").read_bytes() == b"walk audio"
        assert not (archive_root / "2026-09-01 10.00 Walk" / "rec.m4a").exists()
        assert not list(archive_root.rglob("*.partial"))

        monkeypatch.undo()
        rerun = _run(disk_config, archive_root, apply=True)

        assert rerun.errors == []
        assert [planned.archive_dir.name for planned in rerun.archived] == ["2026-09-01 10.00 Walk"]


class TestArguments:
    def test_archive_inside_the_store_is_refused(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
    ) -> None:
        """An archive inside the store would be read back as part of it."""
        with pytest.raises(ValueError, match="inside the store"):
            _run(disk_config, store_dir / "_archive", apply=False)

    def test_negative_age_is_refused(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
    ) -> None:
        """A negative age would archive audio the transcriber may still need."""
        with pytest.raises(ValueError, match="older_than_days"):
            archive.archive_source_audio(
                store_dir,
                archive_root,
                older_than_days=-1,
                include_multi_audio=False,
                apply=False,
                config=disk_config,
            )

    def test_parser_defaults_to_a_dry_run(self) -> None:
        """Only the store, archive and time zone are required, and nothing is changed unless --apply is given."""
        args = archive.build_parser().parse_args(["--store-dir", "store", "--archive-dir", "somewhere", "--timezone", "Europe/Brussels"])

        assert args.timezone == ZoneInfo("Europe/Brussels")
        assert args.log_file is None
        assert args.apply is False
        assert args.include_multi_audio is False
        assert args.older_than_days == archive.DEFAULT_OLDER_THAN_DAYS

    def test_relative_archive_dir_is_recorded_as_an_absolute_path(
        self,
        disk_config: TranscribeConfig,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The records must point at the archive from wherever they are read later."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        monkeypatch.chdir(tmp_path)

        _run(disk_config, Path("relative archive"), apply=True)

        [entry] = _marker_archives(bundle_dir)
        assert entry.archive_dir == str(tmp_path / "relative archive" / "2026-09-01 10.00 Walk")


def _main_args(store_dir: Path, archive_root: Path, *extra: str) -> list[str]:
    """Return the command line a scheduler would use, with the given extra arguments."""
    return ["--store-dir", str(store_dir), "--archive-dir", str(archive_root), "--timezone", "Europe/Brussels", *extra]


class TestMain:
    """The installed command: it needs only its arguments, and reports by output, log file and exit status."""

    def test_runs_from_its_arguments_alone(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No config.custom.toml and no TRANSCRIBER_* settings are needed, as in a scheduler's empty environment."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        monkeypatch.chdir(tmp_path)
        for name in [name for name in os.environ if name.startswith("TRANSCRIBER_")]:
            monkeypatch.delenv(name)

        exit_code = archive.main(_main_args(store_dir, archive_root, "--apply"))

        assert exit_code == archive.EXIT_DONE
        assert not (bundle_dir / "rec.m4a").exists()

    def test_each_run_is_appended_to_the_log_file(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
        tmp_path: Path,
    ) -> None:
        """Every run adds its report under a header with its time, mode and exit status."""
        _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        log_file = tmp_path / "logs" / "archive.log"

        archive.main(_main_args(store_dir, archive_root, "--log-file", str(log_file)))
        archive.main(_main_args(store_dir, archive_root, "--log-file", str(log_file), "--apply"))

        log = log_file.read_text(encoding="utf-8")
        headers = [line for line in log.splitlines() if line.startswith("=== ")]
        assert [header.split(" ", 2)[2] for header in headers] == ["dry run: exit 0", "apply: exit 0"]
        assert "[dry-run] 2026-09-01 10.00 Walk" in log
        assert "[archived] 2026-09-01 10.00 Walk" in log

    def test_failed_bundle_exits_1(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
    ) -> None:
        """A bundle that could not be archived makes the run's exit status 1, for the scheduler to report."""
        bundle_dir = _make_bundle(disk_config, "2026-09-01 10.00 Walk", {"rec.m4a": b"walk audio"})
        (bundle_dir / ARCHIVED_AUDIO_FILENAME).write_text("---\nwritten: by hand\n---\n", encoding="utf-8")

        assert archive.main(_main_args(store_dir, archive_root, "--apply")) == archive.EXIT_BUNDLE_FAILED

    def test_bundle_name_the_output_encoding_lacks_is_escaped(
        self,
        disk_config: TranscribeConfig,
        store_dir: Path,
        archive_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A title character a cp1252 pipe cannot carry is escaped instead of cutting the report off."""
        _make_bundle(disk_config, "2026-09-01 10.00 Plan A → B", {"rec.m4a": b"plan audio"})
        output = io.BytesIO()
        cp1252_stdout = io.TextIOWrapper(output, encoding="cp1252")
        monkeypatch.setattr(sys, "stdout", cp1252_stdout)

        exit_code = archive.main(_main_args(store_dir, archive_root, "--apply"))
        cp1252_stdout.flush()

        assert exit_code == archive.EXIT_DONE
        assert "[archived] 2026-09-01 10.00 Plan A \\u2192 B" in output.getvalue().decode("cp1252")

    def test_archive_inside_the_store_does_nothing_exits_2_and_is_logged(
        self,
        store_dir: Path,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Unusable arguments get a message and the not-started status, not a traceback and the bundle-failed one."""
        log_file = tmp_path / "archive.log"

        exit_code = archive.main(_main_args(store_dir, store_dir / "_archive", "--log-file", str(log_file)))

        assert exit_code == archive.EXIT_NOT_STARTED
        assert "Nothing was done: The archive directory" in capsys.readouterr().err
        assert "exit 2" in log_file.read_text(encoding="utf-8")

    def test_unknown_time_zone_is_refused(
        self,
        store_dir: Path,
        archive_root: Path,
    ) -> None:
        """A time zone name that does not exist stops the command before it looks at any bundle."""
        with pytest.raises(SystemExit) as stopped:
            archive.main(["--store-dir", str(store_dir), "--archive-dir", str(archive_root), "--timezone", "Europe/Atlantis"])

        assert stopped.value.code == archive.EXIT_NOT_STARTED
