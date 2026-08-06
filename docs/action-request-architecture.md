# Action request architecture

This document describes the current action-request design. Terminology is defined in [glossary.md](glossary.md), and the HTTP surface is documented in [http-api.md](http-api.md).

## Goals

- Give destructive or state-changing user intent a durable identity and visible outcome.
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
HTTP or interpreted command
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
 coordinator invalidates only affected bundle work
```

The HTTP server never executes actions. It validates and submits intent, then wakes the daemon. The daemon remains the only bundle execution context.

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
- bounded user-facing terminal error;
- optional command-origin acknowledgement time.

Origins are:

- `HttpActionOrigin`;
- `CommandActionOrigin(bundle_id, command_id)`.

### Results and effects

Terminal results are `ActionSucceeded`, `ActionFailed`, and `ActionBlocked`. Every result can carry:

```text
changed_bundle_ids
removed_bundle_ids
```

The coordinator only understands these effects. It does not branch on merge, delete, or title action types.

Effects are not persisted. After restart, bundles are loaded from durable filesystem state and jobs are derived again. Persisting effects would add audit information but is not needed for recovery correctness.

## Lifecycle

```text
pending -> running -> succeeded
                   -> failed
                   -> blocked
```

- `pending`: durably accepted and not started.
- `running`: the synchronous processor started one execution attempt.
- `succeeded`: mutation completed.
- `failed`: a known terminal failure that is safe to expose and is not retried automatically.
- `blocked`: retry may be unsafe or requires inspection.

SQLite has a partial unique index permitting at most one `running` request. There are no leases because action execution is single-threaded.

If startup finds a request left `running`, it becomes `blocked` with an interruption error. Action-specific recovery is intentionally deferred; exceptional recovery code would otherwise become mandatory complexity for every action.

Terminal requests expire after seven days. A command-origin request is retained until its terminal outcome has also been acknowledged in the command file.

## Persistence

SQLite is used directly through a repository boundary. Domain models and database rows intentionally differ:

- action and origin are validated domain values stored as JSON;
- frequently queried lifecycle and error fields are columns;
- immutable request intent is checked during updates;
- schema migrations use `PRAGMA user_version`.

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
2. idempotently ensure the matching request row exists;
3. execute and persist the terminal request;
4. write the terminal command receipt;
5. acknowledge the request.

If the process stops between steps, the recorded ID makes reconciliation idempotent. Successful delete is the exception to step 4 because deleting the bundle intentionally removes its command origin; its request is acknowledged after successful deletion.

## Commands and interpretation

Commands remain in each bundle's `_commands.md` file. The file records extracted text, structured interpretation, and terminal resolution.

Interpretation is not an action request. It is internal derived work:

- a temporary AI/network exception fails the current job and uses daemon-level retry backoff;
- a successful interpretation that finds no supported operation receives a terminal `rejected` resolution;
- state-changing interpretations submit an action request;
- `ignore` and supersession are terminal non-action resolutions.

There are no command attempt counters or per-command maximums. Action execution attempts belong only to `ActionRequest`. This avoids two retry systems describing the same state change.

Legacy `executed` fields are still read as a safety migration because treating an old destructive command as new could repeat it. They are a data-safety compatibility rule, not an alternate execution path.

## Run-scoped composition

One `ActionRuntime` belongs to each `AudioTranscriber`. It owns:

- the SQLite request store;
- the transport-neutral `ActionService`;
- creation of processors bound to the current bundle cache;
- startup interruption handling, external pending-request processing, and retention pruning.

Command jobs receive this runtime instead of constructing stores and services themselves.

## Coordination and readiness

The coordinator selects one ready derived job at a time. Derived work is not persisted because it can be reconstructed from bundle state.

Readiness rules preserve required ordering:

- transcription precedes command extraction;
- command extraction precedes interpretation and action submission;
- summary and naming wait while any queued command work could introduce an invalidating action.

The summary rule is deliberately conservative across bundles. It prevents an older target from being summarized immediately before a newer source merges into it. A future dependency-aware rule could allow more unrelated summary work, but current execution is synchronous and the simpler predicate is adequate.

Accepted HTTP requests execute by SQLite `created_at`. Derived jobs have no independent enqueue timestamp, so their stable ordering key is bundle recording date followed by bundle ID. There is no work-type priority.

After each action result, the coordinator:

1. removes queued work for `removed_bundle_ids`;
2. discards stale queued work for `changed_bundle_ids`;
3. derives replacement jobs only for changed bundles that still exist;
4. selects the next ready item.

A bounded re-derivation count remains only as an accidental-loop circuit breaker.

## HTTP transport

The first HTTP layer uses Python's standard-library HTTP server to avoid a web-framework dependency for three small endpoints:

- `POST /requests`;
- `GET /requests/{request_id}`;
- `GET /health`.

It runs on one transport thread so submissions can arrive while the daemon waits. The thread writes only request rows and signals the daemon event; it never processes a bundle. SQLite serializes the short submission transaction with lifecycle updates.

The API is disabled by default and restricted to `127.0.0.1` until authentication exists. Caller-provided request IDs make repeated HTTP submissions idempotent.

## Complexity deliberately removed

- Markdown request inbox, projection files, and its second filesystem watcher.
- Per-job construction of SQLite stores, request services, and processors.
- Direct state-changing command handlers and merge-specific scheduler exceptions.
- Command attempt counters, maximum-attempt metadata, stored command errors, and no-op ignore/unknown handlers.
- Historical slice-by-slice proposal text in this document.
- A second durable request type for command interpretation.

## Decisions still open

- Authentication and authorization before any non-loopback HTTP exposure.
- Whether HTTP needs list, cancellation, or explicit resubmission endpoints.
- Whether terminal request effects should be persisted for auditing.
- Whether request retention and database backup location should be configurable.
- Whether global summary readiness becomes measurably too conservative.
- Whether a future permanent activity history is distinct from short-lived request status.

None of these are required for the current synchronous local API.
