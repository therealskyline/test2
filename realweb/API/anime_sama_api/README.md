# Anime-Sama API
An API for anime-sama.fr, also provides a CLI to download videos.

I have implemented all the features I care about. This project is now in maintenance mode. 

# Installation
Requirements:
- Python 3.10 or higher

You can simply install it with (note that you can use tools like pipx to isolate the installation):
```bash
pip install anime-sama-api[cli]
```
And to run it:
```bash
anime-sama
```

## Configuration
You can customize the config at `~/.config/anime-sama_cli/config.toml` for macOS/Linux and at `%USER%/AppData/Local/anime-sama_cli/config.toml` for Windows.

# For developers
## Requirements
- git
- [uv](https://docs.astral.sh/uv/#installation)

## Install locally
```bash
git clone https://github.com/Sky-NiniKo/anime-sama_downloader.git
cd anime-sama_downloader
uv sync --extra cli
```

## Run
```bash
uv run anime-sama
```

## Update
In the `anime_sama` folder:
```bash
git pull
```

## Contribution
I am open to contribution. Please only open a PR for ONE change. AKA, don't do "Various improvements" and explain your motivation behind your improvement ("Various typos fix"/"Cleanup" is fine).
