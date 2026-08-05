"""Archive stale transcripts so retained audio is regenerated with a new model.

The target is the configured ``audio.model``. By default this script only
reports what it would rename; pass ``--apply`` to perform the renames.

Usage:
    uv run python scripts/rename_transcripts_for_regeneration.py
    uv run python scripts/rename_transcripts_for_regeneration.py --apply
    uv run python scripts/rename_transcripts_for_regeneration.py --model new-model --apply
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from enum import Enum, auto
from hashlib import sha256
from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME, TRANSCRIPT_FILENAME
from transcriber.files.file_system import FileSystemService, RealFileSystemService
from transcriber.files.metadata import MetadataFile
from transcriber.globals import is_handled_audio_file

UNKNOWN_MODEL_NAME = "unknown-model"
MAX_ARCHIVE_MODEL_LABEL_LENGTH = 120
_INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*+\x00-\x1f]+')


@dataclass(frozen=True)
class RenameResult:
    """Counts from one scan of the bundle store."""

    scanned: int
    eligible: int
    already_current: int
    without_audio: int
    already_pending: int


@dataclass(frozen=True)
class _PendingRename:
    bundle_name: str
    source: Path
    destination: Path


class _BundleStatus(Enum):
    WITHOUT_AUDIO = auto()
    ALREADY_CURRENT = auto()
    ALREADY_PENDING = auto()


def _archive_model_names(metadata: MetadataFile) -> list[str]:
    """Return the ordered, unique model names represented by a transcript."""
    names: list[str] = []
    for audio_meta in metadata.audio_files:
        recorded_names = audio_meta.transcript_model_used or [UNKNOWN_MODEL_NAME]
        for model_name in recorded_names:
            if model_name not in names:
                names.append(model_name)
    return names or [UNKNOWN_MODEL_NAME]


def _safe_archive_model_label(model_names: list[str]) -> str:
    """Build a readable Windows-safe label, shortening unusually long labels."""
    safe_names: list[str] = []
    for model_name in model_names:
        safe_name = _INVALID_FILENAME_CHARACTERS.sub("_", model_name).strip(" ._")
        safe_names.append(safe_name or UNKNOWN_MODEL_NAME)

    label = "+".join(safe_names)
    if len(label) <= MAX_ARCHIVE_MODEL_LABEL_LENGTH:
        return label

    digest = sha256(label.encode()).hexdigest()[:8]
    prefix_length = MAX_ARCHIVE_MODEL_LABEL_LENGTH - len(digest) - 1
    prefix = label[:prefix_length].rstrip(" ._+-")
    return f"{prefix}-{digest}"


def _next_archive_path(
    bundle_dir: Path,
    metadata: MetadataFile,
    fs_service: FileSystemService,
) -> Path:
    """Return a model-labelled archive path that does not already exist."""
    model_label = _safe_archive_model_label(_archive_model_names(metadata))
    transcript_filename = Path(TRANSCRIPT_FILENAME)
    base_stem = f"{transcript_filename.stem}_{model_label}"
    candidate = bundle_dir / f"{base_stem}{transcript_filename.suffix}"
    counter = 2
    while fs_service.file_exists(candidate):
        candidate = bundle_dir / f"{base_stem}_{counter}{transcript_filename.suffix}"
        counter += 1
    return candidate


def _target_used_for_all_audio(
    audio_paths: list[Path],
    metadata: MetadataFile,
    target_model: str,
) -> bool:
    """Return whether every retained audio records use of the target model."""
    metadata_by_filename = {
        audio_meta.filename: audio_meta
        for audio_meta in metadata.audio_files
    }
    for audio_path in audio_paths:
        audio_meta = metadata_by_filename.get(audio_path.name)
        if audio_meta is None or target_model not in audio_meta.transcript_model_used:
            return False
    return True


def _inspect_bundle(
    bundle_dir: Path,
    target_model: str,
    fs_service: FileSystemService,
) -> _PendingRename | _BundleStatus:
    """Return the regeneration action or skip reason for one bundle."""
    audio_paths = [
        path
        for path in fs_service.list_directory(bundle_dir)
        if fs_service.file_exists(path) and is_handled_audio_file(path.suffix)
    ]
    if not audio_paths:
        return _BundleStatus.WITHOUT_AUDIO

    metadata_path = bundle_dir / METADATA_FILENAME
    if not fs_service.file_exists(metadata_path):
        msg = f"Bundle has audio but no {METADATA_FILENAME}: {bundle_dir}"
        raise FileNotFoundError(msg)
    metadata = MetadataFile.from_file(metadata_path, fs_service)

    if _target_used_for_all_audio(audio_paths, metadata, target_model):
        return _BundleStatus.ALREADY_CURRENT

    transcript_path = bundle_dir / TRANSCRIPT_FILENAME
    if not fs_service.file_exists(transcript_path):
        print(f"[already pending] {bundle_dir.name}: no {TRANSCRIPT_FILENAME}")
        return _BundleStatus.ALREADY_PENDING

    return _PendingRename(
        bundle_name=bundle_dir.name,
        source=transcript_path,
        destination=_next_archive_path(bundle_dir, metadata, fs_service),
    )


def prepare_transcript_regeneration(
    store_dir: Path,
    *,
    target_model: str,
    apply: bool,
    fs_service: FileSystemService,
) -> RenameResult:
    """Archive transcripts for retained audio not yet transcribed by a model.

    The complete store is inspected before any rename is applied, so invalid
    metadata cannot leave an otherwise valid run only partly prepared.

    Args:
        store_dir: Directory containing persisted bundle directories.
        target_model: Model that will be used for the next transcription.
        apply: Whether to perform planned renames instead of only reporting them.
        fs_service: Filesystem implementation used to inspect and rename files.

    Returns:
        Counts describing the scan and its outcome.

    Raises:
        ValueError: If the target model is empty.
        FileNotFoundError: If an audio-containing bundle has no metadata.

    """
    if not target_model.strip():
        msg = "Target transcript model cannot be empty"
        raise ValueError(msg)

    pending_renames: list[_PendingRename] = []
    scanned = 0
    already_current = 0
    without_audio = 0
    already_pending = 0

    for bundle_dir in sorted(
        fs_service.list_directory(store_dir),
        key=lambda path: path.name.casefold(),
    ):
        if not fs_service.directory_exists(bundle_dir) or bundle_dir.name.startswith(("_", ".")):
            continue

        scanned += 1
        inspection = _inspect_bundle(bundle_dir, target_model, fs_service)
        if inspection is _BundleStatus.WITHOUT_AUDIO:
            without_audio += 1
        elif inspection is _BundleStatus.ALREADY_CURRENT:
            already_current += 1
        elif inspection is _BundleStatus.ALREADY_PENDING:
            already_pending += 1
        else:
            pending_renames.append(inspection)

    for pending in pending_renames:
        prefix = "rename" if apply else "dry-run"
        print(f"[{prefix}] {pending.bundle_name}: {pending.source.name} -> {pending.destination.name}")
        if apply:
            # Refuse a destination that appeared after preflight instead of intentionally overwriting it.
            if fs_service.file_exists(pending.destination):
                msg = f"Archive destination appeared during scan: {pending.destination}"
                raise FileExistsError(msg)
            fs_service.move_file(pending.source, pending.destination)

    return RenameResult(
        scanned=scanned,
        eligible=len(pending_renames),
        already_current=already_current,
        without_audio=without_audio,
        already_pending=already_pending,
    )


def main() -> None:
    """Run the transcript regeneration preparation CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Archive stale transcripts. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Assert the expected target model. It must match configured audio.model.",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to general.store_dir.",
    )
    args = parser.parse_args()

    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    target_model = config.audio.model
    if args.model is not None and args.model != target_model:
        parser.error(
            f"--model {args.model!r} does not match configured audio.model "
            f"{target_model!r}; regeneration would use the configured model",
        )

    store_dir = args.store_dir or config.general.store_dir
    fs_service = RealFileSystemService(config)
    print(f"Target transcript model: {target_model}")
    result = prepare_transcript_regeneration(
        store_dir,
        target_model=target_model,
        apply=args.apply,
        fs_service=fs_service,
    )
    mode = "apply" if args.apply else "dry-run"
    print(
        f"\nScanned {result.scanned} bundle(s); "
        f"{result.eligible} transcript(s) eligible ({mode}). "
        f"Skipped: {result.already_current} already current, "
        f"{result.without_audio} without retained audio, "
        f"{result.already_pending} already pending regeneration.",
    )


if __name__ == "__main__":
    main()
