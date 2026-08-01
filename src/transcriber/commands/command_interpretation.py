"""Structured result of interpreting a natural-language command."""

from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from transcriber.commands.command_type import CommandType


class CommandArguments(BaseModel):
    """Typed arguments extracted while interpreting a command."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    title: str | None = None


class CommandInterpretation(BaseModel):
    """A matched command type and the arguments required by its handler."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    command_type: CommandType
    arguments: CommandArguments = Field(default_factory=CommandArguments)
