# Core Photo Tool

[![Download](https://img.shields.io/badge/Download-CorePhotoTool.exe-2ea44f?style=for-the-badge&logo=windows)](https://github.com/Almarant/core-photo-tool/releases/latest/download/CorePhotoTool.exe)
[![Release](https://img.shields.io/github/v/release/Almarant/core-photo-tool?style=flat-square)](https://github.com/Almarant/core-photo-tool/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Almarant/core-photo-tool/total?style=flat-square)](https://github.com/Almarant/core-photo-tool/releases)

### ⬇️ [Download CorePhotoTool.exe](https://github.com/Almarant/core-photo-tool/releases/latest/download/CorePhotoTool.exe) — Windows, 71 MB, nothing to install

> Windows will warn you it's from an unknown publisher (the app isn't code-signed).
> Click **More info → Run anyway**. First launch takes 5–15 seconds while it unpacks —
> it looks frozen, it isn't.

Crops, straightens, colour-corrects, sorts dry/wet and renames drill-core tray
photographs. Point it at a folder, check one preview, type the tray depths, press
go.

Everything runs on your own machine. No internet, no accounts, no API.

Output: `DD26ZOP-006_Dry_Tray22_82.50-86.00m.jpg`

---

## For geologists — just use it

1. Download **CorePhotoTool.exe** from the
   [Releases page](../../releases) and double-click it. Nothing to install.
2. **Step 1 – Photos.** Add what you want to process: **drag folders or
   individual photos onto the list**, or use **Add folder…** / **Add files…**.
   Mix both freely — a whole hole plus one stray reshoot is fine, and processing
   a single photo out of a folder of hundreds works. **Remove** takes items back
   off the list. Choose where to save, then press **Scan photos**. Your originals
   are never modified.
3. **Step 2 – Check the crop.** Look at the red box on a few photos. It should
   sit on top of the label bar, on the tray's bottom rail, and on the tray ends.

   - Consistently too wide or narrow across the whole batch → drag the
     **Tray width** slider.
   - One photo off, or the tray looks keystoned because the camera was not quite
     square to it → **drag the yellow corner handles**. Each corner moves
     independently, so you can match a trapezoid; the crop then warps it back to
     a rectangle. Drag inside the box to move the whole thing.
   - **Copy this adjustment to every photo in the folder** applies the same nudge
     to the batch, for a rig that is consistently off rather than one bad photo.
   - **Undo my corner changes on this photo** puts a single photo back to auto.

   Adjusting corners does not change the output size, so millimetres-per-pixel
   stays identical across the whole set. Photos you adjusted are flagged
   `corners_adjusted` in the manifest.
4. **Step 3 – Labels and run.** One row per photo, each showing its label bar
   enlarged. Fill in the **tray number** and **end depth**, and enter the start
   depth of the first tray once — every later tray starts where the previous one
   ended, so you only type one depth per tray.

   Two rows sharing a tray number are that tray's dry and wet shots. If someone
   reshot a tray, just correct the tray numbers; nothing downstream shifts.
   **Dry / Wet** is decided automatically from brightness, and the dropdown lets
   you override it when the call was close.

   Press **Check** to catch typos, then **Process and save all folders** — every
   hole you scanned is processed in one go.

You get one folder per hole, split into `Dry` and `Wet`, plus a `manifest.csv`
per hole recording every decision.

**The depth boxes come pre-filled** wherever the app could read the label bar —
about 9 out of 10 in testing. They appear in **amber**, meaning read by machine
and not yet checked by a person. Click or type in a box and the amber clears.
The app warns before saving if any amber is left.

Both photos of a tray get the same depth, so one readable shot covers its
partner. Where the app is unsure it leaves the box empty rather than guessing —
an empty box costs you ten seconds, a wrong depth in a filename does not
announce itself.

Read **PHOTO_SOP.md** before your next shift at the core shed. Most of the
quality is decided by how the photos are taken, not by this tool.

---

## What it actually does

**Finds the tray** on the CIELAB b\* channel — a dark teal tray is cool (low b\*)
against a warm concrete floor (high b\*). Plain greyscale thresholding does *not*
work here; floor brightness overlaps the tray.

**Straightens** using a trimmed line fit to the tray's bottom rail. The top rail
is occluded by the label bar, so it can't be used.

**Crops** from the top of the label bar to the bottom rail. The bar is a loose
steel bar that shifts between photos, so its position is found per photo — a
fixed offset either clips the bar or leaves a strip of floor above it.

**Holds millimetres-per-pixel constant** across every photo, so trays are
directly comparable. Because the bar moves, this means output *height* varies by
a few percent. That is intended.

**Corrects exposure and white balance** against the white tape measure — the same
physical object in every photo, so any variation in it is camera or lighting
rather than geology. It never references the core itself: doing that would
flatten genuine lithological brightness differences, which is usually the signal
you care about. It never touches contrast or saturation.

**Decides dry vs wet** by comparing the photos of each tray: the darker core is
wet. Shooting order is *not* used — photographers sometimes shoot wet first.
Close calls (usually a near-empty tray) are flagged `check` in the manifest, and
the Dry/Wet dropdown overrides the decision on any row.

**Groups photos by the tray number you enter**, not by adjacency. Adjacency is
tempting since a tray's two shots are always next to each other, but one reshoot
then shifts every later pair and silently puts the wrong depths on the rest of
the hole.

**Validates depths** before writing anything: tray numbers consecutive, depths
strictly increasing, intervals plausible, two photos per tray.

### Reading the label bar

**There is no OCR engine to install.** The label characters are a fixed set of
physical stencil tiles — the same glyphs in every photo — so the app carries its
own digit reader (`corephoto/glyphs.py`, ~15 KB of templates) rather than a
general OCR engine.

That decision was measured, not assumed. Over 72 real DD26ZOP labels:

| reader | correct | blank | **wrong** |
|---|---|---|---|
| Tesseract, best configuration | 38% | 56% | **6%** |
| built-in digit reader | 93% | 7% | **0%** |

A general OCR engine has to cope with any font; recognising one known alphabet is
a far easier problem, and it needs no installation and adds no 50 MB to the
download.

Three things keep the error rate at zero, and all of them prefer an empty box to
a guess:

- **Confidence gates.** A character is only accepted if it is clearly closer to
  one digit than any other. Across every correct read the worst decision margin
  was 10.4; the misreads sat at 3.9–5.0. The threshold sits between them.
- **The depth sequence vetoes, it never invents.** A reading that would go
  backwards or jump implausibly is discarded. An earlier version tried to
  *repair* such readings from the sequence and turned an obviously-wrong 7.5 into
  a plausible-looking 67.5 — much harder for a human to catch. Rejecting is the
  safer failure.
- **Nothing is auto-accepted.** Every value appears in amber next to the enlarged
  label bar until you pass over the field, and the app warns before saving if any
  amber is left.

Tesseract is still used as a fallback if it happens to be installed, and for the
hole ID and tray number, which are letters rather than digits.

**If the stencil kit ever changes** and depths start coming up blank, retrain
from photos you have already processed correctly:

```bash
python tools/train_glyphs.py path/to/processed -o glyph_templates.npz
```

It labels each character from the depth in the filename, refuses to learn from
photos it cannot segment confidently, and reports how many examples it found per
digit. Copy the result over `corephoto/glyph_templates.npz`.

---

## For whoever maintains this

```
corephoto/core.py     detection, deskew, crop, colour normalisation, rock stats
corephoto/naming.py   grouping, dry/wet, depth chaining, filenames, validation
corephoto/glyphs.py   built-in digit reader (locate, segment, classify)
corephoto/glyph_templates.npz   digit templates, trained on this project's labels
corephoto/ocr.py      reader dispatch + depth-sequence veto; optional Tesseract
corephoto/app.py      tkinter GUI (sv_ttk theme)
corephoto/icon.ico    app icon
corephoto/settings.py remembered preferences
tools/train_glyphs.py retrain the digit reader if the stencil kit changes
tests/test_logic.py   pure-logic checks, run by CI
```

Run from source:

```bash
pip install -r requirements.txt
python run_app.py
```

Depth reading works from source too — the digit reader is part of the package,
so there is nothing extra to install.

Tesseract is optional and only used as a fallback, plus for the hole ID and tray
number. If you want it, run `install_tesseract.bat`. The app looks for it in the
copy bundled in the .exe → a path you chose with **Locate tesseract.exe…**
(remembered in `%LOCALAPPDATA%\CorePhotoTool\settings.json`) → `PATH` → the
usual Windows install folders → the registry. The Windows installer often does
*not* add it to `PATH`, which is why checking `PATH` alone is not enough.

Builds are automatic. Push to `main` and GitHub Actions produces
`CorePhotoTool.exe` under the run's Artifacts. To cut a release:

```bash
git tag v1.0.1
git push --tags
```

### If the photos change

Most changes are handled automatically — camera height, small tilt, tray drift,
bar position. Two things are not:

- **Tray a different colour.** `tray_mask` in `core.py` is the only function to
  change. For a neutral tray on a warm floor, thresholding b\* still works; for a
  wooden tray try the a\* channel or saturation.
- **Tray not spanning most of the frame width.** The row/column coverage profiles
  in `band()` assume it does. If new photos are much wider-framed, crop them down
  first or relax the `frac` argument.

Grey bars at the left or right edge of an output mean the tray ran past the frame
edge in the original. Nothing recovers that; it is flagged in the manifest as
`tray_cut_left` / `tray_cut_right`. Fix it at the camera.
