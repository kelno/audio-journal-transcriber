"""Tests for CommandsFile."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_file_system import FakeFileSystemService
from transcriber.commands.command import Command
from transcriber.commands.command_interpretation import CommandArguments
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_type import CommandType
from transcriber.constants import COMMANDS_FILENAME
from transcriber.exception import InvalidCommandFileException
from transcriber.files.commands_file import CommandsFile


def max_attempts_for(cmd_type: CommandType) -> int:
    """Return the configured max attempts for a command type (mirrors the registry)."""
    return COMMAND_REGISTRY[cmd_type].max_attempts


class TestCommandCreation:
    """Test Command dataclass."""

    def test_command_creation_defaults(self) -> None:
        """Test creating a command with defaults."""
        cmd = Command(text="play music")
        assert cmd.text == "play music"
        assert cmd.executed is False
        assert cmd.executed_at is None

    def test_command_creation_with_execution(self) -> None:
        """Test creating a command with execution state."""
        exec_time = datetime(2026, 5, 29, 10, 30, 0, tzinfo=UTC)
        cmd = Command(text="pause", executed=True, executed_at=exec_time)
        assert cmd.text == "pause"
        assert cmd.executed is True
        assert cmd.executed_at == exec_time

    def test_command_to_dict(self) -> None:
        """Test converting command to dictionary."""
        exec_time = datetime(2026, 5, 29, 10, 30, 0, tzinfo=UTC)
        cmd = Command(text="stop", executed=True, executed_at=exec_time)
        cmd_dict = cmd.to_dict()

        assert cmd_dict["text"] == "stop"
        assert cmd_dict["executed"] is True
        assert cmd_dict["executed_at"] == "2026-05-29T10:30:00+00:00"
        assert cmd_dict["arguments"] == {}

    def test_command_from_dict(self) -> None:
        """Test creating command from dictionary."""
        data = {
            "text": "play music",
            "executed": True,
            "executed_at": "2026-05-29T10:30:00+00:00",
        }
        cmd = Command.from_dict(data)

        assert cmd.text == "play music"
        assert cmd.executed is True
        assert cmd.executed_at == datetime(2026, 5, 29, 10, 30, 0, tzinfo=UTC)

    def test_command_from_dict_without_execution_time(self) -> None:
        """Legacy command data without arguments loads with empty arguments."""
        data = {"text": "pause", "executed": False, "executed_at": None}
        cmd = Command.from_dict(data)

        assert cmd.text == "pause"
        assert cmd.executed is False
        assert cmd.executed_at is None
        assert cmd.arguments == CommandArguments()

    def test_command_arguments_round_trip(self) -> None:
        """Structured arguments survive command serialization."""
        command = Command(
            text="set the title",
            arguments=CommandArguments(title="Planning session"),
        )

        loaded = Command.from_dict(command.to_dict())

        assert loaded.arguments.title == "Planning session"

    def test_command_from_dict_invalid_text_type(self) -> None:
        """Test that from_dict raises pydantic ValidationError if text is not a string."""
        data = {
            "text": 123,
            "executed": False,
            "executed_at": None,
        }

        with pytest.raises(ValidationError):
            Command.from_dict(data)

    def test_command_from_dict_invalid_executed_type(self) -> None:
        """Test that from_dict raises pydantic ValidationError if executed is not a bool."""
        data = {"text": "play", "executed": "yay", "executed_at": None}

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
        assert all(not cmd.executed for cmd in cmd_file.commands)

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
        assert "executed: false" in yaml_str
        assert "executed_at: null" in yaml_str

    def test_parse_yaml_valid(self) -> None:
        """Test parsing valid YAML into commands."""
        yaml_content = """- text: play music
  executed: false
  executed_at: null
- text: pause
  executed: true
  executed_at: 2026-05-29T10:30:00+00:00
"""
        cmd_file = CommandsFile(text=yaml_content)

        assert len(cmd_file.commands) == 2
        assert cmd_file.commands[0].text == "play music"
        assert cmd_file.commands[0].executed is False
        assert cmd_file.commands[1].text == "pause"
        assert cmd_file.commands[1].executed is True

    def test_parse_yaml_invalid_syntax(self) -> None:
        """Test parsing invalid YAML syntax raises ValueError."""
        invalid_yaml = """- text: play music
  executed: false
  invalid: [unclosed bracket
"""
        with pytest.raises(yaml.YAMLError):
            CommandsFile(text=invalid_yaml)

    def test_parse_yaml_invalid_structure_missing_text(self) -> None:
        """Test parsing YAML with missing required 'text' field."""
        yaml_content = """- executed: false
  executed_at: null
"""
        with pytest.raises(ValidationError):
            CommandsFile(text=yaml_content)

    def test_parse_yaml_invalid_structure_wrong_type(self) -> None:
        """Test parsing YAML where command is not a dict."""
        yaml_content = """- "just a string"
- text: valid command
  executed: false
  executed_at: null
"""
        # Only the dict entries should be parsed
        cmd_file = CommandsFile(text=yaml_content)
        assert len(cmd_file.commands) == 1
        assert cmd_file.commands[0].text == "valid command"

    def test_parse_yaml_not_a_list(self) -> None:
        """Test parsing YAML that is not a list."""
        invalid_yaml = """text: play music
executed: false
"""
        # Should create empty commands list since root is not a list
        with pytest.raises(InvalidCommandFileException, match="YAML list"):
            CommandsFile(text=invalid_yaml)


class TestCommandsFileWriteRead:
    """Test CommandsFile file I/O operations."""

    def test_write_and_read_roundtrip(self, fake_fs: FakeFileSystemService) -> None:
        """Test writing and reading back a CommandsFile."""
        original = CommandsFile.from_command_list(["play music", "pause", "stop"])
        original.mark_executed(0, UTC)

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
        assert loaded.commands[0].executed is True
        assert loaded.commands[0].executed_at is not None
        assert loaded.commands[1].text == "pause"
        assert loaded.commands[1].executed is False
        assert loaded.commands[2].text == "stop"
        assert loaded.commands[2].executed is False

    def test_read_invalid_commands_file(self, fake_fs: FakeFileSystemService) -> None:
        """Test reading an invalid _commands.md file raises ValueError."""
        bundle_dir = Path("/fake/bundle")

        # Write invalid YAML to file
        invalid_yaml = """- text: play music
  executed: "not a boolean"
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
    """Test has_commands_needing_processing (executed + retry-budget aware)."""

    def test_empty_file_needs_no_processing(self) -> None:
        """An empty commands file needs no processing."""
        cmd_file = CommandsFile(text="")
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is False

    def test_unexecuted_command_needs_processing(self) -> None:
        """A fresh, unexecuted command needs processing."""
        cmd_file = CommandsFile.from_command_list(["play music"])
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is True

    def test_executed_command_needs_no_processing(self) -> None:
        """An executed command no longer needs processing."""
        cmd_file = CommandsFile.from_command_list(["play music"])
        cmd_file.mark_executed(0, UTC)
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is False

    def test_exhausted_command_needs_no_processing(self) -> None:
        """A command that failed max_attempts times is given up, not re-enqueued."""
        cmd = Command(text="delete", executed=False, matched_type=CommandType.DELETE, attempt_count=2)
        cmd_file = CommandsFile(text="", commands=[cmd])
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is False

    def test_uninterpreted_command_needs_processing(self) -> None:
        """A command without a matched type has not been attempted, so it needs processing."""
        cmd = Command(text="delete", executed=False, matched_type=None, attempt_count=2)
        cmd_file = CommandsFile(text="", commands=[cmd])
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is True

    def test_mixed_commands(self) -> None:
        """Only non-executed, non-exhausted commands count as needing processing."""
        exhausted = Command(text="a", executed=False, matched_type=CommandType.DELETE, attempt_count=2)
        pending = Command(text="b", executed=False, matched_type=CommandType.DELETE, attempt_count=0)
        done = Command(text="c", executed=True, matched_type=CommandType.DELETE, attempt_count=1)
        cmd_file = CommandsFile(text="", commands=[exhausted, pending, done])
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is True

    def test_all_exhausted_or_done_needs_no_processing(self) -> None:
        """A bundle with only exhausted or done commands needs no processing."""
        exhausted = Command(text="a", executed=False, matched_type=CommandType.DELETE, attempt_count=2)
        done = Command(text="c", executed=True, matched_type=CommandType.DELETE, attempt_count=1)
        cmd_file = CommandsFile(text="", commands=[exhausted, done])
        assert cmd_file.has_commands_needing_processing(max_attempts_for) is False


class TestCommandsFileMarkExecuted:
    """Test marking commands as executed."""

    def test_mark_executed_valid_index(self) -> None:
        """Test marking a command as executed by index."""
        cmd_file = CommandsFile.from_command_list(["play music", "pause", "stop"])

        cmd_file.mark_executed(1, UTC)

        assert cmd_file.commands[0].executed is False
        assert cmd_file.commands[1].executed is True
        assert cmd_file.commands[1].executed_at is not None
        assert cmd_file.commands[2].executed is False

    def test_mark_executed_out_of_range_low(self) -> None:
        """Test marking with index too low does nothing."""
        cmd_file = CommandsFile.from_command_list(["play music"])

        cmd_file.mark_executed(-1, UTC)

        assert cmd_file.commands[0].executed is False
        assert cmd_file.commands[0].executed_at is None

    def test_mark_executed_out_of_range_high(self) -> None:
        """Test marking with index too high does nothing."""
        cmd_file = CommandsFile.from_command_list(["play music"])

        cmd_file.mark_executed(5, UTC)

        assert cmd_file.commands[0].executed is False
        assert cmd_file.commands[0].executed_at is None

    def test_mark_executed_sets_timestamp(self) -> None:
        """Test that marking as executed sets the timestamp."""
        cmd_file = CommandsFile.from_command_list(["play music"])
        before = datetime.now(UTC)

        cmd_file.mark_executed(0, UTC)

        after = datetime.now(UTC)
        assert cmd_file.commands[0].executed_at is not None
        assert before <= cmd_file.commands[0].executed_at <= after

    def test_set_last_error_writes_to_commands_file(
        self,
        transcribe_bundle_factory: TranscribeBundleFactory,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Verify that `set_last_error` records error text in the commands file."""
        bundle_name = "2025-01-15_set_last_error"

        # Create bundle with a single command and write initial files
        bundle = transcribe_bundle_factory(
            bundle_name=bundle_name,
            audio_filename="audio.mp3",
            commands=["do something"],
        )
        assert bundle.commands
        cmd = bundle.commands.commands[0]

        # Set a debug error for the command
        error_msg = "something failed during execution"
        bundle.set_last_error(cmd.id, error_msg)

        # Read commands file and assert the last_error was recorded
        commands_file_path = bundle.get_bundle_dir() / COMMANDS_FILENAME
        commands_file = CommandsFile.from_file(commands_file_path, fake_fs)

        assert commands_file.commands, "Commands file should contain at least one command"
        assert commands_file.commands[0].last_error == error_msg
