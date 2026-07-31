"""Add persistent unique IDs to existing bundle metadata.

The application deliberately requires ``bundle_id`` after this migration. This
script therefore edits legacy YAML frontmatter directly instead of loading it
through the current MetadataFile model.

Usage:
    uv run python scripts/migrate_add_bundle_ids.py
    uv run python scripts/migrate_add_bundle_ids.py --apply
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from transcriber.bundle_id import BUNDLE_ID_PATTERN, new_bundle_id
from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME
from transcriber.files.file_system import FileSystemService, RealFileSystemService
from transcriber.files.metadata import MetadataFile


@dataclass(frozen=True)
class MigrationResult:
    """Summary of a bundle ID migration pass."""

    scanned: int
    migrated: int
    already_current: int


@dataclass(frozen=True)
class _PendingWrite:
    metadata_path: Path
    content: str
    bundle_id: str


@dataclass(frozen=True)
class _PreparedBundle:
    metadata_path: Path
    bundle_id: str
    pending_write: _PendingWrite | None


def _parse_metadata(metadata_path: Path, fs_service: FileSystemService) -> tuple[dict[str, object], str]:
    """Return legacy frontmatter data and the untouched text after it.

    Args:
        metadata_path: Legacy metadata file to parse.
        fs_service: Filesystem implementation used to read the file.

    Returns:
        Parsed YAML data and the original text following the frontmatter.

    Raises:
        ValueError: If frontmatter is missing or incomplete.
        TypeError: If the frontmatter does not contain a YAML mapping.

    """
    text = fs_service.read_file(metadata_path)
    if not text.startswith("---"):
        msg = f"Metadata has no YAML frontmatter: {metadata_path}"
        raise ValueError(msg)

    parts = text.split("---", 2)
    if len(parts) != 3:
        msg = f"Metadata has incomplete YAML frontmatter: {metadata_path}"
        raise ValueError(msg)

    data = yaml.safe_load(parts[1])
    if not isinstance(data, dict):
        msg = f"Metadata frontmatter is not a mapping: {metadata_path}"
        raise TypeError(msg)
    return data, parts[2]


def _render_metadata(data: dict[str, object], body: str) -> str:
    """Render metadata with bundle_id first while preserving any body text.

    Args:
        data: Metadata values to serialize as YAML frontmatter.
        body: Original text following the frontmatter delimiters.

    Returns:
        Complete metadata file content.

    """
    yaml_text = yaml.safe_dump(data, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---{body}"


def _validate_bundle_id(bundle_id: object, metadata_path: Path) -> str:
    """Validate an existing ID using the same representation as the model.

    Args:
        bundle_id: Deserialized value to validate.
        metadata_path: Source file used in validation errors.

    Returns:
        The validated lowercase UUID hex string.

    Raises:
        ValueError: If the value is not a valid bundle identifier.

    """
    if not isinstance(bundle_id, str) or re.fullmatch(BUNDLE_ID_PATTERN, bundle_id) is None:
        msg = f"Invalid bundle_id in {metadata_path}: {bundle_id!r}"
        raise ValueError(msg)
    return bundle_id


def _prepare_bundle(bundle_dir: Path, fs_service: FileSystemService) -> _PreparedBundle:
    """Validate one bundle and prepare its metadata update when needed.

    Args:
        bundle_dir: Persisted bundle directory to inspect.
        fs_service: Filesystem implementation used for metadata access.

    Returns:
        Validated identity information and an optional pending write.

    Raises:
        ValueError: If metadata is missing or contains an invalid ID.
        TypeError: If metadata frontmatter is not a mapping.

    """
    metadata_path = bundle_dir / METADATA_FILENAME
    if not fs_service.file_exists(metadata_path):
        msg = f"Bundle has no {METADATA_FILENAME}: {bundle_dir}"
        raise ValueError(msg)

    data, body = _parse_metadata(metadata_path, fs_service)
    pending_write: _PendingWrite | None = None
    if "bundle_id" in data:
        bundle_id = _validate_bundle_id(data["bundle_id"], metadata_path)
    else:
        bundle_id = new_bundle_id()
        data = {"bundle_id": bundle_id, **data}
        pending_write = _PendingWrite(
            metadata_path=metadata_path,
            content=_render_metadata(data, body),
            bundle_id=bundle_id,
        )

    # Validate the complete post-migration shape without relying on the model
    # to invent a missing ID or changing the original serialized data.
    MetadataFile.model_validate(data)
    return _PreparedBundle(
        metadata_path=metadata_path,
        bundle_id=bundle_id,
        pending_write=pending_write,
    )


def migrate_store(
    store_dir: Path,
    apply: bool,
    fs_service: FileSystemService | None = None,
) -> MigrationResult:
    """Plan or apply persistent IDs for every bundle in ``store_dir``.

    The full store is validated before any file is written, preventing malformed
    metadata or duplicate existing IDs from causing a partial migration.

    Args:
        store_dir: Managed store containing bundle directories.
        apply: Whether to write planned metadata changes.
        fs_service: Optional filesystem implementation, primarily for tests.

    Returns:
        Counts of scanned, migrated, and already-current bundles.

    Raises:
        ValueError: If a bundle is invalid or duplicate IDs are found.
        TypeError: If metadata frontmatter is not a mapping.

    """
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    fs_service = fs_service or RealFileSystemService(config)

    seen_ids: dict[str, Path] = {}
    pending: list[_PendingWrite] = []
    scanned = 0
    already_current = 0

    for bundle_dir in sorted(fs_service.list_directory(store_dir)):
        if not fs_service.directory_exists(bundle_dir):
            continue
        if bundle_dir.name.startswith(("_", ".")):
            continue

        scanned += 1
        prepared = _prepare_bundle(bundle_dir, fs_service)
        if prepared.pending_write is None:
            already_current += 1
        else:
            pending.append(prepared.pending_write)

        if previous_path := seen_ids.get(prepared.bundle_id):
            msg = f"Duplicate bundle_id {prepared.bundle_id}: {previous_path.parent} and {bundle_dir}"
            raise ValueError(msg)
        seen_ids[prepared.bundle_id] = prepared.metadata_path

    for change in pending:
        prefix = "write" if apply else "dry-run"
        print(f"[{prefix}] {change.metadata_path.parent.name}: bundle_id={change.bundle_id}")
        if apply:
            fs_service.write_file(change.metadata_path, change.content)

    return MigrationResult(
        scanned=scanned,
        migrated=len(pending),
        already_current=already_current,
    )


def main() -> None:
    """Run the bundle ID migration CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write bundle IDs. Without this flag the script is a dry run.",
    )
    parser.add_argument(
        "--store-dir",
        type=Path,
        default=None,
        help="Bundle store directory. Defaults to general.store_dir.",
    )
    args = parser.parse_args()

    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    store_dir = args.store_dir or config.general.store_dir
    result = migrate_store(store_dir, apply=args.apply)
    mode = "apply" if args.apply else "dry-run"
    print(
        f"\nScanned {result.scanned} bundle(s); {result.migrated} migration(s), {result.already_current} already current ({mode}).",
    )


if __name__ == "__main__":
    main()
