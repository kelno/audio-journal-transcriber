"""Migrate existing bundle commands to add stable IDs.

Commands now have a stable `id` field for unambiguous identification.
This script adds IDs to existing commands that don't have them.

Usage:
    uv run python scripts/migrate_command_ids.py            # dry run
    uv run python scripts/migrate_command_ids.py --apply    # rewrite files
"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

import yaml

from transcriber.config import TranscribeConfig
from transcriber.constants import COMMANDS_FILENAME
from transcriber.files.commands_file import CommandsFile
from transcriber.files.file_system import RealFileSystemService


def _needs_migration(raw_text: str) -> bool:
    """Check if any command in the file is missing the id field.

    The command file is a YAML document (with an explicit ``---`` start marker)
    whose top-level value is a list of command mappings. We parse the raw text
    directly with ``yaml.safe_load`` (which handles the document marker) and
    inspect the parsed structure, rather than relying on in-memory objects that
    already have auto-generated ids from the ``default_factory``.
    """
    try:
        data = yaml.safe_load(raw_text)
    except Exception:
        return False
    if not isinstance(data, list):
        return False
    return any("id" not in cmd for cmd in data if isinstance(cmd, dict))


def migrate_store(store_dir: Path, apply: bool) -> tuple[int, int]:
    """Rewrite command files in `store_dir` to add IDs.

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

        cmd_path = bundle_dir / COMMANDS_FILENAME
        if not cmd_path.is_file():
            continue

        scanned += 1
        raw_text = fs_service.read_file(cmd_path)
        if not _needs_migration(raw_text):
            continue

        rewritten += 1
        commands = CommandsFile.from_file(cmd_path, fs_service)
        # Add IDs to commands that don't have them (they get auto-generated on load,
        # but we want to persist them)
        for cmd in commands.commands:
            if not cmd.id:
                cmd.id = uuid4().hex

        if apply:
            commands.write(bundle_dir, fs_service)
            print(f"[rewrite] {bundle_dir.name}: added IDs to {len(commands.commands)} command(s)")
        else:
            print(f"[dry-run] {bundle_dir.name}: would add IDs to {len(commands.commands)} command(s)")

    return scanned, rewritten


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually rewrite command files. Without this flag the script is a dry run.",
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
