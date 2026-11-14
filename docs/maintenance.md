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

## Archive old audio outside the store

Move the audio of transcribed bundles to another directory, for example to keep a synchronised store small. Only the audio moves; the transcript, summary and other files stay in the bundle.

Unlike the utilities above, this one is an installed command, meant to run unattended every day from a scheduler. Install it, from the repository root, into its own environment, so that what runs does not change with the working copy:

```bash
uv tool install .
```

After updating the repository, run `uv tool install --reinstall .` to update the installed command. It takes everything it needs as arguments and reads none of the transcriber's settings or API keys. The time zone is the transcriber's `general.timezone`. Preview first:

```bash
transcriber-archive-audio --store-dir /path/to/store --archive-dir /path/to/archive --timezone Europe/Brussels
```

Then archive:

```bash
transcriber-archive-audio --store-dir /path/to/store --archive-dir /path/to/archive --timezone Europe/Brussels --apply
```

`--log-file PATH` appends each run's report to that file, under a line giving the run's time, mode and exit status. The exit status says how the run went, for the scheduler or a wrapper around it to report: 0 done, 1 some bundles failed (or the command crashed), 2 nothing done because the arguments are unusable. A daily run with nothing old enough to move is cheap and exits with 0.

To run it daily, call the installed command with `--apply` and `--log-file` from the system's scheduler: a systemd service with a timer on Linux (`Persistent=true` catches up after the machine was off), or a Windows Task Scheduler task set to run as soon as possible after a missed start. Warning someone when a run fails or does not happen is the scheduler's or a monitoring tool's job, not this command's.

A bundle's audio is archived when it has a transcript, is not marked `keep_forever`, has had its voice commands extracted and none is still being processed, holds no `_merge_failed.md` and no audio its metadata does not list, and is more than `--older-than-days` whole days old (default 30). The age is counted from the most recent of the bundle's date and its audio files' modification times. A bundle keeps its transcript when some of its listed audio is missing; only audio its metadata does not list makes the transcriber redo it. The transcriber itself never deletes audio: archiving is the only way audio leaves the store, apart from deleting a whole bundle with a voice command.

Each bundle's audio goes to `<archive-dir>/<bundle directory name>/` (with ` ~<first 8 characters of the bundle ID>` added if another bundle already used that name), next to an `archive.json` listing each file's size and SHA-256. Every file is copied, compared with the original, and only then removed from the store, provided it is still the version that was copied: a newer version put in place meanwhile (by a sync tool, for example) is left in the store and reported. The bundle's `_archived_audio.md` lists every archive folder holding its audio, with the same file lists; archiving the bundle again adds to it rather than replacing it.

With `--apply`, each bundle is checked again right before its audio moves, so a bundle marked `keep_forever` or given a voice command while others are being copied keeps its audio. A bundle that fails is reported on an `[error]` line followed by its next step, and the others go on; the command then exits with status 1. A full archive drive stops the run at the first bundle that does not fit. When the arguments are unusable (for example an archive directory inside the store, or an unknown time zone), the command does nothing and exits with status 2. Running the command again is always safe: the bundle's archive folder is recognised, a file already copied is reused, and a file in the archive with different content is reported with both copies left as they are.

To bring audio back, copy the files listed in `_archived_audio.md` into the bundle directory. A bundle that other bundles were merged into can also hold `_archived_audio_1.md` and so on, one for each of them. Archived audio stays listed in the bundle's `_metadata.md`, through merges too, so a copy brought back counts as the bundle's own audio. Check that each file you bring back is listed under `audio_files` there, and add it if not: audio the metadata does not list makes the transcriber redo the bundle's transcript. Archived audio cannot be transcribed again with a new model until it is brought back.

Bundles holding several recordings are left out unless you add `--include-multi-audio`. Add it only once every transcriber reading this store keeps a bundle's transcript when some of its listed audio is missing. Older versions redo such a bundle from the audio that is left, which can happen when the store is synchronised to another machine and the removal arrives there one file at a time.

## List bundles by transcript length

List longest transcripts first:

```bash
uv run python scripts/list_bundles_by_transcript_length.py
```

List shortest first:

```bash
uv run python scripts/list_bundles_by_transcript_length.py --ascending
```
