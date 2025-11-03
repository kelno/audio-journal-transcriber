Audio transcription and summary script for my personal Obsidian Vault. 

# Installation

```bash
pipenv install
```

# Usage

```bash
pipenv run python transcribe.py --help
```

## Obsidian shell command example

```
$obsidianRoot = (Get-Location).Path; cd _/transcribe; Start-Process powershell -ArgumentList "-Command `"pipenv run python transcribe.py $obsidianRoot; Read-Host 'Press Enter to exit'`" "
```
