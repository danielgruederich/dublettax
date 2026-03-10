#!/usr/bin/env python3
"""
Create Rekordbox playlists from a DublettaX dedup report.

Usage:
    # Write directly to Rekordbox database (close Rekordbox first):
    python3 rekordbox_playlists.py <dedup_report.json>

    # Export a rekordbox XML file (import via File > Import > rekordbox xml):
    python3 rekordbox_playlists.py <dedup_report.json> --xml [output.xml]

Options:
    --wip <path>       WIP source folder (default: ~/Music/WIP)
    --contents <path>  Final Contents folder (default: /Volumes/ssdMonschi/Contents)

Creates a "DublettaX" playlist folder with three playlists:
  - Keep Library   : library tracks where library version is better
  - Keep Source    : source tracks remapped to their final Contents path
  - Review         : library tracks needing manual review
"""

import json
import sys
from pathlib import Path


FOLDER_NAME = "DublettaX"
PLAYLISTS = {
    "keep_library": "Keep Library",
    "keep_source":  "Keep Source",
    "review":       "Review",
}

DEFAULT_WIP      = str(Path.home() / "Music" / "WIP")
DEFAULT_CONTENTS = "/Volumes/ssdMonschi/Contents"


def load_report(report_path: Path) -> list[dict]:
    with open(report_path) as f:
        return json.load(f)


def remap_source_path(source_path: str, wip_root: Path, contents_root: Path) -> str:
    """Swap WIP directory for Contents directory, keeping only the filename."""
    return str(contents_root / Path(source_path).name)


def group_by_recommendation(
    results: list[dict], wip_root: Path, contents_root: Path
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"keep_library": [], "keep_source": [], "review": []}
    for entry in results:
        rec = entry.get("recommendation", "review")
        if rec not in groups:
            rec = "review"
        if rec == "keep_source":
            # Source track is the keeper — remap to its final Contents path
            path = remap_source_path(entry.get("source", ""), wip_root, contents_root)
        else:
            # Library track is already in Contents
            path = entry.get("library", "")
        groups[rec].append(path)
    return groups


# ── Database method ────────────────────────────────────────────────────────────

def build_path_index(db) -> dict[str, object]:
    index = {}
    for track in db.get_content():
        if track.FolderPath:
            index[track.FolderPath] = track
    return index


def get_or_create_folder(db, name: str):
    for pl in db.get_playlist():
        if pl.Name == name and pl.Attribute == 1:
            return pl
    return db.create_playlist_folder(name)


def get_or_create_playlist(db, name: str, parent):
    parent_id = parent.ID if hasattr(parent, "ID") else parent
    for pl in db.get_playlist():
        if pl.Name == name and pl.Attribute == 0 and pl.ParentID == str(parent_id):
            return pl
    return db.create_playlist(name, parent=parent)


def clear_playlist(db, playlist):
    for song in db.get_playlist_songs(playlist):
        db.remove_from_playlist(playlist, song.ContentID)


def run_db(report_path: Path, wip_root: Path, contents_root: Path):
    from pyrekordbox import Rekordbox6Database

    print(f"Loading report: {report_path}")
    results = load_report(report_path)
    print(f"  {len(results)} entries found")

    print("Opening Rekordbox database...")
    db = Rekordbox6Database()

    print("Indexing Rekordbox library by file path...")
    path_index = build_path_index(db)
    print(f"  {len(path_index)} tracks indexed")

    groups = group_by_recommendation(results, wip_root, contents_root)
    not_found = []

    print(f"\nCreating playlist folder '{FOLDER_NAME}'...")
    folder = get_or_create_folder(db, FOLDER_NAME)

    for rec_key, playlist_name in PLAYLISTS.items():
        paths = groups[rec_key]
        print(f"\nPlaylist '{playlist_name}': {len(paths)} tracks")
        playlist = get_or_create_playlist(db, playlist_name, folder)
        clear_playlist(db, playlist)

        added = 0
        for path in paths:
            track = path_index.get(path)
            if track:
                db.add_to_playlist(playlist, track)
                added += 1
            else:
                not_found.append(path)

        print(f"  Added {added} tracks ({len(paths) - added} not found in Rekordbox)")

    db.save()
    print(f"\nDone! Playlists saved to Rekordbox database.")
    _print_not_found(not_found)


# ── XML method ─────────────────────────────────────────────────────────────────

def run_xml(report_path: Path, output_path: Path, wip_root: Path, contents_root: Path):
    from pyrekordbox.rbxml import RekordboxXml

    print(f"Loading report: {report_path}")
    results = load_report(report_path)
    print(f"  {len(results)} entries found")

    groups = group_by_recommendation(results, wip_root, contents_root)

    # Collect all unique library paths
    all_paths = list(dict.fromkeys(
        p for paths in groups.values() for p in paths if p
    ))

    print(f"\nBuilding XML with {len(all_paths)} tracks...")
    xml = RekordboxXml()

    # Add all tracks to the collection and build path → TrackID map
    path_to_id: dict[str, int] = {}
    for path in all_paths:
        try:
            track = xml.add_track(location=path)
            path_to_id[path] = track.TrackID
        except Exception as e:
            print(f"  Warning: could not add {path}: {e}")

    # Create folder and playlists
    folder = xml.add_playlist_folder(FOLDER_NAME)

    for rec_key, playlist_name in PLAYLISTS.items():
        paths = groups[rec_key]
        playlist = folder.add_playlist(playlist_name)

        added = 0
        for path in paths:
            track_id = path_to_id.get(path)
            if track_id is not None:
                playlist.add_track(track_id)
                added += 1

        print(f"Playlist '{playlist_name}': {added}/{len(paths)} tracks added")

    xml.save(output_path)
    print(f"\nDone! XML saved to: {output_path}")
    print("Import in Rekordbox via: File > Import Playlist > rekordbox xml")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _print_not_found(not_found: list[str]):
    if not not_found:
        return
    missing = list(dict.fromkeys(not_found))
    print(f"\n{len(missing)} tracks not found in Rekordbox (not imported yet):")
    for p in missing[:10]:
        print(f"  {p}")
    if len(missing) > 10:
        print(f"  ... and {len(missing) - 10} more")


# ── Entry point ────────────────────────────────────────────────────────────────

def _pop_arg(args: list[str], flag: str, default: str) -> tuple[str, list[str]]:
    if flag in args:
        i = args.index(flag)
        value = args[i + 1]
        args = args[:i] + args[i + 2:]
        return value, args
    return default, args


def main():
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    use_xml = "--xml" in args
    if use_xml:
        args.remove("--xml")

    wip_str,      args = _pop_arg(args, "--wip",      DEFAULT_WIP)
    contents_str, args = _pop_arg(args, "--contents", DEFAULT_CONTENTS)
    wip_root      = Path(wip_str).expanduser()
    contents_root = Path(contents_str).expanduser()

    if not args:
        print("Error: missing report path.")
        sys.exit(1)

    report_path = Path(args[0])
    if not report_path.exists():
        print(f"Report not found: {report_path}")
        sys.exit(1)

    print(f"WIP root:      {wip_root}")
    print(f"Contents root: {contents_root}")

    if use_xml:
        output_path = Path(args[1]) if len(args) > 1 else report_path.with_suffix(".xml")
        run_xml(report_path, output_path, wip_root, contents_root)
    else:
        run_db(report_path, wip_root, contents_root)


if __name__ == "__main__":
    main()
