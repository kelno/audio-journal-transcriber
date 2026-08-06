# Configuration

Audio Journal Transcriber reads its settings from TOML files and environment variables. Configuration is loaded with the following priority, from highest to lowest:

1. Environment variables prefixed with `TRANSCRIBER_`.
2. `config.custom.toml` in the process working directory.
3. The packaged `config.default.toml`.

Only overrides belong in `config.custom.toml`; defaults should remain in the packaged file. Use [`config.custom.toml.example`](config.custom.toml.example) as a starting point. From the repository root:

```bash
cp docs/config.custom.toml.example config.custom.toml
```

The input directory must exist before the transcriber starts. The store directory is created when necessary.

Both service URLs are normalized to include a trailing slash. The audio service must expose an OpenAI-compatible `audio/transcriptions` endpoint. The text service must expose an OpenAI-compatible chat-completions endpoint.

## General settings

| Setting                          | Packaged default  | Description                                                                                                  |
| -------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------ |
| `input_dir`                      | `/data/input`     | Directory tree scanned for new recordings. It must already exist.                                            |
| `store_dir`                      | `/data/store`     | Managed directory containing persistent bundles.                                                             |
| `min_length_seconds`             | `10.0`            | Reject recordings shorter than this duration. Use `0` to disable the check.                                  |
| `remove_short_files`             | `true`            | Remove recordings rejected by the minimum-duration check.                                                    |
| `delete_source_audio_after_days` | `0`               | Remove audio from successfully transcribed bundles after this many days. Use `0` to retain it.               |
| `merge_max_hours`                | `12.0`            | Maximum age gap allowed when a merge command selects the previous bundle. Use `0` to disable merging.        |
| `timezone`                       | `Europe/Brussels` | IANA timezone used when interpreting recording dates.                                                        |
| `safe_delete`                    | `true`            | Move deleted bundle content under `<store_dir>/_deleted` where supported instead of deleting it immediately. |

Retention cleanup removes eligible source audio files after a transcript exists. It is separate from deleting an entire bundle with a voice command.

## Text settings

| Setting           | Packaged default | Description                                                                            |
| ----------------- | ---------------- | -------------------------------------------------------------------------------------- |
| `summary_enabled` | `true`           | Generate summaries and automatic bundle titles.                                        |
| `api_base_url`    | Required         | Base URL of the OpenAI-compatible text API.                                            |
| `api_key`         | Required         | Bearer token passed to the text API. Use a dummy value only if the service accepts it. |
| `model`           | Required         | Chat model used for summaries, titles, and voice-command interpretation.               |
| `extra_context`   | None             | Optional context applied to every summary.                                             |

The text connection values are currently required by the configuration model even when summary generation is disabled, because the same client interprets voice commands.

## Audio settings

| Setting        | Packaged default | Description                                          |
| -------------- | ---------------- | ---------------------------------------------------- |
| `api_base_url` | Required         | Base URL of the OpenAI-compatible transcription API. |
| `api_key`      | Required         | Bearer token passed to the transcription API.        |
| `model`        | Required         | Model sent to the transcription endpoint.            |
| `stream`       | Required         | Ask the service for streaming transcription output.  |

## HTTP settings

| Setting   | Packaged default | Description                                                                                  |
| --------- | ---------------- | -------------------------------------------------------------------------------------------- |
| `enabled` | `false`          | Expose the action-request API while running in daemon mode.                                  |
| `host`    | `127.0.0.1`      | Listening host. Only `127.0.0.1` is accepted until authentication is implemented.             |
| `port`    | `8765`           | Listening TCP port.                                                                          |

The HTTP API accepts durable actions and reports their status; it does not execute bundle
mutations in the HTTP server thread. See [HTTP action requests](http-api.md) for its endpoints
and payloads.

## Environment variables

Environment-variable names use `__` between nested sections and keys:

```bash
TRANSCRIBER_GENERAL__INPUT_DIR=/path/to/audio-inbox
TRANSCRIBER_GENERAL__STORE_DIR=/path/to/audio-journal
TRANSCRIBER_GENERAL__DELETE_SOURCE_AUDIO_AFTER_DAYS=30
TRANSCRIBER_HTTP__ENABLED=true
TRANSCRIBER_HTTP__HOST=127.0.0.1
TRANSCRIBER_HTTP__PORT=8765
TRANSCRIBER_TEXT__SUMMARY_ENABLED=true
TRANSCRIBER_TEXT__API_BASE_URL=https://your-chat-service.example/v1
TRANSCRIBER_TEXT__API_KEY=replace-me
TRANSCRIBER_TEXT__MODEL=your-chat-model
TRANSCRIBER_AUDIO__API_BASE_URL=https://your-transcription-service.example/v1
TRANSCRIBER_AUDIO__API_KEY=replace-me
TRANSCRIBER_AUDIO__MODEL=your-transcription-model
TRANSCRIBER_AUDIO__STREAM=true
```

Environment variables override the matching TOML values, so the two mechanisms can be combined. This is useful for mounting a non-secret configuration file into a container while injecting credentials through its environment.
