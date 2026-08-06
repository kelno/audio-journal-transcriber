# Action request architecture

This document describes the current action-request design. Terminology is defined in [glossary.md](glossary.md), and the HTTP surface is documented in [http-api.md](http-api.md).

## Goals

- Give each destructive or state-changing user request a durable identity and visible outcome.
- Use the same action execution path for voice commands and HTTP submissions.
- Keep bundle mutation logic independent from transport and scheduling.
- Remain synchronous and understandable while the application has one execution loop.
- Derive internal jobs from bundle state instead of creating another durable task system.

## Non-goals

- Parallel action execution, leases, distributed workers, or bundle locks.
- Automatic action retry and backoff.
- A permanent audit log beyond the request retention window.
- Authentication or remote HTTP exposure in the first API version.
- Persisting derived transcription, summary, or command-interpretation jobs.

## Main flow

```text
HTTP submission or interpreted command
            |
            v
      ActionService.submit
            |
            v
   SQLite action_requests row
            |
            v
 synchronous ActionProcessor
            |
            v
    typed ActionExecutor
            |
            v
 ActionResult + ActionEffects
            |
            v
 main processing loop refreshes only affected bundle work
```

The HTTP server never executes actions. It validates and stores an action request, then wakes the continuous main processing loop. That loop remains the only place where bundle changes are executed.

## Domain models

### Actions

Supported immutable actions are:

```text
MergeAction(source_bundle_id, target)
DeleteAction(bundle_id)
SetTitleAction(bundle_id, title)
```

Merge targets are discriminated values:

- `PreviousBundleTarget` resolves through the existing merge-window rule immediately before execution.
- `BundleTarget` uses an explicit stable bundle ID.

Pydantic discriminators validate untyped JSON into the correct concrete action without hand-written tag dispatch.

### Requests

An `ActionRequest` contains:

- immutable request ID, schema version, action, origin, and creation time;
- lifecycle status and timestamps;
- execution-attempt count;
- bounded user-facing error for a failed or blocked request;
- optional command-origin acknowledgement time.

Origins are:

- `HttpActionOrigin`;
- `CommandActionOrigin(bundle_id, command_id)`.

### Results and effects

An effect is the scheduler-facing description of which bundle identities need their derived work reconsidered after an action runs.

After execution finishes, the result is `ActionSucceeded`, `ActionFailed`, or `ActionBlocked`. Every result can carry effects containing:

```text
changed_bundle_ids
removed_bundle_ids
```

`changed_bundle_ids` identifies bundles that still exist but may need fresh jobs. `removed_bundle_ids` identifies bundles whose queued jobs are no longer valid.

The main processing loop only understands these effects. It does not branch on merge, delete, or title action types.

Effects are not persisted. After restart, bundles are loaded from durable filesystem state and jobs are derived again. Persisting effects would add audit information but is not needed for recovery correctness.

## Request lifecycle

```text
pending -> running -> succeeded
                   -> failed
                   -> blocked
```

- `pending`: durably accepted and not started.
- `running`: the synchronous processor started one execution attempt.
- `succeeded`: mutation completed.
- `failed`: a known failure that is safe to expose and is not retried automatically.
- `blocked`: retry may be unsafe or requires inspection.

SQLite has a partial unique index permitting at most one `running` request. There are no leases because action execution is single-threaded.

If startup finds a request left `running`, it changes the request to `blocked` with an interruption error. We have not implemented action-specific recovery yet: startup does not try to infer whether an interrupted merge, delete, or title change partly completed. Targeted recovery can be added later for actions where it is both safe and simple.

The three finished states are `succeeded`, `failed`, and `blocked`. Requests in those states become eligible for deletion after 30 days. A request created from a command is kept until its result has also been written to the command file and acknowledged.

## Persistence

SQLite is used directly through a repository boundary. Domain models and database rows intentionally differ:

- action and origin are validated domain values stored as JSON;
- frequently queried lifecycle and error fields are columns;
- updates may change lifecycle fields, but the store verifies that the original request ID, action, origin, schema version, and creation time did not change;
- schema migrations record their current version in SQLite's application-defined `PRAGMA user_version` field.

The database has a fixed location at `<store_dir>/_state/transcriber.sqlite3`. There is currently no automatic backup mechanism for this database.

The earlier SQLModel experiment was rejected because it coupled constructor/database behavior to domain validation, made frozen immutable fields awkward, and added more code and dependencies than explicit `sqlite3` for this small schema.

### Why commands remain bundle files

Moving commands to SQLite was considered because request submission and command resolution could then share a database transaction. It was rejected for now:

- merge and delete still span SQLite and filesystem mutation, so the overall operation would not become atomic;
- commands are derived bundle-owned data and naturally move with a merged bundle;
- database ownership would require transactional bundle-ID reassignment during merge;
- bundles would become less portable and inspectable;
- it would introduce another repository and migration without removing filesystem crash boundaries.

The current cross-store ordering is therefore deliberate:

1. persist a request ID in the command;
2. create the matching database request if it does not exist, or reuse it when the same ID and action were already stored;
3. execute the action and save its finished request state;
4. write the finished result into the command file;
5. acknowledge the request.

If the process stops between steps, the recorded request ID lets retries resume the same durable request, preventing reconciliation from submitting or executing the action again. Successful delete is the exception to step 4 because deleting the bundle intentionally removes its command file; its request is acknowledged after successful deletion.

## Commands and interpretation

Commands remain in each bundle's `_commands.md` file. The file records extracted text, structured interpretation, and the final resolution.

Interpretation is not an action request. It is internal derived work:

- a temporary AI/network exception fails the current job and uses daemon-level retry backoff;
- a successful interpretation that finds no supported operation receives a final `rejected` resolution;
- state-changing interpretations submit an action request;
- `ignore` and supersession are final non-action resolutions.

There are no command attempt counters or per-command maximums. Action execution attempts belong only to `ActionRequest`. This avoids two retry systems describing the same state change.

## Run-scoped composition

`AudioTranscriber` creates one `ActionRuntime` for the process run. The runtime owns:

- the SQLite request store;
- the `ActionService`, which gives HTTP and command code the same operations for submitting, reading, and acknowledging requests;
- creation of action processors that execute requests against the currently loaded bundles;
- changing a request left `running` by a previous process to `blocked` during startup;
- processing pending HTTP requests before bundle jobs;
- deleting old finished request records after their retention period.

Command jobs receive this runtime instead of constructing stores and services themselves.

## Coordination and readiness

The main processing loop selects one ready derived job at a time. Here, a derived job is internal work such as transcription, command extraction, summarization, or bundle naming. It is not persisted because it can be reconstructed from bundle state.

Readiness checks do two things: they determine whether a job can run now, and they preserve required ordering between dependent jobs:

- transcription precedes command extraction;
- command extraction precedes interpretation and action submission;
- summary and naming wait while any queued command work could introduce an invalidating action.

The summary rule is deliberately conservative across bundles. It prevents an older target from being summarized immediately before a newer source merges into it. A future rule could allow summaries for bundles that cannot be affected by any pending command, but current execution is synchronous and the simpler check is adequate.

Pending HTTP action requests execute oldest first, ordered by SQLite `created_at` and then request ID. Merge, delete, and title requests all use this same order; action type does not add another priority. Derived jobs have no independent enqueue timestamp, so they are ordered by bundle recording date followed by bundle ID, subject to the readiness checks above.

After receiving an `ActionResult`, the main processing loop resolves the action's `ActionEffects` as follows:

1. removes queued work for `removed_bundle_ids`;
2. discards stale queued work for `changed_bundle_ids`;
3. derives replacement jobs only for changed bundles that still exist;
4. selects the next ready item.

The loop limits how many times it can recreate jobs for the same bundle during one update cycle. Reaching that limit reports an error instead of allowing a scheduling bug to loop forever.

## HTTP transport

The HTTP layer is a FastAPI application served by Uvicorn. FastAPI handles route matching, JSON parsing, Pydantic validation, response serialization, and the generated OpenAPI documentation. The application defines three action API endpoints:

- `POST /requests`;
- `GET /requests/{request_id}`;
- `GET /health`.

Uvicorn runs on one transport thread so submissions can arrive while the daemon waits. The route functions perform their short synchronous request-store operations directly on that thread, which keeps HTTP submissions serialized. The thread writes only request rows and signals the daemon event; it never processes a bundle. SQLite coordinates those submission transactions with lifecycle updates from the processing loop.

The API is enabled in the default continuous mode and restricted to `127.0.0.1` until authentication exists. When callers repeat the same action with the same request ID, the API returns the existing request instead of creating a duplicate. `--once` runs one update cycle without starting the watcher or HTTP transport.
