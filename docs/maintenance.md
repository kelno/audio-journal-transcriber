# Maintenance utilities

Run these commands from the repository root. They use the configured bundle
store; pass `--store-dir` to use another one.

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

