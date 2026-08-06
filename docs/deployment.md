# Container deployment

The provided multi-stage container build installs the project with uv, includes FFmpeg, and runs the transcriber continuously by default.

## Build with Docker

Choose an image version and build from the repository root:

```bash
docker build \
  --build-arg VERSION=0.7.0 \
  -t audio-journal-transcriber:0.7.0 \
  -t audio-journal-transcriber:latest \
  .
```

## Build with Podman

The equivalent Podman build is:

```bash
podman build \
  --build-arg VERSION=0.7.0 \
  -t audio-journal-transcriber:0.7.0 \
  -t audio-journal-transcriber:latest \
  .
```

Replace `0.7.0` with the version being built.

## Run with a configuration file

Create a host `config.custom.toml` based on [the example](config.custom.toml.example). Container paths should use `/data/input` and `/data/store`:

```toml
[general]
input_dir = "/data/input"
store_dir = "/data/store"
```

Then mount the configuration and data directories:

```bash
docker run --rm \
  --name audio-journal-transcriber \
  -v /host/audio-inbox:/data/input \
  -v /host/audio-journal:/data/store \
  -v /host/config.custom.toml:/app/config.custom.toml:ro \
  audio-journal-transcriber:latest
```

Use `podman run` with the same arguments when running under Podman.

The image entry point is `transcriber`, whose default mode watches for new recordings until it is stopped. `SIGTERM` and `SIGINT` request a clean shutdown.

The first HTTP API version binds to loopback only. Inside a container, that means it is not reachable from the host or other containers, even if the port is published. Container HTTP exposure remains deferred until the API has authentication and permits a non-loopback bind address.

## Override values with environment variables

Environment variables take precedence over the mounted TOML file. For example:

```bash
docker run --rm \
  --name audio-journal-transcriber \
  -v /host/audio-inbox:/data/input \
  -v /host/audio-journal:/data/store \
  -v /host/config.custom.toml:/app/config.custom.toml:ro \
  -e TRANSCRIBER_GENERAL__INPUT_DIR=/data/input \
  -e TRANSCRIBER_GENERAL__STORE_DIR=/data/store \
  -e TRANSCRIBER_TEXT__API_KEY=replace-me \
  -e TRANSCRIBER_AUDIO__API_KEY=replace-me \
  audio-journal-transcriber:latest
```

See [Configuration](configuration.md) for the complete environment-variable format.

## Run once instead of watching

Pass `--once` to process pending work and exit:

```bash
docker run --rm \
  -v /host/audio-inbox:/data/input \
  -v /host/audio-journal:/data/store \
  -v /host/config.custom.toml:/app/config.custom.toml:ro \
  audio-journal-transcriber:latest --once
```

Use `--once --dry-run` after the image name to preview processing without writing bundle changes.
