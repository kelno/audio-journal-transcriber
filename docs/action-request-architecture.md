# Action request architecture

Status: proposed design under incremental implementation; slices 1 through 3
define the models, SQLite store, and lifecycle service.

## Context

The transcriber currently discovers spoken commands in a transcript and executes
their handlers directly. This couples two separate responsibilities:

- understanding what the user requested;
- applying a state change to one or more bundles.

This is inconvenient for operations initiated outside a transcript. A manual
merge currently requires manufacturing transcript command text, even though the
desired operation is already known. A future application will have the same
problem if bundle mutations are only exposed through voice-command handlers.

The daemon should remain the only process that mutates the managed store. Other
clients submit typed requests and observe their results.

## Decisions

1. A typed `Action` describes a requested state change. It does not contain
   execution state, retry counters, or transport details.
2. An `ActionRequest` is the durable execution envelope for an action.
3. A SQLite database owned by the daemon is the canonical source of request
   execution state. The initial database can contain only an action-request
   table, while leaving room for other daemon state later.
4. The initial submission interface is a file in the global
   `<store_dir>/_requests` directory. This directory is an inbox and status
   projection, not the canonical request store. There is no per-bundle request
   file or per-bundle/global hybrid path.
5. A future local API will call the same application service as the filesystem
   adapter. An API is not part of the initial implementation.
6. Spoken commands that cause state changes submit typed actions to
   `ActionService`. The command-processing path stops mutating bundles directly;
   the same action processor and executor used by filesystem or future API
   submissions performs the operation.
7. A command keeps a reference to its request and, once the request is terminal,
   a terminal receipt. It does not copy live request execution state.
8. Request IDs are ordinary random IDs. They do not need to be derived from
   command IDs.
9. The initial filesystem implementation attempts each request once. A new user
   submission is a new request. Automatic retry policy is deferred.
10. `attempt_count` remains on the request for diagnostics. Crash recovery is
    separate from retry.
11. Terminal requests expire seven days after reaching `succeeded`, `failed`, or
    `blocked`. Other systems must not treat request IDs as permanent records.
12. Failed and blocked requests store a concise user-facing error message. Raw
    tracebacks remain in daemon logs rather than becoming API or UI output.
13. The daemon executes work synchronously on one thread. There is at most one
    `running` action request, enforced by a database uniqueness constraint. The
    initial design therefore needs neither leases nor concurrent entity locks.
14. Executors apply one requested operation and return its outcome and affected
    bundle identities. They do not create, remove, or reorder expected
    downstream work themselves. The coordinator owns those scheduling changes.
15. Readiness and ordering are queue policy, not a reason to rerun global update
    phases. The initial ordering is oldest ready work first by `created_at`.
    Work-type priority is deferred until a concrete latency or wasted-work
    requirement justifies it.

## Goals

- Have one implementation of merge, delete, set-title, and future actions.
- Allow voice commands, request files, and a future API to submit the same typed
  actions.
- Keep mutation ownership inside the daemon.
- Preserve stable bundle identity across directory renames and merges.
- Make request processing restart-safe and observable.
- Keep each implementation slice small enough to review independently.

## Non-goals

- Implementing an HTTP API or user interface now.
- Supporting per-bundle action-request files.
- Building automatic retry or backoff for action requests initially.
- Replacing desired-state reconciliation such as regenerating a missing summary.
- Generalizing every internal job into an action.
- Implementing a permanent request-history or audit-log system.

## Terminology

### Command

A command is text extracted from a transcript, together with its interpretation.
It is one possible source of an action request.

Commands such as `ignore`, an unrecognized command, or a superseded title do not
need to produce an action request.

### Action

An action is typed user intent without execution state. Examples include:

```python
MergeAction(
    source_bundle_id=source_id,
    target=PreviousBundleTarget(),
)

DeleteAction(bundle_id=bundle_id)

SetTitleAction(bundle_id=bundle_id, title="Quarterly planning")
```

An explicit merge submitted by a future application can use a stable target ID:

```python
MergeAction(
    source_bundle_id=source_id,
    target=BundleTarget(bundle_id=target_id),
)
```

### Action request

An action request gives an action a durable identity and processing lifecycle. It
records status, attempts, timestamps, origin, and errors.

### Command resolution

A command resolution records what command processing decided to do. It may point
to an action request, or record a terminal non-action outcome such as ignored or
superseded.

For a merge, the current and proposed responsibilities differ as follows:

```text
Current:
transcript -> interpreted merge command -> handle_merge() -> mutate bundles

Proposed:
transcript -> interpreted merge command -> MergeAction -> ActionService.submit()
                                                     |
                                                     v
                                      ActionProcessor -> MergeExecutor
```

In the proposed flow, resolving a command means deciding whether it represents an
action and, if so, successfully submitting that action. It does not mean the
action has already succeeded. The linked request later records execution and the
terminal command receipt records the final outcome.

### Attempt, retry, resubmission, and recovery

- An **attempt** starts when the processor invokes an action executor.
- An **automatic retry** is another attempt of the same request without new user
  intent. The initial implementation does not do this.
- A **resubmission** is new user intent and creates a new request ID, even if its
  action has the same fields as an earlier request.
- **Recovery** determines what happened to work interrupted by a daemon crash. It
  must not blindly execute a destructive action again.

## Architecture

```text
Spoken command ─────────┐
                       │
Global request file ────┼──> ActionService ──> ActionRequestStore
                       │                         │
Future local API ───────┘                         v
                                      Coordinator / scheduler
                                                │
                                                v
                                         ActionProcessor
                                                │
                                                v
                                         ActionExecutor
                                                │
                                                v
                                          Bundle store
```

### `ActionService`

This is the application boundary used by every submission adapter. Its initial
interface only needs to express known requirements:

```python
class ActionService:
    def submit(self, action: Action, origin: ActionOrigin) -> RequestId: ...
    def get_request(self, request_id: RequestId) -> ActionRequest | None: ...
    def prune_expired(self) -> list[RequestId]: ...
```

The filesystem adapter calls `submit()` after parsing a request file. A future
HTTP endpoint can call the same method without knowing how requests are stored or
executed. In both cases, successful submission means the request was committed to
the canonical database; it does not mean the action has completed.

Execution remains a separate `ActionProcessor` responsibility. This keeps
submission adapters from needing an executor and lets status lookup and retention
operate without exposing processing policy.

### `ActionRequestStore`

The request store owns request persistence, lookup, terminal acknowledgement, and
retention. The first implementation is backed directly by Python's standard
library `sqlite3` driver.

The database initially lives at the reserved daemon-state location
`<store_dir>/_state/transcriber.sqlite3`. SQLite is daemon-owned state and must
not be edited by users. Stores synchronized by Obsidian or another file-sync
tool may later need a configurable path outside the synchronized tree.

The initial SQLite store accepts an explicit database path so callers and tests
do not depend on configuration internals. Its default-path helper resolves to
`<store_dir>/_state/transcriber.sqlite3`; configuration can expose an override
later without changing the repository interface. Bundle discovery already
ignores the underscore-prefixed state directory.

Callers depend on the `ActionRequestStore` interface rather than SQL or database
paths. A future API and action executors therefore do not acquire persistence
responsibilities.

SQLite is a better fit than canonical request Markdown once request state is no
longer intended for direct user editing:

- lifecycle transitions and attempt increments can be transactional;
- pending and expiring requests can be queried without scanning and parsing many
  files;
- a future API can read consistent status while the daemon is working;
- retention cleanup and schema evolution have explicit mechanisms;
- the first schema can remain one `action_requests` table.

The table stores action and origin values as versioned JSON payloads while
keeping lifecycle, timestamp, acknowledgement, and error values in relational
columns. The store explicitly maps the relational row back through
`ActionRequest.model_validate()` so database reads cannot bypass the Pydantic
domain boundary. All timestamps are timezone-aware in the domain model and
normalized to UTC text before persistence so creation-time ordering compares
actual instants.

Database migrations are forward-only and tracked with SQLite's
`PRAGMA user_version`. Each migration and each repository mutation owns its
transaction internally. The initial implementation keeps its small schema and
queries explicit rather than adding an ORM; a higher-level database toolkit can
be reconsidered if daemon state grows into several related tables or migration
management becomes substantial. A general transaction context is deferred until
a processor operation requires multiple repository calls to commit atomically.
Lifecycle updates compare the request's action, origin, creation time, and schema
version with the canonical row inside the same transaction. A mismatch raises a
specific error rather than silently rewriting or ignoring immutable intent.

In dry-run mode, an existing compatible database can be read but not migrated or
mutated. A missing database behaves as an empty store without creating `_state`.
Mutation methods raise a specific read-only error instead of pretending a
request was durably accepted.

The costs are database migrations, backup considerations, and care around synced
vaults. Those costs should stay behind the repository boundary. The filesystem
request remains an ingress and status-projection format rather than a second
persistence model.

### `ActionProcessor`

The processor:

1. accepts the next ready pending request selected by the coordinator;
2. marks a request running;
3. increments `attempt_count` immediately before invoking its executor;
4. dispatches the typed action to exactly one executor, which performs
   action-specific validation and resolves deferred selectors such as `previous`;
5. persists the resulting terminal status, user-facing error, or recovery
   information;
6. acknowledges terminal outcomes back to command origins when applicable;
7. returns effects describing which bundles were removed or changed.

The processor owns lifecycle mechanics. Executors do not decide whether to retry
and do not update voice-command state or schedule downstream work directly.

An executor reports expected failures as typed `ActionFailed` or `ActionBlocked`
results. If it unexpectedly raises, the processor logs the exception and
traceback, then persists a generic bounded `blocked` error. This avoids exposing
internal diagnostics or automatically repeating a possibly partial mutation.

The request store enforces that no more than one request can be `running` at a
time. This is a durable invariant for restart and diagnostic correctness rather
than a coordination mechanism for parallel workers: the initial daemon has no
parallel workers.

### Action executors

An executor owns the validation and mutation rules for one action type. There is
one merge executor, one delete executor, and one set-title executor.

Execution is synchronous. When an executor returns, its mutation has completed
before the coordinator updates the queue. A merge therefore cannot race an
already-running transcription or another action in the initial architecture.
The merge result identifies the surviving, absorbed, and changed bundles; the
coordinator uses those effects to invalidate stale queued work and derive any
replacement work.

The current command handlers will eventually become command-to-action adapters
or disappear. They must not remain a second mutation path.

Merge is the first cutover: the command job persists an active request resolution,
ensures the matching canonical request exists, and invokes `BundleActionExecutor`.
That executor resolves `previous` immediately before execution and calls the
existing `merge_bundles()` function, which remains the sole merge mutation core.
After success, the command is located by its stable ID in the surviving bundle;
its terminal receipt is written before the request is marked acknowledged.

## State ownership

| State | Owner |
| --- | --- |
| Extracted command text and interpretation | `_commands.md` |
| How a command was resolved | `_commands.md` |
| Active request status | Canonical SQLite request row |
| Request attempts and execution errors | Canonical SQLite request row |
| Terminal receipt for a command-origin request | `_commands.md` after acknowledgement |
| Bundle content and metadata | Bundle files |
| Merge inconsistency protection | Existing merge-failure markers until replaced deliberately |

The command's terminal receipt is not a second live source of truth. While a
request is active, its record is authoritative. After it reaches a terminal
state, the action processor acknowledges the outcome into the originating
command. The receipt then makes the command self-contained when the request row
expires after seven days.

## Proposed command model

The current `executed` boolean cannot represent ignored, superseded, submitted,
failed, and blocked outcomes without consulting other fields. Replace it with an
optional discriminated resolution.

A command that has not yet been resolved:

```yaml
id: 70f5c5...
text: merge this with the previous recording
matched_type: merge
resolution: null
```

A command with an active request:

```yaml
resolution:
  type: action_request
  request_id: 019fd...
  outcome: null
  resolved_at: null
```

After terminal acknowledgement:

```yaml
resolution:
  type: action_request
  request_id: 019fd...
  outcome: succeeded
  resolved_at: 2026-08-05T15:30:00+02:00
```

Other command resolutions can be represented without fake requests:

```yaml
resolution:
  type: ignored
  resolved_at: 2026-08-05T15:30:00+02:00
```

```yaml
resolution:
  type: superseded
  resolved_at: 2026-08-05T15:30:00+02:00
```

`Command.attempt_count` initially remains responsible only for failures before a
request has been accepted, such as command interpretation or request submission.
It should later be renamed or replaced if that narrower responsibility is not
useful.

During migration, `executed` and `executed_at` remain serialized as a compatibility
projection. A legacy command with `executed: true` and no resolution loads as a
terminal `legacy_executed` resolution. New ignored and superseded decisions store
their specific resolution while still appearing executed to older readers. An
active `action_request` resolution remains `executed: false` but is not eligible
for dispatch again while its outcome is pending.

### Dispatching a command safely

The command file and global request cannot be updated atomically. Use a simple,
recoverable ordering:

1. Generate a random request ID.
2. Persist an `action_request` resolution containing that ID in `_commands.md`.
3. Ensure that a request with that ID exists in the global request store.

If the daemon crashes after step 2, the next run recreates the missing request
using the already-recorded ID. Reusing the recorded ID is simpler than assigning
another ID because it does not require rewriting the command again. Assigning a
new ID would also be correct; it is not an architectural requirement.

If a command resolution already has a terminal outcome, a missing request must
not cause recreation. The command receipt is sufficient evidence that command
processing is finished.

### Acknowledging terminal requests

For a command-origin request:

1. Execute the action and persist its terminal request status.
2. Locate the originating command. A merge may have moved it into the surviving
   target bundle, but its command ID remains stable.
3. Write the matching terminal outcome into the command resolution.
4. Mark the request as acknowledged by its origin.

Request cleanup must not remove an unacknowledged terminal request belonging to a
command. Acknowledgement should normally happen immediately after terminal state
is persisted. Independently, the request row receives a seven-day expiry so
clients have a bounded window in which to inspect its result.

## Proposed request model

The exact Pydantic and database types are an implementation detail, but the
logical persisted record needs fields equivalent to:

```yaml
schema_version: 1
request_id: 019fd...
action:
  type: merge
  source_bundle_id: a12b...
  target:
    type: previous
origin:
  type: command
  bundle_id: a12b...
  command_id: 70f5c5...
status: pending
attempt_count: 0
created_at: 2026-08-05T15:29:00+02:00
started_at: null
finished_at: null
expires_at: null
error: null
acknowledged_at: null
```

Filesystem submissions use a filesystem origin rather than manufacturing a
command:

```yaml
origin:
  type: filesystem
```

The origin is acknowledgement and short-lived diagnostic information. It does
not change action semantics and must not be treated as a permanent audit record.

## Request lifecycle

The initial generic states are:

```text
pending ──> running ──> succeeded
                    ├─> failed
                    └─> blocked
```

Only one request may have status `running`. SQLite should enforce this with a
partial unique index rather than relying solely on an in-memory assertion. Any
number of requests may remain `pending` while the synchronous processor handles
them one at a time.

- `failed` means the attempt ended and will not be retried automatically.
- `blocked` means repeating the operation may be unsafe or human intervention is
  required. Existing merge-failure markers are one reason to use this state.
- Pressing a button again, issuing another voice command, or creating another
  request file creates a new request. It does not reset the old request.

Only explicitly classified transient errors should ever become automatically
retryable. That policy is deferred. Unknown errors in destructive actions must
default to `failed` or `blocked`, not automatic repetition.

### Error reporting

Failed and blocked requests need an error suitable for a filesystem client and a
future UI:

- `ActionError.code` is a stable machine-readable category when one is available;
- `ActionError.message` is a concise message safe to show to a user;
- `ActionError.details` is optional, bounded diagnostic context for
  troubleshooting.

The Python domain model groups these fields as an optional `ActionError` on
`ActionRequest.error`. An error is one conceptual value, and grouping it
prevents partial states such as a message without an error code. Failed and
blocked `ActionResult` variants reuse the same value type.

The SQLite schema stores the value in nullable `error_code`, `error_message`, and
`error_details` columns. Separate columns are easier to constrain and inspect
than a small JSON object. `ActionRequestStore` owns the explicit mapping between
the nested domain value and these flat persistence columns. The filesystem
projection may use the nested representation because it is intended for users,
not direct database mapping.

The initial implementation can normalize existing exception messages into a
bounded `error_message` and use a generic error code. It should collapse noisy
newlines and must not store an entire traceback. Full exceptions and tracebacks
remain in daemon logs. Executors can introduce more specific typed errors later
without changing the request-status contract.

### Seven-day retention

When a request becomes `succeeded`, `failed`, or `blocked`, the processor sets:

```text
expires_at = finished_at + 7 days
```

A periodic daemon cleanup deletes terminal rows whose `expires_at` has passed,
together with any filesystem status projection associated with them. Pending or
running requests are never pruned based on age; a stale running request must go
through recovery.

The public contract is that request status is temporary:

- clients may inspect a request for seven days after it finishes;
- after `expires_at`, lookup may return “request not found”;
- clients must not use request existence as permanent evidence that an action
  happened;
- command-origin actions retain their terminal receipt in `_commands.md`;
- a future permanent activity history, if wanted, is a separate feature and
  storage model.

### Crash recovery

A request found in `running` after restart is not treated as a normal retry. The
initial processor marks it `blocked` with an `interrupted_by_restart` error,
without invoking an executor or incrementing `attempt_count`. The daemon's normal
startup scan reconstructs derived work from current durable bundle state.

This deliberately keeps exceptional-case machinery small. An action-specific
recovery handler may be added later only when its durable completion evidence is
simple and reliable. Otherwise the generic blocked state and existing safety
markers require inspection rather than risking a repeated destructive action.

## Global filesystem interface

The initial user-facing adapter watches `<store_dir>/_requests` for Markdown
files with YAML frontmatter. The directory is global and already follows the
store convention that underscore-prefixed root directories are not bundles.

An initial manual merge request can contain only submission fields:

```yaml
---
schema_version: 1
action:
  type: merge
  source_bundle_id: a12b...
  target:
    type: previous
---
```

The filesystem adapter uses the same recoverable submission ordering as command
dispatch:

1. validate the action fields;
2. assign a random request ID and persist it in the Markdown file;
3. ensure that the canonical SQLite request with that ID exists;
4. update the Markdown file with a projection of request status and any
   user-facing error.

The request ID makes file reprocessing idempotent if the daemon crashes between
steps. The SQLite row remains authoritative. The Markdown status is a
user-facing transport response and may be rebuilt from the row; editing it does
not alter canonical execution state. An explicit target uses
`target.type: bundle_id` and a `bundle_id` field.

When the request expires after seven days, cleanup removes both its database row
and filesystem projection. A malformed submission that never creates a request
can instead receive a validation error in its file and remains the user's
responsibility to correct or remove.

The file adapter is a transport, not the application boundary. A future user
interface submits the same action through `ActionService` and does not need to
write request files directly.

The adapter reconciles only top-level Markdown files. It writes an assigned ID
before canonical insertion, preserves any Markdown body, and rewrites a file only
when its canonical projection changes so its own watcher does not create a loop.
The daemon watches `_requests` separately from the audio input tree and filters
wake-ups to Markdown request files.

The daemon currently watches only the audio input directory. Filesystem request
support therefore needs a narrowly filtered watcher for the global request
directory. Bundle mutations elsewhere in the store must not continuously wake
the daemon.

## Processing-loop integration

Today, bundle lifecycle jobs, command jobs, and post-processing jobs are gathered
up front for each bundle. Merge is special: it mutates the bundle cache, aborts
the source's remaining jobs, and re-gathers jobs for the surviving target.

The daemon remains a synchronous single writer. The coordinator owns one logical
collection of available work, containing derived jobs and pending action
requests without making them the same persisted domain concept. It repeatedly
selects and executes one ready item:

```text
load bundles, pending requests, and derivable jobs
        ↓
discard work invalidated by current state
        ↓
select the oldest ready item
        ↓
execute it synchronously
        ↓
apply its result and update affected queued work
        └──────── select again until no ready work remains
```

Selecting again is an ordinary queue operation, not a global update cycle. Only
the bundles named by an execution result need their queued work reconsidered.
Every action is still revalidated against current bundle state immediately
before execution.

### Readiness

Readiness captures required ordering. For example, command extraction is not
ready before transcription exists, and summary generation is not ready until
command extraction and resolution establish that no invalidating action remains
for that bundle. These are dependencies or predicates on current state, not
work-type priorities.

The coordinator skips work that is not ready and chooses the ready item with the
oldest `created_at`. An older blocked item therefore does not prevent newer ready
work from running. Derived work must have a stable enqueue timestamp for the
lifetime of that queued item; repeatedly deriving the same item must not make it
artificially new.

Strict work-type priority is not an initial requirement. It could reduce wasted
work—for example, preferring a pending merge over a summary that the merge will
invalidate—or improve manual-action latency, but neither is required for
correctness when requests are revalidated and stale queued work is invalidated
eagerly. If experience establishes such a requirement, the selection key can be
extended without changing executors.

### Command/action synchronization

Command resolution must preserve the current destructive-action ordering. It
must not submit every command in a bundle at once. For each bundle and
scheduling pass, it applies command policies and submits only the next
eligible state-changing command when that action can invalidate the bundle or
move its remaining commands.

For example, a source bundle may contain both merge and set-title commands:

```text
transcribe and extract both commands
        ↓
submit merge request; defer set-title
        ↓
execute merge and reload surviving target
        ↓
find the still-unresolved set-title command in the target
        ↓
submit and execute set-title against the target
        ↓
generate the final summary and name
```

Submitting both requests before the merge would incorrectly bind set-title to
the source bundle that is about to disappear. Processing one invalidating command
at a time preserves the current behavior without teaching request executors about
voice-command ordering.

Commands that resolve without actions, such as ignored or superseded commands,
can be finalized during command resolution. Non-invalidating actions may later be
batched as an optimization, but are processed sequentially initially.

### Request validation

The initial coordinator executes ready work serially by creation time. A
filesystem or future API request targets existing persistent bundle IDs; a
missing source or explicit target is a validation failure rather than an implicit
job dependency. The `previous` selector is resolved and frozen immediately before
execution.

If two requests affect the same bundle, the first request changes the store and
the second is validated against that new state. The second may consequently fail
with a user-facing error. The initial implementation does not need parallel
bundle locks because the daemon processes one action at a time.

Because execution is synchronous, a request submitted externally while one item
is running is observed after that item completes or on the next daemon pass.
Correctness does not depend on immediate execution: subsequent actions revalidate
state, and any derived output they invalidate is regenerated afterward.

### Reaching a stable state

After each item, the coordinator applies its result, discards stale work, reloads
changed bundles when necessary, and derives replacement work only for affected
bundles. Processing is idle when:

- no queued request or job is running or ready;
- no actionable command is ready for resolution;
- no execution result remains to be applied.

The queue should reject duplicate derived work, and the action-request table
prevents more than one running request. A separate loop guard may remain as a
diagnostic safeguard, but correctness should come from readiness, invalidation,
and idempotent work derivation rather than bounded global reconciliation rounds.

Every terminal `ActionResult`, including failed and blocked results, should
describe scheduler effects rather than requiring the scheduler to understand
merge-specific exceptions. A failed operation may have changed durable state
before it stopped. Expected effects include:

- source bundle removed;
- target bundle changed and needs fresh jobs;
- current bundle renamed;
- no further jobs should run for a bundle.

The existing merge exception and requeue logic can remain during intermediate
slices. Replacing it is a later cleanup after request execution is proven.

## Requests versus jobs

Action requests and jobs overlap mechanically, but they currently represent
different layers:

| Action request | Job |
| --- | --- |
| Durable external intent | Internal work derived from current bundle state |
| Submitted by a command, file, or future API | Gathered by the transcriber |
| Has a client-visible ID and result | Can usually be regenerated by rescanning |
| Examples: merge, delete, set title | Examples: transcribe, summarize, name, remove retained audio |
| Retained temporarily for status lookup | Currently exists only as processing-loop state |

Replacing all jobs with requests now would expose internal implementation steps
as user operations, persist work that can already be derived from bundle state,
and blur what “request succeeded” means.

Instead, an action executor can change bundle state and return scheduler effects.
The job system then performs any derived work. For example, a future
`RegenerateSummaryAction` might invalidate or remove the current summary; the
ordinary summary job would generate its replacement.

Some infrastructure may eventually be shared: IDs, status reporting, dependency
ordering, or a persistent work queue. If future UI requirements need progress for
long-running transcription and summary work, jobs may themselves become durable
internal tasks linked to an action request. They should still remain semantically
distinct:

```text
Action request = why the system is changing
Job            = work the system performs to reach the resulting state
```

No job-system replacement is part of this refactor. The immediate orchestration
change is only to stop `RunCommandsJob` from performing user actions directly and
to let action results invalidate or re-gather the appropriate jobs.

## Incremental implementation plan

Each slice should leave the repository reviewable and preserve current behavior
unless the slice explicitly changes it.

### Slice 1: Define action and request models

- Add typed action models for merge, delete, and set-title.
- Add request identity, origin, status, attempt, and result models.
- Add serialization tests, including discriminated action and target types.
- Do not connect models to command execution yet.

Validation:

- targeted model tests;
- `uv run basedpyright` for the new modules;
- `uv run ruff check --fix` for the new modules.

### Slice 2: Add the SQLite request store

- Implement `ActionRequestStore` behind an interface.
- Create the initial SQLite schema and migration/version mechanism.
- Persist one canonical request row per submitted action.
- Load, validate, update, and list pending requests.
- Store action and origin payloads without coupling callers to SQL columns.
- Place daemon state in a reserved location that bundle discovery ignores.

Validation:

- database round-trip and schema-version tests;
- request ID collision, transaction rollback, and missing-directory tests;
- dry-run tests proving no writes.

### Slice 3: Add `ActionService` and processor lifecycle

- Implement submission without knowing the origin transport.
- Implement pending-to-running-to-terminal transitions.
- Enforce at most one `running` request with a SQLite partial unique index.
- Increment `attempt_count` only when an executor starts.
- Persist bounded user-facing error fields while keeping tracebacks in logs.
- Set `expires_at` for terminal requests and prune them after seven days.
- Never age-prune pending or running requests.
- Use fake executors to test success, failure, blocked outcomes, and restart
  handling.
- Do not add automatic retries.

Validation:

- processor state-transition tests;
- repeated processing does not rerun terminal requests;
- attempting to mark a second request running violates the database invariant;
- a new request with the same action is still allowed.
- terminal rows remain before expiry and disappear after expiry;
- active rows are not removed by retention cleanup.

### Slice 4: Introduce command resolutions compatibly

- Add typed command-resolution models.
- Preserve loading of existing `_commands.md` files containing `executed`.
- Define an explicit migration or compatibility mapping before changing writes.
- Update command selection policies to distinguish unresolved, submitted, and
  terminally resolved commands.
- Submit at most the next invalidating command action for a bundle, so merge or
  delete can relocate/remove remaining commands before they are reconsidered.
- Keep current handlers executing operations during this slice.

Validation:

- legacy command-file fixtures still load;
- resolution round-trip tests;
- ignored and superseded commands do not create requests.

### Slice 5: Move merge execution behind the action processor

- Extract merge validation and mutation into the sole merge action executor.
- Make the voice merge path submit a request instead of mutating directly.
- Preserve target-ID retention, merge-window semantics for `previous`, manual
  title behavior, additional-file handling, summary invalidation, and failure
  markers.
- Acknowledge the terminal result into the command copied to the surviving
  target.
- Preserve same-run target reprocessing, initially through the existing requeue
  mechanism if that keeps the slice smaller.

Validation:

- existing merge-handler tests adapted to the executor boundary;
- command dispatch crash-window tests;
- missing active request is recreated from its command resolution;
- missing request after a terminal receipt is not recreated;
- merge scheduling and summary-regeneration regression tests.

### Slice 6: Add manual filesystem submission

- Parse new Markdown request drafts in the global `_requests` directory.
- Persist a request ID in the draft before ensuring its SQLite row exists.
- Project canonical status and user-facing errors back into the Markdown file.
- Add a filtered watcher that wakes the daemon for request-directory activity.
- Validate source and explicit target IDs before mutation.
- Support `previous` with the existing merge-window rules.
- Remove expired request projections with their canonical rows.

Validation:

- filesystem request integration tests;
- crash recovery between file ID assignment and database insertion;
- watcher filtering tests proving normal bundle output does not create a loop;
- manual merge uses the same executor as a voice-command merge;
- failed and blocked requests do not run again automatically.

### Slice 7: Convert delete and set-title

- Convert remaining state-changing command handlers into action submission.
- Reuse the same processor and terminal acknowledgement mechanism.
- Keep `ignore` and unknown-command handling in command processing.

Validation:

- existing delete and set-title behavior remains unchanged;
- latest-title and destructive-action ordering policies remain explicit;
- deletion safely handles the disappearance of its command origin.

### Slice 8: Simplify orchestration

- Replace merge-specific scheduler exceptions with generic `ActionResult`
  effects.
- Introduce a synchronous coordinator that owns readiness, invalidation, and
  derivation of downstream work.
- Select the oldest ready item by creation time; do not introduce work-type
  priorities without a concrete requirement.
- Reconsider queued work only for bundles affected by the completed item rather
  than repeating global update phases.
- Keep action requests as external intent and jobs as derived internal work;
  introduce durable internal tasks only if a later requirement justifies them.
- Reassess command-level attempt fields and the bundle-level retry manager after
  action requests own execution attempts.
- Update user documentation only after behavior is implemented.

Validation:

- targeted scheduler and daemon tests;
- full `uv run pytest` after the slices are reviewed together;
- project-wide `uv run basedpyright` and `uv run ruff check --fix` as agreed for
  the final integration pass.

## Deferred decisions

- Whether a future API uses HTTP, a local socket, or another transport.
- Which failures, if any, support automatic retry and backoff.
- The final configurable location and backup policy for daemon-owned SQLite
  state, especially when the bundle store is synchronized by another tool.
- Whether a later permanent activity history is needed in addition to the
  seven-day request-status window.
- How a future UI exposes cancellation for pending requests.
- Whether measured latency or wasted work later justifies priority dimensions
  beyond oldest-ready-first ordering.

These decisions do not need to be resolved to implement the initial filesystem
submission path and shared action executors.
