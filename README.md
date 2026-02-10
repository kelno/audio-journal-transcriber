Audio transcription and summary script for my personal Obsidian Vault. 

# Requirements
Requires ffmpeg in path.

# Installation

```bash
uv sync
```

# Usage

See usage with:  
```bash
uv run transcriber --help
```

## Obsidian PowerShell command example

```powershell
$inputDir = Join-Path (Get-Location).Path "Transcription/attachments"
$storeDir = Join-Path (Get-Location).Path "Transcription/Output"
cd _/transcribe
Start-Process powershell -ArgumentList "-Command `"uv run transcriber '$inputDir' --store '$storeDir'; Read-Host 'Press Enter to exit'`" "
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
