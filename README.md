Audio transcription and summary script for my personal Obsidian Vault. 

# Requirements
Requires ffmpeg in path.

# Installation

```bash
uv sync
```

# Configuration

The application can be configured via config files or env variables. 
Most configuration values must be provided, see `config.default.toml` to check which ones are set by default.  

Configuration will be merged in the order of priority:  
- Environment variables (prefixed with `TRANSCRIBER_`)
- `config.custom.toml`:  From working directory, overrides specific values.
- `config.default.toml`: From this repository. Provides base values. 

For example you can create a `config.custom.toml` with only the keys you want to change, example:

```toml
[text]
api_key = "sk-xxxx"

[audio]
api_base_url = "http://localhost:8000/v1"
stream = true
```

#### Option B: With environment variable

Pass values as environment variables, example:  
`TRANSCRIBER_GENERAL__DELETE_SOURCE_AUDIO_AFTER_DAYS=30`

(Environment variables take precedence over the config files.)


# Usage

See usage with:  
```bash
uv run transcriber --help
```


## Obsidian PowerShell command example

```powershell
cd _/transcribe
Start-Process powershell -ArgumentList "-Command `"uv run transcriber; Read-Host 'Press Enter to exit'`" "
```
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
