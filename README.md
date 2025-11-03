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

- Those file can be Obsidian notes and contain metadata on top such as the model used, the original file name, date of processing
- Also we can store a "status" there to indicate success/failure and know what to retry, separate for audio & summary
- Try grouping records being done very close to each other. Group them as a single result file basically.
- Do a "remove empty audio" pass
- Maybe use IA to name those recordings too, based on their content ?
- (?) Move everything to a "Transcription" subdir, so that we can have our own structure inside and not depends on attachements
- Change arguments? So we'd be given an input directory and the output where we place our results

Future improvements:
Maybe a way to provide context to the AI, such as a list of topics we care about, so that it can focus on those ? 
get a list of recents notes titles or tasks to give to it.
