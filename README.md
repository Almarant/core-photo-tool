# Core Photo Tool

Crops, straightens, colour-corrects, sorts dry/wet and renames drill-core tray
photographs. Point it at a folder, check one preview, type the tray depths, press
go.

Everything runs on your own machine. No internet, no accounts, no API.

Output: `DD26ZOP-006_Dry_Tray22_82.50-86.00m.jpg`

---

## For geologists — just use it

1. Download **CorePhotoTool.exe** from the
   [Releases page](../../releases) and double-click it. Nothing to install.
2. **Step 1 – Folders.** Pick the folder holding your photos (one subfolder per
   hole) and where to save results. Press **Scan photos**. Your originals are
   never modified.
3. **Step 2 – Check the crop.** Look at the red box on a few photos. It should
   sit on top of the label bar, on the tray's bottom rail, and on the tray ends.
   If it is consistently too wide or narrow, drag the **Tray width** slider until
   it fits. Flick through with **Next** to confirm.
4. **Step 3 – Labels and run.** For each tray you get its label bar shown large.
   Type the **tray number** and **end depth**. Enter the start depth of the first
   tray once — every later tray starts where the previous one ended. Press
   **Check** to catch typos, then **Process and save**.

You get one folder per hole, split into `Dry` and `Wet`, plus a `manifest.csv`
recording every decision.

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

**Decides dry vs wet** by comparing the two photos of each tray: the darker core
is wet. Shooting order is *not* used — photographers sometimes shoot wet first.
Pairs that come out nearly equal in brightness (usually a near-empty tray) are
flagged `check` in the manifest.

**Validates depths** before writing anything: tray numbers consecutive, depths
strictly increasing, intervals plausible, two photos per tray.

### Why you type the depths instead of OCR reading them

We measured it. Tesseract over 72 real DD26ZOP labels got the hole ID right 36%
of the time, the tray number 59%, and the end depth 67%. The stencil font, the
dark rusty bar and the raised-dot decimal (`86·00` reads as `86-00`) all hurt it.
A wrong depth in a filename propagates silently into Leapfrog, so that accuracy
is worse than useless.

If Tesseract *is* installed and on PATH the app will pre-fill the fields as a
hint, but the numbers are always shown next to the enlarged label bar for you to
confirm, and the whole depth sequence is validated before anything is written.
That is more reliable than either OCR or blind typing.

---

## For whoever maintains this

```
corephoto/core.py     detection, deskew, crop, colour normalisation, rock stats
corephoto/naming.py   pairing, dry/wet, depth chaining, filenames, validation
corephoto/ocr.py      optional Tesseract pre-fill
corephoto/app.py      tkinter GUI
tests/test_logic.py   pure-logic checks, run by CI
```

Run from source:

```bash
pip install -r requirements.txt
python run_app.py
```

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
