Audio transcription and summary script for my personal Obsidian Vault. 

# Installation

```bash
pipenv install
```

# Usage

```bash
pipenv run python transcribe.py --help

Or:

pipenv shell
python transcribe.py --help
```

## Obsidian shell command example

```
$obsidianRoot = (Get-Location).Path; cd _/transcribe; Start-Process powershell -ArgumentList "-Command `"pipenv run python transcribe.py $obsidianRoot; Read-Host 'Press Enter to exit'`" "
```

# Improve me

- Min record length
- Try grouping records being done very close to each other. Group them as a single bundle, and process them accordingly. 
- Add a "do not delete" property checkbox
  - Extra swag if it can be auto enabled if I mention it in the record
- Remove links to records from the Obsidian record document
- Do a "remove empty audio" pass
- Change cli arguments? So we'd be given an input directory and the output where we place our results
- refine the prompt, what am I expecting from those summaries?
  -  Maybe provide context such as a list of topics we care about
  -  Maybe some standard ways to interact with it like a key phrase to tell it to create a task in my vault. 
