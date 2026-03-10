#!/usr/bin/env python3
"""
DublettaX — GUI
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from tkinterdnd2 import TkinterDnD, DND_FILES
import shutil
import signal
import subprocess as _subprocess
from deduplicator import (
    scan_folder, find_duplicates, save_report, apply_results,
    AUDIO_EXTENSIONS, proposed_renames, apply_renames,
    load_library_cache, save_library_cache, get_duration
)

DEFAULT_WIP      = str(Path.home() / "Music" / "WIP")
DEFAULT_CONTENTS = "/Volumes/ssdMonschi/Contents"
DEFAULT_REVIEW   = str(Path.home() / "Music" / "Dedup_Review")

BG     = "#1f2421"   # almost black
BG2    = "#2a3230"   # card background
BG3    = "#216869"   # dark teal (borders, muted)
FG     = "#dce1de"   # off-white text
GREEN  = "#49a078"   # medium green (success)
RED    = "#c97b6a"   # warm red (warning/danger)
YELLOW = "#9cc5a1"   # light green (review/neutral)
ACCENT = "#49a078"   # primary accent


class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("DublettaX")
        self.geometry("900x680")
        self.resizable(True, True)
        self.configure(bg=BG)

        self._results   = []
        self._renames   = []
        self._observer  = None
        self._wip_timer = None

        # Player state (uses afplay — no microphone permission needed)
        self._player_path     = None
        self._player_duration = 0.0
        self._player_pos      = 0.0    # position when play was called
        self._player_start_t  = None   # wall-clock time when play started
        self._player_paused   = False
        self._player_poll_id  = None
        self._player_proc     = None   # afplay subprocess

        self._apply_styles()
        self._build_ui()

    # ── Styles ───────────────────────────────────────────────────────────────

    def _apply_styles(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame",       background=BG)
        s.configure("TLabel",       background=BG,  foreground=FG,  font=("SF Pro", 13))
        s.configure("TButton",      background=BG2, foreground=FG,  font=("SF Pro", 13), padding=6)
        s.configure("TEntry",       fieldbackground=BG2, foreground=FG, font=("SF Pro", 12))
        s.configure("TCheckbutton", background=BG,  foreground=FG,  font=("SF Pro", 12))
        s.configure("Treeview",     background=BG2, foreground=FG,
                    fieldbackground=BG2, font=("SF Pro", 11), rowheight=24)
        s.configure("Treeview.Heading", background=BG3, foreground=FG,
                    font=("SF Pro", 12, "bold"))
        s.map("Treeview",           background=[("selected", "#585b70")])
        s.configure("TProgressbar", troughcolor=BG2, background=ACCENT)

    # ── Top bar (always visible) ──────────────────────────────────────────────

    def _build_topbar(self):
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=20, pady=(16, 0))

        tk.Label(bar, text="DublettaX", font=("SF Pro", 20, "bold"),
                 bg=BG, fg=FG).pack(side="left")

        # Folder settings on the right side of top bar
        sf = tk.Frame(bar, bg=BG)
        sf.pack(side="right")

        tk.Label(sf, text="WIP:", font=("SF Pro", 11), bg=BG, fg=BG3).pack(side="left")
        self.var_wip = tk.StringVar(value=DEFAULT_WIP)
        self.lbl_wip = tk.Label(sf, textvariable=self.var_wip, font=("SF Pro", 11),
                                bg=BG, fg=ACCENT, cursor="hand2")
        self.lbl_wip.pack(side="left", padx=(4, 2))
        self.lbl_wip.bind("<Button-1>", lambda e: self._pick_folder(self.var_wip, "WIP folder"))

        tk.Label(sf, text="  |  Library:", font=("SF Pro", 11), bg=BG, fg=BG3).pack(side="left")
        self.var_library = tk.StringVar(value=DEFAULT_CONTENTS)
        self.lbl_lib = tk.Label(sf, textvariable=self.var_library, font=("SF Pro", 11),
                                bg=BG, fg=ACCENT, cursor="hand2")
        self.lbl_lib.pack(side="left", padx=(4, 0))
        self.lbl_lib.bind("<Button-1>", lambda e: self._pick_folder(self.var_library, "Library folder"))

        tk.Frame(self, bg=BG3, height=1).pack(fill="x", padx=20, pady=(10, 0))

    def _pick_folder(self, var: tk.StringVar, title: str):
        d = filedialog.askdirectory(title=f"Select {title}")
        if d:
            var.set(d)

    # ── Main UI ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_topbar()

        self._container = tk.Frame(self, bg=BG)
        self._container.pack(fill="both", expand=True)

        # Build all frames
        self._home_frame   = tk.Frame(self._container, bg=BG)
        self._frame_check  = tk.Frame(self._container, bg=BG)
        self._frame_wip    = tk.Frame(self._container, bg=BG)
        self._frame_dedup  = tk.Frame(self._container, bg=BG)
        self._frame_rename = tk.Frame(self._container, bg=BG)

        self._build_home(self._home_frame)
        self._wrap_with_back(self._frame_check,  "🔍  Quick Check",      self._build_check_content)
        self._wrap_with_back(self._frame_wip,    "👁  WIP Monitor",      self._build_wip_content)
        self._wrap_with_back(self._frame_dedup,  "⚖️  Duplicate Finder", self._build_dedup_content)
        self._wrap_with_back(self._frame_rename, "✏️  Rename Files",     self._build_rename_content)

        self._show(self._home_frame)

    def _show(self, frame):
        for f in [self._home_frame, self._frame_check, self._frame_wip,
                  self._frame_dedup, self._frame_rename]:
            f.pack_forget()
        frame.pack(fill="both", expand=True)

    def _wrap_with_back(self, parent, title: str, builder):
        nav = tk.Frame(parent, bg=BG)
        nav.pack(fill="x", padx=20, pady=(14, 0))

        tk.Button(nav, text="← Home", font=("SF Pro", 12), bg=BG, fg=ACCENT,
                  activebackground=BG, activeforeground=FG,
                  relief="flat", bd=0, cursor="hand2",
                  command=lambda: self._show(self._home_frame)
                  ).pack(side="left")

        tk.Label(nav, text=title, font=("SF Pro", 16, "bold"),
                 bg=BG, fg=FG).pack(side="left", padx=16)

        tk.Frame(parent, bg=BG3, height=1).pack(fill="x", padx=20, pady=(8, 4))

        content = tk.Frame(parent, bg=BG)
        content.pack(fill="both", expand=True)
        builder(content)

    # ── Home ─────────────────────────────────────────────────────────────────

    def _build_home(self, parent):
        # Greeting
        greet = tk.Frame(parent, bg=BG)
        greet.pack(fill="x", padx=32, pady=(24, 4))
        tk.Label(greet, text="What do you want to do?",
                 font=("SF Pro", 22, "bold"), bg=BG, fg=FG).pack(anchor="w")

        outer = tk.Frame(parent, bg=BG)
        outer.pack(expand=True, fill="both", padx=24, pady=(8, 24))

        cards_data = [
            ("🔍", "Quick Check",      "Is this track already in your library?",
             "#216869", "#1a5253", self._frame_check),
            ("👁",  "WIP Monitor",     "Auto-scan new music when it arrives",
             "#49a078", "#357358", self._frame_wip),
            ("⚖️", "Duplicate Finder", "Compare folders and find duplicates",
             "#216869", "#1a5253", self._frame_dedup),
            ("✏️", "Rename Files",     "Remove track numbers from filenames",
             "#49a078", "#357358", self._frame_rename),
        ]

        for i, data in enumerate(cards_data):
            row, col = divmod(i, 2)
            self._make_card(outer, *data, row, col)

        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1, minsize=190)
        outer.rowconfigure(1, weight=1, minsize=190)

    def _make_card(self, parent, icon, title, desc, color, hover_color,
                   target_frame, row, col):
        CARD_BG    = "#252e2b"
        CARD_HOVER = "#2d3a36"

        card = tk.Frame(parent, bg=CARD_BG, cursor="hand2")
        card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

        # Coloured left border (Tailwind style)
        border = tk.Frame(card, bg=color, width=5)
        border.pack(side="left", fill="y")

        # Main content
        content = tk.Frame(card, bg=CARD_BG)
        content.pack(side="left", fill="both", expand=True)

        # Bottom text — packed first so it anchors to bottom
        bottom = tk.Frame(content, bg=CARD_BG)
        bottom.pack(side="bottom", fill="x", padx=20, pady=(0, 20))

        lbl_title = tk.Label(bottom, text=title, font=("SF Pro", 15, "bold"),
                             bg=CARD_BG, fg=FG, cursor="hand2")
        lbl_title.pack(anchor="w")

        lbl_desc = tk.Label(bottom, text=desc, font=("SF Pro", 11),
                            bg=CARD_BG, fg="#9cc5a1", justify="left",
                            cursor="hand2")
        lbl_desc.pack(anchor="w", pady=(4, 0))

        # Top area with icon — fills remaining space
        top = tk.Frame(content, bg=CARD_BG)
        top.pack(side="top", fill="both", expand=True, padx=20, pady=(20, 8))

        lbl_icon = tk.Label(top, text=icon, font=("SF Pro", 36),
                            bg=CARD_BG, fg=FG, cursor="hand2")
        lbl_icon.pack(anchor="nw")

        # Click
        go = lambda e, f=target_frame: self._show(f)
        all_w = [card, border, content, top, bottom, lbl_icon, lbl_title, lbl_desc]
        for w in all_w:
            w.bind("<Button-1>", go)

        # Hover
        def on_enter(e):
            for w in [card, content, top, bottom, lbl_icon, lbl_title, lbl_desc]:
                try: w.configure(bg=CARD_HOVER)
                except Exception: pass

        def on_leave(e):
            for w in [card, content, top, bottom, lbl_icon, lbl_title, lbl_desc]:
                try: w.configure(bg=CARD_BG)
                except Exception: pass

        for w in all_w:
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)

        # Drag-and-drop
        def on_drop(e, f=target_frame):
            path = e.data.strip().strip("{}")  # macOS wraps paths with spaces in {}
            self._handle_drop(path, f)

        def on_drag_enter(e):
            for w in [card, content, top, bottom, lbl_icon, lbl_title, lbl_desc]:
                try: w.configure(bg=color)
                except Exception: pass

        def on_drag_leave(e):
            for w in [card, content, top, bottom, lbl_icon, lbl_title, lbl_desc]:
                try: w.configure(bg=CARD_BG)
                except Exception: pass

        for w in [card, content, top, bottom]:
            w.drop_target_register(DND_FILES)
            w.dnd_bind("<<Drop>>",          on_drop)
            w.dnd_bind("<<DragEnter>>",     on_drag_enter)
            w.dnd_bind("<<DragLeave>>",     on_drag_leave)

    # ── Drop handler ─────────────────────────────────────────────────────────

    def _handle_drop(self, raw_path: str, target_frame):
        """Route a dropped file/folder to the appropriate screen."""
        path = Path(raw_path)

        if target_frame is self._frame_check:
            # Drop onto Quick Check → prefill and search
            self._show(self._frame_check)
            self.var_check_input.set(str(path))
            self.after(100, self._run_check)

        elif target_frame is self._frame_wip:
            # Drop onto WIP Monitor → copy/move into WIP folder
            import shutil
            wip = Path(self.var_wip.get())
            dest = wip / path.name
            try:
                if path.is_dir():
                    shutil.copytree(str(path), str(dest), dirs_exist_ok=True)
                else:
                    shutil.copy2(str(path), str(dest))
                self._show(self._frame_wip)
                self._wip_log(f"Copied: {path.name}")
            except Exception as ex:
                messagebox.showerror("Copy failed", str(ex))

        elif target_frame is self._frame_dedup:
            # Drop onto Duplicate Finder → set as source folder
            self._show(self._frame_dedup)
            src = str(path) if path.is_dir() else str(path.parent)
            self.var_source.set(src)

        elif target_frame is self._frame_rename:
            # Drop onto Rename → set as target folder and preview
            self._show(self._frame_rename)
            folder = str(path) if path.is_dir() else str(path.parent)
            self.var_rename_folder.set(folder)
            self.after(100, self._preview_renames)

    # ── Quick Check ───────────────────────────────────────────────────────────

    def _build_check_content(self, parent):
        pad = {"padx": 20, "pady": 6}

        inp = tk.Frame(parent, bg=BG)
        inp.pack(fill="x", **pad)

        tk.Label(inp, text="Track name or file path:", bg=BG, fg=FG,
                 font=("SF Pro", 12)).grid(row=0, column=0, sticky="w")
        self.var_check_input = tk.StringVar()
        ttk.Entry(inp, textvariable=self.var_check_input, width=55).grid(
            row=0, column=1, padx=8)
        ttk.Button(inp, text="Browse file",
                   command=self._check_browse).grid(row=0, column=2)

        ttk.Button(parent, text="🔍  Search Library",
                   command=self._run_check).pack(padx=20, pady=8, anchor="w")

        self.var_check_status = tk.StringVar(value="")
        tk.Label(parent, textvariable=self.var_check_status,
                 font=("SF Pro", 13), bg=BG, fg=ACCENT).pack(anchor="w", padx=20)

        cols = ("match", "path", "format", "duration")
        ft = ttk.Frame(parent)
        ft.pack(fill="both", expand=True, padx=20, pady=6)

        self.check_tree = ttk.Treeview(ft, columns=cols, show="headings", height=16)
        self.check_tree.heading("match",    text="Match")
        self.check_tree.heading("path",     text="Path in Library")
        self.check_tree.heading("format",   text="Format")
        self.check_tree.heading("duration", text="Duration")
        self.check_tree.column("match",    width=180)
        self.check_tree.column("path",     width=460)
        self.check_tree.column("format",   width=70)
        self.check_tree.column("duration", width=80)
        self.check_tree.tag_configure("found",    foreground=GREEN)
        self.check_tree.tag_configure("possible", foreground=YELLOW)

        scroll = ttk.Scrollbar(ft, orient="vertical", command=self.check_tree.yview)
        self.check_tree.configure(yscrollcommand=scroll.set)
        self.check_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

    def _check_browse(self):
        path = filedialog.askopenfilename(
            title="Select track file",
            filetypes=[("Audio files", "*.flac *.aiff *.aif *.wav *.mp3 *.m4a *.ogg"),
                       ("All files", "*")]
        )
        if path:
            self.var_check_input.set(path)

    def _run_check(self):
        raw = self.var_check_input.get().strip()
        if not raw:
            messagebox.showerror("Error", "Enter a track name or file path first.")
            return
        library = Path(self.var_library.get())
        if not library.exists():
            messagebox.showerror("Error", f"Library folder not found:\n{library}")
            return
        self.check_tree.delete(*self.check_tree.get_children())
        self.var_check_status.set("🔍  Searching…")
        threading.Thread(target=self._check_thread, args=(raw, library), daemon=True).start()

    def _check_thread(self, raw: str, library: Path):
        from deduplicator import normalize_stem, get_duration

        p = Path(raw)
        query = normalize_stem(p.stem if p.suffix.lower() in AUDIO_EXTENSIONS else raw)
        query_tokens = [t for t in query.split() if len(t) > 1]

        if not query_tokens:
            self.after(0, lambda: self.var_check_status.set("❌  Query too short"))
            return

        exact   = []
        partial = []

        for f in library.rglob("*"):
            if f.suffix.lower() not in AUDIO_EXTENSIONS or f.name.startswith("._"):
                continue
            lib_stem = normalize_stem(f.stem)
            if lib_stem == query:
                exact.append((f, 1.0))
                continue
            hits = sum(1 for t in query_tokens if t in lib_stem)
            score = hits / len(query_tokens)
            if score >= 0.5:
                partial.append((f, score))

        partial.sort(key=lambda x: x[1], reverse=True)

        def fmt_dur(d):
            if d is None: return "—"
            m, s = divmod(int(d), 60)
            return f"{m}:{s:02d}"

        def update():
            self.check_tree.delete(*self.check_tree.get_children())
            for f, _ in exact:
                self.check_tree.insert("", "end", values=(
                    "✅ Exact match", str(f),
                    f.suffix.lower().lstrip(".").upper(), fmt_dur(get_duration(f)),
                ), tags=("found",))
            for f, score in partial:
                self.check_tree.insert("", "end", values=(
                    f"🟡 Partial ({int(score*100)}%)", str(f),
                    f.suffix.lower().lstrip(".").upper(), fmt_dur(get_duration(f)),
                ), tags=("possible",))
            if exact:
                self.var_check_status.set(
                    f"✅  {len(exact)} exact match(es)"
                    + (f"  +  {len(partial)} partial" if partial else "")
                )
            elif partial:
                self.var_check_status.set(
                    f"🟡  No exact match — {len(partial)} partial match(es)")
            else:
                self.var_check_status.set("❌  Not found in library")

        self.after(0, update)

    # ── WIP Monitor ───────────────────────────────────────────────────────────

    def _build_wip_content(self, parent):
        pad = {"padx": 20, "pady": 6}

        tk.Label(parent,
                 text="Drop music files or folders into the WIP folder.\n"
                      "DublettaX waits for the copy to finish, then scans for duplicates.",
                 font=("SF Pro", 12), bg=BG, fg=FG, justify="left").pack(**pad, anchor="w")

        df = tk.Frame(parent, bg=BG)
        df.pack(fill="x", **pad)
        tk.Label(df, text="Wait before processing:", bg=BG, fg=FG,
                 font=("SF Pro", 12)).pack(side="left")
        self.var_wip_delay = tk.IntVar(value=5)
        self.lbl_delay = tk.Label(df, text="5s", bg=BG, fg=ACCENT, font=("SF Pro", 12))
        tk.Scale(df, from_=2, to=30, orient="horizontal",
                 variable=self.var_wip_delay, length=140,
                 bg=BG, fg=FG, troughcolor=BG2, highlightthickness=0,
                 command=lambda v: self.lbl_delay.config(text=f"{int(float(v))}s")
                 ).pack(side="left", padx=6)
        self.lbl_delay.pack(side="left")

        bf = tk.Frame(parent, bg=BG)
        bf.pack(fill="x", **pad)
        self.btn_watch = ttk.Button(bf, text="▶  Start Watching",
                                    command=self._toggle_watcher)
        self.btn_watch.pack(side="left")

        self.var_wip_status = tk.StringVar(value="Not watching.")
        tk.Label(parent, textvariable=self.var_wip_status,
                 font=("SF Pro", 13), bg=BG, fg=ACCENT).pack(anchor="w", padx=20)

        tk.Label(parent, text="Activity log:", bg=BG, fg=BG3,
                 font=("SF Pro", 11)).pack(anchor="w", padx=20, pady=(10, 2))

        lf = tk.Frame(parent, bg=BG)
        lf.pack(fill="both", expand=True, padx=20, pady=(0, 10))

        self.wip_log = tk.Text(lf, height=14, bg=BG2, fg=FG,
                               font=("SF Mono", 11), state="disabled",
                               relief="flat", borderwidth=0)
        log_scroll = ttk.Scrollbar(lf, orient="vertical", command=self.wip_log.yview)
        self.wip_log.configure(yscrollcommand=log_scroll.set)
        self.wip_log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="left", fill="y")

    def _wip_log(self, msg: str):
        self.wip_log.configure(state="normal")
        self.wip_log.insert("end", f"{time.strftime('%H:%M:%S')}  {msg}\n")
        self.wip_log.see("end")
        self.wip_log.configure(state="disabled")

    def _toggle_watcher(self):
        if self._observer and self._observer.is_alive():
            self._stop_watcher()
        else:
            self._start_watcher()

    def _start_watcher(self):
        wip_path = Path(self.var_wip.get())
        if not wip_path.exists():
            messagebox.showerror("Error", f"WIP folder not found:\n{wip_path}")
            return
        handler = WIPHandler(self)
        self._observer = Observer()
        self._observer.schedule(handler, str(wip_path), recursive=True)
        self._observer.start()
        self.var_wip_status.set(f"👁  Watching  {wip_path}")
        self.btn_watch.configure(text="⏹  Stop Watching")
        self._wip_log(f"Started watching: {wip_path}")

    def _stop_watcher(self):
        if self._observer:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        if self._wip_timer:
            self.after_cancel(self._wip_timer)
            self._wip_timer = None
        self.var_wip_status.set("Not watching.")
        self.btn_watch.configure(text="▶  Start Watching")
        self._wip_log("Stopped watching.")

    def _schedule_wip_processing(self):
        if self._wip_timer:
            self.after_cancel(self._wip_timer)
        delay_ms = self.var_wip_delay.get() * 1000
        self._wip_timer = self.after(delay_ms, self._process_wip)

    def _process_wip(self):
        self._wip_timer = None
        wip_path = Path(self.var_wip.get())
        lib_path = Path(self.var_library.get())
        audio_files = [f for f in wip_path.rglob("*")
                       if f.suffix.lower() in AUDIO_EXTENSIONS
                       and not f.name.startswith("._")]
        if not audio_files:
            return
        self._wip_log(f"Detected {len(audio_files)} audio file(s) — starting scan…")
        threading.Thread(target=self._wip_pipeline,
                         args=(wip_path, lib_path), daemon=True).start()

    def _wip_pipeline(self, wip_path: Path, lib_path: Path):
        renames = proposed_renames(wip_path)
        safe  = [(o, n, w) for o, n, w in renames if not w]
        risky = [(o, n, w) for o, n, w in renames if w]
        self._wip_log(f"Renames ready: {len(safe)} safe, {len(risky)} need review.")

        self._wip_log("Scanning for duplicates…")
        if not lib_path.exists():
            self._wip_log(f"  ✗  Library not found: {lib_path}")
            return

        source_tracks  = scan_folder(wip_path)
        library_tracks = load_library_cache(lib_path)
        if library_tracks is None:
            self._wip_log("  Building library cache (first time)…")
            library_tracks = scan_folder(lib_path)
            save_library_cache(library_tracks, lib_path)
            self._wip_log(f"  Cache saved — {len(library_tracks)} tracks.")
        else:
            self._wip_log(f"  Cache loaded — {len(library_tracks)} tracks.")
        results        = find_duplicates(source_tracks, library_tracks)

        keep_src = sum(1 for r in results if r.recommendation == "keep_source")
        keep_lib = sum(1 for r in results if r.recommendation == "keep_library")
        review   = sum(1 for r in results if r.recommendation == "review")

        self._wip_log(f"Found {len(results)} duplicate(s):")
        self._wip_log(f"  🟢 {keep_src} — your version is better")
        self._wip_log(f"  🔴 {keep_lib} — library version is better")
        self._wip_log(f"  🟡 {review} — needs manual review")
        self._wip_log("Done. Open Duplicate Finder to review and act.")

        self._results = results
        self.after(0, self._populate_table)

    # ── Duplicate Finder ──────────────────────────────────────────────────────

    def _build_dedup_content(self, parent):
        pad = {"padx": 20, "pady": 5}

        fp = tk.Frame(parent, bg=BG)
        fp.pack(fill="x", **pad)

        tk.Label(fp, text="New music folder:", bg=BG, fg=FG,
                 font=("SF Pro", 12)).grid(row=0, column=0, sticky="w")
        self.var_source = tk.StringVar()
        ttk.Entry(fp, textvariable=self.var_source, width=52).grid(row=0, column=1, padx=8)
        ttk.Button(fp, text="Browse",
                   command=self._pick_source).grid(row=0, column=2)

        tk.Label(fp, text="Review folder:", bg=BG, fg=FG,
                 font=("SF Pro", 12)).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.var_review = tk.StringVar(value=DEFAULT_REVIEW)
        ttk.Entry(fp, textvariable=self.var_review, width=52).grid(
            row=1, column=1, padx=8, pady=(8, 0))
        ttk.Button(fp, text="Browse",
                   command=self._pick_review).grid(row=1, column=2, pady=(8, 0))

        op = tk.Frame(parent, bg=BG)
        op.pack(fill="x", **pad)

        self.var_fingerprint = tk.BooleanVar(value=False)
        ttk.Checkbutton(op, text="Use audio fingerprinting (slower — catches renamed duplicates)",
                        variable=self.var_fingerprint).pack(anchor="w")

        tf = tk.Frame(op, bg=BG)
        tf.pack(anchor="w", pady=(4, 0))
        tk.Label(tf, text="Similarity threshold:", bg=BG, fg=FG,
                 font=("SF Pro", 12)).pack(side="left")
        self.var_fp_threshold = tk.DoubleVar(value=85)
        self.lbl_threshold = tk.Label(tf, text="85%", bg=BG, fg=ACCENT, font=("SF Pro", 12))
        tk.Scale(tf, from_=60, to=99, orient="horizontal",
                 variable=self.var_fp_threshold, length=140,
                 bg=BG, fg=FG, troughcolor=BG2, highlightthickness=0,
                 command=lambda v: self.lbl_threshold.config(text=f"{int(float(v))}%")
                 ).pack(side="left", padx=6)
        self.lbl_threshold.pack(side="left")

        bf = tk.Frame(parent, bg=BG)
        bf.pack(padx=20, pady=6, anchor="w")
        ttk.Button(bf, text="▶  Scan for Duplicates",
                   command=self._run_scan).pack(side="left")
        ttk.Button(bf, text="🔄  Rebuild Library Cache",
                   command=self._rebuild_cache).pack(side="left", padx=(10, 0))

        self.var_progress_text = tk.StringVar(value="Ready.")
        tk.Label(parent, textvariable=self.var_progress_text,
                 font=("SF Pro", 12), bg=BG, fg=GREEN).pack(anchor="w", padx=20)
        self.progress = ttk.Progressbar(parent, length=820, mode="determinate")
        self.progress.pack(padx=20, pady=4)

        cols = ("source", "library_match", "match_type", "recommendation", "reason")
        ft = ttk.Frame(parent)
        ft.pack(fill="both", expand=True, padx=20, pady=4)

        self.tree = ttk.Treeview(ft, columns=cols, show="headings", height=8)
        self.tree.heading("source",         text="Your File")
        self.tree.heading("library_match",  text="Library Match")
        self.tree.heading("match_type",     text="Match")
        self.tree.heading("recommendation", text="Action")
        self.tree.heading("reason",         text="Reason")
        self.tree.column("source",          width=185)
        self.tree.column("library_match",   width=185)
        self.tree.column("match_type",      width=90)
        self.tree.column("recommendation",  width=110)
        self.tree.column("reason",          width=260)
        self.tree.tag_configure("keep_source",  foreground=GREEN)
        self.tree.tag_configure("keep_library", foreground=RED)
        self.tree.tag_configure("review",       foreground=YELLOW)

        scroll = ttk.Scrollbar(ft, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")

        # Single click → load source; double-click → load library match
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>",         self._on_tree_double)

        # ── Mini Player ──────────────────────────────────────────────────────
        player = tk.Frame(parent, bg=BG2)
        player.pack(fill="x", padx=20, pady=(4, 0))

        # Track label
        self.var_player_track = tk.StringVar(value="No track selected")
        tk.Label(player, textvariable=self.var_player_track,
                 font=("SF Pro", 11), bg=BG2, fg=FG,
                 anchor="w", width=40).pack(side="left", padx=12, pady=8)

        # Controls
        ctrl = tk.Frame(player, bg=BG2)
        ctrl.pack(side="left", padx=8)

        self.btn_play = tk.Button(ctrl, text="▶", font=("SF Pro", 14),
                                  bg=BG2, fg=GREEN, relief="flat", bd=0,
                                  activebackground=BG2, cursor="hand2",
                                  command=self._player_toggle)
        self.btn_play.pack(side="left", padx=4)

        tk.Button(ctrl, text="⏹", font=("SF Pro", 14),
                  bg=BG2, fg=FG, relief="flat", bd=0,
                  activebackground=BG2, cursor="hand2",
                  command=self._player_stop).pack(side="left", padx=4)

        tk.Button(ctrl, text="−30s", font=("SF Pro", 11),
                  bg=BG2, fg=FG, relief="flat", bd=0,
                  activebackground=BG2, cursor="hand2",
                  command=lambda: self._player_seek(-30)).pack(side="left", padx=4)

        tk.Button(ctrl, text="+30s", font=("SF Pro", 11),
                  bg=BG2, fg=FG, relief="flat", bd=0,
                  activebackground=BG2, cursor="hand2",
                  command=lambda: self._player_seek(30)).pack(side="left", padx=4)

        # Time + seek slider
        right = tk.Frame(player, bg=BG2)
        right.pack(side="right", padx=12)

        self.var_player_time = tk.StringVar(value="0:00 / 0:00")
        tk.Label(right, textvariable=self.var_player_time,
                 font=("SF Mono", 10), bg=BG2, fg=BG3).pack(side="right", padx=8)

        self.player_seek_var = tk.DoubleVar(value=0)
        self.player_slider = tk.Scale(right, variable=self.player_seek_var,
                                      from_=0, to=100, orient="horizontal",
                                      length=200, showvalue=False,
                                      bg=BG2, fg=FG, troughcolor=BG3,
                                      highlightthickness=0, bd=0,
                                      command=self._on_slider_move)
        self.player_slider.pack(side="right")
        self._slider_dragging = False
        self.player_slider.bind("<ButtonPress-1>",   lambda e: setattr(self, "_slider_dragging", True))
        self.player_slider.bind("<ButtonRelease-1>", self._on_slider_release)

        # Source label (which version: source / library)
        self.var_player_src = tk.StringVar(value="")
        tk.Label(player, textvariable=self.var_player_src,
                 font=("SF Pro", 10), bg=BG2, fg=ACCENT).pack(side="right", padx=4)

        af = tk.Frame(parent, bg=BG)
        af.pack(fill="x", padx=20, pady=8)
        ttk.Button(af, text="💾  Save Report",
                   command=self._save_report).pack(side="left", padx=4)
        ttk.Button(af, text="🗂  Move Inferior Tracks to Review Folder",
                   command=self._apply).pack(side="left", padx=4)
        ttk.Button(af, text="📦  Move to Contents & Export Playlist",
                   command=self._move_and_playlist).pack(side="left", padx=4)

    # ── Player ───────────────────────────────────────────────────────────────

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx >= len(self._results):
            return
        self._player_load(self._results[idx].source_track.path, label="Your file")

    def _on_tree_double(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx >= len(self._results):
            return
        self._player_load(self._results[idx].library_track.path, label="Library")

    def _player_load(self, path: Path, label: str = ""):
        self._player_stop()
        self._player_path     = path
        self._player_duration = get_duration(path) or 0
        self._player_pos      = 0.0
        self._player_paused   = False
        self.var_player_track.set(path.name)
        self.var_player_src.set(label)
        self.player_seek_var.set(0)
        self._update_time_label(0)
        self._player_play_from(0)

    def _player_play_from(self, pos: float):
        self._kill_proc()
        try:
            self._player_proc = _subprocess.Popen(
                ["afplay", "-t", str(pos), str(self._player_path)],
                stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL
            )
            self._player_pos     = pos
            self._player_start_t = time.time()
            self._player_paused  = False
            self.btn_play.configure(text="⏸")
            self._player_poll()
        except Exception as e:
            self.var_player_track.set(f"Error: {e}")

    def _player_toggle(self):
        if not self._player_path:
            return
        if self._player_paused:
            # Resume: send SIGCONT
            if self._player_proc:
                self._player_proc.send_signal(signal.SIGCONT)
            self._player_start_t = time.time() - self._player_pos
            self._player_paused  = False
            self.btn_play.configure(text="⏸")
            self._player_poll()
        else:
            # Pause: send SIGSTOP
            if self._player_proc:
                self._player_proc.send_signal(signal.SIGSTOP)
            self._player_pos    = self._current_pos()
            self._player_paused = True
            self.btn_play.configure(text="▶")
            if self._player_poll_id:
                self.after_cancel(self._player_poll_id)

    def _player_stop(self):
        self._kill_proc()
        self._player_paused  = False
        self._player_start_t = None
        self.btn_play.configure(text="▶")
        if self._player_poll_id:
            self.after_cancel(self._player_poll_id)
            self._player_poll_id = None

    def _kill_proc(self):
        if self._player_proc and self._player_proc.poll() is None:
            try:
                self._player_proc.send_signal(signal.SIGCONT)  # unfreeze if paused
                self._player_proc.terminate()
            except Exception:
                pass
        self._player_proc = None

    def _player_seek(self, delta: float):
        if not self._player_path:
            return
        new_pos = max(0, min(self._current_pos() + delta, self._player_duration))
        self._player_play_from(new_pos)

    def _current_pos(self) -> float:
        if self._player_paused or self._player_start_t is None:
            return self._player_pos
        return self._player_pos + (time.time() - self._player_start_t)

    def _update_time_label(self, pos: float):
        def fmt(s):
            m, sec = divmod(int(s), 60)
            return f"{m}:{sec:02d}"
        self.var_player_time.set(f"{fmt(pos)} / {fmt(self._player_duration)}")

    def _player_poll(self):
        if self._player_paused:
            return
        # Check if afplay finished naturally
        if self._player_proc and self._player_proc.poll() is not None:
            self._player_stop()
            return
        pos = self._current_pos()
        self._update_time_label(pos)
        if not self._slider_dragging and self._player_duration > 0:
            self.player_seek_var.set(pos / self._player_duration * 100)
        self._player_poll_id = self.after(500, self._player_poll)

    def _on_slider_move(self, val):
        if self._slider_dragging and self._player_duration > 0:
            pos = float(val) / 100 * self._player_duration
            self._update_time_label(pos)

    def _on_slider_release(self, event):
        self._slider_dragging = False
        if self._player_path and self._player_duration > 0:
            pos = self.player_seek_var.get() / 100 * self._player_duration
            self._player_play_from(pos)

    # ── Rename Files ──────────────────────────────────────────────────────────

    def _build_rename_content(self, parent):
        pad = {"padx": 20, "pady": 6}

        tk.Label(parent,
                 text="Remove leading track numbers from filenames.\n"
                      "e.g.  01 - Sasha - Xpander.flac  →  Sasha - Xpander.flac",
                 font=("SF Pro", 12), bg=BG, fg=FG, justify="left").pack(**pad, anchor="w")

        fp = tk.Frame(parent, bg=BG)
        fp.pack(fill="x", **pad)
        tk.Label(fp, text="Target folder:", bg=BG, fg=FG,
                 font=("SF Pro", 12)).grid(row=0, column=0, sticky="w")
        self.var_rename_folder = tk.StringVar()
        ttk.Entry(fp, textvariable=self.var_rename_folder, width=55).grid(
            row=0, column=1, padx=8)
        ttk.Button(fp, text="Browse",
                   command=lambda: self.var_rename_folder.set(
                       filedialog.askdirectory(title="Select folder")
                       or self.var_rename_folder.get()
                   )).grid(row=0, column=2)

        ttk.Button(parent, text="🔍  Preview Renames",
                   command=self._preview_renames).pack(padx=20, pady=6, anchor="w")

        cols = ("before", "after", "warning")
        ft = ttk.Frame(parent)
        ft.pack(fill="both", expand=True, padx=20, pady=4)

        self.rename_tree = ttk.Treeview(ft, columns=cols, show="headings", height=14)
        self.rename_tree.heading("before",  text="Before")
        self.rename_tree.heading("after",   text="After")
        self.rename_tree.heading("warning", text="⚠ Warning")
        self.rename_tree.column("before",   width=300)
        self.rename_tree.column("after",    width=300)
        self.rename_tree.column("warning",  width=260)
        self.rename_tree.tag_configure("safe",  foreground=GREEN)
        self.rename_tree.tag_configure("risky", foreground=YELLOW)

        scroll2 = ttk.Scrollbar(ft, orient="vertical", command=self.rename_tree.yview)
        self.rename_tree.configure(yscrollcommand=scroll2.set)
        self.rename_tree.pack(side="left", fill="both", expand=True)
        scroll2.pack(side="left", fill="y")

        self.var_rename_count = tk.StringVar(value="")
        tk.Label(parent, textvariable=self.var_rename_count,
                 font=("SF Pro", 11), bg=BG, fg=ACCENT).pack(anchor="w", padx=20)

        af = tk.Frame(parent, bg=BG)
        af.pack(fill="x", padx=20, pady=8)
        ttk.Button(af, text="✅  Apply Safe Renames",
                   command=lambda: self._apply_renames(skip_warnings=True)
                   ).pack(side="left", padx=4)
        ttk.Button(af, text="⚠  Apply All (incl. warnings)",
                   command=lambda: self._apply_renames(skip_warnings=False)
                   ).pack(side="left", padx=4)
        tk.Label(af, text="← Preview first!", bg=BG, fg=RED,
                 font=("SF Pro", 11)).pack(side="left", padx=10)

    # ── Rename logic ─────────────────────────────────────────────────────────

    def _preview_renames(self):
        folder = Path(self.var_rename_folder.get())
        if not folder.exists():
            messagebox.showerror("Error", "Folder not found.")
            return
        self._renames = proposed_renames(folder)
        self.rename_tree.delete(*self.rename_tree.get_children())
        safe_count = risky_count = 0
        for old, new, warning in self._renames:
            tag = "risky" if warning else "safe"
            self.rename_tree.insert("", "end",
                values=(old.name, new.name, warning or ""), tags=(tag,))
            if warning: risky_count += 1
            else:       safe_count  += 1
        self.var_rename_count.set(
            f"{len(self._renames)} file(s) to rename  —  "
            f"{safe_count} safe  /  {risky_count} with warnings"
        )

    def _apply_renames(self, skip_warnings: bool = True):
        if not self._renames:
            messagebox.showinfo("Nothing to do", "Run Preview first.")
            return
        to_apply = [r for r in self._renames if not (skip_warnings and r[2])]
        skipped  = len(self._renames) - len(to_apply)
        msg = f"Rename {len(to_apply)} file(s)?"
        if skipped:
            msg += f"\n{skipped} file(s) with warnings will be skipped."
        msg += "\n\nThis cannot be undone."
        if not messagebox.askyesno("Confirm", msg):
            return
        done = apply_renames(self._renames, skip_warnings=skip_warnings)
        messagebox.showinfo("Done", f"Renamed {len(done)} file(s).")
        self._preview_renames()

    # ── Folder pickers ────────────────────────────────────────────────────────

    def _pick_source(self):
        d = filedialog.askdirectory(title="Select new music folder")
        if d: self.var_source.set(d)

    def _pick_review(self):
        d = filedialog.askdirectory(title="Select review folder")
        if d: self.var_review.set(d)

    def _rebuild_cache(self):
        library_path = Path(self.var_library.get())
        if not library_path.exists():
            messagebox.showerror("Error", "Library folder not found.")
            return
        self.after(0, lambda: self.var_progress_text.set("Rebuilding cache…"))
        def _do():
            self._scan_start_time = time.time()
            tracks = scan_folder(library_path,
                                 progress_cb=lambda c, t, n: self._set_progress(c, t, n))
            save_library_cache(tracks, library_path)
            n = len(tracks)
            self.after(0, lambda: self.var_progress_text.set(
                f"Cache rebuilt — {n} tracks."))
            self.after(0, lambda: self.progress.configure(value=100))
        threading.Thread(target=_do, daemon=True).start()

    # ── Scan ─────────────────────────────────────────────────────────────────

    def _set_progress(self, current, total, label=""):
        """Thread-safe progress update with ETA."""
        def _update():
            pct = int(current / total * 100) if total else 0
            self.progress["value"] = pct

            # ETA calculation
            elapsed = time.time() - self._scan_start_time
            if current > 0 and elapsed > 1:
                rate = current / elapsed
                remaining = (total - current) / rate
                m, s = divmod(int(remaining), 60)
                eta = f"  —  ~{m}m {s:02d}s left" if remaining > 5 else "  —  almost done"
            else:
                eta = ""

            name = label[:45] + "…" if len(label) > 45 else label
            self.var_progress_text.set(
                f"{name}  ({current}/{total}){eta}"
            )
        self.after(0, _update)

    def _run_scan(self):
        source_path  = Path(self.var_source.get())
        library_path = Path(self.var_library.get())
        if not source_path.exists():
            messagebox.showerror("Error", "New music folder not found.")
            return
        if not library_path.exists():
            messagebox.showerror("Error", "Library folder not found.")
            return
        self.tree.delete(*self.tree.get_children())
        self._results = []
        threading.Thread(target=self._scan_thread, daemon=True).start()

    def _scan_thread(self):
        source_path  = Path(self.var_source.get())
        library_path = Path(self.var_library.get())
        use_fp       = self.var_fingerprint.get()
        threshold    = self.var_fp_threshold.get() / 100.0
        self._scan_start_time = time.time()

        self.after(0, lambda: self.var_progress_text.set("Scanning new music folder…"))
        source_tracks = scan_folder(source_path, with_fingerprint=use_fp,
                                    progress_cb=lambda c, t, n: self._set_progress(c, t, n))

        self.var_progress_text.set("Loading library (checking cache)…")
        self._scan_start_time = time.time()
        library_tracks = load_library_cache(library_path)
        if library_tracks is None:
            self.after(0, lambda: self.var_progress_text.set(
                "Scanning library — first time, building cache…"))
            library_tracks = scan_folder(library_path, with_fingerprint=use_fp,
                                         progress_cb=lambda c, t, n: self._set_progress(c, t, n))
            save_library_cache(library_tracks, library_path)
            n = len(library_tracks)
            self.after(0, lambda: self.var_progress_text.set(f"Cache saved — {n} tracks."))
        else:
            n = len(library_tracks)
            self.after(0, lambda: self.var_progress_text.set(f"Cache loaded — {n} tracks."))

        self.after(0, lambda: self.var_progress_text.set("Comparing…"))
        self._results = find_duplicates(
            source_tracks, library_tracks,
            fingerprint_threshold=threshold,
            progress_cb=lambda c, t, n: self._set_progress(c, t, n),
        )
        results_n = len(self._results)
        self.after(0, self._populate_table)
        self.after(0, lambda: self.var_progress_text.set(
            f"Done — {results_n} duplicate(s) found."))
        self.after(0, lambda: self.progress.configure(value=100))

    def _populate_table(self):
        self.tree.delete(*self.tree.get_children())
        for r in self._results:
            self.tree.insert("", "end", values=(
                r.source_track.path.name,
                r.library_track.path.name,
                r.match_type,
                r.recommendation,
                r.reason,
            ), tags=(r.recommendation,))

    # ── Save / Apply ─────────────────────────────────────────────────────────

    def _save_report(self):
        if not self._results:
            messagebox.showinfo("Nothing to save", "Run a scan first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="dedup_report.json",
        )
        if path:
            save_report(self._results, Path(path))
            messagebox.showinfo("Saved", f"Report saved to:\n{path}")

    def _apply(self):
        if not self._results:
            messagebox.showinfo("Nothing to apply", "Run a scan first.")
            return
        to_move = [r for r in self._results
                   if r.recommendation in ("keep_source", "keep_library")]
        review_items = [r for r in self._results if r.recommendation == "review"]
        msg = (
            f"Move {len(to_move)} inferior file(s) to the review folder.\n"
            f"{len(review_items)} item(s) marked 'review' will NOT be moved.\n\n"
            f"Review folder:\n{self.var_review.get()}\n\nProceed?"
        )
        if not messagebox.askyesno("Confirm", msg):
            return
        review_folder = Path(self.var_review.get())
        wip_root      = Path(self.var_wip.get())
        moved = self._apply_with_folders(self._results, review_folder, wip_root)
        messagebox.showinfo("Done", f"Moved {len(moved)} file(s) to:\n{review_folder}")
        self._run_scan()

    def _apply_with_folders(self, results, review_folder: Path, wip_root: Path):
        """Move inferior tracks to review folder, preserving WIP subfolder names as FolderB_N."""
        review_folder.mkdir(parents=True, exist_ok=True)

        # Map original WIP subfolder name → review subfolder Path (built once per session)
        folder_map: dict[str, Path] = {}

        def _review_dest(file_path: Path) -> Path:
            # Check if file is inside a WIP subfolder (not directly in wip_root)
            try:
                rel = file_path.relative_to(wip_root)
            except ValueError:
                # File is not under WIP (e.g. a library track) — move flat
                return review_folder / file_path.name

            parts = rel.parts
            if len(parts) <= 1:
                # File is directly in WIP root — move flat
                return review_folder / file_path.name

            subfolder_name = parts[0]
            if subfolder_name not in folder_map:
                # Find the next available FolderB_N that doesn't exist yet
                n = 1
                while (review_folder / f"{subfolder_name}_{n}").exists():
                    n += 1
                folder_map[subfolder_name] = review_folder / f"{subfolder_name}_{n}"
                folder_map[subfolder_name].mkdir(parents=True, exist_ok=True)

            return folder_map[subfolder_name] / file_path.name

        moved = []
        for r in results:
            if r.recommendation == "keep_source":
                track = r.library_track
            elif r.recommendation == "keep_library":
                track = r.source_track
            else:
                continue

            dest = _review_dest(track.path)
            if dest.exists():
                continue
            try:
                track.path.rename(dest)
                moved.append((track.path, dest))
            except Exception:
                pass

        return moved


    def _move_and_playlist(self):
        wip      = Path(self.var_wip.get())
        contents = Path(self.var_library.get())

        # Collect audio files (recursive, flat — skip folders and non-audio)
        audio_files = [
            f for f in wip.rglob("*")
            if f.is_file()
            and not f.name.startswith("._")
            and f.suffix.lower() in AUDIO_EXTENSIONS
        ]

        if not audio_files:
            messagebox.showinfo("Nothing to move", "No audio files found in WIP folder.")
            return

        msg = (
            f"Move {len(audio_files)} audio file(s) from WIP to Contents.\n\n"
            f"From: {wip}\n"
            f"To:   {contents}\n\n"
            f"Files will be placed directly in Contents (no subfolders).\n"
            f"Album art and folders are left in place.\n"
        )
        if self._results:
            msg += "\nA rekordbox XML playlist will also be exported.\n"
        msg += "\nProceed?"

        if not messagebox.askyesno("Confirm", msg):
            return

        threading.Thread(
            target=self._move_and_playlist_thread,
            args=(audio_files, wip, contents),
            daemon=True,
        ).start()

    def _move_and_playlist_thread(self, audio_files: list, wip: Path, contents: Path):
        import sys
        import tempfile

        self.after(0, lambda: self.var_progress_text.set("Moving files…"))
        moved   = []
        skipped = []

        for i, src in enumerate(audio_files):
            dest = contents / src.name
            self.after(0, lambda i=i, n=src.name: self._set_progress(i + 1, len(audio_files), n))
            if dest.exists():
                skipped.append(src)
                continue
            try:
                shutil.move(str(src), str(dest))
                moved.append((src, dest))
            except Exception:
                skipped.append(src)

        # Generate XML playlist if a scan was done
        xml_path = None
        if self._results:
            tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
            tmp_json = Path(tmp.name)
            tmp.close()
            save_report(self._results, tmp_json)

            xml_path = str(Path.home() / "Music" / "dublettax_playlists.xml")
            script   = Path(__file__).parent / "rekordbox_playlists.py"
            _subprocess.run([
                sys.executable, str(script),
                str(tmp_json), "--xml", xml_path,
                "--wip",      str(wip),
                "--contents", str(contents),
            ])
            tmp_json.unlink(missing_ok=True)

        def _done():
            self.var_progress_text.set(f"Done. Moved {len(moved)} file(s).")
            result_msg = f"Moved {len(moved)} file(s) to:\n{contents}"
            if skipped:
                result_msg += f"\n\n{len(skipped)} file(s) skipped (already exist at destination)."
            if xml_path:
                result_msg += (
                    f"\n\nPlaylist XML saved to:\n{xml_path}\n\n"
                    f"Import via Rekordbox:\nFile > Import Playlist > rekordbox xml"
                )
            messagebox.showinfo("Complete", result_msg)

        self.after(0, _done)


class WIPHandler(FileSystemEventHandler):
    def __init__(self, app: App):
        super().__init__()
        self.app = app

    def on_created(self, event):
        self.app._wip_log(f"  + {Path(event.src_path).name}")
        self.app.after(0, self.app._schedule_wip_processing)

    def on_moved(self, event):
        self.app._wip_log(f"  → {Path(event.dest_path).name}")
        self.app.after(0, self.app._schedule_wip_processing)


if __name__ == "__main__":
    app = App()
    app.lift()
    app.attributes("-topmost", True)
    app.after(500, lambda: app.attributes("-topmost", False))
    app.mainloop()
