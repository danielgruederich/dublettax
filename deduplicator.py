#!/usr/bin/env python3
"""
DublettaX
Compares a source folder against the Contents library.
Detects duplicates by filename matching and audio fingerprinting.
"""

import os
import re
import json
import hashlib
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import mutagen
from mutagen.mp3 import MP3
from mutagen.flac import FLAC
from mutagen.wave import WAVE
from mutagen.aiff import AIFF

# ── Quality ranking (higher = better) ──────────────────────────────────────
QUALITY_RANK = {
    ".flac": 4,
    ".aiff": 4,
    ".aif":  4,
    ".wav":  3,
    ".mp3":  2,
    ".m4a":  2,
    ".ogg":  1,
}

AUDIO_EXTENSIONS = set(QUALITY_RANK.keys())

# ── Regex to strip track numbers from filenames ─────────────────────────────
TRACK_NUMBER_RE = re.compile(
    r"^(?:\d{1,3}[\s\.\-_]+)+",  # leading numbers like "01 - " or "1."
)


def rename_risk(original_stem: str, new_stem: str) -> str | None:
    """
    Return a warning string if the rename looks risky, else None.

    Risky cases:
    - Result starts with a lowercase letter (number was likely part of the name,
      e.g. "2 Unlimited", "10cc", "50 Cent")
    - Result is empty or too short (< 3 chars)
    - The stripped prefix looks like it's part of the artist/title rather than
      a track index (single digit + space before a lowercase word)
    """
    if not new_stem:
        return "Result would be empty"

    if len(new_stem) < 3:
        return f"Result is very short: '{new_stem}'"

    # Starts with lowercase → number was probably part of artist/title
    if new_stem[0].islower():
        return f"Result starts lowercase — '{original_stem}' may not have a track number"

    # Single digit followed by space and lowercase = likely "2 unlimited", "4 to the floor"
    single_digit_match = re.match(r"^(\d)\s+([a-z])", original_stem)
    if single_digit_match:
        return f"Single digit before lowercase word — '{original_stem}' may be an artist/title starting with a number"

    return None


def proposed_renames(folder: Path) -> list[tuple[Path, Path, str | None]]:
    """
    Return list of (original_path, new_path, warning) for files whose names
    start with track numbers. warning is None if safe, a string if risky.
    Does NOT rename anything — preview only.
    """
    renames = []
    for f in sorted(folder.rglob("*")):
        if not f.is_file():
            continue
        if f.name.startswith("._") or f.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        new_stem = TRACK_NUMBER_RE.sub("", f.stem).strip()
        if new_stem and new_stem != f.stem:
            new_path = f.with_name(new_stem + f.suffix)
            warning  = rename_risk(f.stem, new_stem)
            renames.append((f, new_path, warning))
    return renames


def apply_renames(renames: list[tuple[Path, Path, str | None]], skip_warnings: bool = False) -> list[tuple[Path, Path]]:
    """
    Actually rename files. Skips if destination already exists.
    If skip_warnings=True, skips any rename flagged with a warning.
    Returns list of (old, new) that were successfully renamed.
    """
    done = []
    for old, new, warning in renames:
        if warning and skip_warnings:
            continue
        if new.exists():
            continue
        try:
            old.rename(new)
            done.append((old, new))
        except Exception:
            pass
    return done

VERSION_KEYWORDS = {
    "extended": ["extended mix", "extended version", "extended edit", "ext mix", "ext. mix"],
    "original":  ["original mix", "original version", "original edit"],
    "remix":     ["remix", "rmx", "re-edit", "edit", "mashup", "bootleg", "rework", "flip"],
}


@dataclass
class Track:
    path: Path
    stem_normalized: str       # filename without extension and track numbers
    ext: str                   # lowercase extension
    quality_rank: int
    bitrate: Optional[int]     # kbps
    duration: Optional[float]  # seconds
    version_type: str          # "extended", "original", "remix", "unknown"
    fingerprint: Optional[str] = None
    acoustid: Optional[str] = None


def normalize_stem(stem: str) -> str:
    """Remove track numbers and normalize whitespace/case."""
    stem = TRACK_NUMBER_RE.sub("", stem)
    stem = re.sub(r"\s+", " ", stem).strip().lower()
    return stem


def detect_version_type(stem: str) -> str:
    stem_lower = stem.lower()
    for vtype, keywords in VERSION_KEYWORDS.items():
        for kw in keywords:
            if kw in stem_lower:
                return vtype
    return "unknown"


def get_bitrate(path: Path) -> Optional[int]:
    try:
        ext = path.suffix.lower()
        if ext == ".mp3":
            audio = MP3(path)
            return int(audio.info.bitrate / 1000)
        elif ext in (".flac",):
            audio = FLAC(path)
            return None  # lossless — no bitrate comparison needed
        elif ext in (".wav",):
            audio = WAVE(path)
            return None
        elif ext in (".aiff", ".aif"):
            audio = AIFF(path)
            return None
    except Exception:
        pass
    return None


def get_duration(path: Path) -> Optional[float]:
    try:
        audio = mutagen.File(path)
        if audio and hasattr(audio, "info"):
            return audio.info.length
    except Exception:
        pass
    return None


def fingerprint_track(path: Path, segments: int = 3) -> Optional[str]:
    """
    Fingerprint multiple segments of a track and combine.
    segments=3 → beginning, middle, end (each 60s)
    """
    try:
        duration = get_duration(path)
        if not duration:
            return None

        fps = []
        # Define segment start times
        positions = [0]
        if duration > 120:
            positions.append(duration / 2 - 30)
        if duration > 180:
            positions.append(duration - 90)

        for pos in positions:
            result = subprocess.run(
                ["fpcalc", "-length", "60", "-offset", str(int(pos)), str(path)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    if line.startswith("FINGERPRINT="):
                        fps.append(line.split("=", 1)[1])
                        break

        if fps:
            return "|".join(fps)
    except Exception:
        pass
    return None


def fingerprint_similarity(fp1: str, fp2: str) -> float:
    """
    Compare two multi-segment fingerprints.
    Returns similarity score 0.0 - 1.0.
    """
    if not fp1 or not fp2:
        return 0.0

    segs1 = fp1.split("|")
    segs2 = fp2.split("|")

    scores = []
    for s1, s2 in zip(segs1, segs2):
        # Compare fingerprint strings as bit arrays
        min_len = min(len(s1), len(s2))
        if min_len == 0:
            continue
        matches = sum(c1 == c2 for c1, c2 in zip(s1[:min_len], s2[:min_len]))
        scores.append(matches / min_len)

    return sum(scores) / len(scores) if scores else 0.0


def scan_folder(folder: Path, with_fingerprint: bool = False, progress_cb=None) -> list[Track]:
    """Scan a folder and return list of Track objects."""
    tracks = []
    files = [f for f in folder.rglob("*") if f.suffix.lower() in AUDIO_EXTENSIONS and not f.name.startswith("._")]

    for i, f in enumerate(files):
        stem = normalize_stem(f.stem)
        ext = f.suffix.lower()
        rank = QUALITY_RANK.get(ext, 0)
        bitrate = get_bitrate(f)
        duration = get_duration(f)
        version = detect_version_type(f.stem)

        fp = None
        if with_fingerprint:
            fp = fingerprint_track(f)

        tracks.append(Track(
            path=f,
            stem_normalized=stem,
            ext=ext,
            quality_rank=rank,
            bitrate=bitrate,
            duration=duration,
            version_type=version,
            fingerprint=fp,
        ))

        if progress_cb:
            progress_cb(i + 1, len(files), str(f.name))

    return tracks


@dataclass
class DuplicateResult:
    source_track: Track
    library_track: Track
    match_type: str          # "exact_name", "fingerprint", "possible"
    similarity: float        # 0.0 - 1.0
    recommendation: str      # "keep_source", "keep_library", "review"
    reason: str


def compare_quality(source: Track, library: Track) -> tuple[str, str]:
    """Returns (recommendation, reason)."""
    # Compare format quality
    if source.quality_rank > library.quality_rank:
        return "keep_source", f"Better format: {source.ext} > {library.ext}"
    if source.quality_rank < library.quality_rank:
        return "keep_library", f"Library has better format: {library.ext} > {source.ext}"

    # Same format — compare bitrate (MP3 only)
    if source.bitrate and library.bitrate:
        if source.bitrate > library.bitrate + 16:
            return "keep_source", f"Higher bitrate: {source.bitrate} > {library.bitrate} kbps"
        if library.bitrate > source.bitrate + 16:
            return "keep_library", f"Library has higher bitrate: {library.bitrate} > {source.bitrate} kbps"

    # Same quality — prefer extended over original (not for remixes)
    if source.version_type not in ("remix",) and library.version_type not in ("remix",):
        if source.version_type == "extended" and library.version_type == "original":
            return "keep_source", "Extended version preferred over Original"
        if library.version_type == "extended" and source.version_type == "original":
            return "keep_library", "Library has Extended version, source has Original"

    return "review", "Same quality — manual review recommended"


def find_duplicates(
    source_tracks: list[Track],
    library_tracks: list[Track],
    fingerprint_threshold: float = 0.85,
    progress_cb=None,
) -> list[DuplicateResult]:
    """Compare source tracks against library tracks."""

    # Build lookup dict from library
    library_by_name: dict[str, list[Track]] = {}
    for t in library_tracks:
        library_by_name.setdefault(t.stem_normalized, []).append(t)

    results = []

    for i, source in enumerate(source_tracks):
        if progress_cb:
            progress_cb(i + 1, len(source_tracks), source.path.name)

        # Step 1: exact name match
        name_matches = library_by_name.get(source.stem_normalized, [])
        if name_matches:
            for lib_match in name_matches:
                rec, reason = compare_quality(source, lib_match)
                results.append(DuplicateResult(
                    source_track=source,
                    library_track=lib_match,
                    match_type="exact_name",
                    similarity=1.0,
                    recommendation=rec,
                    reason=reason,
                ))
            continue

        # Step 2: fingerprint match (if available)
        if source.fingerprint:
            best_sim = 0.0
            best_lib = None
            for lib in library_tracks:
                if lib.fingerprint:
                    sim = fingerprint_similarity(source.fingerprint, lib.fingerprint)
                    if sim > best_sim:
                        best_sim = sim
                        best_lib = lib

            if best_lib and best_sim >= fingerprint_threshold:
                rec, reason = compare_quality(source, best_lib)
                match_type = "fingerprint" if best_sim >= 0.95 else "possible"
                results.append(DuplicateResult(
                    source_track=source,
                    library_track=best_lib,
                    match_type=match_type,
                    similarity=best_sim,
                    recommendation=rec,
                    reason=f"{reason} (fingerprint similarity: {best_sim:.0%})",
                ))

    return results


CACHE_PATH = Path.home() / ".dublettax_library_cache.json"


def save_library_cache(tracks: list[Track], library_path: Path):
    """Serialize scanned library tracks to a cache file."""
    data = {
        "library_path": str(library_path),
        "file_count": len(tracks),
        "tracks": [
            {
                "path":            str(t.path),
                "stem_normalized": t.stem_normalized,
                "ext":             t.ext,
                "quality_rank":    t.quality_rank,
                "bitrate":         t.bitrate,
                "duration":        t.duration,
                "version_type":    t.version_type,
                "fingerprint":     t.fingerprint,
            }
            for t in tracks
        ],
    }
    with open(CACHE_PATH, "w") as f:
        json.dump(data, f)


def load_library_cache(library_path: Path) -> list[Track] | None:
    """
    Load cached tracks. If the library has new files, scan only those and
    merge them into the cache (incremental update).
    Returns None only if cache is missing or from a different library.
    """
    if not CACHE_PATH.exists():
        return None
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)

        if data.get("library_path") != str(library_path):
            return None

        cached_tracks = [
            Track(
                path=Path(t["path"]),
                stem_normalized=t["stem_normalized"],
                ext=t["ext"],
                quality_rank=t["quality_rank"],
                bitrate=t["bitrate"],
                duration=t["duration"],
                version_type=t["version_type"],
                fingerprint=t.get("fingerprint"),
            )
            for t in data["tracks"]
        ]

        # Find files on disk
        cached_paths = {t.path for t in cached_tracks}
        all_files = [
            f for f in library_path.rglob("*")
            if f.suffix.lower() in AUDIO_EXTENSIONS and not f.name.startswith("._")
        ]
        new_files = [f for f in all_files if f not in cached_paths]

        if new_files:
            # Scan only new files and merge into cache
            for f in new_files:
                stem = normalize_stem(f.stem)
                ext  = f.suffix.lower()
                cached_tracks.append(Track(
                    path=f, stem_normalized=stem, ext=ext,
                    quality_rank=QUALITY_RANK.get(ext, 0),
                    bitrate=get_bitrate(f), duration=get_duration(f),
                    version_type=detect_version_type(f.stem),
                ))
            save_library_cache(cached_tracks, library_path)

        return cached_tracks
    except Exception:
        return None


def _scan_file_list(files: list[Path], with_fingerprint: bool = False,
                    progress_cb=None) -> list[Track]:
    """Scan a specific list of files (used for incremental cache updates)."""
    tracks = []
    for i, f in enumerate(files):
        stem     = normalize_stem(f.stem)
        ext      = f.suffix.lower()
        rank     = QUALITY_RANK.get(ext, 0)
        bitrate  = get_bitrate(f)
        duration = get_duration(f)
        version  = detect_version_type(f.stem)
        fp       = fingerprint_track(f) if with_fingerprint else None
        tracks.append(Track(
            path=f, stem_normalized=stem, ext=ext, quality_rank=rank,
            bitrate=bitrate, duration=duration, version_type=version,
            fingerprint=fp,
        ))
        if progress_cb:
            progress_cb(i + 1, len(files), str(f.name))
    return tracks


def save_report(results: list[DuplicateResult], output_path: Path):
    """Save report as JSON."""
    data = []
    for r in results:
        data.append({
            "source": str(r.source_track.path),
            "library": str(r.library_track.path),
            "match_type": r.match_type,
            "similarity": round(r.similarity, 3),
            "recommendation": r.recommendation,
            "reason": r.reason,
            "source_format": r.source_track.ext,
            "library_format": r.library_track.ext,
            "source_version": r.source_track.version_type,
            "library_version": r.library_track.version_type,
        })
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)


def apply_results(results: list[DuplicateResult], review_folder: Path):
    """
    Move inferior duplicates to review folder.
    Only acts on 'keep_source' or 'keep_library' recommendations.
    """
    review_folder.mkdir(parents=True, exist_ok=True)
    moved = []

    for r in results:
        if r.recommendation == "keep_source":
            # Library track is inferior — move it to review
            dest = review_folder / r.library_track.path.name
            r.library_track.path.rename(dest)
            moved.append((r.library_track.path, dest, "library inferior"))
        elif r.recommendation == "keep_library":
            # Source track is inferior — move it to review
            dest = review_folder / r.source_track.path.name
            r.source_track.path.rename(dest)
            moved.append((r.source_track.path, dest, "source inferior"))

    return moved
