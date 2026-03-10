# DublettaX

A macOS desktop app for DJ library management. Scans a WIP folder against your main library, detects duplicates, compares quality, and helps you move clean tracks to your final library with Rekordbox playlist export.

---

## Features

- **Duplicate detection** — matches tracks by filename and audio fingerprint
- **Quality comparison** — ranks by format (FLAC/AIFF > WAV > MP3), bitrate, and version type (Extended > Original)
- **Mini player** — preview source and library versions side by side before deciding
- **WIP monitor** — watches a folder for new files and auto-scans in the background
- **Quick check** — instantly search a single file against your library
- **Rename tool** — strips track numbers from filenames with safe/risky detection
- **Move to Contents** — moves WIP audio files flat to your library folder, preserving subfolder names in the Review folder
- **Rekordbox export** — creates a `DublettaX` playlist folder with three playlists:
  - **Keep Library** — library version is better
  - **Keep Source** — WIP version is better (mapped to final library path)
  - **Review** — needs manual decision

---

## Workflow

1. Set your **WIP** and **Library** folders in the top bar
2. Run **Duplicate Finder** to scan WIP against your library
3. Review results — click to preview tracks in the mini player
4. Click **Move Inferior Tracks to Review Folder** to clear out worse versions
   - Files inside WIP subfolders are grouped in `FolderName_1/` in the Review folder
5. Click **Move to Contents & Export Playlist** to move clean tracks to the library and generate a Rekordbox XML

Import the XML in Rekordbox via **File > Import Playlist > rekordbox xml**.

---

## Requirements

- macOS
- Python 3.10+
- `fpcalc` (Chromaprint) for audio fingerprinting — `brew install chromaprint`

### Python dependencies

```bash
pip install mutagen watchdog tkinterdnd2 pyrekordbox pygame
```

---

## Usage

```bash
python3 gui.py
```

### Rekordbox playlist script (CLI)

```bash
# Write directly to Rekordbox database (close Rekordbox first):
python3 rekordbox_playlists.py dedup_report.json

# Export XML (Rekordbox can stay open):
python3 rekordbox_playlists.py dedup_report.json --xml

# Custom paths:
python3 rekordbox_playlists.py dedup_report.json --xml \
  --wip ~/Music/WIP \
  --contents /Volumes/ssdMonschi/Contents
```

---

## Default paths

| Setting | Default |
|---|---|
| WIP folder | `~/Music/WIP` |
| Library folder | `/Volumes/ssdMonschi/Contents` |
| Review folder | `~/Music/Dedup_Review` |

These can be changed in the app's top bar.
