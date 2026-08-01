"""Replace legacy bundle-name booleans with explicit bundle title states.

The application deliberately requires ``bundle_title_state`` after this
migration. This script therefore edits legacy YAML frontmatter directly instead
of loading it through the current MetadataFile model.

Usage:
    uv run python scripts/migrate_bundle_title_state.py
    uv run python scripts/migrate_bundle_title_state.py --apply
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import yaml

from transcriber.bundle_title import BundleTitleState
from transcriber.config import TranscribeConfig
from transcriber.constants import METADATA_FILENAME
from transcriber.files.file_system import FileSystemService, RealFileSystemService
from transcriber.files.metadata import MetadataFile

LEGACY_TITLE_FIELD = "bundle_name_generated"
TITLE_STATE_FIELD = "bundle_title_state"


@dataclass(frozen=True)
class MigrationResult:
    """Summary of a bundle title-state migration pass."""

    scanned: int
    migrated: int
    already_current: int


@dataclass(frozen=True)
class _PendingWrite:
    metadata_path: Path
    content: str
    title_state: BundleTitleState


def _parse_metadata(metadata_path: Path, fs_service: FileSystemService) -> tuple[dict[str, object], str]:
    """Return metadata frontmatter and the untouched text following it."""
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
    """Render migrated frontmatter while preserving any metadata body text."""
    yaml_text = yaml.safe_dump(data, sort_keys=False).strip()
    return f"---\n{yaml_text}\n---{body}"


def _prepare_bundle(bundle_dir: Path, fs_service: FileSystemService) -> _PendingWrite | None:
    """Validate one bundle and prepare its title-state update when needed."""
    metadata_path = bundle_dir / METADATA_FILENAME
    if not fs_service.file_exists(metadata_path):
        msg = f"Bundle has no {METADATA_FILENAME}: {bundle_dir}"
        raise ValueError(msg)

    data, body = _parse_metadata(metadata_path, fs_service)
    has_legacy_field = LEGACY_TITLE_FIELD in data
    has_state_field = TITLE_STATE_FIELD in data

    if has_legacy_field and has_state_field:
        msg = f"Metadata contains both {LEGACY_TITLE_FIELD} and {TITLE_STATE_FIELD}: {metadata_path}"
        raise ValueError(msg)

    if has_state_field:
        MetadataFile.model_validate(data)
        return None

    if not has_legacy_field:
        msg = f"Metadata contains neither {LEGACY_TITLE_FIELD} nor {TITLE_STATE_FIELD}: {metadata_path}"
        raise ValueError(msg)

    legacy_generated = data[LEGACY_TITLE_FIELD]
    if not isinstance(legacy_generated, bool):
        msg = f"Invalid {LEGACY_TITLE_FIELD} in {metadata_path}: {legacy_generated!r}"
        raise TypeError(msg)

    title_state = BundleTitleState.GENERATED if legacy_generated else BundleTitleState.PENDING
    migrated_data: dict[str, object] = {}
    for key, value in data.items():
        if key == LEGACY_TITLE_FIELD:
            migrated_data[TITLE_STATE_FIELD] = title_state.value
        else:
            migrated_data[key] = value
    MetadataFile.model_validate(migrated_data)
    return _PendingWrite(
        metadata_path=metadata_path,
        content=_render_metadata(migrated_data, body),
        title_state=title_state,
    )


def migrate_store(
    store_dir: Path,
    apply: bool,
    fs_service: FileSystemService | None = None,
) -> MigrationResult:
    """Plan or apply title states for every bundle in ``store_dir``.

    The full store is validated before any file is written, preventing malformed
    or ambiguous metadata from causing a partial migration.
    """
    config = TranscribeConfig()  # pyright: ignore[reportCallIssue]
    fs_service = fs_service or RealFileSystemService(config)

    pending: list[_PendingWrite] = []
    scanned = 0
    already_current = 0

    for bundle_dir in sorted(fs_service.list_directory(store_dir)):
        if not fs_service.directory_exists(bundle_dir):
            continue
        if bundle_dir.name.startswith(("_", ".")):
            continue

        scanned += 1
        if change := _prepare_bundle(bundle_dir, fs_service):
            pending.append(change)
        else:
            already_current += 1

    for change in pending:
        prefix = "write" if apply else "dry-run"
        print(f"[{prefix}] {change.metadata_path.parent.name}: {TITLE_STATE_FIELD}={change.title_state.value}")
        if apply:
            fs_service.write_file(change.metadata_path, change.content)

    return MigrationResult(
        scanned=scanned,
        migrated=len(pending),
        already_current=already_current,
    )


def main() -> None:
    """Run the bundle title-state migration CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write bundle title states. Without this flag the script is a dry run.",
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
        f"\nScanned {result.scanned} bundle(s); {result.migrated} migration(s), "
        f"{result.already_current} already current ({mode}).",
    )


if __name__ == "__main__":
    main()
