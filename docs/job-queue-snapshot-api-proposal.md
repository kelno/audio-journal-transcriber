# Proposal: job queue snapshot HTTP API

> **Status: proposal for a future change and pull request.** Nothing in this
> document is implemented by the current HTTP API. Names, paths, and payload
> fields below are conceptual and should be finalized during implementation.

## Objective

Expose a read-only, point-in-time view of the daemon's current job queue for a
future web or application interface.

The endpoint should help a user understand what the daemon is doing now, what
work it may perform next, and which bundle-local steps remain. It must not turn
derived jobs into durable records or imply that a snapshot is a stable execution
plan.

## Context

The current architecture has two intentionally different kinds of work:

- A **derived job** is reconstructible from current bundle state. Transcription,
  command extraction and interpretation, summary generation, and naming are
  derived again on each processing run. The queue and action effects live only
  in memory.
- An **action request** is durable user intent. Merge, delete, and set-title
  requests have stable IDs and persisted lifecycle state in SQLite. Their
  canonical status is available through the action-request API.

The coordinator owns the derived queue and executes one ready job at a time.
An action can remove queued work, invalidate stale work, and derive a replacement
pipeline for a changed bundle. Consequently, the queue can change between two
HTTP reads even when no new recording arrives.

The existing distinction should remain intact. This proposal exposes an
observation of derived state; it does not introduce another durable task system.
See [Action request architecture](action-request-architecture.md) and the
[Glossary](glossary.md) for the current terminology.

## Non-goals

The first implementation should not:

- persist job records or queue history;
- accept commands that reorder, cancel, retry, or create jobs;
- replace action-request status with queue state;
- promise an exact future execution order or completion time;
- expose transcript, summary, command text, or other bundle content;
- require parallel job execution or a distributed coordinator.

## Proposed read endpoint

Add one loopback-only read endpoint to the existing HTTP server. A working name
is:

```http
GET /job-queue
```

The final route name is deliberately not fixed by this proposal. The endpoint
would return `200 OK` with the latest immutable snapshot, including when the
daemon is starting or idle. A successful empty response should be distinguishable
from an unavailable or not-yet-published snapshot.

The endpoint should be cheap to call and should not wake the processing loop or
cause bundle discovery. It reports the coordinator's latest published view; it
does not construct a fresh queue on the HTTP transport thread.

## Conceptual contract

The following example illustrates the information a client may need. It is not
a normative schema:

```json
{
  "revision": 42,
  "captured_at": "2026-08-06T15:12:30Z",
  "daemon_state": "processing",
  "active_job": {
    "bundle_id": "0123456789abcdef0123456789abcdef",
    "kind": "transcription"
  },
  "bundles": [
    {
      "bundle_id": "0123456789abcdef0123456789abcdef",
      "display_name": "2026-08-06 14.30 Planning notes",
      "recording_date": "2026-08-06T14:30:00+02:00",
      "requeue_count": 0,
      "jobs": [
        {"kind": "transcription", "state": "running"},
        {"kind": "command_extraction", "state": "queued"},
        {"kind": "command_processing", "state": "queued"},
        {"kind": "summary", "state": "waiting"}
      ]
    }
  ]
}
```

An implementation should define stable public job kinds rather than exposing
Python class names directly. Bundle IDs are the stable identity; a display name
is advisory because an action may rename the directory.

The current scheduler does not maintain one permanent total order. It selects a
ready bundle again after every job, and summary or naming work can wait while
command work remains elsewhere. The contract should therefore describe the
active job, bundle-local pipelines, and readiness where useful instead of
assigning misleading global queue positions.

## Snapshot semantics

A response is an observation at one coordinator revision:

- `captured_at` records when the coordinator published the view, not when the
  client received it.
- A monotonically increasing in-process revision lets clients detect change.
  Revisions may restart when the process restarts unless a later requirement
  justifies durable generations.
- The snapshot may already be stale when it reaches the client. It must never be
  presented as a reservation or execution guarantee.
- An idle snapshot should explicitly say that no job is active and expose an
  empty queue.
- A starting or between-runs state should not be confused with a confirmed empty
  store.

The coordinator should publish a new snapshot after meaningful transitions,
including initial queue derivation, job start, job completion or failure, action
effect application, targeted re-derivation, and transition to idle.

## Concurrency and ownership

The HTTP server runs on a transport thread while the daemon owns and mutates the
queue on its single execution thread. The request handler must not iterate the
live queue or bundle cache while the coordinator is changing them.

The preferred design is for the coordinator to build an immutable API-facing
snapshot and publish it through a small thread-safe boundary. The handler reads
the latest complete value using an atomic reference swap or a narrowly scoped
lock. Snapshot construction should happen on the coordinator thread; JSON
serialization may happen on the HTTP thread after it obtains the immutable
value.

This keeps the coordinator as the single writer, prevents torn responses, and
avoids holding a lock across transcription, model calls, filesystem mutation,
or socket writes. If later measurements show that copying large queues is
expensive, structural sharing or bounded fields can be considered without
changing the endpoint's semantics.

## Interaction with action requests

The snapshot and action-request API answer different questions:

- the queue snapshot answers "what derived work does this daemon currently see?";
- an action request answers "what happened to this durable user intent?".

An externally submitted HTTP action is persisted before the daemon wakes. The
daemon loads bundles, executes pending external actions, and only then derives
the queue. Its effects are therefore already reflected in the first snapshot of
that queue.

A command-origin action is created while `RunCommandsJob` is executing, after a
queue already exists. Its effects can remove a bundle pipeline or replace the
jobs for a changed bundle. The coordinator should publish the next snapshot only
after invalidation and targeted re-derivation have produced a coherent queue.

Where a useful correlation already exists, an active job may expose an
`action_request_id`. The snapshot must not duplicate the full request lifecycle
or invent request status. Clients should follow the canonical request resource
for that information. Pending HTTP requests that have not regained coordinator
control are also not derived jobs; a future combined UI may display them beside
the queue by reading both resources.

## Privacy and exposure

The initial endpoint should inherit the existing loopback-only HTTP boundary.
Even locally, the payload should contain only operational metadata needed by the
client. In particular, it should omit transcript text, command text, summaries,
model prompts, filesystem paths, and error details that may contain recording
content.

Any future non-loopback exposure depends on authentication and authorization for
the HTTP API as a whole, not on this endpoint alone.

## Possible extensions

Later proposals may consider:

- server-sent events or another change stream keyed by snapshot revision;
- filters or pagination for very large stores;
- explicit action-request correlation in the UI;
- coarse progress counts that do not imply duration estimates;
- a separately designed activity history for completed work;
- administrative controls for cancellation or retry, with their own durable
  semantics and authorization rules.

Streaming, history, and control operations should not be added by silently
changing the meaning of the read-only snapshot.

## Acceptance criteria for a future implementation

A future pull request implementing this proposal should demonstrate that:

1. The endpoint is documented as a read-only, point-in-time observation and is
   available only under the configured local HTTP boundary.
2. Calling it does not mutate bundle state, submit an action, wake processing, or
   persist derived jobs.
3. Every response comes from one complete immutable snapshot with a capture time
   and an in-process change indicator.
4. The transport thread never traverses the coordinator's live mutable queue or
   bundle cache.
5. Starting, idle, processing, and failed-job states are distinguishable without
   exposing recording content.
6. Bundle identity uses `bundle_id`; display names are not treated as stable
   identifiers.
7. Action effects are reflected only after removed work is discarded and changed
   bundle pipelines are re-derived.
8. HTTP-origin and command-origin action requests remain canonical in the
   existing request store and are not redefined as queue jobs.
9. Tests cover coherent reads during job transitions and action-driven queue
   invalidation, as well as idle and startup snapshots.
10. Existing action-request and processing behavior remains unchanged when no
    client reads the endpoint.
