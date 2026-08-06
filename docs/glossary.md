# Transcriber glossary

This glossary defines the terms used by the transcription, command, and action-request architecture.

## Action

An immutable description of a user-requested state change.

Examples are merging two bundles, deleting a bundle, or setting a manual title. An action describes intent only; it has no lifecycle fields and does not execute itself.

## Action effect

The bundle IDs whose derived work must be reconsidered after an action runs.

- `changed_bundle_ids` identifies bundles that still exist but may need fresh jobs.
- `removed_bundle_ids` identifies bundles whose queued jobs are now invalid.

Effects let the main processing loop handle results without knowing individual action types.

## Action executor

The component that applies one action to the currently loaded bundles and returns a terminal result.

Executors own action-specific validation and call the shared bundle mutation functions. They do not persist request lifecycle transitions or choose retries.

## Action request

A durable envelope around one action.

It gives the action an ID, origin, status, timestamps, execution-attempt count, and optional error. HTTP and interpreted voice commands both submit through the same request service.

When this project says “request” without qualification, it normally means an action request rather than an HTTP request message.

## Action result

The terminal outcome returned by an executor: `succeeded`, `failed`, or `blocked`.

Every result can include action effects. `failed` means the action ended unsuccessfully and is not automatically retried. `blocked` means retrying may be unsafe or requires inspection.

## Action runtime

The run-scoped object that owns the action-request store and service.

It creates processors bound to the current bundle cache and processes pending external requests. There is one runtime per `AudioTranscriber`, rather than separate store/service construction inside adapters and jobs.

## Bundle

The persistent directory representing one recording or merged group of recordings.

A bundle owns its audio, transcript, summary, metadata, context, and command file. Its stable `bundle_id` does not change when its human-readable directory name changes.

## Command

Natural-language instruction extracted from a bundle transcript.

A command is bundle-owned derived data. It is interpreted into a typed command and either submits an action request or receives a terminal non-action resolution such as `ignored`, `superseded`, or `rejected`.

## Command interpretation

The internal AI step that maps command text to a supported command type and structured arguments.

Interpretation is a derived job, not an action request. A transient interpretation failure is retried through daemon-level backoff. An interpretation that successfully determines that no command is supported becomes a terminal `rejected` resolution.

## Command resolution

The durable record of what command processing decided.

For a state-changing command, the resolution links to an action-request ID and later records its terminal outcome. Non-action resolutions include `ignored`, `superseded`, and `rejected`. A `migrated` resolution records a command completed before action requests existed, without inventing a request or action history.

## Main processing loop

The synchronous job loop in `AudioTranscriber`.

It selects one ready derived job at a time, applies action effects, discards stale work, and re-derives jobs only for affected bundles. It does not contain merge-specific scheduling behavior.

## Derived job

Internal work that can be reconstructed from bundle state.

Examples include transcription, command extraction, command interpretation, summary generation, and naming. Jobs are intentionally not stored in SQLite because the application can derive them again after restart.

## Origin

The durable source of an action request.

Current origins are `command` and `http`. Command origins identify the bundle and command that must receive the terminal receipt.

## Request service

The transport-neutral application boundary for submitting and reading action requests.

Both the command path and HTTP transport use `ActionService`; neither writes request rows directly.

## Request store

The persistence boundary for action requests. The current implementation uses SQLite.

The store enforces lifecycle constraints, immutable intent/origin fields, stable oldest-first pending order, and at most one `running` request.
