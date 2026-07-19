"""Migrate existing bundles to include a custom_context.md file.

New bundles get a ``custom_context.md`` (seeded by CreateBundleJob) so users
have a discoverable place to add per-recording summary context. Existing bundles
created before this feature lack the file. This script backfills it.

Usage:
    uv run python scripts/migrate_add_custom_context.py            # dry run
    uv run python scripts/migrate_add_custom_context.py --apply    # write files
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import (
    CUSTOM_CONTEXT_FILENAME,
    DEFAULT_CUSTOM_CONTEXT_CONTENT,
    METADATA_FILENAME,
)
from transcriber.files.file_system import (
    FileSystemService,
    RealFileSystemService,
)
from transcriber.files.metadata import MetadataFile
from transcriber.files.text_file import CustomContextFile


def migrate_store(
    store_dir: Path,
    apply: bool,
    fs_service: FileSystemService | None = None,
) -> tuple[int, int, int]:
    """Add a custom_context.md to bundles that don't have one.

    Args:
        store_dir: The bundle store directory.
        apply: When False, only report what would change (dry run).
        fs_service: Optional file-system service (for testing). Defaults to a
            real service built from the configured TranscribeConfig.

    Returns:
        Tuple of (number of bundles scanned, number seeded).

    """
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    fs_service = fs_service or RealFileSystemService(config)

    scanned = 0
    seeded = 0
    hashes_written = 0

    for bundle_dir in sorted(store_dir.iterdir()):
        if not bundle_dir.is_dir():
            continue
        if bundle_dir.name.startswith("_") or bundle_dir.name.startswith("."):
            continue

        scanned += 1
        context_path = bundle_dir / CUSTOM_CONTEXT_FILENAME
        if not fs_service.file_exists(context_path):
            seeded += 1
            if apply:
                CustomContextFile(DEFAULT_CUSTOM_CONTEXT_CONTENT).write(bundle_dir, fs_service)
                print(f"[write] {bundle_dir.name}: created {CUSTOM_CONTEXT_FILENAME}")
            else:
                print(f"[dry-run] {bundle_dir.name}: would create {CUSTOM_CONTEXT_FILENAME}")

        # Backfill the context hash so the field is always present. Compute it from
        # the (now current) custom_context.md, or empty if the file is absent.
        metadata_path = bundle_dir / METADATA_FILENAME
        if fs_service.file_exists(metadata_path):
            metadata = MetadataFile.from_file(metadata_path, fs_service)
            if metadata.summary_context_hash is None:
                context_text = fs_service.read_file(context_path) if fs_service.file_exists(context_path) else ""
                # Mirror TranscribeBundle.compute_context_hash (sha256, 16 chars).
                effective = "\n".join(line for line in context_text.splitlines() if not line.lstrip().startswith("[//]:")).strip()
                metadata.summary_context_hash = sha256(effective.encode("utf-8")).hexdigest()[:16]
                if apply:
                    metadata.write(bundle_dir, fs_service)
                    print(f"[write] {bundle_dir.name}: backfilled summary_context_hash")
                else:
                    print(f"[dry-run] {bundle_dir.name}: would backfill summary_context_hash")
                hashes_written += 1

    return scanned, seeded, hashes_written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the context files. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to the configured general.store_dir.",
    )
    args = parser.parse_args()

    store_dir = args.store_dir or TranscribeConfig().general.store_dir  # pyright: ignore[reportCallIssue]
    scanned, seeded, hashes_written = migrate_store(store_dir, apply=args.apply)

    mode = "apply" if args.apply else "dry-run"
    print(f"\nScanned {scanned} bundle(s); {seeded} would be/ were seeded, {hashes_written} hash(es) would be/ were backfilled ({mode}).")


if __name__ == "__main__":
    main()
