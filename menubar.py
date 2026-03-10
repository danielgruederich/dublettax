#!/usr/bin/env python3
"""
DublettaX — Menu Bar App
Watches ~/Music/WIP automatically and runs the pipeline when files arrive.
"""

import rumps
import threading
import time
import subprocess
import sys
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from deduplicator import (
    scan_folder, find_duplicates, proposed_renames,
    AUDIO_EXTENSIONS
)

WIP_PATH     = Path.home() / "Music" / "WIP"
CONTENTS_PATH = Path("/Volumes/ssdMonschi/Contents")
SETTLE_DELAY    = 5    # seconds to wait after last file event before checking sizes
SIZE_CHECK_INTERVAL = 5  # seconds between size checks
SIZE_STABLE_COUNT   = 3  # how many consecutive stable checks before processing


class WIPHandler(FileSystemEventHandler):
    def __init__(self, app):
        super().__init__()
        self.app = app

    def on_created(self, event):
        self.app.schedule_processing()

    def on_moved(self, event):
        self.app.schedule_processing()


class DublettaXApp(rumps.App):
    def __init__(self):
        super().__init__(
            "♪",
            menu=[
                rumps.MenuItem("Status: Starting…"),
                None,  # separator
                rumps.MenuItem("Open Duplicate Finder", callback=self.open_gui),
                rumps.MenuItem("Open WIP Folder",       callback=self.open_wip),
                None,
                rumps.MenuItem("Process WIP Now",       callback=self.process_now),
                None,
            ],
            quit_button="Quit DublettaX"
        )

        self._observer   = None
        self._timer      = None
        self._processing = False
        self.status_item = self.menu["Status: Starting…"]

        self._start_watcher()

    # ── Watcher ──────────────────────────────────────────────────────────────

    def _start_watcher(self):
        WIP_PATH.mkdir(parents=True, exist_ok=True)

        handler = WIPHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(WIP_PATH), recursive=True)
        self._observer.start()

        self._set_status("👁  Watching WIP folder")

    def schedule_processing(self):
        """Debounce: reset timer on each new file event."""
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(SETTLE_DELAY, self._wait_for_stable_size)
        self._timer.start()
        self._set_status("⏳  Files detected — waiting…")

    def _folder_size(self) -> int:
        """Return total byte size of all files in WIP_PATH."""
        total = 0
        try:
            for f in WIP_PATH.rglob("*"):
                if f.is_file():
                    try:
                        total += f.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
        return total

    def _wait_for_stable_size(self):
        """Poll WIP folder size until stable, then trigger pipeline."""
        stable_count = 0
        last_size = -1

        while stable_count < SIZE_STABLE_COUNT:
            current_size = self._folder_size()
            gb = current_size / 1_073_741_824

            if current_size == last_size:
                stable_count += 1
                self._set_status(
                    f"⏳  Waiting for copy… ({gb:.1f} GB, stable {stable_count}/{SIZE_STABLE_COUNT})"
                )
            else:
                stable_count = 0
                self._set_status(
                    f"⏳  Copying… ({gb:.1f} GB)"
                )

            last_size = current_size

            if stable_count < SIZE_STABLE_COUNT:
                time.sleep(SIZE_CHECK_INTERVAL)

        self._run_pipeline()

    def process_now(self, _=None):
        """Manual trigger from menu."""
        if self._timer:
            self._timer.cancel()
        threading.Thread(target=self._run_pipeline, daemon=True).start()

    # ── Pipeline ─────────────────────────────────────────────────────────────

    def _run_pipeline(self):
        if self._processing:
            return
        self._processing = True

        try:
            # Check for audio files
            audio_files = [
                f for f in WIP_PATH.rglob("*")
                if f.suffix.lower() in AUDIO_EXTENSIONS and not f.name.startswith("._")
            ]
            if not audio_files:
                self._set_status("👁  Watching WIP folder")
                return

            # Step 1 — Preview renames (no auto-apply; user adds to Rekordbox manually)
            renames = proposed_renames(WIP_PATH)
            safe    = [(o, n, w) for o, n, w in renames if not w]
            risky   = [(o, n, w) for o, n, w in renames if w]

            # Step 2 — Duplicate scan
            self._set_status("🔍  Scanning for duplicates…")

            if not CONTENTS_PATH.exists():
                rumps.notification(
                    "DublettaX",
                    "Library not found",
                    f"Contents folder not found at {CONTENTS_PATH}",
                )
                self._set_status("⚠️  Library not found")
                return

            source_tracks  = scan_folder(WIP_PATH)
            library_tracks = scan_folder(CONTENTS_PATH)
            results        = find_duplicates(source_tracks, library_tracks)

            keep_src = sum(1 for r in results if r.recommendation == "keep_source")
            keep_lib = sum(1 for r in results if r.recommendation == "keep_library")
            review   = sum(1 for r in results if r.recommendation == "review")
            risky_n  = len(risky)

            # Notification
            summary = []
            if safe:
                summary.append(f"{len(safe)} rename(s) ready")
            if results:
                summary.append(f"{len(results)} duplicate(s) found")
                summary.append(f"🟢{keep_src} 🔴{keep_lib} 🟡{review}")
            if risky_n:
                summary.append(f"⚠ {risky_n} risky rename(s) need review")

            if summary:
                rumps.notification(
                    "DublettaX",
                    "WIP processed",
                    "  |  ".join(summary),
                )

            self._set_status(
                f"✅  Done — {len(results)} duplicate(s)  |  Open Duplicate Finder to review"
                if results else "✅  Done — no duplicates found"
            )

        finally:
            self._processing = False

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _set_status(self, text: str):
        self.status_item.title = text

    @rumps.clicked("Open Duplicate Finder")
    def open_gui(self, _=None):
        script = Path(__file__).parent / "gui.py"
        subprocess.Popen([sys.executable, str(script)])

    @rumps.clicked("Open WIP Folder")
    def open_wip(self, _=None):
        subprocess.Popen(["open", str(WIP_PATH)])


if __name__ == "__main__":
    DublettaXApp().run()
