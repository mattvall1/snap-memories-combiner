# Snapchat Memories Combiner

**This script does not download anything! It just processes your files**

This script combines the "-main" files with their respective "-overlay" files you get from exporting your memories from Snapchat.

## Requirements
- `python >= 3.10` or `uv`
- `ffmpeg`

## Usage
Extract the .zip file(s) you got from Snapchat. There should be a `memories/` folder in each of them (Sometimes you get more than one zip).

Move all of the images and videos into a single `memories/` folder in the same directory as the `main.py` file.

Open a terminal in the same directory and run the script with `uv run main.py`. (If you don't have uv, install it from [here](https://docs.astral.sh/uv/getting-started/installation/))

All output files will be placed in an `out/` folder. Files with overlays will be combined with their respective overlays and the combined version will be placed in `out/`. Base files that have no overlay are skipped by default.

Pass `--check-previous` to skip files that already have a combined/copied output in `out/` (useful for re-running on a `memories/` folder that has new files mixed in with ones you've already processed).

Pass `--include-bases` to copy base files that have no overlay into `out/` instead of skipping them.

Pass `--videos-only` to skip images and only process video files.
