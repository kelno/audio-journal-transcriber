"""Command interpreter for processing transcription commands.

Matches user commands to predefined command types using LLM-assisted matching.
"""

from transcriber.ai_manager import AIManager
from transcriber.commands.command_type import COMMAND_REGISTRY, CommandType
from transcriber.logger import logger


def interpret_command(command_string: str, ai_manager: AIManager) -> CommandType:
    """Interpret a user command and match it to a known command type.

    Uses the LLM to semantically match the command string to one of the
    registered command types. Only matches with high confidence; returns
    UNKNOWN if no clear match is found.

    Args:
        command_string: The raw command text to interpret.
        ai_manager: AIManager instance for querying the LLM.

    Returns:
        The matched CommandType, or CommandType.UNKNOWN if no confident match.

    Raises:
        ValueError: When the LLM returns an invalid or unparseable response.

    """
    logger.debug(f"Interpreting command: {command_string}")

    # Build the command registry description for the prompt
    command_descriptions = "\n".join(
        f"- {meta.command_type.value.upper()}: {meta.description} (aliases: {', '.join(meta.aliases or [])})" for meta in COMMAND_REGISTRY.values()
    )

    prompt = f"""
You are a command matcher in an automated pipeline. Your task is to match user commands to predefined command types.

**Available Commands:**
{command_descriptions}

**Matching Instructions:**
1. Only match a command if you are VERY CONFIDENT it maps to one of the available commands.
2. Consider both the exact wording and semantic meaning.
3. If the command is ambiguous or doesn't clearly match any known command, respond with exactly: "UNKNOWN"
4. Your response must be ONLY the command type in uppercase (e.g., "MERGE", "DELETE", or "UNKNOWN") with no other text.

**User Command:**
{command_string}

**Your Response:**"""

    response = ai_manager.query_chat_completion(prompt).strip().upper()

    # Try to match the response to a CommandType
    for cmd_type in CommandType:
        if response == cmd_type.value.upper():
            return cmd_type

    # If no match and response is "UNKNOWN", return UNKNOWN
    if response == "UNKNOWN":
        return CommandType.UNKNOWN

    # Invalid response
    msg = f"LLM returned unexpected response: {response}"
    logger.error(msg)
    raise ValueError(msg)
