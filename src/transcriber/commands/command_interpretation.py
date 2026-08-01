"""Structured result of interpreting a natural-language command."""

from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from transcriber.commands.command_type import CommandType


class StrictCommandModel(BaseModel):
    """Base model that rejects fields outside a command's declared schema."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class EmptyCommandArguments(StrictCommandModel):
    """Arguments for commands that do not accept structured values."""


class SetTitleCommandArguments(StrictCommandModel):
    """Arguments extracted for a SET_TITLE command."""

    title: str

    @field_validator("title")
    @classmethod
    def validate_title(cls, title: str) -> str:
        """Reject titles containing only whitespace while preserving user text."""
        if not title.strip():
            msg = "SET_TITLE requires a non-empty arguments.title string"
            raise ValueError(msg)
        return title


type CommandArguments = EmptyCommandArguments | SetTitleCommandArguments
type ArgumentlessCommandType = Literal[
    CommandType.MERGE,
    CommandType.DELETE,
    CommandType.UNKNOWN,
    CommandType.IGNORE,
]


class ArgumentlessCommandInterpretation(StrictCommandModel):
    """Interpretation of a command that accepts no structured arguments."""

    command_type: ArgumentlessCommandType
    arguments: EmptyCommandArguments = Field(default_factory=EmptyCommandArguments)


class SetTitleCommandInterpretation(StrictCommandModel):
    """Interpretation of a command that supplies a manual recording title."""

    command_type: Literal[CommandType.SET_TITLE]
    arguments: SetTitleCommandArguments


type CommandInterpretation = Annotated[
    ArgumentlessCommandInterpretation | SetTitleCommandInterpretation,
    Field(discriminator="command_type"),
]

_COMMAND_INTERPRETATION_ADAPTER = TypeAdapter(CommandInterpretation)


def parse_command_interpretation(value: object) -> CommandInterpretation:
    """Parse a structured response using its command type as discriminator."""
    # Pydantic selects a discriminated-union branch before coercing field
    # values, so normalize the JSON string to the enum used by our Literal tags.
    if isinstance(value, dict) and isinstance(command_type := value.get("command_type"), str):
        try:
            normalized_command_type = CommandType(command_type)
        except ValueError:
            pass
        else:
            value = {**value, "command_type": normalized_command_type}

    return _COMMAND_INTERPRETATION_ADAPTER.validate_python(value)
