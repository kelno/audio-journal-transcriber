Audio transcription and summary script for my personal Obsidian Vault. 

# Requirements
Requires `uv` package manager.  
Requires `ffmpeg` in path.

# Installation

```bash
uv sync
```

# Configuration

The application can be configured via config files or env variables. 
Most configuration values must be provided, see `config.default.toml` to check which ones are set by default.  

Configuration will be merged in the order of priority:  
- Environment variables (prefixed with `TRANSCRIBER_`)
- `config.custom.toml`: From working directory, overrides specific values.
- `config.default.toml`: From this repository. Provides base values. 

For example you can create a `config.custom.toml` with only the keys you want to change:  

```toml
[text]
api_key = "sk-xxxx"

[audio]
api_base_url = "http://localhost:8000/v1"
stream = true
```

## Environment variables format

Some examples:
```
TRANSCRIBER_GENERAL__DELETE_SOURCE_AUDIO_AFTER_DAYS=30
TRANSCRIBER_TEXT__API_KEY=sk-xxxx
```


# CLI usage

See usage with:  
```bash
uv run transcriber --help
# or when in venv
transcriber --help
```


# Docker / Podman

## Build

```bash
# Get the current git tag
$version = git describe --tags --abbrev=0

# Build the image
docker build --build-arg VERSION=$version -t audio-journal-transcriber:$version .
```

## Configuration

See "Configuration" above.  
The image working directory is `/app` if using the config file, mount it at `/app/config.custom.toml` 

## Examples

CHANGE ME: Configure input and store in config
LATER CHANGE ME: Make this app a file watch thingy that will update periodically?

```bash
# Using env variables 
docker run -v /path/to/input:/data/input \
           -v /path/to/store:/data/store \
           -e TRANSCRIBER_GENERAL__DELETE_SOURCE_AUDIO_AFTER_DAYS=30 \
           -e TRANSCRIBER_GENERAL__MIN_LENGTH_SECONDS=10.0 \
           -e TRANSCRIBER_TEXT__SUMMARY_ENABLED=true \
           -e TRANSCRIBER_TEXT__API_BASE_URL=https://api.openai.com/v1/ \
           -e TRANSCRIBER_TEXT__MODEL=gpt-4o-mini \
           -e TRANSCRIBER_TEXT__API_KEY=sk-xxxx \
           -e TRANSCRIBER_AUDIO__API_BASE_URL=https://api.openai.com/v1/ \
           -e TRANSCRIBER_AUDIO__MODEL=whisper-1 \
           -e TRANSCRIBER_AUDIO__API_KEY=sk-xxxx \
           audio-journal-transcriber:latest

# Using config file

docker run -v /path/to/input:/data/input \
           -v /path/to/store:/data/store \
           -v /path/to/config.toml:/app/config.custom.toml \
           -e TRANSCRIBER_GENERAL__INPUT_DIR=/data/input \
           -e TRANSCRIBER_GENERAL__STORE_DIR=/data/store \
           audio-journal-transcriber:latest

# (Or combine both as needed)
```

# Improve me

- Do a "remove empty audio" pass
- Cut audio when above 20min, to pass to the transcription
- Refining the prompt:
  - Look into summary strategies, currently it's free and unspecific
  - Be more explicit about what am I expecting from those summaries?
  - Maybe provide context such as a list of topics we care about. About how we use those records.
- The big vocal commands feature
  - Trigger it with specific start - end keywords. Like "Start command" & "Cancel command" & "Validate command".
  - Make those configurable?
  - Command ideas: 
    - Append to last record
    - Delete this record
    - Never delete this record / Keep forever
  - Commands are defined with a name & a description and LLM is tasked to match the text between the keywords to a command. 

## Obsidian PowerShell command example

```powershell
cd _/transcribe
Start-Process powershell -ArgumentList "-Command `"uv run transcriber; Read-Host 'Press Enter to exit'`" "
```
