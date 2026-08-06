"""Migrate bundle command files to resolution-only action-request state.

The migration scans and validates the complete store before writing anything.
It is a dry run by default; pass ``--apply`` after reviewing the report.

Changed command files are copied below
``<store>/_migration_backups/action-request-commands-v1`` before replacement.
The transcriber must be stopped while applying the migration.

Usage:
    uv run python scripts/migrate_action_request_commands.py
    uv run python scripts/migrate_action_request_commands.py --apply
    uv run python scripts/migrate_action_request_commands.py --store-dir D:/recordings/store
"""

# pyright: reportExplicitAny=false, reportAny=false

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast
from uuid import uuid4

import yaml
from pydantic import ValidationError

from transcriber.commands.command import Command
from transcriber.config import TranscribeConfig
from transcriber.constants import COMMANDS_FILENAME
from transcriber.files.commands_file import CommandsFile

BACKUP_DIRECTORY: Final = Path("_migration_backups") / "action-request-commands-v1"

_CANONICAL_FIELDS: Final = frozenset(
    {
        "id",
        "text",
        "matched_type",
        "arguments",
        "resolution",
    },
)
_LEGACY_FIELDS: Final = frozenset(
    {
        "executed",
        "executed_at",
        "attempt_count",
        "last_error",
    },
)
_KNOWN_FIELDS: Final = _CANONICAL_FIELDS | _LEGACY_FIELDS
_MISSING: Final = object()


@dataclass(frozen=True)
class CommandMigrationCounts:
    """Command-level changes planned for one file."""

    command_count: int = 0
    migrated_resolutions: int = 0
    generated_ids: int = 0
    removed_legacy_fields: int = 0

    def __add__(self, other: CommandMigrationCounts) -> CommandMigrationCounts:
        """Combine counts from independently migrated commands or files."""
        return CommandMigrationCounts(
            command_count=self.command_count + other.command_count,
            migrated_resolutions=self.migrated_resolutions + other.migrated_resolutions,
            generated_ids=self.generated_ids + other.generated_ids,
            removed_legacy_fields=self.removed_legacy_fields + other.removed_legacy_fields,
        )


@dataclass(frozen=True)
class PlannedCommandFileMigration:
    """Fully validated replacement for one command file."""

    source: Path
    backup: Path
    original_text: str
    migrated_text: str
    counts: CommandMigrationCounts

    @property
    def needs_write(self) -> bool:
        """Return whether canonical serialization differs from the source file."""
        return self.original_text != self.migrated_text


@dataclass(frozen=True)
class CommandStoreMigrationPlan:
    """Preflighted migration plan for a complete bundle store."""

    store_dir: Path
    files: tuple[PlannedCommandFileMigration, ...]

    @property
    def totals(self) -> CommandMigrationCounts:
        """Combine command-level counts from every scanned file."""
        total = CommandMigrationCounts()
        for file in self.files:
            total += file.counts
        return total

    @property
    def changed_file_count(self) -> int:
        """Return how many command files need canonical rewriting."""
        return sum(file.needs_write for file in self.files)


def _load_legacy_commands(command_file: Path, text: str) -> list[dict[str, Any]]:
    """Parse supported historical command-file shapes without using the strict model."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as error:
        msg = f"Invalid YAML in {command_file}: {error}"
        raise ValueError(msg) from error

    if loaded is None:
        return []
    if isinstance(loaded, str):
        return [{"text": line} for line in text.splitlines() if line.strip()]
    if not isinstance(loaded, list):
        msg = f"Expected a YAML list in {command_file}, got {type(loaded).__name__}"
        raise TypeError(msg)

    commands: list[dict[str, Any]] = []
    for index, item in enumerate(cast("list[object]", loaded), start=1):
        if not isinstance(item, dict):
            msg = f"Command {index} in {command_file} is not a mapping"
            raise TypeError(msg)
        if not all(isinstance(key, str) for key in item):
            msg = f"Command {index} in {command_file} contains a non-string field name"
            raise TypeError(msg)
        commands.append(cast("dict[str, Any]", item))
    return commands


def _migrate_command(
    raw: dict[str, Any],
    *,
    command_file: Path,
    index: int,
) -> tuple[Command, CommandMigrationCounts]:
    """Convert one historical command mapping into the strict current model."""
    unexpected_fields = set(raw) - _KNOWN_FIELDS
    if unexpected_fields:
        fields = ", ".join(sorted(str(field) for field in unexpected_fields))
        msg = f"Command {index} in {command_file} has unknown fields: {fields}"
        raise ValueError(msg)

    generated_id = "id" not in raw or raw["id"] in {None, ""}
    canonical: dict[str, Any] = {
        field: raw[field]
        for field in _CANONICAL_FIELDS
        if field in raw
    }
    if generated_id:
        canonical["id"] = uuid4().hex

    executed = raw.get("executed", _MISSING)
    if executed is not _MISSING and not isinstance(executed, bool):
        msg = f"Command {index} in {command_file} has a non-boolean executed field"
        raise ValueError(msg)

    resolution = raw.get("resolution")
    legacy_typed_resolution = (
        isinstance(resolution, dict)
        and resolution.get("type") == "legacy_executed"
    )
    migrated_resolution = executed is True and resolution is None
    if legacy_typed_resolution:
        canonical["resolution"] = {
            **resolution,
            "type": "migrated",
        }
    elif migrated_resolution:
        canonical["resolution"] = {
            "type": "migrated",
            "resolved_at": raw.get("executed_at"),
        }
    elif executed in {False, _MISSING} and raw.get("executed_at") is not None:
        msg = f"Command {index} in {command_file} has executed_at without completed execution"
        raise ValueError(msg)

    try:
        command = Command.model_validate(canonical)
    except ValidationError as error:
        msg = f"Command {index} in {command_file} cannot be migrated: {error}"
        raise ValueError(msg) from error

    if executed is not _MISSING and resolution is not None and executed != command.is_resolved:
        msg = f"Command {index} in {command_file} disagrees between executed and resolution"
        raise ValueError(msg)

    return command, CommandMigrationCounts(
        command_count=1,
        migrated_resolutions=int(migrated_resolution or legacy_typed_resolution),
        generated_ids=int(generated_id),
        removed_legacy_fields=sum(field in raw for field in _LEGACY_FIELDS),
    )


def _command_file_paths(store_dir: Path) -> list[Path]:
    """Return command files from active and recoverable deleted bundles."""
    active_files = [
        command_file
        for bundle_dir in sorted(store_dir.iterdir(), key=lambda path: path.name.casefold())
        if bundle_dir.is_dir() and not bundle_dir.name.startswith(("_", "."))
        if (command_file := bundle_dir / COMMANDS_FILENAME).is_file()
    ]
    deleted_dir = store_dir / "_deleted"
    deleted_files = list(deleted_dir.rglob(COMMANDS_FILENAME)) if deleted_dir.is_dir() else []
    return sorted(
        [*active_files, *deleted_files],
        key=lambda path: path.relative_to(store_dir).as_posix().casefold(),
    )


def plan_command_store_migration(store_dir: Path) -> CommandStoreMigrationPlan:
    """Preflight every command file and return an all-or-nothing write plan."""
    if not store_dir.is_dir():
        msg = f"Store directory does not exist: {store_dir}"
        raise FileNotFoundError(msg)

    planned_files: list[PlannedCommandFileMigration] = []
    active_command_locations: dict[str, Path] = {}
    for command_file in _command_file_paths(store_dir):
        original_text = command_file.read_text(encoding="utf-8")
        raw_commands = _load_legacy_commands(command_file, original_text)
        commands: list[Command] = []
        file_command_ids: set[str] = set()
        counts = CommandMigrationCounts()
        for index, raw in enumerate(raw_commands, start=1):
            command, command_counts = _migrate_command(
                raw,
                command_file=command_file,
                index=index,
            )
            if command.id in file_command_ids:
                msg = f"Duplicate command ID {command.id} within {command_file}"
                raise ValueError(msg)
            file_command_ids.add(command.id)

            relative_path = command_file.relative_to(store_dir)
            is_active_bundle = relative_path.parts[0] != "_deleted"
            if is_active_bundle and (previous_file := active_command_locations.get(command.id)):
                msg = f"Duplicate command ID {command.id} in {previous_file} and {command_file}"
                raise ValueError(msg)
            if is_active_bundle:
                active_command_locations[command.id] = command_file
            commands.append(command)
            counts += command_counts

        migrated_text = CommandsFile(text="", commands=commands).to_yaml()
        relative_path = command_file.relative_to(store_dir)
        planned_files.append(
            PlannedCommandFileMigration(
                source=command_file,
                backup=store_dir / BACKUP_DIRECTORY / relative_path,
                original_text=original_text,
                migrated_text=migrated_text,
                counts=counts,
            ),
        )

    return CommandStoreMigrationPlan(store_dir=store_dir, files=tuple(planned_files))


def apply_command_store_migration(plan: CommandStoreMigrationPlan) -> None:
    """Back up and replace every changed file from a preflighted plan."""
    for file in plan.files:
        if not file.needs_write:
            continue
        if file.source.read_text(encoding="utf-8") != file.original_text:
            msg = f"Command file changed after preflight: {file.source}"
            raise RuntimeError(msg)

        file.backup.parent.mkdir(parents=True, exist_ok=True)
        if file.backup.exists():
            if file.backup.read_text(encoding="utf-8") != file.original_text:
                msg = f"Migration backup already exists with different content: {file.backup}"
                raise FileExistsError(msg)
        else:
            file.backup.write_text(file.original_text, encoding="utf-8")

        file.source.write_text(file.migrated_text, encoding="utf-8")


def _print_plan(plan: CommandStoreMigrationPlan, *, apply: bool) -> None:
    """Print changed paths and an aggregate migration summary."""
    prefix = "rewrite" if apply else "dry-run"
    for file in plan.files:
        if file.needs_write:
            print(f"[{prefix}] {file.source.relative_to(plan.store_dir)}")

    totals = plan.totals
    print(
        f"\nScanned {len(plan.files)} command file(s), {totals.command_count} command(s); "
        f"{plan.changed_file_count} file(s) need rewriting.",
    )
    print(
        f"Terminal migrated resolutions: {totals.migrated_resolutions}; "
        f"generated stable IDs: {totals.generated_ids}; "
        f"legacy fields removed: {totals.removed_legacy_fields}.",
    )
    if not apply and plan.changed_file_count:
        print("Dry run only. Stop the transcriber and pass --apply to write these changes.")


def main() -> None:
    """Run the one-time command-store migration."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the preflighted migration. The default is a read-only dry run.",
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
    plan = plan_command_store_migration(store_dir)
    _print_plan(plan, apply=args.apply)
    if args.apply:
        apply_command_store_migration(plan)
        print(f"Backups are stored under {store_dir / BACKUP_DIRECTORY}.")


if __name__ == "__main__":
    main()
