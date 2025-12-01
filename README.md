Audio transcription and summary script for my personal Obsidian Vault. 

# Requirements
Requires ffmpeg in path.

# Installation

```bash
pipenv install
```

# Usage

See usage with:  
```bash
pipenv run python transcribe.py --help

Or:

pipenv shell
python transcribe.py --help
```

## Obsidian shell command example

```
$inputDir = Join-Path (Get-Location).Path "Transcription/attachments"
$storeDir = Join-Path (Get-Location).Path "Transcription/Output"
cd _/transcribe
Start-Process powershell -ArgumentList "-Command `"pipenv run python transcribe.py '$inputDir' --store '$storeDir'; Read-Host 'Press Enter to exit'`" "
```

# Improve me

- Look into summary strategies? Currently it's quite unspecific
- Try grouping records being done very close to each other. Group them as a single bundle, and process them accordingly. 
- Add a "do not delete" property checkbox
  - Extra swag if it can be auto enabled if I mention it in the record
- Do a "remove empty audio" pass
- refine the prompt, what am I expecting from those summaries?
  -  Maybe provide context such as a list of topics we care about
- Vocal commands with start - end keywords. Like "Start command" & "Cancel command" & "Validate command".
  - Commands are defined with a name & a description and LLM is tasked to match to a command
  - Command ideas: "Append to last record". "Delete this record". "Do not delete this record"
