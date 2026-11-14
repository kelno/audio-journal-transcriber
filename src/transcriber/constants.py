TRANSCRIPT_FILENAME = "transcript.md"
SUMMARY_FILENAME = "summary.md"
COMMANDS_FILENAME = "_commands.md"
METADATA_FILENAME = "_metadata.md"
MERGE_FAILED_FILENAME = "_merge_failed.md"
DELETED_DIR_NAME = "_deleted"

# Written by the transcriber-archive-audio command (archive_source_audio.py) when it moves a bundle's audio to an
# archive outside the store: where the audio went and how to check it. It is not a
# managed file, so a merge carries it along like a user-added attachment, under a
# collision-safe name (_archived_audio_1.md) when the target already has one.
ARCHIVED_AUDIO_FILENAME = "_archived_audio.md"

# Per-bundle optional context the user can write to help the summary process.
# Lines starting with the markdown comment marker below are ignored when the
# effective context is computed, so the pregenerated header never triggers a
# summary regeneration.
CUSTOM_CONTEXT_FILENAME = "custom_context.md"

# Files managed by Transcriber. Bundle merges handle each file according to
# its specific purpose instead of moving it as a user-added attachment.
MANAGED_BUNDLE_FILENAMES = frozenset(
    {
        TRANSCRIPT_FILENAME,
        SUMMARY_FILENAME,
        COMMANDS_FILENAME,
        METADATA_FILENAME,
        MERGE_FAILED_FILENAME,
        CUSTOM_CONTEXT_FILENAME,
    },
)

DEFAULT_CUSTOM_CONTEXT_CONTENT = (
    "[//]: # Optional context to help the summary. Lines starting with [//]: are ignored.\n"
    "[//]: # Changes to this file will trigger a new summary and bundle name generation.\n"
    "[//]: # Example: this voice memo is a draft message to my friend Alice.\n"
)

MULTIPLE_TRANSCRIPTS_SEPARATOR = "\n\n---\n(next transcripted audio follows)\n\n"
