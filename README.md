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

- Split transcript and summary in different files
  - On re run, the existence of those files can be checked and if they're existing, we assume they're good
  - We'll need some kind of "job" concept here to only run some of the tasks depending on what was successfull
  - The goal is to be able to re run the script and it will resume whatever was failed

- Try grouping records being done very close to each other. Group them as a single result file basically.
- Do a "remove empty audio" pass
- Use IA to name those recordings too, based on their content ?
- (?) Move everything to a "Transcription" subdir, so that we can have our own structure inside and not depends on attachements
- Change arguments? So we'd be given an input directory and the output where we place our results

Future improvements:
Maybe a way to provide context to the AI, such as a list of topics we care about, so that it can focus on those ? 
get a list of recents notes titles or tasks to give to it.
