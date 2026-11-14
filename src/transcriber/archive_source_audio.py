"""Move the source audio of old, transcribed bundles out of the store into an archive.

Only the audio leaves: the transcript, summary and the rest of each bundle stay in
the store. Each bundle's audio goes to ``<archive-dir>/<bundle directory name>/``
with an ``archive.json`` describing it, and the bundle's ``_archived_audio.md`` lists
every archive folder holding its audio. Every file is copied, checked against the
original by SHA-256, and only then removed from the store, provided it is still the
version that was copied.

By default this command only reports what it would do; pass ``--apply`` to do it.
Running it again after an interruption or a failure finishes the job: the bundle's
archive folder is recognised, a verified copy already there is reused, and nothing
is overwritten.

It is meant to run unattended from a scheduler (a systemd timer, Windows Task
Scheduler), so it takes everything it needs as arguments and reads none of the
transcriber's settings: it never calls the transcription or text APIs. It can append
each run's report to a log file, and its exit status says how the run went
(``EXIT_*`` below), for the scheduler or a wrapper to act on.

Usage:
    transcriber-archive-audio --store-dir STORE --archive-dir ARCHIVE --timezone Europe/Brussels
    transcriber-archive-audio --store-dir STORE --archive-dir ARCHIVE --timezone Europe/Brussels --apply --log-file LOG
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import io
import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pydantic
import yaml

from transcriber.audio_service import RealAudioService
from transcriber.config import AudioConfig, GeneralConfig, TextConfig, TranscribeConfig
from transcriber.constants import ARCHIVED_AUDIO_FILENAME, MERGE_FAILED_FILENAME
from transcriber.exception import AudioTranscriberException
from transcriber.files import metadata
from transcriber.files.file_system import RealFileSystemService
from transcriber.transcribe_bundle import AudioRetentionBlocker, TranscribeBundle

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_OLDER_THAN_DAYS = 30
ARCHIVE_RECORD_FILENAME = "archive.json"
# Key of the list of archive records in the frontmatter of a bundle's _archived_audio.md.
MARKER_ARCHIVES_KEY = "archives"
# Suffix of a file being written; it only gets its real name once complete.
PARTIAL_SUFFIX = ".partial"
COPY_CHUNK_BYTES = 1024 * 1024
MULTI_AUDIO_FLAG = "--include-multi-audio"
# PyYAML folds values longer than its line width; paths must stay on one line to be read and searched.
YAML_NO_FOLD_WIDTH = 1_000_000
EXIT_DONE = 0
# Some bundles failed, each reported with its next step; the others were archived. An
# unexpected crash also exits with 1, Python's own status for an uncaught exception.
EXIT_BUNDLE_FAILED = 1
# Nothing was done: the arguments are unusable (argparse uses 2 for its own errors too).
EXIT_NOT_STARTED = 2


class SkipReason(StrEnum):
    """Why a bundle keeps its audio in this run."""

    NO_AUDIO = AudioRetentionBlocker.NO_AUDIO.value
    NO_TRANSCRIPT = AudioRetentionBlocker.NO_TRANSCRIPT.value
    KEEP_FOREVER = AudioRetentionBlocker.KEEP_FOREVER.value
    TOO_RECENT = AudioRetentionBlocker.TOO_RECENT.value
    # The transcriber redoes a bundle holding audio its metadata does not list;
    # archiving it first would take audio out from under that work.
    UNLISTED_AUDIO = "audio its metadata does not list"
    # A merge that stopped half way; someone has to look at the bundle first.
    MERGE_FAILED = "a failed merge to look at"
    # The transcriber extracts voice commands only while the bundle holds audio.
    COMMANDS_NOT_EXTRACTED = "voice commands not extracted yet"
    # A pending command may still merge or change this bundle.
    COMMANDS_PENDING = "voice commands still being processed"
    SEVERAL_RECORDINGS = "several recordings"


class InvalidArgumentsError(ValueError):
    """The arguments would archive the wrong audio or put the archive in the wrong place."""


class ArchiveConflictError(Exception):
    """The archive or the bundle already holds something this run must not overwrite."""


class ArchiveVerificationError(Exception):
    """A copy in the archive does not match its original."""


class SourceRemovalError(Exception):
    """Audio already copied and verified in the archive could not be removed from the store."""


class SourceChangedError(Exception):
    """Audio changed in the store after it was copied, so the archived copy is not of what would be removed."""


@dataclass(frozen=True)
class FileIdentity:
    """What tells two versions of a file apart, whether replaced by a rename or changed in place."""

    # The file ID on Windows (NTFS), the inode elsewhere: it changes when a sync tool replaces the file.
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class ArchivedFile:
    """One audio file as stored in the archive."""

    name: str
    size: int
    sha256: str
    modified: str


@dataclass(frozen=True)
class ArchiveRecord:
    """What one archive folder holds for one bundle: the folder's ``archive.json``, and one entry of the bundle's marker."""

    bundle_id: str
    bundle_name: str
    archived_at: str
    archive_dir: str
    files: list[ArchivedFile]


_RECORD_ADAPTER = pydantic.TypeAdapter(ArchiveRecord)
_RECORDS_ADAPTER = pydantic.TypeAdapter(list[ArchiveRecord])


@dataclass(frozen=True)
class PlannedBundle:
    """A bundle whose audio this run archives."""

    bundle: TranscribeBundle
    archive_dir: Path
    # Measured when planning, since the files are gone from the store once archived.
    total_bytes: int

    @property
    def bundle_name(self) -> str:
        """Return the bundle's directory name."""
        return self.bundle.bundle_name


@dataclass(frozen=True)
class BundleError:
    """A bundle this run could not inspect or fully archive, and what to do about it."""

    bundle_name: str
    message: str
    next_step: str


@dataclass(frozen=True)
class BundleNote:
    """Something worth knowing about a bundle that was archived."""

    bundle_name: str
    message: str


@dataclass
class ArchiveReport:
    """What one run found and did."""

    archive_root: Path
    planned: list[PlannedBundle] = field(default_factory=list)
    archived: list[PlannedBundle] = field(default_factory=list)
    skipped: Counter[SkipReason] = field(default_factory=Counter)
    errors: list[BundleError] = field(default_factory=list)
    notes: list[BundleNote] = field(default_factory=list)
    # Why the run stopped before inspecting every bundle, if it did.
    stopped_early: str | None = None


# Failures confined to one bundle: reported with their next step while the other bundles go on.
_BUNDLE_ERRORS = (
    AudioTranscriberException,
    ArchiveConflictError,
    ArchiveVerificationError,
    SourceRemovalError,
    SourceChangedError,
    pydantic.ValidationError,
    yaml.YAMLError,
    OSError,
)


def _is_disk_full(error: Exception) -> bool:
    return isinstance(error, OSError) and error.errno == errno.ENOSPC


# What to do about each kind of failure, the first matching entry winning.
_NEXT_STEPS: tuple[tuple[type[Exception], str], ...] = (
    (SourceRemovalError, "close whatever holds the file (a player, a sync tool), then run again: the verified copies are reused"),
    (SourceChangedError, "run again: an identical copy in the archive is reused, and a different one is reported as a conflict to look at"),
    (ArchiveConflictError, "look at the folder or file named above and move aside what does not belong to this bundle, then run again"),
    (ArchiveVerificationError, "run again; if it fails again, check the archive drive"),
    (OSError, "fix the cause above (drive missing, permissions), then run again: what was already copied is reused"),
)
_DISK_FULL_NEXT_STEP = "free space on the archive drive, then run again: what was already copied is reused"
_UNREADABLE_NEXT_STEP = "a file of this bundle or of its archive folder could not be read; look at it, then run again"


def _next_step(error: Exception) -> str:
    """Return what the reader should do about a bundle's failure."""
    if _is_disk_full(error):
        return _DISK_FULL_NEXT_STEP
    return next((step for kind, step in _NEXT_STEPS if isinstance(error, kind)), _UNREADABLE_NEXT_STEP)


def _bundle_error(bundle_name: str, error: Exception) -> BundleError:
    return BundleError(bundle_name, f"{type(error).__name__}: {error}", _next_step(error))


def _sha256_of_file(path: Path) -> str:
    """Return the SHA-256 of a file's content."""
    with path.open("rb") as reader:
        return hashlib.file_digest(reader, "sha256").hexdigest()


def _copy_and_hash(source: Path, target: Path) -> str:
    """Copy a file, flush it to disk, and return the SHA-256 of what was read."""
    digest = hashlib.sha256()
    with source.open("rb") as reader, target.open("wb") as writer:
        while chunk := reader.read(COPY_CHUNK_BYTES):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _describe(path: Path, sha256: str, tz: ZoneInfo) -> ArchivedFile:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=tz).isoformat(timespec="seconds")
    return ArchivedFile(name=path.name, size=stat.st_size, sha256=sha256, modified=modified)


def _copy_and_verify(source: Path, target: Path) -> str:
    """Copy a file with its times, check the copy against the original, and return its SHA-256.

    Raises:
        ArchiveVerificationError: If the copy does not match the original.

    """
    source_sha256 = _copy_and_hash(source, target)
    shutil.copystat(source, target)
    if _sha256_of_file(target) != source_sha256:
        msg = f"The copy of {source.name} does not match the original"
        raise ArchiveVerificationError(msg)
    return source_sha256


def _copy_verified(source: Path, destination: Path, tz: ZoneInfo) -> ArchivedFile:
    """Put one file into the archive, reusing an identical copy already there.

    Raises:
        ArchiveConflictError: If the destination holds different content.
        ArchiveVerificationError: If the new copy does not match the original.
        OSError: If reading or writing fails.

    """
    if destination.exists():
        source_sha256 = _sha256_of_file(source)
        if _sha256_of_file(destination) != source_sha256:
            msg = f"{destination} already exists with different content; both were left as they are"
            raise ArchiveConflictError(msg)
        return _describe(destination, source_sha256, tz)

    partial = destination.with_name(destination.name + PARTIAL_SUFFIX)
    try:
        source_sha256 = _copy_and_verify(source, partial)
        partial.rename(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return _describe(destination, source_sha256, tz)


def _write_text_atomically(path: Path, text: str) -> None:
    """Write a whole text file so readers never see it half written."""
    partial = path.with_name(path.name + PARTIAL_SUFFIX)
    partial.write_text(text, encoding="utf-8")
    partial.replace(path)


def _read_archive_record(archive_dir: Path) -> ArchiveRecord | None:
    """Return an archive folder's record, or None if it has none.

    Raises:
        pydantic.ValidationError: If the record cannot be read.

    """
    record_path = archive_dir / ARCHIVE_RECORD_FILENAME
    if not record_path.is_file():
        return None
    return _RECORD_ADAPTER.validate_json(record_path.read_bytes())


def _write_archive_record(record: ArchiveRecord) -> None:
    text = json.dumps(asdict(record), indent=2, ensure_ascii=False) + "\n"
    _write_text_atomically(Path(record.archive_dir) / ARCHIVE_RECORD_FILENAME, text)


def _make_record(bundle: TranscribeBundle, archive_dir: Path, files: list[ArchivedFile], tz: ZoneInfo) -> ArchiveRecord:
    return ArchiveRecord(
        bundle_id=bundle.bundle_id,
        bundle_name=bundle.bundle_name,
        archived_at=datetime.now(tz).isoformat(timespec="seconds"),
        archive_dir=str(archive_dir),
        files=files,
    )


def _merge_files(earlier: list[ArchivedFile], new: list[ArchivedFile]) -> list[ArchivedFile]:
    """Return the earlier entries updated with the new ones, one entry per file name."""
    by_name = {archived.name: archived for archived in earlier}
    by_name.update({archived.name: archived for archived in new})
    return list(by_name.values())


def _belongs_to(folder: Path, bundle: TranscribeBundle) -> bool:
    """Return whether an existing archive folder is this bundle's.

    It is when its record names the bundle. A folder with no record is this bundle's
    when it holds nothing but what archiving this bundle writes: the state a run
    stopped between creating the folder and writing its record leaves.

    Raises:
        pydantic.ValidationError: If the folder's record cannot be read.

    """
    if record := _read_archive_record(folder):
        return record.bundle_id == bundle.bundle_id
    own_names = {path.name for path in bundle.source_audios} | {ARCHIVE_RECORD_FILENAME}
    own_names |= {name + PARTIAL_SUFFIX for name in own_names}
    return all(entry.name in own_names for entry in folder.iterdir())


def _choose_archive_dir(archive_root: Path, bundle: TranscribeBundle) -> Path:
    """Return the archive folder for a bundle: its own name, or the ID-suffixed name if another bundle has it.

    A folder this bundle already started wins, so a rerun finishes the job there.

    Raises:
        ArchiveConflictError: If both names already belong to something else.
        pydantic.ValidationError: If an existing folder's record cannot be read.

    """
    candidates = [archive_root / bundle.bundle_name, archive_root / bundle.make_id_suffixed_name(bundle.bundle_name)]
    for candidate in candidates:
        if candidate.exists() and _belongs_to(candidate, bundle):
            return candidate
    for candidate in candidates:
        if not candidate.exists():
            return candidate
    msg = f"{candidates[0]} and {candidates[1]} already exist and hold another bundle's audio or unknown files"
    raise ArchiveConflictError(msg)


def _read_marker_records(bundle_dir: Path) -> list[ArchiveRecord]:
    """Return the archive records listed in a bundle's ``_archived_audio.md``, or an empty list if it has none.

    Raises:
        ArchiveConflictError: If the file is not in the format this script writes, so it must not be overwritten.
        yaml.YAMLError: If its frontmatter is not valid YAML.
        pydantic.ValidationError: If its records cannot be read.

    """
    marker_path = bundle_dir / ARCHIVED_AUDIO_FILENAME
    if not marker_path.is_file():
        return []
    frontmatter = metadata.extract_frontmatter(marker_path.read_text(encoding="utf-8"))
    data: object = yaml.safe_load(frontmatter) if frontmatter else None
    if not isinstance(data, dict) or MARKER_ARCHIVES_KEY not in data:
        msg = f"{marker_path} is not in the format this script writes, so it was left as it is"
        raise ArchiveConflictError(msg)
    return _RECORDS_ADAPTER.validate_python(data[MARKER_ARCHIVES_KEY])


def _marker_text(records: list[ArchiveRecord]) -> str:
    """Return the ``_archived_audio.md`` content listing every archive folder holding a bundle's audio."""
    data = {MARKER_ARCHIVES_KEY: [asdict(record) for record in records]}
    frontmatter = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=YAML_NO_FOLD_WIDTH).strip()
    return (
        f"---\n{frontmatter}\n---\n"
        "The audio of this recording was moved out of the store to the archive folders listed above (archive_dir). "
        "To bring it back, copy each folder's listed files into this folder.\n"
    )


def _with_record(records: list[ArchiveRecord], record: ArchiveRecord) -> list[ArchiveRecord]:
    """Return the records with the entry for the same archive folder replaced, or the record added at the end."""
    if any(existing.archive_dir == record.archive_dir for existing in records):
        return [record if existing.archive_dir == record.archive_dir else existing for existing in records]
    return [*records, record]


def _identity(path: Path) -> FileIdentity:
    stat = path.stat()
    return FileIdentity(inode=stat.st_ino, size=stat.st_size, modified_ns=stat.st_mtime_ns)


def _find_removable_sources(bundle: TranscribeBundle, identities: dict[str, FileIdentity]) -> tuple[list[Path], list[str]]:
    """Return the bundle's audio still exactly as it was before copying, and the names already gone from the store.

    Raises:
        SourceChangedError: If any file changed since, so the archive holds another version than the one removal would delete.
        OSError: If a file cannot be inspected.

    """
    removable: list[Path] = []
    gone: list[str] = []
    changed: list[str] = []
    for source in bundle.source_audios:
        try:
            identity = _identity(source)
        except FileNotFoundError:
            gone.append(source.name)
            continue
        if identity == identities[source.name]:
            removable.append(source)
        else:
            changed.append(source.name)
    if changed:
        msg = f"{', '.join(changed)} changed in the store after being copied (a sync tool may have brought a newer version), so this bundle's audio was left in place"
        raise SourceChangedError(msg)
    return removable, gone


def _remove_source_audio(sources: list[Path]) -> list[str]:
    """Remove audio from the store, trying every file even when one fails, and return the names already gone.

    Plain unlink on purpose: the store's safe delete would move the audio to
    <store>/_deleted, keeping it in the store. The verified archive copy is the
    safety net. All of a bundle's files go one right after the other, so a sync
    tool is likely to carry the removal as one change.

    Raises:
        SourceRemovalError: If any file could not be removed, naming those that were.

    """
    removed: list[str] = []
    gone: list[str] = []
    failed: list[str] = []
    # Every file is tried, so one locked file leaves as little as possible behind.
    for source in sources:
        try:
            source.unlink()
        except FileNotFoundError:
            gone.append(source.name)
        except OSError as error:
            failed.append(f"{source.name} ({error.strerror or error})")
        else:
            removed.append(source.name)
    if failed:
        done = f"Partly archived: removed {', '.join(removed)}, but" if removed else "Copied and verified, but"
        msg = f"{done} could not remove {', '.join(failed)} from the store"
        raise SourceRemovalError(msg)
    return gone


def _archive_bundle(planned: PlannedBundle, tz: ZoneInfo) -> list[str]:
    """Copy, verify and record one bundle's audio, then remove it from the store.

    Records already in the archive folder and in the bundle's marker are kept and
    completed, never replaced, so a rerun or a later archive of the same bundle adds
    to them. A file is removed only if it is still the version that was copied.

    Returns:
        The names of audio files already gone from the store when their removal came; their copies are verified.

    Raises:
        ArchiveConflictError: If the archive or the marker holds something that must not be overwritten.
        ArchiveVerificationError: If a copy does not match its original.
        SourceChangedError: If audio changed in the store after being copied; nothing was removed.
        SourceRemovalError: If the audio could not all be removed from the store after archiving.
        pydantic.ValidationError: If an existing record cannot be read.
        yaml.YAMLError: If the bundle's marker is not valid YAML.
        OSError: If reading or writing fails.

    """
    bundle = planned.bundle
    archive_dir = planned.archive_dir
    archive_dir.mkdir(parents=True, exist_ok=True)
    earlier = _read_archive_record(archive_dir)
    earlier_files = earlier.files if earlier else []
    # Claim the folder before copying, so a run that stops part way leaves a folder the next run recognises.
    _write_archive_record(_make_record(bundle, archive_dir, earlier_files, tz))

    # Taken before any byte is read, so any change from here on is caught before removal.
    identities = {source.name: _identity(source) for source in bundle.source_audios}
    copied = [_copy_verified(source, archive_dir / source.name, tz) for source in bundle.source_audios]
    record = _make_record(bundle, archive_dir, _merge_files(earlier_files, copied), tz)
    _write_archive_record(record)
    marker_records = _with_record(_read_marker_records(bundle.get_bundle_dir()), record)
    _write_text_atomically(bundle.get_bundle_dir() / ARCHIVED_AUDIO_FILENAME, _marker_text(marker_records))

    removable, gone = _find_removable_sources(bundle, identities)
    return gone + _remove_source_audio(removable)


def _check_arguments(store_dir: Path, archive_root: Path, older_than_days: int) -> None:
    """Refuse arguments that would archive the wrong audio or put the archive in the store.

    Raises:
        InvalidArgumentsError: If ``older_than_days`` is negative, the store directory does not exist,
            or the archive is inside the store.

    """
    if older_than_days < 0:
        msg = f"older_than_days must be 0 or more, not {older_than_days}"
        raise InvalidArgumentsError(msg)
    if not store_dir.is_dir():
        msg = f"Store directory not found: {store_dir}"
        raise InvalidArgumentsError(msg)
    resolved_store = store_dir.resolve()
    resolved_archive = archive_root.resolve()
    if resolved_archive == resolved_store or resolved_store in resolved_archive.parents:
        msg = f"The archive directory {archive_root} is inside the store {store_dir}"
        raise InvalidArgumentsError(msg)


def _plan_bundle(
    bundle_dir: Path,
    archive_root: Path,
    *,
    older_than_days: int,
    include_multi_audio: bool,
    config: TranscribeConfig,
) -> PlannedBundle | SkipReason | BundleError:
    """Inspect one bundle directory without changing anything."""
    try:
        bundle = TranscribeBundle.from_existing_directory(bundle_dir, config, RealFileSystemService(config), RealAudioService())
        if reason := _find_skip_reason(bundle, older_than_days, include_multi_audio=include_multi_audio):
            return reason
        # Read now so that a marker this script must not overwrite shows in a dry run too.
        _read_marker_records(bundle_dir)
        archive_dir = _choose_archive_dir(archive_root, bundle)
        total_bytes = sum(path.stat().st_size for path in bundle.source_audios)
    except _BUNDLE_ERRORS as error:
        return _bundle_error(bundle_dir.name, error)
    return PlannedBundle(bundle, archive_dir, total_bytes)


def _find_skip_reason(bundle: TranscribeBundle, older_than_days: int, *, include_multi_audio: bool) -> SkipReason | None:
    """Return why this bundle keeps its audio in this run, or None to archive it."""
    if blocker := bundle.find_audio_retention_blocker(older_than_days):
        return SkipReason(blocker.value)
    if reason := _find_unfinished_work(bundle):
        return reason
    # A transcriber from before the fix that keeps transcripts when listed audio is
    # missing would redo a bundle seen with only part of its audio gone, which can
    # happen on another machine while a sync tool applies the removal file by file.
    if len(bundle.source_audios) > 1 and not include_multi_audio:
        return SkipReason.SEVERAL_RECORDINGS
    return None


def _find_unfinished_work(bundle: TranscribeBundle) -> SkipReason | None:
    """Return what the transcriber or a person still has to do that needs this bundle's audio, if anything."""
    if (bundle.get_bundle_dir() / MERGE_FAILED_FILENAME).exists():
        return SkipReason.MERGE_FAILED
    listed_audio = {audio_meta.filename for audio_meta in bundle.metadata.audio_files}
    if any(path.name not in listed_audio for path in bundle.source_audios):
        return SkipReason.UNLISTED_AUDIO
    if bundle.commands is None:
        return SkipReason.COMMANDS_NOT_EXTRACTED
    if bundle.commands.has_commands_needing_processing():
        return SkipReason.COMMANDS_PENDING
    return None


def _apply_plan(planned: PlannedBundle, report: ArchiveReport, tz: ZoneInfo) -> bool:
    """Archive one planned bundle into the report, and return whether the run can go on."""
    try:
        gone = _archive_bundle(planned, tz)
    except _BUNDLE_ERRORS as error:
        report.errors.append(_bundle_error(planned.bundle_name, error))
        if _is_disk_full(error):
            # Every following bundle would fail the same way, each leaving an empty claimed folder.
            report.stopped_early = "The archive drive is full, so the run stopped there; the bundles after it were not looked at."
            return False
    else:
        report.archived.append(planned)
        if gone:
            message = f"already gone from the store when it came to removing it, with its copy in the archive verified: {', '.join(gone)}"
            report.notes.append(BundleNote(planned.bundle_name, message))
    return True


def archive_source_audio(
    store_dir: Path,
    archive_root: Path,
    *,
    older_than_days: int,
    include_multi_audio: bool,
    apply: bool,
    config: TranscribeConfig,
) -> ArchiveReport:
    """Find the bundles whose audio can leave the store and, with ``apply``, move it to the archive.

    With ``apply``, each bundle is inspected right before its audio moves, so a change
    made while earlier bundles were being copied (keep forever, a new voice command)
    is seen. A bundle that cannot be read or archived is reported in ``errors`` with
    what to do next; the others proceed.

    Args:
        store_dir: Directory containing persisted bundle directories.
        archive_root: Directory receiving one folder per archived bundle.
        older_than_days: Archive audio more than this many whole days old.
        include_multi_audio: Also archive bundles holding several recordings.
        apply: Move the audio; otherwise only report what would move.
        config: Configuration providing the time zone and bundle loading settings.

    Returns:
        What was planned, archived, skipped and failed.

    Raises:
        InvalidArgumentsError: If ``older_than_days`` is negative, the store directory does not exist,
            or the archive is inside the store.

    """
    _check_arguments(store_dir, archive_root, older_than_days)
    # Written into every record and marker, which must keep pointing at the archive from anywhere.
    archive_root = archive_root.resolve()
    # Bundles find their own directory through the configured store, so it must be the one scanned.
    config = config.model_copy(update={"general": config.general.model_copy(update={"store_dir": store_dir})})
    report = ArchiveReport(archive_root=archive_root)

    for bundle_dir in sorted(store_dir.iterdir(), key=lambda path: path.name.casefold()):
        if not bundle_dir.is_dir() or bundle_dir.name.startswith(("_", ".")):
            continue
        match _plan_bundle(
            bundle_dir,
            archive_root,
            older_than_days=older_than_days,
            include_multi_audio=include_multi_audio,
            config=config,
        ):
            case PlannedBundle() as planned:
                report.planned.append(planned)
                if apply and not _apply_plan(planned, report, config.general.timezone):
                    break
            case BundleError() as error:
                report.errors.append(error)
            case SkipReason() as reason:
                report.skipped[reason] += 1

    return report


def _format_size(size_bytes: int) -> str:
    gigabyte = 1024**3
    if size_bytes >= gigabyte:
        return f"{size_bytes / gigabyte:.2f} GB"
    return f"{size_bytes / 1024**2:.1f} MB"


def _bundle_line(label: str, planned: PlannedBundle) -> str:
    """Return a bundle's report line: its archive folder's name, under the archive directory the report starts with.

    The folder name is the bundle's, or its ID-suffixed form when another bundle already has that name.
    """
    count = len(planned.bundle.source_audios)
    files = "1 file" if count == 1 else f"{count} files"
    return f"[{label}] {planned.archive_dir.name} ({files}, {_format_size(planned.total_bytes)})"


def report_lines(report: ArchiveReport, *, apply: bool) -> list[str]:
    """Return the report: one line per bundle acted on, then a summary ending with what to do next."""
    lines = [f"Archive directory: {report.archive_root}"]
    label = "archived" if apply else "dry-run"
    lines += [_bundle_line(label, planned) for planned in (report.archived if apply else report.planned)]
    lines += [f"[note] {note.bundle_name}: {note.message}" for note in report.notes]
    for error in report.errors:
        lines += [f"[error] {error.bundle_name}: {error.message}", f"        next step: {error.next_step}"]
    if report.stopped_early:
        lines.append(report.stopped_early)

    if report.skipped:
        counts = ", ".join(f"{count} {reason}" for reason, count in report.skipped.most_common())
        lines.append(f"Kept their audio: {counts}.")
    if report.skipped[SkipReason.SEVERAL_RECORDINGS]:
        lines.append(
            f"Bundles with several recordings were left out; add {MULTI_AUDIO_FLAG} once every transcriber "
            "watching this store keeps transcripts when listed audio is missing (see docs/maintenance.md).",
        )

    if apply:
        size = _format_size(sum(planned.total_bytes for planned in report.archived))
        lines.append(f"Archived the audio of {len(report.archived)} bundle(s), {size}, into {report.archive_root}.")
    elif report.planned:
        size = _format_size(sum(planned.total_bytes for planned in report.planned))
        lines.append(
            f"Dry run: nothing was copied or removed. {len(report.planned)} bundle(s), {size}, would be archived; add --apply to do it.",
        )
    else:
        lines.append("Dry run: nothing to archive.")
    if report.errors:
        lines.append(
            f"{len(report.errors)} bundle(s) failed; each [error] line is followed by its next step. "
            "Running again is safe: what is already in the archive is reused, never overwritten.",
        )
    return lines


def print_report(report: ArchiveReport, *, apply: bool) -> None:
    """Print the report returned by ``report_lines``."""
    for line in report_lines(report, apply=apply):
        print(line)


def _append_to_log(log_file: Path, header: str, lines: Iterable[str]) -> None:
    """Add one run's lines to the log, under a header naming the run, creating the file if needed."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log:
        log.write(f"=== {header}\n")
        log.writelines(f"{line}\n" for line in lines)


def _time_zone(name: str) -> ZoneInfo:
    """Return the time zone an IANA name such as ``Europe/Brussels`` names, for argparse."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        msg = f"unknown time zone {name!r}; use an IANA name such as Europe/Brussels"
        raise argparse.ArgumentTypeError(msg) from error


def build_parser() -> argparse.ArgumentParser:
    """Return the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="transcriber-archive-audio",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--store-dir", type=Path, required=True, help="Bundle store directory.")
    parser.add_argument(
        "--archive-dir",
        type=Path,
        required=True,
        help="Directory receiving one folder per archived bundle. Must be outside the store.",
    )
    parser.add_argument(
        "--timezone",
        type=_time_zone,
        required=True,
        help="Time zone of the recordings, as an IANA name such as Europe/Brussels: the transcriber's general.timezone.",
    )
    parser.add_argument(
        "--older-than-days",
        type=int,
        default=DEFAULT_OLDER_THAN_DAYS,
        help=f"Archive audio more than this many whole days old (default {DEFAULT_OLDER_THAN_DAYS}).",
    )
    parser.add_argument(
        MULTI_AUDIO_FLAG,
        action="store_true",
        help="Also archive bundles holding several recordings. Only safe once every transcriber watching the store "
        "keeps transcripts when listed audio is missing.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Also append each run's report to this file, under a line giving the run's time, mode and exit status.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Copy, verify and remove the audio. Without this flag the command is a dry run.",
    )
    return parser


def maintenance_config(store_dir: Path, timezone: ZoneInfo) -> TranscribeConfig:
    """Return the configuration bundles are loaded with, built from this command's arguments alone.

    Loading and moving bundle files uses only the store and the time zone. The other
    general settings are fixed to their neutral values, and the API sections hold
    placeholders: this command never calls the transcription or text APIs, so it needs
    none of the transcriber's settings or keys.
    """
    unused = "unused by the archive command"
    return TranscribeConfig(
        general=GeneralConfig(
            input_dir=store_dir,
            store_dir=store_dir,
            delete_source_audio_after_days=0,
            min_length_seconds=0.0,
            remove_short_files=False,
            timezone=timezone,
            safe_delete=True,
            merge_max_hours=0.0,
        ),
        text=TextConfig(summary_enabled=False, api_base_url=unused, model=unused, api_key=unused),
        audio=AudioConfig(api_base_url=unused, model=unused, api_key=unused, stream=False),
    )


def _escape_unencodable_output() -> None:
    """Print characters the output's encoding lacks as escapes instead of failing.

    On Windows, output to a pipe (as when a scheduler or an agent runs this command) is
    often cp1252, while bundle titles may hold any character; a failed print would cut
    the report off after the audio has already moved.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    """Run the source audio archiving command.

    Returns:
        ``EXIT_DONE`` when every planned bundle succeeded, ``EXIT_BUNDLE_FAILED`` when
        any bundle failed, ``EXIT_NOT_STARTED`` when nothing was done because the
        arguments are unusable.

    """
    _escape_unencodable_output()
    args = build_parser().parse_args(argv)
    started = datetime.now(args.timezone).isoformat(timespec="seconds")
    mode = "apply" if args.apply else "dry run"
    try:
        report = archive_source_audio(
            args.store_dir,
            args.archive_dir,
            older_than_days=args.older_than_days,
            include_multi_audio=args.include_multi_audio,
            apply=args.apply,
            config=maintenance_config(args.store_dir, args.timezone),
        )
    except InvalidArgumentsError as error:
        message = f"Nothing was done: {error}"
        print(message, file=sys.stderr)
        if args.log_file:
            _append_to_log(args.log_file, f"{started} {mode}: exit {EXIT_NOT_STARTED}", [message])
        return EXIT_NOT_STARTED

    lines = report_lines(report, apply=args.apply)
    for line in lines:
        print(line)
    exit_status = EXIT_BUNDLE_FAILED if report.errors else EXIT_DONE
    if args.log_file:
        _append_to_log(args.log_file, f"{started} {mode}: exit {exit_status}", lines)
    return exit_status


if __name__ == "__main__":
    sys.exit(main())
