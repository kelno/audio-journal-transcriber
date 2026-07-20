"""Migrate existing bundles to include a canonical bundle_date in metadata.

New bundles compute ``bundle_date`` at creation (init_metadata). Existing bundles
created before this field existed have no ``bundle_date``. This script backfills
it so the field is always present and ``get_bundle_date`` can treat it as the
single source of truth (no backward-compat fallback in the code).

Usage:
    uv run python scripts/migrate_add_bundle_date.py            # dry run
    uv run python scripts/migrate_add_bundle_date.py --apply    # write files
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME
from transcriber.files.file_system import (
    FileSystemService,
    RealFileSystemService,
)
from transcriber.files.metadata import MetadataFile
from transcriber.globals import is_handled_audio_file
from transcriber.utils import extract_date_from_recording_filename, get_file_modified_date


def _compute_bundle_date(
    bundle_dir: Path,
    fs_service: FileSystemService,
    tz: ZoneInfo,
) -> datetime | None:
    """Compute the bundle date from audio files or the bundle name prefix."""
    audio_files = sorted(p for p in fs_service.list_directory(bundle_dir) if is_handled_audio_file(p.suffix))
    for audio_path in audio_files:
        filename = audio_path.name
        date = extract_date_from_recording_filename(filename, tz)
        if date:
            return date
    # Fall back to file modified date of the first audio file.
    for audio_path in audio_files:
        return get_file_modified_date(audio_path, tz)
    # Finally, the bundle name's YYYY-MM-DD prefix.
    try:
        return datetime.strptime(bundle_dir.name[:10], "%Y-%m-%d").replace(tzinfo=tz)
    except ValueError:
        return None


def migrate_store(
    store_dir: Path,
    apply: bool,
    fs_service: FileSystemService | None = None,
) -> tuple[int, int]:
    """Backfill bundle_date into bundle metadata that lacks it.

    Args:
        store_dir: The bundle store directory.
        apply: When False, only report what would change (dry run).
        fs_service: Optional file-system service (for testing). Defaults to a
            real service built from the configured TranscribeConfig.

    Returns:
        Tuple of (number of bundles scanned, number backfilled).

    """
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    fs_service = fs_service or RealFileSystemService(config)
    tz = config.general.timezone

    scanned = 0
    backfilled = 0

    for bundle_dir in sorted(store_dir.iterdir()):
        if not bundle_dir.is_dir():
            continue
        if bundle_dir.name.startswith("_") or bundle_dir.name.startswith("."):
            continue

        metadata_path = bundle_dir / METADATA_FILENAME
        if not fs_service.file_exists(metadata_path):
            continue

        scanned += 1
        metadata = MetadataFile.from_file(metadata_path, fs_service)
        if metadata.bundle_date is not None:
            continue

        date = _compute_bundle_date(bundle_dir, fs_service, tz)
        if date is None:
            print(f"[skip] {bundle_dir.name}: could not determine a date")
            continue

        backfilled += 1
        if apply:
            metadata.bundle_date = date
            metadata.write(bundle_dir, fs_service)
            print(f"[write] {bundle_dir.name}: set bundle_date to {date}")
        else:
            print(f"[dry-run] {bundle_dir.name}: would set bundle_date to {date}")

    return scanned, backfilled


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the metadata files. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to the configured general.store_dir.",
    )
    args = parser.parse_args()

    store_dir = args.store_dir or TranscribeConfig().general.store_dir  # pyright: ignore[reportCallIssue]
    scanned, backfilled = migrate_store(store_dir, apply=args.apply)

    mode = "apply" if args.apply else "dry-run"
    print(f"\nScanned {scanned} bundle(s); {backfilled} would be/ were backfilled ({mode}).")


if __name__ == "__main__":
    main()
