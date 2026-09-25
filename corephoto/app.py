"""
Core Photo Tool - desktop app.

Three steps: pick folders, check the crop on a real photo, fill in the labels
and run. Everything runs on this machine; nothing is uploaded anywhere.

The label table has one row PER PHOTO, not per pair. That is deliberate: pairing
on adjacency breaks the moment somebody reshoots a tray, and the breakage is
silent - every later tray gets the wrong depth. An explicit tray number per photo
cannot drift, and it gives you somewhere to override a wrong dry/wet call.
"""
import os
import queue
import threading
import traceback
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import cv2
import numpy as np
from PIL import Image, ImageTk

from . import __version__, core, logfile, naming, ocr

# Drag-and-drop needs a different root window class, so decide before App is
# defined. If the library is missing the app still works - you just use the
# Add folder / Add files buttons.
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    _Root = TkinterDnD.Tk
    HAVE_DND = True
except Exception:                                    # pragma: no cover
    _Root = tk.Tk
    DND_FILES = None
    HAVE_DND = False

APP_TITLE = f"Core Photo Tool {__version__}"

# Scanning only needs the crop for dry/wet luminance, the label reading and a
# thumbnail. Doing that at half size is four times less pixel work per photo;
# the old code rendered every photo at the full 5952 px during the scan and
# then the run rendered them all again. Saving is always full resolution.
SCAN_REDUCE = 2

# Above this L* gap between a tray's two shots, the dry/wet call is taken as
# settled; below it the pair is listed for a human to look at. 12 was read off
# the DD26ZOP set, where the core is pale and the wet/dry contrast is large. On
# the darker UG26ZOP core, pairs that are obvious to the eye separate by 4-6,
# so 12 flags most of the batch and the list stops meaning anything.
DRY_WET_MARGIN = 6.0
UNCONFIRMED_BG = "#fff3cd"      # amber: filled by OCR, not yet checked by a human
CONFIRMED_BG = "#ffffff"
HERE = os.path.dirname(os.path.abspath(__file__))
THUMB_COL = 600      # px reserved for the label-bar image column
ROW_H = 62           # px per label row


def cv_to_tk(img, max_w, max_h):
    h, w = img.shape[:2]
    s = min(max_w / w, max_h / h, 1.0)
    if s < 1.0:
        img = cv2.resize(img, (max(int(w * s), 1), max(int(h * s), 1)),
                         interpolation=cv2.INTER_AREA)
    return ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))


class FolderState:
    """One input subfolder = one hole. Vars live here so values survive the user
    switching between folders and the widgets being rebuilt."""
    def __init__(self, rel, photos):
        self.rel = rel
        self.photos = sorted(photos)
        self.hole = tk.StringVar()
        self.first_start = tk.StringVar()
        guess = naming.suggest_trays(self.photos)
        self.rows = [dict(src=s,
                          tray=tk.StringVar(value=str(guess[s])),
                          depth=tk.StringVar(),
                          cond=tk.StringVar(value="Auto"),
                          confirmed=True,          # nothing auto-filled yet
                          tray_confirmed=True) for s in self.photos]


class App(_Root):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x840")
        self.minsize(1080, 740)
        self._theme()
        self._icon()

        self.params = core.Params()
        self.sources = []                 # folders and/or individual image files
        self.outdir = tk.StringVar()
        self.normalise = tk.BooleanVar(value=True)
        self.use_ocr = tk.BooleanVar(value=ocr.available())
        self.show_raw = tk.BooleanVar(value=False)

        self.photos, self.det, self.core_L, self.folders = [], {}, {}, {}
        self.recovered = set()   # photos the tray finder could not read
        self.adj = {}            # path -> [[dx,dy] x4] manual corner nudges
        self.profiles = {}       # folder -> calibrated box, see _apply_box_all
        self._drag = None
        self._pv = None          # canvas<->image mapping for the preview
        self.preview_idx = 0
        self._q = queue.Queue()
        self._busy = False
        # True only while the detection results are being replaced. Separate
        # from _busy on purpose: _scan_done runs on the main thread BEFORE the
        # callback that clears _busy, so a preview drawn from inside it was
        # thrown away by the _busy guard and the first photo came up blank
        # until you stepped to the next one and back.
        self._det_lock = False
        self._thumbs = []

        self._build()
        self._drain_id = self.after(80, self._drain)
        logfile.session_header(__version__)

    # ------------------------------------------------------------ chrome
    def _theme(self):
        self.tk_theme = "default"
        try:
            import sv_ttk
            sv_ttk.set_theme("light")
            self.tk_theme = "sv"
            return
        except Exception:
            pass
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")

    def _icon(self):
        for name in ("icon.png", "icon.ico"):
            p = os.path.join(HERE, name)
            if os.path.exists(p):
                try:
                    if name.endswith(".ico"):
                        self.iconbitmap(p)
                    else:
                        self._ico = ImageTk.PhotoImage(Image.open(p))
                        self.iconphoto(True, self._ico)
                    return
                except Exception:
                    pass

    def _build(self):
        s = ttk.Style(self)
        s.configure("Head.TLabel", font=("Segoe UI", 14, "bold"))
        s.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#6b6b6b")
        s.configure("Hint.TLabel", foreground="#6b6b6b")
        s.configure("Warn.TLabel", foreground="#a33")
        s.configure("Go.TButton", font=("Segoe UI", 10, "bold"))

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=14, pady=(14, 6))
        self.tab1 = ttk.Frame(self.nb, padding=6); self.nb.add(self.tab1, text="  1  Folders  ")
        self.tab2 = ttk.Frame(self.nb, padding=6); self.nb.add(self.tab2, text="  2  Check the crop  ")
        self.tab3 = ttk.Frame(self.nb, padding=6); self.nb.add(self.tab3, text="  3  Labels and run  ")
        self._build_t1(); self._build_t2(); self._build_t3()

        bar = ttk.Frame(self); bar.pack(fill="x", padx=16, pady=(0, 12))
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = ttk.Label(bar, text="Ready", style="Hint.TLabel")
        self.status.pack(side="right", padx=(14, 0))

    # ------------------------------------------------------------- tab 1
    def _build_t1(self):
        f = self.tab1
        ttk.Label(f, text="Which photos?", style="Head.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(14, 2))
        hint = ("Add whole folders, or just the few photos you want. "
                "One folder per hole. Your originals are never modified.")
        if HAVE_DND:
            hint = "Drag photos or folders straight onto the list below. " + hint
        ttk.Label(f, text=hint, style="Sub.TLabel", wraplength=1120,
                  justify="left").grid(row=1, column=0, columnspan=3, sticky="w",
                                       padx=14, pady=(0, 10))

        listwrap = ttk.Frame(f)
        listwrap.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(14, 8))
        self.srclist = tk.Listbox(listwrap, height=7, activestyle="none",
                                  selectmode="extended", bd=1, relief="solid",
                                  highlightthickness=0, font=("Segoe UI", 9))
        sb = ttk.Scrollbar(listwrap, orient="vertical", command=self.srclist.yview)
        self.srclist.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); self.srclist.pack(side="left", fill="both", expand=True)
        if HAVE_DND:
            for w in (self.srclist, f):
                try:
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass

        btns = ttk.Frame(f); btns.grid(row=2, column=2, sticky="n", padx=(0, 14))
        ttk.Button(btns, text="Add folder…", command=self._add_folder).pack(fill="x", pady=2)
        ttk.Button(btns, text="Add files…", command=self._add_files).pack(fill="x", pady=2)
        ttk.Button(btns, text="Remove", command=self._remove_sel).pack(fill="x", pady=2)
        ttk.Button(btns, text="Clear", command=self._clear_sources).pack(fill="x", pady=2)

        out = ttk.Frame(f); out.grid(row=3, column=0, columnspan=3, sticky="we",
                                     padx=14, pady=(12, 4))
        ttk.Label(out, text="Save results to").pack(side="left")
        ttk.Entry(out, textvariable=self.outdir).pack(side="left", fill="x",
                                                      expand=True, padx=10)
        ttk.Button(out, text="Browse", command=self._pick_out).pack(side="left")

        opts = ttk.Frame(f); opts.grid(row=4, column=0, columnspan=3, sticky="w",
                                       padx=14, pady=(12, 4))
        ttk.Checkbutton(opts, text="Correct exposure and white balance "
                                   "(referenced to the tape measure)",
                        variable=self.normalise).pack(anchor="w", pady=2)
        self.ocr_cb = ttk.Checkbutton(
            opts, text="Pre-fill depths by reading the label bar  —  a hint only, "
                       "shown in amber until you check it",
            variable=self.use_ocr)
        self.ocr_cb.pack(anchor="w", pady=2)
        ocrrow = ttk.Frame(opts); ocrrow.pack(anchor="w", pady=(2, 0))
        self.ocr_lbl = ttk.Label(ocrrow, text="", style="Hint.TLabel")
        self.ocr_lbl.pack(side="left")
        ttk.Button(ocrrow, text="Locate Tesseract (optional)…",
                   command=self._locate_tesseract).pack(side="left", padx=8)
        ttk.Button(ocrrow, text="Re-check", command=self._refresh_ocr).pack(side="left")
        self._refresh_ocr()

        self.scan_btn = ttk.Button(f, text="Scan photos", style="Go.TButton", command=self.scan)
        self.scan_btn.grid(row=5, column=0, sticky="w", padx=14, pady=(14, 8))

        self.log = tk.Text(f, height=9, relief="flat", bg="#fbfbfb", bd=1,
                           highlightthickness=1, highlightbackground="#dcdcdc",
                           font=("Consolas", 9), padx=10, pady=8)
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=14, pady=(4, 12))
        f.columnconfigure(1, weight=1); f.rowconfigure(6, weight=1)

    # ----------------------------------------------------------- sources
    def _add_sources(self, paths):
        added = 0
        for p in paths:
            p = os.path.abspath(p.strip())
            if not p or not os.path.exists(p):
                continue
            if os.path.isfile(p) and not p.lower().endswith(core.IMAGE_EXT):
                continue
            if any(os.path.normcase(p) == os.path.normcase(q) for q in self.sources):
                continue
            self.sources.append(p)
            added += 1
        self._refresh_sources()
        if added and not self.outdir.get():
            first = self.sources[0]
            base = first if os.path.isdir(first) else os.path.dirname(first)
            self.outdir.set(os.path.join(base, "processed"))
        return added

    def _refresh_sources(self):
        self.srclist.delete(0, "end")
        for p in self.sources:
            tag = "[folder]" if os.path.isdir(p) else "[file]  "
            self.srclist.insert("end", f"{tag}  {p}")

    def _on_drop(self, event):
        # tk returns a brace-quoted list when paths contain spaces
        paths = self.tk.splitlist(event.data)
        n = self._add_sources(paths)
        self.status.configure(text=f"added {n} item(s)")

    def _add_folder(self):
        d = filedialog.askdirectory(title="Folder with core photos")
        if d:
            self._add_sources([d])

    def _add_files(self):
        fs = filedialog.askopenfilenames(
            title="Choose photos",
            filetypes=[("JPEG images", "*.jpg *.jpeg *.JPG *.JPEG"), ("All files", "*.*")])
        if fs:
            self._add_sources(list(fs))

    def _remove_sel(self):
        for i in sorted(self.srclist.curselection(), reverse=True):
            del self.sources[i]
        self._refresh_sources()

    def _clear_sources(self):
        self.sources = []
        self._refresh_sources()

    # ------------------------------------------------------------ tesseract
    def _refresh_ocr(self):
        """The built-in reader always works; Tesseract is only a fallback."""
        if glyphs_available := ocr.reader_name():
            self.ocr_cb.state(["!disabled"])
            extra = ""
            if not ocr.tesseract_available():
                extra = "   (Tesseract not installed — not needed)"
            self.ocr_lbl.configure(text=f"Reader: {glyphs_available}{extra}")
        else:
            self.use_ocr.set(False)
            self.ocr_cb.state(["disabled"])
            self.ocr_lbl.configure(text="No reader available — depths start blank.")

    def _locate_tesseract(self):
        p = filedialog.askopenfilename(
            title="Find tesseract.exe",
            initialdir=r"C:\Program Files\Tesseract-OCR" if os.name == "nt" else "/usr/bin",
            filetypes=[("tesseract", "tesseract.exe tesseract"), ("All files", "*.*")])
        if not p:
            return
        if ocr.set_manual_path(p):
            self.use_ocr.set(True)
            self._refresh_ocr()
            messagebox.showinfo(APP_TITLE, "Tesseract found and remembered.\n\n"
                                           "It is only a fallback — the built-in "
                                           "reader is used first.")
        else:
            messagebox.showwarning(
                APP_TITLE,
                "That file did not run as Tesseract.\n\nLooked for it in:\n  • "
                + "\n  • ".join(ocr.search_paths()))

    # ------------------------------------------------------------- tab 2
    def _build_t2(self):
        f = self.tab2
        top = ttk.Frame(f); top.pack(fill="x", padx=14, pady=(14, 4))
        ttk.Label(top, text="Does the red box sit on the tray?",
                  style="Head.TLabel").pack(side="left")
        ttk.Button(top, text="Next ›", command=lambda: self._step(1)).pack(side="right", padx=4)
        ttk.Button(top, text="‹ Prev", command=lambda: self._step(-1)).pack(side="right", padx=4)
        self.pick = ttk.Combobox(top, width=44, state="readonly")
        self.pick.pack(side="right", padx=12)
        self.pick.bind("<<ComboboxSelected>>", lambda e: self._set_idx(self.pick.current()))

        ttk.Label(f, text="Drag the yellow corner handles to fit the tray exactly — useful "
                          "when the photo is slightly off-square and the tray looks "
                          "keystoned. Drag inside the box to move the whole thing. The "
                          "slider changes the starting width for every photo.",
                  style="Sub.TLabel", wraplength=1150, justify="left").pack(
                      anchor="w", padx=14, pady=(0, 8))

        mid = ttk.Frame(f); mid.pack(fill="both", expand=True, padx=14)
        lf = ttk.LabelFrame(mid, text=" Original photo — drag the corners ", padding=8)
        lf.pack(side="left", fill="both", expand=True, padx=(0, 10))
        self.cv_orig = tk.Canvas(lf, bg="#1e1e1e", width=560, height=400,
                                 highlightthickness=0, cursor="crosshair")
        self.cv_orig.pack(fill="both", expand=True)
        self.cv_orig.bind("<Button-1>", self._grab)
        self.cv_orig.bind("<B1-Motion>", self._drag_move)
        self.cv_orig.bind("<ButtonRelease-1>", self._drop)
        rf = ttk.LabelFrame(mid, text=" Result ", padding=8)
        rf.pack(side="left", fill="both", expand=True)
        self.cv_res = tk.Label(rf, bg="#1e1e1e"); self.cv_res.pack(fill="both", expand=True)

        ctl = ttk.Frame(f); ctl.pack(fill="x", padx=14, pady=(12, 4))
        ttk.Label(ctl, text="Tray width").pack(side="left")
        self.sl_lbl = ttk.Label(ctl, text=str(self.params.tray_width), width=5)
        self.sl = ttk.Scale(ctl, from_=600, to=860, orient="horizontal", length=300)
        self.sl.set(self.params.tray_width)
        self.sl.configure(command=self._slide)     # attach AFTER set so it cannot fire early
        self.sl.pack(side="left", padx=12)
        self.sl_lbl.pack(side="left")
        ttk.Button(ctl, text="Reset width",
                   command=lambda: self.sl.set(744)).pack(side="left", padx=8)
        ttk.Checkbutton(ctl, text="Show without colour correction", variable=self.show_raw,
                        command=self._draw_preview).pack(side="left", padx=16)

        ctl2 = ttk.Frame(f); ctl2.pack(fill="x", padx=14, pady=(0, 4))
        ttk.Button(ctl2, text="Undo my corner changes on this photo",
                   command=self._reset_adj).pack(side="left")
        ttk.Button(ctl2, text="Use this box for every photo in this folder",
                   command=self._apply_box_all).pack(side="left", padx=10)
        self.adj_lbl = ttk.Label(ctl2, text="", style="Hint.TLabel")
        self.adj_lbl.pack(side="left", padx=12)
        self.info = ttk.Label(f, text="", style="Hint.TLabel")
        self.info.pack(anchor="w", padx=14, pady=(0, 8))

    # ------------------------------------------------------------- tab 3
    def _build_t3(self):
        f = self.tab3
        top = ttk.Frame(f); top.pack(fill="x", padx=14, pady=(14, 2))
        ttk.Label(top, text="Read each label bar and fill in the numbers",
                  style="Head.TLabel").pack(side="left")
        ttk.Label(top, text="Folder").pack(side="left", padx=(30, 6))
        self.folder_pick = ttk.Combobox(top, width=20, state="readonly")
        self.folder_pick.pack(side="left")
        self.folder_pick.bind("<<ComboboxSelected>>", lambda e: self._show_folder())
        ttk.Label(top, text="Hole").pack(side="left", padx=(18, 6))
        self.hole_entry = ttk.Entry(top, width=18); self.hole_entry.pack(side="left")

        second = ttk.Frame(f); second.pack(fill="x", padx=14, pady=(10, 2))
        ttk.Label(second, text="First tray starts at (m)").pack(side="left")
        self.start_entry = ttk.Entry(second, width=10)
        self.start_entry.pack(side="left", padx=(8, 0))
        ttk.Label(second, text="Every later tray starts where the previous one ended.",
                  style="Hint.TLabel").pack(side="left", padx=12)

        ttk.Label(f, text="One row per photo. Two rows sharing a tray number are that tray's "
                          "dry and wet shots — change a tray number if a reshoot has put "
                          "them out of step. Depth is needed once per tray.",
                  style="Sub.TLabel", wraplength=1120, justify="left").pack(
                      anchor="w", padx=14, pady=(8, 8))

        hdr = ttk.Frame(f); hdr.pack(fill="x", padx=14)
        self._col(hdr, "Label bar", THUMB_COL)
        self._col(hdr, "Tray", 74)
        self._col(hdr, "End depth (m)", 124)
        self._col(hdr, "Dry / Wet", 104)
        self._col(hdr, "File", 200)

        wrap = ttk.Frame(f); wrap.pack(fill="both", expand=True, padx=14, pady=(4, 8))
        self.canvas = tk.Canvas(wrap, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#dcdcdc")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y"); self.canvas.pack(side="left", fill="both", expand=True)
        self.rowsf = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.rowsf, anchor="nw")
        self.rowsf.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        # bind, not bind_all: bind_all sent the wheel to this table from
        # anywhere in the app, so scrolling the log on step 1 scrolled step 3.
        self._bind_wheel(self.canvas)

        bot = ttk.Frame(f); bot.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(bot, text="Check", command=self.validate).pack(side="left")
        self.run_btn = ttk.Button(bot, text="Process and save all folders",
                                  style="Go.TButton", command=self.run)
        self.run_btn.pack(side="left", padx=12)
        self.t3msg = ttk.Label(bot, text="", style="Hint.TLabel")
        self.t3msg.pack(side="left", padx=14)

    # ------------------------------------------------------------ plumbing
    def _wheel(self, e=None, d=None):
        if d is None:
            d = int(-getattr(e, "delta", 0) / 120) or 0
        try:
            if d:
                self.canvas.yview_scroll(d, "units")
        except tk.TclError:
            pass
        return "break"      # stops ttk's own wheel binding on the combobox

    def _bind_wheel(self, w):
        """Wheel scrolling from any widget inside the QC table.

        Binding only the canvas and its frame did nothing once the rows were
        built: the rows cover the canvas completely, so the pointer is always
        over a child widget and the wheel event never reached anything that
        knew how to scroll. Every descendant needs the binding.

        The Dry/Wet combobox makes this more than a convenience. ttk binds the
        wheel on a combobox to CHANGE ITS VALUE, so a scroll that landed on
        that column silently edited the data instead of moving the list.
        Returning "break" from a per-widget binding stops the class binding
        from running, which kills that off as well.
        """
        try:
            w.bind("<MouseWheel>", self._wheel)
            w.bind("<Button-4>", lambda e: self._wheel(e, -1))
            w.bind("<Button-5>", lambda e: self._wheel(e, 1))
        except tk.TclError:
            return
        for c in w.winfo_children():
            self._bind_wheel(c)

    @staticmethod
    def _label(path):
        """Short, unambiguous name: parent folder + filename."""
        return os.path.join(os.path.basename(os.path.dirname(path)),
                            os.path.basename(path)).replace(os.sep, "/")

    def say(self, m):
        self.log.insert("end", m + "\n"); self.log.see("end")

    def _pick_out(self):
        d = filedialog.askdirectory(title="Where to save results")
        if d:
            self.outdir.set(d)

    def _drain(self):
        try:
            while True:
                self._q.get_nowait()()
        except queue.Empty:
            pass
        self._drain_id = self.after(80, self._drain)

    def destroy(self):
        # Without this, closing the window leaves the 80 ms drain timer armed
        # and Tk prints `invalid command name ..._drain` on the way out.
        try:
            if getattr(self, "_drain_id", None):
                self.after_cancel(self._drain_id)
                self._drain_id = None
        except Exception:
            pass
        super().destroy()

    def _bg(self, work):
        if self._busy:
            return
        self._busy = True
        self.scan_btn.state(["disabled"]); self.run_btn.state(["disabled"])

        def wrapper():
            try:
                work()
            except Exception:
                tb = traceback.format_exc()
                self._q.put(lambda: messagebox.showerror(APP_TITLE, tb[-1800:]))
            finally:
                def done():
                    self._busy = False
                    self._det_lock = False
                    self.scan_btn.state(["!disabled"]); self.run_btn.state(["!disabled"])
                self._q.put(done)
        threading.Thread(target=wrapper, daemon=True).start()

    def _set_progress(self, i, n, text=""):
        def f():
            self.progress["maximum"] = max(n, 1)
            self.progress["value"] = i
            self.status.configure(text=text or f"{i}/{n}")
        self._q.put(f)

    # ---------------------------------------------------------------- scan
    def scan(self):
        if not self.sources:
            messagebox.showwarning(APP_TITLE, "Add a folder or some photos first.")
            return
        first = self.sources[0]
        base = first if os.path.isdir(first) else os.path.dirname(first)
        outdir = self.outdir.get().strip() or os.path.join(base, "processed")
        self.outdir.set(outdir)
        self.params.normalise = self.normalise.get()
        self.log.delete("1.0", "end")
        self._det_lock = True
        self._bg(lambda: self._scan_work(list(self.sources), outdir))

    def _scan_work(self, sources, outdir):
        """Worker thread. Produces PLAIN DATA only.

        Nothing here may create or set a tkinter variable. The previous version
        built FolderState - and therefore StringVars - on this thread, and then
        wrote to them from here too. It happens to survive on a Tcl built with
        threading, which is why it never failed here, but it is the classic
        cause of a tkinter app that crashes on one machine and nobody else's.
        Everything now goes back to the main thread in `_scan_done`.
        """
        photos = core.find_photos(sources, skip_dirs=(outdir,))
        do_ocr = self.use_ocr.get() and ocr.available()
        if not photos:
            self._q.put(lambda: messagebox.showwarning(
                APP_TITLE, "No photos found (looked for "
                           + ", ".join(core.IMAGE_EXT) + ")."))
            return
        logfile.write(f"scan: {len(photos)} photo(s), ocr={do_ocr}, out={outdir}")
        self._q.put(lambda: self.say(f"Found {len(photos)} photos."))
        det, coreL, reads, recovered = {}, {}, {}, []
        for i, p in enumerate(photos, 1):
            self._set_progress(i, len(photos), f"Reading {i} of {len(photos)}")
            try:
                d = core.detect(p)
                if d is None:
                    # Do not drop it. A photo the tray finder cannot read used
                    # to be logged as "skipped" and then vanish, with no way to
                    # rescue it from inside the app. It now arrives with a
                    # default box the corner handles can be dragged onto.
                    d = core.manual_detect(p)
                    if d is None:
                        logfile.write(f"  unreadable file: {p}")
                        continue
                    recovered.append(p)
                det[p] = d
                crop, _ = core.render(p, d, self.params, reduce=SCAN_REDUCE)
                coreL[p] = core.core_stats(crop)[0]
                if do_ocr:
                    reads[p] = ocr.read_label(
                        core.label_strips(crop, d, self.params))
                w = 1500
                d["strip"] = core.label_strip(
                    cv2.resize(crop, (w, max(int(w * crop.shape[0] / crop.shape[1]), 1)),
                               interpolation=cv2.INTER_AREA))
                logfile.write(
                    f"  {os.path.basename(p)}: bar={d['bar_method']} "
                    f"tilt={d['angle']:+.2f} tray_px={d['tray_px']} "
                    f"L={coreL[p]:.1f} read={reads.get(p)}")
            except Exception:
                logfile.exception(f"scan {p}")
        ordered = [p for p in photos if p in det]
        self._q.put(lambda: self._scan_done(ordered, det, coreL, reads,
                                            recovered, do_ocr))

    def _scan_done(self, photos, det, coreL, reads, recovered, do_ocr):
        """Main thread. Builds all the tkinter state."""
        self.photos, self.det, self.core_L = photos, det, coreL
        self.recovered = set(recovered)
        self._det_lock = False          # new data is in place; previews may draw

        # Seed the tray width from what was actually measured, so a different
        # camera height does not silently crop every photo wrong until somebody
        # finds the slider. Across 19 UG26ZOP photos the detected width sat
        # between 712 and 742 px against a hard-coded default of 744.
        widths = [d["tray_px"] for p, d in det.items() if p not in self.recovered]
        if len(widths) >= 3:
            auto = int(np.median(widths))
            if 560 <= auto <= 900 and abs(auto - self.params.tray_width) > 4:
                self.params.tray_width = auto
                try:
                    self.sl.set(auto)
                except Exception:
                    pass
                self.say(f"Tray width measured at {auto} px "
                         f"(slider moved; adjust it if the box looks wrong).")

        # Lock the whole batch to one tray shape, measured from the photos the
        # shape check passed. The bottom rail is the least reliable edge, and
        # letting it set each crop's height independently is what produced a
        # set at aspects 2.15, 2.55 and 4.09. With it locked every photo in a
        # batch comes out on an identical canvas.
        good = [d["tray_aspect"] for d in det.values() if d.get("aspect_ok")]
        if len(good) >= 3:
            self.params.tray_aspect = float(np.median(good))
            self.say(f"Tray shape measured at {self.params.tray_aspect:.2f} "
                     f"(width ÷ height) from {len(good)} photos; every crop in "
                     f"this batch will use it, so they all come out the same size.")

        # Each photo says whether the box it found has the right SHAPE for a
        # core tray. That is a far better health check than the spread of
        # measured widths, because it judges each photo on its own.
        shaky = [p for p, d in det.items() if not d.get("aspect_ok", True)]
        methods = {}
        for d in det.values():
            methods[d.get("mask_method", "?")] = methods.get(d.get("mask_method", "?"), 0) + 1
        if methods:
            self.say("  tray found by: " +
                     ", ".join(f"{k} on {v}" for k, v in sorted(methods.items())))
        if shaky:
            self.say("")
            self.say(f"  ! {len(shaky)} of {len(det)} photo(s) gave a box that is "
                     f"the wrong shape for a core tray, so the crop on those is "
                     f"probably wrong.")
            self.say("    Fix the box on ONE photo in step 2, then press "
                     "\"Use this box for every photo in this folder\" - it "
                     "re-anchors on each photo's own label bar.")
            logfile.write(f"shape check failed on {len(shaky)}/{len(det)}: "
                          + ", ".join(os.path.basename(p) for p in shaky[:8]))

        by = {}
        for p in self.photos:
            by.setdefault(os.path.dirname(p), []).append(p)
        self.folders = {}
        for d, ps in by.items():
            name = os.path.basename(d) or d
            while name in self.folders:            # two folders, same basename
                name += "_"
            fs = FolderState(d, ps)
            # Seed the hole from the folder name. An empty Hole field produced
            # files called "_Dry_Tray1_...", which is the sort of thing you only
            # notice after processing a whole hole.
            fs.hole.set(name)
            self.folders[name] = fs

        if do_ocr:
            self._apply_reads(reads)

        def finish():
            for p in sorted(self.recovered):
                self.say(f"  no tray found in {os.path.basename(p)} - added with a "
                         f"default box, drag its corners on step 2")
            cut = sum(1 for p in self.photos
                      if self.det[p]["cut_left"] or self.det[p]["cut_right"])
            self.say(f"Detected trays in {len(self.photos)} photos.")
            if cut:
                self.say(f"  {cut} photo(s) have the tray running past the frame edge; "
                         f"those ends are padded grey and flagged in the manifest.")
            for name, fs in self.folders.items():
                self.say(f"  {name}: {len(fs.photos)} photos "
                         f"({len(fs.photos)//2} trays expected)")
            self.pick["values"] = [self._label(p) for p in self.photos]
            if self.photos:
                self.pick.current(0)
            self.folder_pick["values"] = list(self.folders.keys())
            if self.folders:
                self.folder_pick.current(0)
            self._show_folder()
            # Select the tab and let it lay out BEFORE the first draw. On an
            # unmapped canvas winfo_width() is 1, so the photo was scaled to a
            # fallback 560x400 and the crop handles sat away from the box.
            self.nb.select(self.tab2)
            self.update_idletasks()
            self._set_idx(0)
            self.status.configure(text="Scan complete")
        finish()

    def _apply_reads(self, reads):
        """Put the label readings into the table. Main thread only.

        TRAY numbers are filled first, then depths are grouped BY TRAY. Both
        shots of a tray carry the same depth, so a per-photo sequence check
        rejects the second one every time - the gap to the previous reading is
        zero. Grouping also lets one good read cover its partner.

        Everything lands amber - unconfirmed - until a human passes over it.
        The hole name is never touched: the reader does not read hole numbers
        (see ocr.read_label for why), and the folder name is a better guess.
        """
        filled_t = filled_d = 0
        for fs in self.folders.values():
            for r in fs.rows:
                t = (reads.get(r["src"]) or {}).get("tray")
                if t is not None and str(t) != r["tray"].get():
                    r["tray"].set(str(t))
                    r["tray_confirmed"] = False
                    filled_t += 1
            trays = {}
            for r in fs.rows:
                try:
                    t = int(r["tray"].get())
                except ValueError:
                    continue
                trays.setdefault(t, []).append(r)
            prev = prev_t = None
            for t in sorted(trays):
                rows = trays[t]
                seen = [(reads.get(r["src"]) or {}).get("depth") for r in rows]
                seen = [v for v in seen if v is not None]
                if not seen or len(set(seen)) > 1:
                    continue          # nothing read, or the two shots disagree
                # How many trays back the comparison depth came from. Without
                # this the window stays one tray wide while the real gap grows,
                # so a single unread tray rejects every tray after it.
                since = 1 if prev_t is None else max(t - prev_t, 1)
                v = ocr.veto_depth(seen[0], prev, trays_since=since)
                if v is None:
                    continue          # cannot be right given the previous tray
                prev, prev_t = v, t
                for r in rows:
                    r["depth"].set(f"{v:.2f}")
                    r["confirmed"] = False
                    filled_d += 1
        for fs in self.folders.values():
            self._seed_start(fs)
        total = sum(len(fs.rows) for fs in self.folders.values())
        self.say(f"  read {filled_d} of {total} depths and {filled_t} tray numbers "
                 f"off the label bars (amber = unchecked, please look them over)")
        logfile.write(f"reads: depths {filled_d}/{total}, trays {filled_t}/{total}")

    # ------------------------------------------------------------- preview
    def _step(self, k):
        if self.photos:
            self._set_idx((self.preview_idx + k) % len(self.photos))

    def _set_idx(self, i):
        if not self.photos:
            return
        self.preview_idx = max(0, min(i, len(self.photos) - 1))
        self.pick.current(self.preview_idx)
        self._draw_preview()

    def _slide(self, v):
        self.params.tray_width = int(float(v))
        self.sl_lbl.configure(text=str(self.params.tray_width))
        self._draw_preview()

    # -- manual corner adjustment -------------------------------------------
    def _cur_adj(self, path=None):
        path = path or self.photos[self.preview_idx]
        return self.adj.setdefault(path, [[0.0, 0.0] for _ in range(4)])

    def _reset_adj(self):
        if self.photos:
            self.adj[self.photos[self.preview_idx]] = [[0.0, 0.0] for _ in range(4)]
            self._draw_preview()

    def _apply_box_all(self):
        """Calibrate the whole folder from the box on screen.

        This used to copy the four corner NUDGES verbatim. That only helps if
        every photo's automatic box was wrong in the same way, and it is not:
        the label bar is a loose steel bar that shifts between shots, so the
        right box sits at a different height in each frame.

        Instead the corrected box is stored as a rule - keep its width and
        height, re-anchor the top on each photo's OWN detected bar - which
        follows the bar. Measured against four hand-corrected DD_ZOP_014 crops,
        calibrating from any one of them landed the other three to within 1.2%
        of the tray width; per-photo automatic detection was out by 13%.
        """
        if not self.photos:
            return
        cur = self.photos[self.preview_idx]
        folder = os.path.dirname(cur)
        prof = core.profile_from_quad(self.det[cur], self.params, self._cur_adj(cur))
        self.profiles[folder] = prof
        n = 0
        for p in self.photos:
            if os.path.dirname(p) == folder:
                self.adj.pop(p, None)        # the profile replaces per-photo nudges
                n += 1
        self.adj[cur] = [[0.0, 0.0] for _ in range(4)]
        self.adj_lbl.configure(
            text=f"this box now sets all {n} photos in the folder (re-anchored "
                 f"on each one's label bar)")
        logfile.write(f"calibrated folder {folder}: {prof}")
        self._draw_preview()

    def _prof(self, path=None):
        path = path or (self.photos[self.preview_idx] if self.photos else None)
        return self.profiles.get(os.path.dirname(path)) if path else None

    def _canvas_quad(self):
        p = self.photos[self.preview_idx]
        q = core.crop_quad(self.det[p], self.params, self._cur_adj(p), self._prof(p))
        s, ox, oy = self._pv["s"], self._pv["ox"], self._pv["oy"]
        return [(ox + x * s, oy + y * s) for x, y in q]

    def _grab(self, ev):
        if self._busy or not self.photos or not self._pv:
            return
        pts = self._canvas_quad()
        for i, (cx, cy) in enumerate(pts):
            if abs(ev.x - cx) < 14 and abs(ev.y - cy) < 14:
                self._drag = i
                break
        else:
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            self._drag = "all" if (min(xs) < ev.x < max(xs) and
                                   min(ys) < ev.y < max(ys)) else None
        self._last = (ev.x, ev.y)

    def _drag_move(self, ev):
        if self._busy or self._drag is None or not self._pv:
            return
        s = self._pv["s"]
        dx, dy = (ev.x - self._last[0]) / s, (ev.y - self._last[1]) / s
        self._last = (ev.x, ev.y)
        adj = self._cur_adj()
        idxs = range(4) if self._drag == "all" else [self._drag]
        for i in idxs:
            adj[i][0] += dx
            adj[i][1] += dy
        self._draw_outline()          # cheap: outline only while dragging

    def _drop(self, ev):
        if self._drag is not None:
            self._drag = None
            self._draw_preview()      # re-render the result once, on release

    # -- preview -------------------------------------------------------------
    def _draw_outline(self):
        c = self.cv_orig
        c.delete("box")
        pts = self._canvas_quad()
        c.create_polygon([v for pt in pts for v in pt], outline="#ff3b30",
                         fill="", width=2, tags="box")
        for cx, cy in pts:
            c.create_rectangle(cx - 6, cy - 6, cx + 6, cy + 6, fill="#ffd60a",
                               outline="#333", width=1, tags="box")
        moved = any(abs(v) > 0.5 for pt in self._cur_adj() for v in pt)
        self.adj_lbl.configure(text="corners adjusted on this photo" if moved else "")

    def _draw_preview(self):
        # A scan replaces self.det wholesale. Dragging a corner while that is
        # happening used to raise a KeyError from the canvas binding, which is
        # not disabled by the button states. The guard is _det_lock and not
        # _busy so that the first preview of a finished scan can be drawn from
        # inside _scan_done, which runs while _busy is still set.
        if self._det_lock or not self.photos:
            return
        p = self.photos[self.preview_idx]
        d = self.det.get(p)
        if d is None:
            return
        small = d["small"]
        cw = max(self.cv_orig.winfo_width(), 560)
        ch = max(self.cv_orig.winfo_height(), 400)
        # leave a margin: the crop box often extends past the photo edge (the tray
        # runs out of frame), and those corner handles must still be grabbable
        s = min(cw * 0.86 / small.shape[1], ch * 0.86 / small.shape[0])
        disp = cv2.resize(small, (max(int(small.shape[1] * s), 1),
                                  max(int(small.shape[0] * s), 1)),
                          interpolation=cv2.INTER_AREA)
        self._img1 = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(disp, cv2.COLOR_BGR2RGB)))
        ox = (cw - disp.shape[1]) // 2
        oy = (ch - disp.shape[0]) // 2
        self._pv = dict(s=s, ox=ox, oy=oy)
        self.cv_orig.delete("all")
        self.cv_orig.create_image(ox, oy, anchor="nw", image=self._img1)
        self._draw_outline()

        pr = core.Params(**{**self.params.to_dict(),
                            "normalise": self.params.normalise and not self.show_raw.get()})
        crop, gains = core.render(p, d, pr, self._cur_adj(p), prof=self._prof(p))
        self._img2 = cv_to_tk(crop, 540, 400)
        self.cv_res.configure(image=self._img2)
        msg = (f"tilt {d['angle']:+.2f}°    tray found by {d.get('mask_method','?')}"
               f"    shape {d.get('tray_aspect','?')}"
               f"{'' if d.get('aspect_ok', True) else ' (WRONG SHAPE - check this box)'}"
               f"    output {crop.shape[1]}×{crop.shape[0]} px")
        if gains:
            msg += f"    colour gains B/G/R {gains[0]:.2f}/{gains[1]:.2f}/{gains[2]:.2f}"
        if d["cut_left"] or d["cut_right"]:
            msg += "    •  tray runs past the frame edge, ends padded grey"
        self.info.configure(text=msg)

    # ---------------------------------------------------------------- rows
    def _cur(self):
        return self.folders.get(self.folder_pick.get())

    @staticmethod
    def _seed_start(fs):
        """Tray 1 starts at 0.00 m. Nobody should have to type that.

        Only ever fills an EMPTY field: a hole that carries on from a previous
        run starts somewhere else, and a start depth already typed - or
        already seeded and then corrected - must win over the default.
        """
        if fs.first_start.get().strip():
            return
        trays = []
        for r in fs.rows:
            try:
                trays.append(int(r["tray"].get()))
            except ValueError:
                pass
        if trays and min(trays) == 1:
            fs.first_start.set("0.00")

    def _show_folder(self):
        fs = self._cur()
        if not fs:
            return
        self._seed_start(fs)
        self.hole_entry.configure(textvariable=fs.hole)
        self.start_entry.configure(textvariable=fs.first_start)
        self._build_rows()

    def _build_rows(self):
        for w in self.rowsf.winfo_children():
            w.destroy()
        self._thumbs = []
        fs = self._cur()
        if not fs:
            return
        for i, r in enumerate(fs.rows):
            row = ttk.Frame(self.rowsf, padding=(4, 3))
            row.grid(row=i, column=0, sticky="w")
            tbox = tk.Frame(row, width=THUMB_COL, height=ROW_H)
            tbox.pack(side="left"); tbox.pack_propagate(False)
            strip = self.det[r["src"]].get("strip")
            if strip is not None:
                im = cv_to_tk(strip, THUMB_COL - 14, ROW_H - 8)
                self._thumbs.append(im)
                tk.Label(tbox, image=im, bd=1, relief="solid").pack(side="left", expand=True)
            # fixed width AND height: without an explicit height a frame with
            # pack_propagate(False) collapses and squashes the widget inside it
            b1 = tk.Frame(row, width=74, height=ROW_H)
            b1.pack(side="left"); b1.pack_propagate(False)
            te = tk.Entry(b1, textvariable=r["tray"], width=5, justify="center",
                          relief="solid", bd=1,
                          bg=CONFIRMED_BG if r.get("tray_confirmed", True)
                          else UNCONFIRMED_BG)
            te.pack(expand=True)
            r["tray_entry"] = te
            te.bind("<FocusIn>", lambda ev, rr=r: self._confirm_tray(rr))
            te.bind("<Key>", lambda ev, rr=r: self._confirm_tray(rr))
            b2 = tk.Frame(row, width=124, height=ROW_H)
            b2.pack(side="left"); b2.pack_propagate(False)
            e = tk.Entry(b2, textvariable=r["depth"], width=11, relief="solid", bd=1,
                         justify="center",
                         bg=CONFIRMED_BG if r["confirmed"] else UNCONFIRMED_BG)
            e.pack(expand=True)
            r["entry"] = e
            e.bind("<FocusIn>", lambda ev, rr=r: self._confirm(rr))
            e.bind("<Key>", lambda ev, rr=r: self._confirm(rr))
            b3 = tk.Frame(row, width=104, height=ROW_H)
            b3.pack(side="left"); b3.pack_propagate(False)
            ttk.Combobox(b3, textvariable=r["cond"], width=7, state="readonly",
                         values=("Auto", "Dry", "Wet")).pack(expand=True)
            ttk.Label(row, text=os.path.basename(r["src"]),
                      style="Hint.TLabel").pack(side="left", padx=4, expand=True)
        self._bind_wheel(self.rowsf)

    @staticmethod
    def _col(parent, text, w):
        """Fixed-pixel column, so the header lines up with the rows beneath it."""
        box = tk.Frame(parent, width=w, height=22)
        box.pack(side="left"); box.pack_propagate(False)
        ttk.Label(box, text=text, style="Hint.TLabel").pack(side="left")

    def _confirm(self, r):
        if not r["confirmed"]:
            r["confirmed"] = True
            try:
                r["entry"].configure(bg=CONFIRMED_BG)
            except tk.TclError:
                pass

    def _confirm_tray(self, r):
        if not r.get("tray_confirmed", True):
            r["tray_confirmed"] = True
            try:
                r["tray_entry"].configure(bg=CONFIRMED_BG)
            except tk.TclError:
                pass

    # ----------------------------------------------------------- collecting
    def _collect(self, fs):
        """-> (hole, groups {tray: [src]}, ends {tray: depth}, first_start, cond_overrides,
              problems_from_parsing)"""
        probs = []
        tray_of, ends, overrides = {}, {}, {}
        for r in fs.rows:
            t = r["tray"].get().strip()
            try:
                tray = int(t)
            except ValueError:
                tray = None
                probs.append(f"{os.path.basename(r['src'])}: tray number '{t}' is not a number.")
            tray_of[r["src"]] = tray
            if r["cond"].get() in ("Dry", "Wet"):
                overrides[r["src"]] = r["cond"].get()
            dv = r["depth"].get().strip().replace(",", ".")
            if dv and tray is not None:
                try:
                    v = float(dv)
                except ValueError:
                    probs.append(f"{os.path.basename(r['src'])}: depth '{dv}' is not a number.")
                    continue
                if tray in ends and abs(ends[tray] - v) > 1e-9:
                    probs.append(f"Tray {tray}: two different end depths entered "
                                 f"({ends[tray]:.2f} and {v:.2f}).")
                ends[tray] = v
        groups = naming.group_by_tray(tray_of)
        fsv = fs.first_start.get().strip().replace(",", ".")
        try:
            first = float(fsv) if fsv else None
        except ValueError:
            first = None
            probs.append("Start depth of the first tray is not a number.")
        return fs.hole.get().strip(), groups, ends, first, overrides, probs

    def blocking(self):
        """Problems that must not be overridable - see naming.blocking_problems."""
        out = []
        for rel, fs in self.folders.items():
            hole, groups, ends, first, _, probs = self._collect(fs)
            for m in naming.blocking_problems(hole, groups, ends, first):
                out.append(f"[{rel}] {m}")
        return out

    def validate(self, silent=False):
        """Validate EVERY scanned folder, not just the one on screen."""
        allp = []
        for rel, fs in self.folders.items():
            hole, groups, ends, first, _, probs = self._collect(fs)
            probs += naming.validate(hole, groups, ends, first)
            allp += [f"[{rel}] {p}" for p in probs]
        if not silent:
            if allp:
                messagebox.showwarning(APP_TITLE, "Please check:\n\n• " +
                                       "\n• ".join(allp[:14]))
            else:
                messagebox.showinfo(APP_TITLE, "All good — depths run continuously and "
                                               "every tray has two photos.")
        self.t3msg.configure(text="" if not allp else f"{len(allp)} thing(s) to check")
        return allp

    # ------------------------------------------------------------------ run
    def run(self):
        if not self.folders:
            messagebox.showwarning(APP_TITLE, "Scan some photos first.")
            return
        stop = self.blocking()
        if stop:
            # Not a yes/no. Each of these puts a wrong number into a filename
            # or writes a file with no hole name, and neither is visible in the
            # output afterwards.
            messagebox.showerror(
                APP_TITLE,
                "These have to be fixed before anything can be saved:\n\n• "
                + "\n• ".join(stop[:10])
                + "\n\nA missing end depth is not skippable: every tray below "
                  "it would be named with a start depth that is not its own.")
            self.nb.select(self.tab3)
            return
        unconf = sum(1 for fs in self.folders.values() for r in fs.rows
                     if not r["confirmed"] or not r.get("tray_confirmed", True))
        problems = self.validate(silent=True)
        warn = list(problems)
        if unconf:
            warn.insert(0, f"{unconf} box(es) are still reader guesses you have not "
                           f"checked (the amber ones).")
        if self.recovered:
            warn.insert(0, f"{len(self.recovered)} photo(s) had no tray detected and "
                           f"use a default crop box.")
        outdir_now = self.outdir.get().strip()
        if outdir_now and os.path.isdir(outdir_now) and os.listdir(outdir_now):
            warn.append(f"{outdir_now} already has files in it; "
                        f"any with the same name will be overwritten.")
        if warn and not messagebox.askyesno(
                APP_TITLE, "Before saving:\n\n• " + "\n• ".join(warn[:10]) +
                           "\n\nProcess anyway?"):
            return
        self.params.normalise = self.normalise.get()
        outdir = self.outdir.get().strip()
        jobs = [(rel, ) + self._collect(fs)[:5] for rel, fs in self.folders.items()]
        self._bg(lambda: self._run_work(jobs, outdir))

    def _run_work(self, jobs, outdir):
        os.makedirs(outdir, exist_ok=True)
        total = sum(len(s) for _, _, groups, _, _, _ in
                    [(j[0], j[1], j[2], j[3], j[4], j[5]) for j in jobs]
                    for s in groups.values())
        n, written, per_hole, skipped = 0, [], {}, []
        for rel, hole, groups, ends, first, overrides in jobs:
            hole = hole or (rel or "hole")
            chained = naming.chain_depths(sorted(groups), ends,
                                          first if first is not None else 0.0)
            manifest = []
            for tray in sorted(groups):
                srcs = groups[tray]
                start, end = chained[tray]
                if end is None or start is None:
                    # Cannot happen via the button - run() blocks first - but a
                    # silent `continue` here is what turned a missing depth into
                    # a wrongly-named neighbour, so say so rather than drop it.
                    n += len(srcs)
                    skipped.append((hole, tray, len(srcs)))
                    logfile.write(f"  SKIPPED {hole} tray {tray}: "
                                  f"start={start} end={end}")
                    continue
                conds, margin = naming.decide_conditions(srcs, self.core_L, overrides)
                names = naming.disambiguate(
                    [naming.make_name(hole, conds[s], tray, start, end) for s in srcs])
                for s, name in zip(srcs, names):
                    n += 1
                    self._set_progress(n, total, f"Saving {n} of {total}")
                    crop, gains = core.render(s, self.det[s], self.params,
                                              self.adj.get(s),
                                              prof=self.profiles.get(os.path.dirname(s)))
                    dst = os.path.join(outdir, hole, conds[s], name)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    cv2.imwrite(dst, crop, [cv2.IMWRITE_JPEG_QUALITY, self.params.jpeg_quality])
                    d = self.det[s]
                    manifest.append(dict(
                        source=self._label(s),
                        final=os.path.relpath(dst, outdir), hole=hole, tray=tray,
                        from_m=f"{start:.2f}", to_m=f"{end:.2f}",
                        interval_m=round(end - start, 2), condition=conds[s],
                        overridden=int(s in overrides),
                        dry_wet_margin_L=round(margin, 1),
                        confidence=("high" if margin >= DRY_WET_MARGIN or s in overrides
                                    else "check"),
                        core_L=round(self.core_L.get(s, 0), 1),
                        tilt_deg=round(d["angle"], 2),
                        bar_method=d.get("bar_method", ""),
                        mask_method=d.get("mask_method", ""),
                        tray_aspect=d.get("tray_aspect", ""),
                        shape_ok=int(bool(d.get("aspect_ok", True))),
                        tray_px=d.get("tray_px", ""),
                        auto_detect_failed=int(bool(d.get("auto_failed"))),
                        tool_version=__version__,
                        tray_cut_left=d["cut_left"], tray_cut_right=d["cut_right"],
                        corners_adjusted=int(any(abs(v) > 0.5 for pt in
                                                 self.adj.get(s, []) for v in pt)),
                        folder_calibrated=int(os.path.dirname(s) in self.profiles),
                        gain_B=round(gains[0], 3) if gains else "",
                        gain_G=round(gains[1], 3) if gains else "",
                        gain_R=round(gains[2], 3) if gains else ""))
                    written.append(dst)
            if manifest:
                mp = os.path.join(outdir, f"manifest_{hole}.csv")
                naming.write_manifest(mp, manifest)
                per_hole[hole] = (len(manifest), [m["final"] for m in manifest
                                                  if m["confidence"] != "high"])

        logfile.write(f"run: wrote {len(written)} file(s) to {outdir}; "
                      f"{len(skipped)} tray(s) skipped")

        def finish():
            self.status.configure(text="Done")
            lines = [f"{h}: {c} images" + (f"  ({len(chk)} to check by eye)" if chk else "")
                     for h, (c, chk) in per_hole.items()]
            msg = "Saved to\n" + outdir + "\n\n" + "\n".join(lines)
            if skipped:
                msg += ("\n\nNOT saved, because the depth chain was broken:\n  "
                        + "\n  ".join(f"{h} tray {t} ({c} photo(s))"
                                      for h, t, c in skipped))
            chk_all = [c for _, chk in per_hole.values() for c in chk]
            if chk_all:
                msg += ("\n\nDry/wet was a close call on these (usually a near-empty tray). "
                        "Check them and set Dry/Wet by hand if needed:\n  "
                        + "\n  ".join(os.path.basename(c) for c in chk_all[:6]))
            messagebox.showinfo(APP_TITLE, msg)
            try:
                webbrowser.open("file://" + os.path.abspath(outdir))
            except Exception:
                pass
        self._q.put(finish)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
