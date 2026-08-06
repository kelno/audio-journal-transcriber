"""Tests for CommandsFile."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tests.fake_file_system import FakeFileSystemService
from transcriber.commands.command import Command
from transcriber.commands.command_interpretation import EmptyCommandArguments, SetTitleCommandArguments
from transcriber.commands.command_resolution import MigratedCommandResolution
from transcriber.exception import InvalidCommandFileException
from transcriber.files.commands_file import CommandsFile


class TestCommandCreation:
    """Test Command dataclass."""

    def test_command_creation_defaults(self) -> None:
        """Test creating a command with defaults."""
        cmd = Command(text="play music")
        assert cmd.text == "play music"
        assert cmd.resolution is None

    def test_command_creation_with_migrated_resolution(self) -> None:
        """A migrated command has an explicit terminal resolution."""
        exec_time = datetime(2026, 5, 29, 10, 30, 0, tzinfo=UTC)
        cmd = Command(
            text="pause",
            resolution=MigratedCommandResolution(resolved_at=exec_time),
        )
        assert cmd.text == "pause"
        assert cmd.is_resolved

    def test_command_to_dict(self) -> None:
        """Test converting command to dictionary."""
        exec_time = datetime(2026, 5, 29, 10, 30, 0, tzinfo=UTC)
        cmd = Command(
            text="stop",
            resolution=MigratedCommandResolution(resolved_at=exec_time),
        )
        cmd_dict = cmd.to_dict()

        assert cmd_dict["text"] == "stop"
        assert cmd_dict["resolution"] == {
            "type": "migrated",
            "resolved_at": "2026-05-29T10:30:00Z",
        }
        assert cmd_dict["arguments"] == {}

    def test_command_from_dict(self) -> None:
        """Test creating command from dictionary."""
        data = {
            "text": "play music",
            "resolution": {
                "type": "migrated",
                "resolved_at": "2026-05-29T10:30:00+00:00",
            },
        }
        cmd = Command.from_dict(data)

        assert cmd.text == "play music"
        assert cmd.resolution == MigratedCommandResolution(
            resolved_at=datetime(2026, 5, 29, 10, 30, 0, tzinfo=UTC),
        )

    def test_command_from_dict_without_resolution(self) -> None:
        """Pending command data without arguments loads with empty arguments."""
        data = {"text": "pause", "resolution": None}
        cmd = Command.from_dict(data)

        assert cmd.text == "pause"
        assert cmd.resolution is None
        assert cmd.arguments == EmptyCommandArguments()

    def test_command_arguments_round_trip(self) -> None:
        """Structured arguments survive command serialization."""
        command = Command(
            text="set the title",
            arguments=SetTitleCommandArguments(title="Planning session"),
        )

        loaded = Command.from_dict(command.to_dict())

        assert isinstance(loaded.arguments, SetTitleCommandArguments)
        assert loaded.arguments.title == "Planning session"

    def test_command_from_dict_invalid_text_type(self) -> None:
        """Test that from_dict raises pydantic ValidationError if text is not a string."""
        data = {
            "text": 123,
            "resolution": None,
        }

        with pytest.raises(ValidationError):
            Command.from_dict(data)

    def test_command_from_dict_rejects_legacy_execution_fields(self) -> None:
        """Old execution fields must pass through the explicit store migration."""
        data = {"text": "play", "executed": True}

        with pytest.raises(ValidationError):
            Command.from_dict(data)


class TestCommandsFileCreation:
    """Test CommandsFile creation."""

    def test_commands_file_from_command_list(self) -> None:
        """Test creating CommandsFile from a list of command strings."""
        cmd_file = CommandsFile.from_command_list(["play music", "pause", "stop"])

        assert len(cmd_file.commands) == 3
        assert cmd_file.commands[0].text == "play music"
        assert cmd_file.commands[1].text == "pause"
        assert cmd_file.commands[2].text == "stop"
        assert all(cmd.resolution is None for cmd in cmd_file.commands)

    def test_commands_file_empty_text(self) -> None:
        """Test creating CommandsFile with empty text."""
        cmd_file = CommandsFile(text="")

        assert len(cmd_file.commands) == 0

    def test_commands_file_get_filename(self) -> None:
        """Test that CommandsFile returns correct filename."""
        cmd_file = CommandsFile(text="")

        assert cmd_file.get_filename() == "_commands.md"


class TestCommandsFileSerialization:
    """Test CommandsFile YAML serialization and deserialization."""

    def test_to_yaml_empty(self) -> None:
        """Test serializing empty commands to YAML."""
        cmd_file = CommandsFile(text="")

        yaml_str = cmd_file.to_yaml()
        assert yaml_str == "--- []\n"

    def test_to_yaml_with_commands(self) -> None:
        """Test serializing commands to YAML."""
        cmd_file = CommandsFile.from_command_list(["play music", "pause"])
        yaml_str = cmd_file.to_yaml()

        assert "text: play music" in yaml_str
        assert "text: pause" in yaml_str
        assert "resolution: null" in yaml_str
        assert "executed" not in yaml_str

    def test_parse_yaml_valid(self) -> None:
        """Test parsing valid YAML into commands."""
        yaml_content = """- text: play music
  resolution: null
- text: pause
  resolution:
    type: migrated
    resolved_at: 2026-05-29T10:30:00+00:00
"""
        cmd_file = CommandsFile(text=yaml_content)

        assert len(cmd_file.commands) == 2
        assert cmd_file.commands[0].text == "play music"
        assert cmd_file.commands[0].resolution is None
        assert cmd_file.commands[1].text == "pause"
        assert cmd_file.commands[1].is_resolved

    def test_parse_yaml_invalid_syntax(self) -> None:
        """Test parsing invalid YAML syntax raises ValueError."""
        invalid_yaml = """- text: play music
  resolution: null
  invalid: [unclosed bracket
"""
        with pytest.raises(yaml.YAMLError):
            CommandsFile(text=invalid_yaml)

    def test_parse_yaml_invalid_structure_missing_text(self) -> None:
        """Test parsing YAML with missing required 'text' field."""
        yaml_content = """- resolution: null
"""
        with pytest.raises(ValidationError):
            CommandsFile(text=yaml_content)

    def test_parse_yaml_invalid_structure_wrong_type(self) -> None:
        """Test parsing YAML where command is not a dict."""
        yaml_content = """- "just a string"
- text: valid command
  resolution: null
"""
        # Only the dict entries should be parsed
        cmd_file = CommandsFile(text=yaml_content)
        assert len(cmd_file.commands) == 1
        assert cmd_file.commands[0].text == "valid command"

    def test_parse_yaml_not_a_list(self) -> None:
        """Test parsing YAML that is not a list."""
        invalid_yaml = """text: play music
resolution: null
"""
        # Should create empty commands list since root is not a list
        with pytest.raises(InvalidCommandFileException, match="YAML list"):
            CommandsFile(text=invalid_yaml)


class TestCommandsFileWriteRead:
    """Test CommandsFile file I/O operations."""

    def test_write_and_read_roundtrip(self, fake_fs: FakeFileSystemService) -> None:
        """Test writing and reading back a CommandsFile."""
        original = CommandsFile.from_command_list(["play music", "pause", "stop"])
        original.commands[0].resolution = MigratedCommandResolution(
            resolved_at=datetime.now(UTC),
        )

        # Write to file
        bundle_dir = Path("/fake/bundle")
        original.write(bundle_dir, fake_fs)

        # Read back
        loaded = CommandsFile.from_file(
            bundle_dir / "_commands.md",
            fake_fs,
        )

        assert len(loaded.commands) == 3
        assert loaded.commands[0].text == "play music"
        assert loaded.commands[0].is_resolved
        assert loaded.commands[1].text == "pause"
        assert loaded.commands[1].resolution is None
        assert loaded.commands[2].text == "stop"
        assert loaded.commands[2].resolution is None

    def test_read_invalid_commands_file(self, fake_fs: FakeFileSystemService) -> None:
        """Test reading an invalid _commands.md file raises ValueError."""
        bundle_dir = Path("/fake/bundle")

        # Write invalid YAML to file
        invalid_yaml = """- text: play music
  resolution:
    type: migrated
    unexpected: true
"""
        fake_fs.write_file(bundle_dir / "_commands.md", invalid_yaml)

        with pytest.raises(ValidationError):
            CommandsFile.from_file(bundle_dir / "_commands.md", fake_fs)

    def test_read_completely_invalid_yaml(self, fake_fs: FakeFileSystemService) -> None:
        """Test reading a completely malformed YAML file."""
        bundle_dir = Path("/fake/bundle")

        # Write completely invalid YAML
        invalid_yaml = """this is: [not: valid: yaml
"""
        fake_fs.write_file(bundle_dir / "_commands.md", invalid_yaml)

        # Should raise
        with pytest.raises(yaml.YAMLError):
            CommandsFile.from_file(bundle_dir / "_commands.md", fake_fs)


class TestCommandsFileNeedsProcessing:
    """Test resolution-based command work detection."""

    def test_empty_file_needs_no_processing(self) -> None:
        """An empty commands file needs no processing."""
        cmd_file = CommandsFile(text="")
        assert cmd_file.has_commands_needing_processing() is False

    def test_unexecuted_command_needs_processing(self) -> None:
        """A fresh, unexecuted command needs processing."""
        cmd_file = CommandsFile.from_command_list(["play music"])
        assert cmd_file.has_commands_needing_processing() is True

    def test_resolved_command_needs_no_processing(self) -> None:
        """A resolved command no longer needs processing."""
        cmd_file = CommandsFile.from_command_list(["play music"])
        cmd_file.commands[0].resolution = MigratedCommandResolution()
        assert cmd_file.has_commands_needing_processing() is False

    def test_mixed_commands(self) -> None:
        """A pending command keeps a mixed file actionable."""
        pending = Command(text="pending")
        done = Command(text="done", resolution=MigratedCommandResolution())
        cmd_file = CommandsFile(text="", commands=[pending, done])
        assert cmd_file.has_commands_needing_processing() is True

    def test_all_done_needs_no_processing(self) -> None:
        """A bundle with only terminal commands needs no processing."""
        done = Command(text="done", resolution=MigratedCommandResolution())
        cmd_file = CommandsFile(text="", commands=[done])
        assert cmd_file.has_commands_needing_processing() is False
