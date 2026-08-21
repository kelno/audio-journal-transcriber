# Voice commands

Voice commands let a recording change how its bundle is processed. They are spoken inside explicit boundary phrases, transcribed with the rest of the audio, and interpreted by the configured text model.

## Command boundaries

A command begins after a phrase equivalent to `start command` and finishes before a phrase equivalent to `stop command` or `validate command`.

For example:

```text
Start command, merge this with the previous recording, stop command.
```

Boundary phrases can be spoken in other languages. The command extractor recognizes equivalent phrases such as `début commande` and `fin commande`; the text between the boundaries remains in the language in which it was spoken.

A cancellation boundary such as `cancel command` discards the pending command. Nested starts, unmatched boundaries, and passages containing multiple unrelated instructions are also discarded.

## Supported commands

### Merge with the previous recording

Example:

```text
Start command, merge this with the previous recording, stop command.
```

The current bundle is merged into the most recent earlier bundle within `general.merge_max_hours`. Audio, transcripts, command state, and recording-specific context are combined. The combined summary is regenerated because a summary of either original recording would be stale.

Additional files and directories stored directly inside the current bundle are also moved into the target bundle.

The target title can be replaced after the combined summary is generated, unless it's marked as a "manual" title.

If a merge fails while processing, `_merge_failed.md` markers block automatic retries. Inspect the affected bundles, correct any inconsistency, and remove the failure marker file to allow retries.

### Delete the current recording

Example:

```text
Start command, delete this recording, stop command.
```

The current bundle is removed from active processing. With `general.safe_delete = true`, the bundle is moved under `<store_dir>/_deleted` so that it can be recovered manually. With safe deletion disabled, it is deleted from the filesystem.

### Set a manual title

Example:

```text
Start command, set the title to quarterly planning, stop command.
```

The requested title replaces the descriptive part of the bundle directory name while preserving its date prefix. Manual titles survive automatic summary regeneration and later processing runs.

Titles are normalized for NTFS and ext4 compatibility, whitespace is collapsed, and titles longer than 60 characters are shortened at a word boundary when possible.

## Processing and retries

Extracted commands and their execution state are stored in `_commands.md`. This prevents completed commands from running again when an existing bundle is revisited.

Temporary interpretation failures use the daemon's retry delay. Successfully interpreted but unsupported commands receive a terminal `rejected` resolution, while action execution outcomes are recorded on their durable requests. The underscore-prefixed command file is application state and should normally not be edited manually.
