"""
Core Photo Tool - desktop app.

Three steps: pick folders, check the crop on a real photo, fill in the labels
and run. Everything runs on this machine; nothing is uploaded anywhere.
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
BG = "#f4f4f2"


def cv_to_tk(img, max_w, max_h):
    h, w = img.shape[:2]
    s = min(max_w / w, max_h / h, 1.0)
    if s < 1.0:
        img = cv2.resize(img, (max(int(w * s), 1), max(int(h * s), 1)),
                         interpolation=cv2.INTER_AREA)
    return ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)))


class FolderState:
    """One input subfolder = one hole."""
    def __init__(self, rel):
        self.rel = rel
        self.hole = ""
        self.first_start = None
        self.pairs = []        # list of (src_a, src_b)
        self.rows = []         # dicts: tray, end_m, thumb (cv2 img), widgets


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x800")
        self.minsize(1040, 720)
        self.configure(bg=BG)

        self.params = core.Params()
        self.indir = tk.StringVar()
        self.outdir = tk.StringVar()
        self.normalise = tk.BooleanVar(value=True)
        self.use_ocr = tk.BooleanVar(value=ocr.available())
        self.tray_width = tk.IntVar(value=self.params.tray_width)
        self.show_raw = tk.BooleanVar(value=False)

        self.photos = []
        self.det = {}            # path -> detection dict
        self.core_L = {}         # path -> median rock L*
        self.folders = {}        # rel -> FolderState
        self.preview_idx = 0
        self._q = queue.Queue()
        self._busy = False

        self._build()
        self.after(100, self._drain)

    # ------------------------------------------------------------- layout
    def _build(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TNotebook.Tab", padding=(16, 8))
        style.configure("Head.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure("Hint.TLabel", foreground="#555")
        style.configure("Warn.TLabel", foreground="#a33")

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.tab1 = ttk.Frame(self.nb); self.nb.add(self.tab1, text="1.  Folders")
        self.tab2 = ttk.Frame(self.nb); self.nb.add(self.tab2, text="2.  Check the crop")
        self.tab3 = ttk.Frame(self.nb); self.nb.add(self.tab3, text="3.  Labels and run")
        self._build_t1(); self._build_t2(); self._build_t3()

        bar = ttk.Frame(self); bar.pack(fill="x", padx=12, pady=(0, 10))
        self.progress = ttk.Progressbar(bar, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status = ttk.Label(bar, text="Ready", style="Hint.TLabel")
        self.status.pack(side="right", padx=(12, 0))

    def _build_t1(self):
        f = self.tab1
        ttk.Label(f, text="Where are the photos?", style="Head.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", padx=16, pady=(18, 2))
        ttk.Label(f, text="One subfolder per hole. Originals are never modified.",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=3, sticky="w",
                                            padx=16, pady=(0, 12))

        ttk.Label(f, text="Photo folder").grid(row=2, column=0, sticky="e", padx=(16, 8), pady=6)
        ttk.Entry(f, textvariable=self.indir, width=74).grid(row=2, column=1, sticky="we", pady=6)
        ttk.Button(f, text="Browse...", command=self._pick_in).grid(row=2, column=2, padx=8)

        ttk.Label(f, text="Save results to").grid(row=3, column=0, sticky="e", padx=(16, 8), pady=6)
        ttk.Entry(f, textvariable=self.outdir, width=74).grid(row=3, column=1, sticky="we", pady=6)
        ttk.Button(f, text="Browse...", command=self._pick_out).grid(row=3, column=2, padx=8)

        opts = ttk.Frame(f); opts.grid(row=4, column=1, sticky="w", pady=(14, 6))
        ttk.Checkbutton(opts, text="Correct exposure and white balance "
                                   "(referenced to the tape measure)",
                        variable=self.normalise).pack(anchor="w")
        cb = ttk.Checkbutton(opts, text="Try to pre-fill labels with OCR "
                                        "(a hint only - always check them)",
                             variable=self.use_ocr)
        cb.pack(anchor="w", pady=(4, 0))
        if not ocr.available():
            cb.state(["disabled"])
            ttk.Label(opts, text="Tesseract OCR not found on this machine - "
                                 "you will type the labels in step 3.",
                      style="Hint.TLabel").pack(anchor="w", pady=(2, 0))

        self.scan_btn = ttk.Button(f, text="Scan photos", command=self.scan)
        self.scan_btn.grid(row=5, column=1, sticky="w", pady=(16, 8))

        self.log = tk.Text(f, height=15, width=100, relief="flat", bg="#ffffff",
                           font=("TkFixedFont", 9))
        self.log.grid(row=6, column=0, columnspan=3, sticky="nsew", padx=16, pady=(6, 16))
        f.columnconfigure(1, weight=1); f.rowconfigure(6, weight=1)

    def _build_t2(self):
        f = self.tab2
        top = ttk.Frame(f); top.pack(fill="x", padx=16, pady=(14, 6))
        ttk.Label(top, text="Does the red box sit on the tray?",
                  style="Head.TLabel").pack(side="left")
        ttk.Button(top, text="< Prev", command=lambda: self._step(-1)).pack(side="right", padx=4)
        ttk.Button(top, text="Next >", command=lambda: self._step(1)).pack(side="right", padx=4)
        self.pick = ttk.Combobox(top, width=42, state="readonly")
        self.pick.pack(side="right", padx=10)
        self.pick.bind("<<ComboboxSelected>>", lambda e: self._set_idx(self.pick.current()))

        ttk.Label(f, text="Top edge on top of the label bar, bottom edge on the tray rail, "
                          "sides on the tray ends. If it is consistently too wide or too "
                          "narrow, move the slider.",
                  style="Hint.TLabel").pack(anchor="w", padx=16)

        mid = ttk.Frame(f); mid.pack(fill="both", expand=True, padx=16, pady=10)
        lf = ttk.LabelFrame(mid, text="Original photo with crop box")
        lf.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.cv_orig = tk.Label(lf, bg="#222"); self.cv_orig.pack(fill="both", expand=True, padx=6, pady=6)
        rf = ttk.LabelFrame(mid, text="Result")
        rf.pack(side="left", fill="both", expand=True)
        self.cv_res = tk.Label(rf, bg="#222"); self.cv_res.pack(fill="both", expand=True, padx=6, pady=6)

        ctl = ttk.Frame(f); ctl.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Label(ctl, text="Tray width").pack(side="left")
        self.sl_lbl = ttk.Label(ctl, text=str(self.params.tray_width), width=5)
        self.sl = ttk.Scale(ctl, from_=600, to=860, orient="horizontal", length=340)
        self.sl.set(self.params.tray_width)
        self.sl.configure(command=self._slide)      # attach AFTER set, so the
        self.sl.pack(side="left", padx=10)          # callback cannot fire early
        self.sl_lbl.pack(side="left")
        ttk.Button(ctl, text="Reset", command=lambda: self.sl.set(744)).pack(side="left", padx=8)
        ttk.Checkbutton(ctl, text="Show without colour correction",
                        variable=self.show_raw,
                        command=self._draw_preview).pack(side="left", padx=18)
        self.info = ttk.Label(f, text="", style="Hint.TLabel")
        self.info.pack(anchor="w", padx=16, pady=(0, 10))

    def _build_t3(self):
        f = self.tab3
        top = ttk.Frame(f); top.pack(fill="x", padx=16, pady=(14, 4))
        ttk.Label(top, text="Read the label bar in each strip and fill in the numbers",
                  style="Head.TLabel").pack(side="left")
        ttk.Label(top, text="Hole").pack(side="left", padx=(28, 4))
        self.hole_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.hole_var, width=18).pack(side="left")
        self.hole_var.trace_add("write", lambda *a: self._store_hole())
        ttk.Label(top, text="Folder").pack(side="left", padx=(20, 4))
        self.folder_pick = ttk.Combobox(top, width=22, state="readonly")
        self.folder_pick.pack(side="left")
        self.folder_pick.bind("<<ComboboxSelected>>", lambda e: self._show_folder())

        sub = ttk.Frame(f); sub.pack(fill="x", padx=16, pady=(6, 2))
        ttk.Label(sub, text="Start depth of the first tray (m)").pack(side="left")
        self.first_start = tk.StringVar()
        ttk.Entry(sub, textvariable=self.first_start, width=10).pack(side="left", padx=8)
        ttk.Label(sub, text="Every other tray starts where the previous one ended.",
                  style="Hint.TLabel").pack(side="left", padx=6)

        wrap = ttk.Frame(f); wrap.pack(fill="both", expand=True, padx=16, pady=8)
        self.canvas = tk.Canvas(wrap, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#ccc")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y"); self.canvas.pack(side="left", fill="both", expand=True)
        self.rowsf = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.rowsf, anchor="nw")
        self.rowsf.bind("<Configure>",
                        lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>",
                             lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.canvas.bind_all("<Button-4>", lambda e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda e: self.canvas.yview_scroll(1, "units"))

        bot = ttk.Frame(f); bot.pack(fill="x", padx=16, pady=(0, 14))
        ttk.Button(bot, text="Check", command=self.validate).pack(side="left")
        self.run_btn = ttk.Button(bot, text="Process and save", command=self.run)
        self.run_btn.pack(side="left", padx=10)
        self.t3msg = ttk.Label(bot, text="", style="Hint.TLabel")
        self.t3msg.pack(side="left", padx=14)

    # -------------------------------------------------------------- helpers
    def say(self, msg):
        self.log.insert("end", msg + "\n"); self.log.see("end")

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
                fn = self._q.get_nowait()
                fn()
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

    # ----------------------------------------------------------------- scan
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
            d["strip"] = core.label_strip(
                cv2.resize(crop, (1500, max(int(1500 * crop.shape[0] / crop.shape[1]), 1)),
                           interpolation=cv2.INTER_AREA))
        self.photos = [p for p in photos if p in det]
        self.det, self.core_L = det, coreL

        folders = {}
        for p in self.photos:
            rel = os.path.relpath(os.path.dirname(p), indir)
            folders.setdefault(rel, FolderState(rel)).pairs.append(p)
        for fs in folders.values():
            srcs = fs.pairs
            fs.pairs = naming.pair_photos(srcs)
        self.folders = folders

        def finish():
            for p in failed:
                self.say(f"  could not find a tray in {os.path.basename(p)} - skipped")
            cut = sum(1 for p in self.photos
                      if self.det[p]["cut_left"] or self.det[p]["cut_right"])
            self.say(f"Detected trays in {len(self.photos)} photos.")
            if cut:
                self.say(f"  note: in {cut} photo(s) the tray runs past the frame edge; "
                         f"those ends are padded grey and flagged in the manifest.")
            for rel, fs in folders.items():
                self.say(f"  {rel or '.'}: {len(fs.pairs)} trays")
            self.pick["values"] = [os.path.relpath(p, indir) for p in self.photos]
            if self.photos:
                self.pick.current(0)
            self.folder_pick["values"] = list(folders.keys())
            if folders:
                self.folder_pick.current(0)
            self._build_rows()
            self._set_idx(0)
            self.nb.select(self.tab2)
            self.status.configure(text="Scan complete")
        self._q.put(finish)

    # -------------------------------------------------------------- preview
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
        w = int(float(v))
        self.sl_lbl.configure(text=str(w))
        self.params.tray_width = w
        self._draw_preview()

    def _draw_preview(self):
        if not self.photos:
            return
        p = self.photos[self.preview_idx]
        d = self.det[p]
        small = d["small"].copy()
        bx0, by0, bx1, by1 = core.crop_box(d, self.params)
        cv2.polylines(small, [np.int32([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]])],
                      True, (0, 0, 255), 2)
        self._img1 = cv_to_tk(small, 520, 380)
        self.cv_orig.configure(image=self._img1)

        pr = core.Params(**{**self.params.to_dict(),
                            "normalise": self.params.normalise and not self.show_raw.get()})
        crop, gains = core.render(p, d, pr)
        self._img2 = cv_to_tk(crop, 520, 380)
        self.cv_res.configure(image=self._img2)

        msg = (f"tilt {d['angle']:+.2f}°   label bar offset {int(d['bb'][3]) - d['bar']} px   "
               f"output {crop.shape[1]}x{crop.shape[0]} px")
        if gains:
            msg += f"   colour gains B/G/R {gains[0]:.2f}/{gains[1]:.2f}/{gains[2]:.2f}"
        if d["cut_left"] or d["cut_right"]:
            msg += "   -  tray runs past the frame edge, ends padded grey"
        self.info.configure(text=msg)

    # ---------------------------------------------------------------- rows
    def _cur(self):
        rel = self.folder_pick.get()
        return self.folders.get(rel)

    def _store_hole(self):
        fs = self._cur()
        if fs:
            fs.hole = self.hole_var.get().strip()

    def _show_folder(self):
        fs = self._cur()
        if not fs:
            return
        self.hole_var.set(fs.hole)
        self.first_start.set("" if fs.first_start is None else f"{fs.first_start:.2f}")
        self._build_rows()

    def _build_rows(self):
        for w in self.rowsf.winfo_children():
            w.destroy()
        fs = self._cur()
        if not fs:
            return
        hdr = ttk.Frame(self.rowsf); hdr.grid(row=0, column=0, sticky="w", pady=(4, 6))
        ttk.Label(hdr, text="Label bar", width=90).pack(side="left")
        ttk.Label(hdr, text="Tray", width=8).pack(side="left")
        ttk.Label(hdr, text="End depth (m)", width=14).pack(side="left")

        self._thumbs = []
        fs.rows = []
        for i, pair in enumerate(fs.pairs):
            src = pair[0]
            strip = self.det[src].get("strip")
            row = ttk.Frame(self.rowsf, padding=(0, 3))
            row.grid(row=i + 1, column=0, sticky="w")
            if strip is not None:
                im = cv_to_tk(strip, 720, 90)
                self._thumbs.append(im)
                tk.Label(row, image=im, bd=1, relief="solid").pack(side="left", padx=(0, 10))
            tray_v = tk.StringVar(value=str(i + 1))
            dep_v = tk.StringVar()
            if self.use_ocr.get() and strip is not None:
                _, t, dm = ocr.read_bar(strip)
                if t:
                    tray_v.set(str(t))
                if dm:
                    dep_v.set(f"{dm:.2f}")
                if not fs.hole:
                    h, _, _ = ocr.read_bar(strip)
                    if h:
                        self.hole_var.set(f"DD26ZOP-{h}")
            ttk.Entry(row, textvariable=tray_v, width=7).pack(side="left", padx=4)
            ttk.Entry(row, textvariable=dep_v, width=13).pack(side="left", padx=4)
            ttk.Label(row, text=" / ".join(os.path.basename(x) for x in pair),
                      style="Hint.TLabel").pack(side="left", padx=10)
            fs.rows.append(dict(pair=pair, tray_v=tray_v, dep_v=dep_v))

    def _collect(self):
        """Read the entry widgets into plain values. Returns (fs, rows, first_start)."""
        fs = self._cur()
        if not fs:
            return None, [], None
        fs.hole = self.hole_var.get().strip()
        try:
            fs.first_start = float(self.first_start.get()) if self.first_start.get().strip() else None
        except ValueError:
            fs.first_start = None
        rows = []
        for r in fs.rows:
            try:
                tray = int(r["tray_v"].get())
            except ValueError:
                tray = None
            try:
                end = float(r["dep_v"].get().replace(",", "."))
            except ValueError:
                end = None
            rows.append(dict(tray=tray, end_m=end, pair=r["pair"]))
        return fs, rows, fs.first_start

    def validate(self, silent=False):
        fs, rows, first = self._collect()
        if fs is None:
            return ["Nothing scanned yet."]
        bad = [r for r in rows if r["tray"] is None]
        problems = [f"{len(bad)} row(s) have no tray number."] if bad else []
        good = [r for r in rows if r["tray"] is not None]
        counts = {r["tray"]: len(r["pair"]) for r in good}
        problems += naming.validate(fs.hole, good, first, counts)
        if not silent:
            if problems:
                messagebox.showwarning(APP_TITLE, "Please check:\n\n• " + "\n• ".join(problems[:12]))
            else:
                messagebox.showinfo(APP_TITLE, "All good - depths run continuously and every "
                                               "tray has two photos.")
        self.t3msg.configure(text="" if not problems else f"{len(problems)} thing(s) to check")
        return problems

    # ------------------------------------------------------------------ run
    def run(self):
        problems = self.validate(silent=True)
        if problems:
            if not messagebox.askyesno(
                    APP_TITLE,
                    "Some things look wrong:\n\n• " + "\n• ".join(problems[:8]) +
                    "\n\nProcess anyway?"):
                return
        fs, rows, first = self._collect()
        outdir = self.outdir.get().strip()
        self.params.normalise = self.normalise.get()
        self._bg(lambda: self._run_work(fs, rows, first if first is not None else 0.0, outdir))

    def _run_work(self, fs, rows, first, outdir):
        rows = [r for r in rows if r["tray"] is not None and r["end_m"] is not None]
        chained = naming.chain_depths(rows, first)
        os.makedirs(outdir, exist_ok=True)
        manifest, n, total = [], 0, sum(len(r["pair"]) for r in chained)
        for r in chained:
            wet, margin = naming.decide_wet(r["pair"], self.core_L)
            for src in r["pair"]:
                n += 1
                self._set_progress(n, total, f"Saving {n} of {total}")
                cond = "Wet" if src == wet else "Dry"
                crop, gains = core.render(src, self.det[src], self.params)
                name = naming.make_name(fs.hole, cond, r["tray"], r["start_m"], r["end_m"])
                dst = os.path.join(outdir, fs.hole, cond, name)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                cv2.imwrite(dst, crop, [cv2.IMWRITE_JPEG_QUALITY, self.params.jpeg_quality])
                d = self.det[src]
                manifest.append(dict(
                    source=os.path.relpath(src, self.indir.get()),
                    final=os.path.relpath(dst, outdir),
                    hole=fs.hole, tray=r["tray"],
                    from_m=f"{r['start_m']:.2f}", to_m=f"{r['end_m']:.2f}",
                    interval_m=round(r["end_m"] - r["start_m"], 2), condition=cond,
                    dry_wet_margin_L=round(margin, 1),
                    confidence="high" if margin >= 12 else "check",
                    core_L=round(self.core_L.get(src, 0), 1),
                    tilt_deg=round(d["angle"], 2),
                    tray_cut_left=d["cut_left"], tray_cut_right=d["cut_right"],
                    gain_B=round(gains[0], 3) if gains else "",
                    gain_G=round(gains[1], 3) if gains else "",
                    gain_R=round(gains[2], 3) if gains else ""))
        mpath = os.path.join(outdir, f"manifest_{fs.hole or 'hole'}.csv")
        naming.write_manifest(mpath, manifest)
        checks = [m["final"] for m in manifest if m["confidence"] != "high"]

        def finish():
            self.status.configure(text="Done")
            msg = f"Saved {len(manifest)} images to\n{outdir}\n\nManifest: {os.path.basename(mpath)}"
            if checks:
                msg += ("\n\nCheck dry/wet by eye on these - the two photos were nearly "
                        "the same brightness (usually a near-empty tray):\n  "
                        + "\n  ".join(os.path.basename(c) for c in checks[:6]))
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
