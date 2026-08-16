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

from . import core, naming, ocr

APP_TITLE = "Core Photo Tool"
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
                          confirmed=(True, )) for s in self.photos]
        for r in self.rows:
            r["confirmed"] = True     # nothing auto-filled yet


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1240x840")
        self.minsize(1080, 740)
        self._theme()
        self._icon()

        self.params = core.Params()
        self.indir = tk.StringVar()
        self.outdir = tk.StringVar()
        self.normalise = tk.BooleanVar(value=True)
        self.use_ocr = tk.BooleanVar(value=ocr.available())
        self.show_raw = tk.BooleanVar(value=False)

        self.photos, self.det, self.core_L, self.folders = [], {}, {}, {}
        self.adj = {}            # path -> [[dx,dy] x4] manual corner nudges
        self._drag = None
        self._pv = None          # canvas<->image mapping for the preview
        self.preview_idx = 0
        self._q = queue.Queue()
        self._busy = False
        self._thumbs = []

        self._build()
        self.after(80, self._drain)

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
        ttk.Label(f, text="Where are the photos?", style="Head.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=14, pady=(16, 2))
        ttk.Label(f, text="One subfolder per hole. Your originals are never modified.",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=3, sticky="w",
                                           padx=14, pady=(0, 16))

        ttk.Label(f, text="Photo folder").grid(row=2, column=0, sticky="e", padx=(14, 10), pady=7)
        ttk.Entry(f, textvariable=self.indir).grid(row=2, column=1, sticky="we", pady=7)
        ttk.Button(f, text="Browse", command=self._pick_in).grid(row=2, column=2, padx=10)

        ttk.Label(f, text="Save results to").grid(row=3, column=0, sticky="e", padx=(14, 10), pady=7)
        ttk.Entry(f, textvariable=self.outdir).grid(row=3, column=1, sticky="we", pady=7)
        ttk.Button(f, text="Browse", command=self._pick_out).grid(row=3, column=2, padx=10)

        opts = ttk.Frame(f); opts.grid(row=4, column=1, sticky="w", pady=(18, 6))
        ttk.Checkbutton(opts, text="Correct exposure and white balance "
                                   "(referenced to the tape measure)",
                        variable=self.normalise).pack(anchor="w", pady=3)
        cb = ttk.Checkbutton(opts, text="Pre-fill depths with OCR  —  a hint only, "
                                        "shown in amber until you check it",
                             variable=self.use_ocr)
        cb.pack(anchor="w", pady=3)
        if not ocr.available():
            cb.state(["disabled"])
            ttk.Label(opts, text="Tesseract OCR not found, so depths start blank. "
                                 "Install it and restart to enable pre-fill.",
                      style="Hint.TLabel").pack(anchor="w", pady=(2, 0))

        self.scan_btn = ttk.Button(f, text="Scan photos", style="Go.TButton", command=self.scan)
        self.scan_btn.grid(row=5, column=1, sticky="w", pady=(20, 10))

        self.log = tk.Text(f, height=14, relief="flat", bg="#fbfbfb", bd=1,
                           highlightthickness=1, highlightbackground="#dcdcdc",
                           font=("Consolas", 9), padx=10, pady=8)
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=14, pady=(8, 12))
        f.columnconfigure(1, weight=1); f.rowconfigure(6, weight=1)

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
        ttk.Button(ctl2, text="Copy this adjustment to every photo in the folder",
                   command=self._apply_adj_all).pack(side="left", padx=10)
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
        for seq, d in (("<MouseWheel>", None), ("<Button-4>", -1), ("<Button-5>", 1)):
            self.canvas.bind_all(seq, lambda e, d=d: self.canvas.yview_scroll(
                d if d is not None else int(-e.delta / 120), "units"))

        bot = ttk.Frame(f); bot.pack(fill="x", padx=14, pady=(0, 12))
        ttk.Button(bot, text="Check", command=self.validate).pack(side="left")
        self.run_btn = ttk.Button(bot, text="Process and save all folders",
                                  style="Go.TButton", command=self.run)
        self.run_btn.pack(side="left", padx=12)
        self.t3msg = ttk.Label(bot, text="", style="Hint.TLabel")
        self.t3msg.pack(side="left", padx=14)

    # ------------------------------------------------------------ plumbing
    def say(self, m):
        self.log.insert("end", m + "\n"); self.log.see("end")

    def _pick_in(self):
        d = filedialog.askdirectory(title="Folder with core photos")
        if d:
            self.indir.set(d)
            if not self.outdir.get():
                self.outdir.set(os.path.join(d, "processed"))

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
        self.after(80, self._drain)

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
        indir = self.indir.get().strip()
        if not indir or not os.path.isdir(indir):
            messagebox.showwarning(APP_TITLE, "Choose the folder with your core photos first.")
            return
        outdir = self.outdir.get().strip() or os.path.join(indir, "processed")
        self.outdir.set(outdir)
        self.params.normalise = self.normalise.get()
        self.log.delete("1.0", "end")
        self._bg(lambda: self._scan_work(indir, outdir))

    def _scan_work(self, indir, outdir):
        photos = core.find_photos(indir, skip_dirs=(outdir,))
        if not photos:
            self._q.put(lambda: messagebox.showwarning(APP_TITLE, "No JPEG photos found."))
            return
        self._q.put(lambda: self.say(f"Found {len(photos)} photos."))
        det, coreL, failed = {}, {}, []
        for i, p in enumerate(photos, 1):
            self._set_progress(i, len(photos), f"Reading {i} of {len(photos)}")
            d = core.detect(p)
            if d is None:
                failed.append(p); continue
            det[p] = d
            crop, _ = core.render(p, d, self.params)
            coreL[p] = core.core_stats(crop)[0]
            w = 1500
            d["strip"] = core.label_strip(
                cv2.resize(crop, (w, max(int(w * crop.shape[0] / crop.shape[1]), 1)),
                           interpolation=cv2.INTER_AREA))
        self.photos = [p for p in photos if p in det]
        self.det, self.core_L = det, coreL

        by = {}
        for p in self.photos:
            by.setdefault(os.path.relpath(os.path.dirname(p), indir), []).append(p)
        self.folders = {rel: FolderState(rel, ps) for rel, ps in by.items()}

        if self.use_ocr.get() and ocr.available():
            self._ocr_fill(len(self.photos))

        def finish():
            for p in failed:
                self.say(f"  no tray found in {os.path.basename(p)} - skipped")
            cut = sum(1 for p in self.photos
                      if self.det[p]["cut_left"] or self.det[p]["cut_right"])
            self.say(f"Detected trays in {len(self.photos)} photos.")
            if cut:
                self.say(f"  {cut} photo(s) have the tray running past the frame edge; "
                         f"those ends are padded grey and flagged in the manifest.")
            for rel, fs in self.folders.items():
                self.say(f"  {rel or '.'}: {len(fs.photos)} photos "
                         f"({len(fs.photos)//2} trays expected)")
            self.pick["values"] = [os.path.relpath(p, self.indir.get()) for p in self.photos]
            if self.photos:
                self.pick.current(0)
            self.folder_pick["values"] = list(self.folders.keys())
            if self.folders:
                self.folder_pick.current(0)
            self._show_folder()
            self._set_idx(0)
            self.nb.select(self.tab2)
            self.status.configure(text="Scan complete")
        self._q.put(finish)

    def _ocr_fill(self, total):
        """Pre-fill depths. Values are marked unconfirmed (amber) and repaired
        against the depth sequence where the sequence makes the fix unambiguous."""
        done = 0
        for fs in self.folders.values():
            prev = None
            for r in fs.rows:
                done += 1
                self._set_progress(done, total, f"Reading labels {done} of {total}")
                strip = self.det[r["src"]].get("strip")
                v = ocr.read_depth(strip)
                if prev is not None:
                    v = ocr.repair_depth(v, prev)
                if v is not None:
                    r["depth"].set(f"{v:.2f}")
                    r["confirmed"] = False
                    prev = v
            h, _ = ocr.read_hole_and_tray(self.det[fs.photos[0]].get("strip")) \
                if fs.photos else (None, None)
            if h and not fs.hole.get():
                fs.hole.set(f"DD26ZOP-{h}")

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

    def _apply_adj_all(self):
        """Same nudge on every photo of this folder - for a rig that is
        consistently off rather than one crooked photo."""
        if not self.photos:
            return
        cur = self.photos[self.preview_idx]
        adj = [list(c) for c in self._cur_adj(cur)]
        folder = os.path.dirname(cur)
        n = 0
        for p in self.photos:
            if os.path.dirname(p) == folder:
                self.adj[p] = [list(c) for c in adj]
                n += 1
        self.adj_lbl.configure(text=f"copied to {n} photos in this folder")

    def _canvas_quad(self):
        p = self.photos[self.preview_idx]
        q = core.crop_quad(self.det[p], self.params, self._cur_adj(p))
        s, ox, oy = self._pv["s"], self._pv["ox"], self._pv["oy"]
        return [(ox + x * s, oy + y * s) for x, y in q]

    def _grab(self, ev):
        if not self.photos or not self._pv:
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
        if self._drag is None or not self._pv:
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
        if not self.photos:
            return
        p = self.photos[self.preview_idx]
        d = self.det[p]
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
        crop, gains = core.render(p, d, pr, self._cur_adj(p))
        self._img2 = cv_to_tk(crop, 540, 400)
        self.cv_res.configure(image=self._img2)
        msg = (f"tilt {d['angle']:+.2f}°    label bar offset {int(d['bb'][3]) - d['bar']} px"
               f"    output {crop.shape[1]}×{crop.shape[0]} px")
        if gains:
            msg += f"    colour gains B/G/R {gains[0]:.2f}/{gains[1]:.2f}/{gains[2]:.2f}"
        if d["cut_left"] or d["cut_right"]:
            msg += "    •  tray runs past the frame edge, ends padded grey"
        self.info.configure(text=msg)

    # ---------------------------------------------------------------- rows
    def _cur(self):
        return self.folders.get(self.folder_pick.get())

    def _show_folder(self):
        fs = self._cur()
        if not fs:
            return
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
            ttk.Entry(b1, textvariable=r["tray"], width=5, justify="center").pack(expand=True)
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

    def validate(self, silent=False):
        """Validate EVERY scanned folder, not just the one on screen."""
        allp = []
        for rel, fs in self.folders.items():
            hole, groups, ends, first, _, probs = self._collect(fs)
            probs += naming.validate(hole, groups, ends, first)
            allp += [f"[{rel or '.'}] {p}" for p in probs]
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
        unconf = sum(1 for fs in self.folders.values() for r in fs.rows if not r["confirmed"])
        problems = self.validate(silent=True)
        warn = list(problems)
        if unconf:
            warn.insert(0, f"{unconf} depth(s) are still OCR guesses you have not checked "
                           f"(the amber boxes).")
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
        n, written, per_hole = 0, [], {}
        for rel, hole, groups, ends, first, overrides in jobs:
            hole = hole or (rel or "hole")
            chained = naming.chain_depths(sorted(groups), ends,
                                          first if first is not None else 0.0)
            manifest = []
            for tray in sorted(groups):
                srcs = groups[tray]
                start, end = chained[tray]
                if end is None:
                    n += len(srcs)
                    continue
                conds, margin = naming.decide_conditions(srcs, self.core_L, overrides)
                names = naming.disambiguate(
                    [naming.make_name(hole, conds[s], tray, start, end) for s in srcs])
                for s, name in zip(srcs, names):
                    n += 1
                    self._set_progress(n, total, f"Saving {n} of {total}")
                    crop, gains = core.render(s, self.det[s], self.params, self.adj.get(s))
                    dst = os.path.join(outdir, hole, conds[s], name)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    cv2.imwrite(dst, crop, [cv2.IMWRITE_JPEG_QUALITY, self.params.jpeg_quality])
                    d = self.det[s]
                    manifest.append(dict(
                        source=os.path.relpath(s, self.indir.get()),
                        final=os.path.relpath(dst, outdir), hole=hole, tray=tray,
                        from_m=f"{start:.2f}", to_m=f"{end:.2f}",
                        interval_m=round(end - start, 2), condition=conds[s],
                        overridden=int(s in overrides),
                        dry_wet_margin_L=round(margin, 1),
                        confidence="high" if margin >= 12 or s in overrides else "check",
                        core_L=round(self.core_L.get(s, 0), 1),
                        tilt_deg=round(d["angle"], 2),
                        tray_cut_left=d["cut_left"], tray_cut_right=d["cut_right"],
                        corners_adjusted=int(any(abs(v) > 0.5 for pt in
                                                 self.adj.get(s, []) for v in pt)),
                        gain_B=round(gains[0], 3) if gains else "",
                        gain_G=round(gains[1], 3) if gains else "",
                        gain_R=round(gains[2], 3) if gains else ""))
                    written.append(dst)
            if manifest:
                mp = os.path.join(outdir, f"manifest_{hole}.csv")
                naming.write_manifest(mp, manifest)
                per_hole[hole] = (len(manifest), [m["final"] for m in manifest
                                                  if m["confidence"] != "high"])

        def finish():
            self.status.configure(text="Done")
            lines = [f"{h}: {c} images" + (f"  ({len(chk)} to check by eye)" if chk else "")
                     for h, (c, chk) in per_hole.items()]
            msg = "Saved to\n" + outdir + "\n\n" + "\n".join(lines)
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
