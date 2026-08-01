# Example setup: sync voice recordings from a phone

This is one example of a hands-off capture workflow. It combines a phone, a file synchronization tool, and daemon mode:

```text
Phone recorder → synchronized input directory → transcriber daemon → Markdown archive
```

## 1. Record into a syncable directory

Use any recorder that can save files in a directory accessible to your synchronization tool. On Android, this could be an application such as [Fossify Voice Recorder](https://github.com/FossifyOrg/Voice-Recorder).

An example recording directory is:

```text
/storage/emulated/0/Android/media/voice_recordings/
```

## 2. Synchronize the recordings

Synchronize the phone directory to the computer or server that runs the transcriber. [Syncthing](https://syncthing.net/) is one option, but any tool that writes completed files into the configured input directory can be used.

Configure the receiving directory as `general.input_dir` in `config.custom.toml`.

The transcriber waits briefly after filesystem activity so that a file can settle before processing. Even so, the synchronization tool should preferably use temporary names or atomic renames while transferring incomplete files.

## 3. Run the watcher

Start the transcriber in daemon mode:

```bash
uv run transcriber --daemon
```

The daemon processes existing recordings at startup, watches for later filesystem activity, retries failed bundles with increasing delays, and performs an hourly fallback scan.

## Permissions

The transcriber moves recordings out of the input directory and writes bundles to the store directory. Its operating-system user therefore needs read, write, move, and delete permissions in both locations.

When Syncthing or another service owns the synchronized files, running both services as the same user is the simplest arrangement.

## Browsing the bundles store

The generated transcript, summary, and context files can be browsed with any tool that reads Markdown. One convenient option is to point `general.store_dir` to a directory inside an Obsidian vault.
