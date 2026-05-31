"""Command interpreter for processing transcription commands.

Matches user commands to predefined command types using LLM-assisted matching.
"""

from transcriber.ai_manager import AIManager
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_type import CommandType
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
        f"- {meta.command_type.value.upper()}: {meta.description} (aliases: {', '.join(meta.aliases or [])})"
        for meta in COMMAND_REGISTRY.values()
    )

    prompt = f"""
You are a command matcher in an automated pipeline. Your task is to match user commands to predefined command types.

**Available Commands:**
{command_descriptions}

**Matching Instructions:**
1. Only match a command if you are VERY CONFIDENT it maps to one of the available commands.
2. Consider both the exact wording and semantic meaning.
3. The commands can be given in any language, not only english.
4. If the command is ambiguous or doesn't clearly match any known command, respond with exactly: "UNKNOWN"
5. Your response must be ONLY the command type in uppercase (e.g., "MERGE", "DELETE", or "UNKNOWN") with no other text.

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


def extract_commands(text: str, bundle_name: str, ai_manager: AIManager) -> list[str]:
    """Extract commands from text.

    Delegates to the injected text_client instead of doing HTTP directly.
    """
    logger.debug(f"AIManager Extracting commands for {bundle_name}")
    commands: list[str] = []
    prompt = f"""
    You are part of an automated pipeline that transcribes personal audio recordings and summarizes them.
    Your task: You act as a function to extract vocal commands given by the user in the following transcripts.
    Return format:
        - One command per line, separated by unix newlines
        - If no commands are found, return the exact 4 characters string "none"
        - Do not include any other text or delimiters or special formatting in the response
    Vocal commands are defined as follows:
        - It starts when the user says "start command"
        - It ends with "stop command" or "validate command"
        - A user can also say "cancel command", in this case just ignore the command
        - Words can be translated + or be out of order. "fin commande" is valid, as well as "command stop"
        - The command is whatever the user says between those two, without extra processing
        - If the command is more than one sentence, something is wrong in the transcript and the command should be skipped.


    Transcript: {text}
    """
    response = ai_manager.query_chat_completion(prompt)
    lines = response.splitlines()
    if len(lines) == 1 and lines[0] == "none":
        return []

    commands = [line.strip() for line in lines if line.strip() != ""]

    return commands
