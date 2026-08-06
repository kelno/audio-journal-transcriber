# Audio Journal Transcriber

Turn personal voice recordings into organized Markdown records.

Audio Journal Transcriber is a self-hosted, filesystem-first pipeline. It watches an input directory, creates a dated bundle for each recording, and transcribes the audio through an OpenAI-compatible transcription API. It can also use an OpenAI-compatible chat API to detect spoken commands and optionally generate structured summaries and readable titles.

A bundle is a self-contained directory that keeps a recording together with its transcript, optional summary and user-provided context, and the state needed to process it over time. Bundles can be browsed with any tool that reads Markdown; storing them in an Obsidian vault is one convenient option.

## How it works

1. The transcriber scans an input directory recursively for recognized recording files.
2. Each new recording is moved into a dated bundle in the managed store directory.
3. The audio is sent to the configured transcription API and saved as `transcript.md`.
4. The transcript is inspected for spoken commands, such as merging with the previous recording or assigning a title.
5. When summaries are enabled, a chat model creates `summary.md` and a short directory title.
6. In daemon mode, the input directory remains watched and failed work is retried automatically.

## Features

- Run once or continuously as a filesystem-watching daemon.
- Discover `.mp3`, `.wav`, `.m4a`, `.flac`, `.ogg`, `.aac`, `.mkv`, and `.mp4` recordings. Processing also depends on FFmpeg being able to inspect the file and the configured transcription service accepting its format.
- Use separately configurable OpenAI-compatible APIs and models for transcription and text processing.
- Produce plain Markdown transcripts and structured summaries with topics, a narrative summary, and action items.
- Keep the recording and its generated files together in a persistent bundle, with metadata and command state for later processing.
- Generate chronological, readable directory names from the recording date and summary.
- Optionally add your own recording-specific context in `custom_context.md`; changing that context triggers summary regeneration.
- Use spoken commands to merge recordings, delete a recording, or assign a persistent manual title.
- Resume incomplete bundles and retry failures with increasing delays in daemon mode.
- Filter recordings by duration and optionally remove processed source audio after a retention period.
- Preview processing without modifying bundles with `--dry-run`.
- Move user-deleted bundles to a recoverable `_deleted` directory with safe deletion.

## Requirements

- Python 3.13 or newer.
- [uv](https://docs.astral.sh/uv/) for dependency and environment management.
- [FFmpeg](https://ffmpeg.org/) available on `PATH`.

## Quick start

Install the project dependencies from the repository root:

```bash
uv sync
```

Copy the example configuration to the working directory:

```bash
cp docs/config.custom.toml.example config.custom.toml
```

Edit `config.custom.toml` with your directories, API endpoints, credentials, and model names. Ensure that the configured input directory already exists, then process all pending recordings:

```bash
uv run transcriber
```

See [Configuration](configuration.md) for every setting and environment-variable equivalents.

## Bundle output

A processed recording produces a directory similar to:

```text
store/
└── 2026-08-01 14.30 Unexpected fox encounter/
    ├── Recording 20260801143000.m4a
    ├── transcript.md
    ├── summary.md
    ├── custom_context.md
    ├── _commands.md
    └── _metadata.md
```

`transcript.md`, `summary.md`, and `custom_context.md` are intended to remain readable and editable. Files prefixed with `_` hold application state and should normally be left to the transcriber.

`custom_context.md` can be used to provide additional context for this bundle to the summarizer. Changing its content causes the summary to be regenerated on the next processing run.

### Recording dates

The transcriber recognizes unambiguous, year-first dates and timestamps anywhere in
a filename. Dates may be compact or use `-`, `_`, or `.` separators. Times may be
compact or use `:`, `-`, `_`, or `.` separators, and may include seconds.

```text
Recording 20260801143000.m4a
Recording_20260801_143000.m4a
2026-08-01_recording.m4a
meeting_2026-08-01_14-30-00.m4a
Voice 002_W_20230102_213412.m4a
2026-08-01T14:30:00Z_interview.m4a
GMT20260801-143000_call.mp3
```

Supported timestamps always put the year first. Date-only values are interpreted at
midnight in the configured time zone. A timestamp without an explicit time zone uses
the configured time zone; `Z` and `GMT` explicitly indicate UTC. Ambiguous
locale-dependent dates such as `08-01-2026` are intentionally not inferred.

If the filename contains conflicting timestamps, no valid timestamp, or no supported
format, the file modification time is used.

The generated bundle directory name combines the recording date with a short summary-derived or user-provided title.

## CLI usage

Show all command-line options:

```bash
uv run transcriber --help
```

Common modes:

```bash
# Process pending work and exit
uv run transcriber

# Continue watching the input directory
uv run transcriber --daemon

# Show intended work without modifying bundle files
uv run transcriber --dry-run
```

## Guides

- [Configuration](configuration.md): TOML files, environment variables, and setting reference.
- [HTTP action requests](http-api.md): Submit durable actions and inspect their status locally.
- [Voice commands](voice-commands.md): Command boundaries and supported recording operations.
- [Glossary](glossary.md): Definitions for the processing and action-request model.
- [Container deployment](deployment.md): Docker and Podman builds and runtime configuration.
- [Example phone sync setup](phone-sync-workflow.md): One way to send recordings automatically from a phone to the daemon.
- [Maintenance utilities](maintenance.md): Tools for inspecting and maintaining the bundle store.

## Development

The current action-request design and its remaining decisions are documented in
[Action-request architecture](action-request-architecture.md).

Run the full test suite from the repository root:

```bash
uv run pytest
```

Run a single test file:

```bash
uv run pytest tests/test_transcribe_bundle.py
```
