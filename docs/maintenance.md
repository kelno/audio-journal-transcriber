# Maintenance utilities

Run these commands from the repository root. They use the configured bundle
store; pass `--store-dir` to use another one.

## Migrate command state to action requests

Stop the transcriber before applying this one-time store migration. Preview and
validate every active and safely deleted bundle without writing anything:

```bash
uv run python scripts/migrate_action_request_commands.py
```

Apply the preflighted rewrites:

```bash
uv run python scripts/migrate_action_request_commands.py --apply
```

The migration converts former `executed: true` state into an explicit terminal
`migrated` resolution, assigns stable IDs to older commands that lack one, and
removes obsolete execution and retry fields. Changed originals are copied under
`<store_dir>/_migration_backups/action-request-commands-v1` before rewriting.
Running the dry run again after a successful migration reports zero files to
rewrite.

## Regenerate transcripts with a new model

After configuring the new `audio.model` and stopping the daemon, preview the
eligible bundles:

```bash
uv run python scripts/rename_transcripts_for_regeneration.py --model "new-model"
```

Apply the changes:

```bash
uv run python scripts/rename_transcripts_for_regeneration.py --model "new-model" --apply
```

Previous transcripts are kept under model-labelled filenames. `--model` must
match the configured model. Run the transcriber to regenerate them:

```bash
uv run transcriber
```

## List bundles by transcript length

List longest transcripts first:

```bash
uv run python scripts/list_bundles_by_transcript_length.py
```

List shortest first:

```bash
uv run python scripts/list_bundles_by_transcript_length.py --ascending
```
