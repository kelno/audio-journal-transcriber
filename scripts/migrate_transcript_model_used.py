"""Migrate existing bundle metadata to the list-based `transcript_model_used` format.

Historically `transcript_model_used` was stored as a single string. It is now a
list of model names (an ordered set). The runtime already coerces a legacy string
into a one-element list on read, but this script rewrites the on-disk `_metadata.md`
files so they are stored in the new format directly.

Usage:
    uv run python scripts/migrate_transcript_model_used.py            # dry run
    uv run python scripts/migrate_transcript_model_used.py --apply    # rewrite files

Only bundles whose `transcript_model_used` is currently a string (or missing while
a legacy value exists) are rewritten. Bundles already using the list format are left
untouched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME
from transcriber.files.file_system import RealFileSystemService
from transcriber.files.metadata import MetadataFile


def _normalize(value: object) -> list[str]:
    """Convert a legacy string / list / None into a deduped, ordered list of models."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        seen: set[str] = set()
        deduped: list[str] = []
        for item in value:
            if isinstance(item, str) and item not in seen:
                seen.add(item)
                deduped.append(item)
        return deduped
    # Unexpected type: keep as-is so the caller can surface it.
    return value  # type: ignore[return-value]


def migrate_store(store_dir: Path, apply: bool) -> tuple[int, int]:
    """Rewrite bundle metadata files in `store_dir`.

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
        metadata = MetadataFile.from_file(meta_path, fs_service)

        # Determine the raw on-disk value to decide if a rewrite is needed.
        raw_text = fs_service.read_file(meta_path)
        raw_value = None
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("transcript_model_used:"):
                # Extract the value after the colon, unquoted.
                _, _, rest = stripped.partition(":")
                raw_value = rest.strip().strip('"').strip("'")
                if raw_value.lower() in {"null", "none", "~", ""}:
                    raw_value = None
                break

        # Only rewrite when the on-disk value is a legacy string (not a YAML list/None).
        needs_rewrite = isinstance(raw_value, str)

        if not needs_rewrite:
            continue

        normalized = _normalize(raw_value)
        metadata.transcript_model_used = normalized
        rewritten += 1

        if apply:
            metadata.write(bundle_dir, fs_service)
            print(f"[rewrite] {bundle_dir.name}: {raw_value!r} -> {normalized}")
        else:
            print(f"[dry-run] {bundle_dir.name}: would rewrite {raw_value!r} -> {normalized}")

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
