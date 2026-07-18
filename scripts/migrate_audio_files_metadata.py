"""Migrate existing bundle metadata to the per-file `audio_files` format.

Historically metadata stored a flat `original_audio_filenames` list plus a single
bundle-level `transcript_model_used`. It now stores `audio_files`: a list of
`AudioFileMeta` records (filename + per-file transcript_model_used).

The runtime already converts legacy metadata on read (see `MetadataFile.from_file`),
but this script rewrites the on-disk `_metadata.md` files so they are stored in the
new format directly (and so legacy keys are gone from disk).

Usage:
    uv run python scripts/migrate_audio_files_metadata.py            # dry run
    uv run python scripts/migrate_audio_files_metadata.py --apply    # rewrite files
"""

from __future__ import annotations

import argparse
from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME
from transcriber.files.file_system import RealFileSystemService
from transcriber.files.metadata import MetadataFile


def _is_legacy(raw_text: str) -> bool:
    """Return True if the raw metadata still uses the old flat format."""
    # The legacy format has either the singular or plural flat filename key and no
    # audio_files key.
    return "original_audio_filename" in raw_text and "audio_files:" not in raw_text


def migrate_store(store_dir: Path, apply: bool) -> tuple[int, int]:
    """Rewrite legacy bundle metadata files in `store_dir`.

    Args:
        store_dir: The bundle store directory.
        apply: When False, only report what would change (dry run).

    Returns:
        Tuple of (number of bundles scanned, number rewritten).

    """
    config = TranscribeConfig()
    fs_service = RealFileSystemService(config)

    scanned = 0
    rewritten = 0

    for bundle_dir in sorted(store_dir.iterdir()):
        if not bundle_dir.is_dir():
            continue
        if bundle_dir.name.startswith("_") or bundle_dir.name.startswith("."):
            continue

        meta_path = bundle_dir / METADATA_FILENAME
        if not meta_path.is_file():
            continue

        scanned += 1
        raw_text = fs_service.read_file(meta_path)
        if not _is_legacy(raw_text):
            continue

        # Loading converts the legacy format into the new audio_files format.
        metadata = MetadataFile.from_file(meta_path, fs_service)

        rewritten += 1
        if apply:
            metadata.write(bundle_dir, fs_service)
            print(f"[rewrite] {bundle_dir.name}: {len(metadata.audio_files)} audio file(s)")
        else:
            print(f"[dry-run] {bundle_dir.name}: would rewrite {len(metadata.audio_files)} audio file(s)")

    return scanned, rewritten


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite metadata files. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to the configured general.store_dir.",
    )
    args = parser.parse_args()

    store_dir = args.store_dir or TranscribeConfig().general.store_dir
    scanned, rewritten = migrate_store(store_dir, apply=args.apply)

    mode = "apply" if args.apply else "dry-run"
    print(f"\nScanned {scanned} bundle(s); {rewritten} would be/ were rewritten ({mode}).")


if __name__ == "__main__":
    main()
