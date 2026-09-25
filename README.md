# Core Photo Tool

[![Download](https://img.shields.io/badge/Download-CorePhotoTool.exe-2ea44f?style=for-the-badge&logo=windows)](https://github.com/Almarant/core-photo-tool/releases/latest/download/CorePhotoTool.exe)
[![Release](https://img.shields.io/github/v/release/Almarant/core-photo-tool?style=flat-square)](https://github.com/Almarant/core-photo-tool/releases/latest)
[![Downloads](https://img.shields.io/github/downloads/Almarant/core-photo-tool/total?style=flat-square)](https://github.com/Almarant/core-photo-tool/releases)

### ⬇️ [Download CorePhotoTool.exe](https://github.com/Almarant/core-photo-tool/releases/latest/download/CorePhotoTool.exe) — Windows, 71 MB, nothing to install

> Windows will warn you it's from an unknown publisher (the app isn't code-signed).
> Click **More info → Run anyway**. First launch takes 5–15 seconds while it unpacks —
> it looks frozen, it isn't.

Point it at a folder of core-tray photographs. It finds the tray, straightens
it, crops it to the tray, corrects exposure and white balance, works out which
shot is dry and which is wet, reads the label bar, and writes files named after
the hole, tray and depth interval.

    IMG_0421.JPG, IMG_0422.JPG   ->   DDZOP-014_Dry_Tray26_93.30-96.90m.jpg
                                      DDZOP-014_Wet_Tray26_93.30-96.90m.jpg

Everything runs on your own machine: no internet, no accounts, no API. Your
originals are never modified. Output is one folder per hole, split into `Dry`
and `Wet`, with a `manifest.csv` recording every decision the tool made.

It was developed on core from the Zopkhito Sb-Au deposit in Georgia, and the
built-in label reader is trained on that project's stencil kit. Everything else
is generic - if your trays and labels look different, see
[If your photos are different](#if-your-photos-are-different).

---

## Using it

1. **Download the .exe** with the button above and double-click it. Nothing to
   install.
2. **Step 1 - Photos.** Drag folders or individual photos onto the list, or use
   **Add folder** / **Add files**. Mixing them is fine. Choose where to save,
   then press **Scan photos**.
3. **Step 2 - Check the crop.** Look at the red box on a few photos: it should
   sit on the top of the label bar, the tray's bottom rail and the tray ends.
   - Consistently too wide or narrow across the batch - drag the **Tray width**
     slider.
   - One photo off, or the tray looks keystoned because the camera was not
     square to it - drag the **yellow corner handles**. Each corner moves
     independently, so a trapezoid can be matched and the crop warps it back to
     a rectangle.
   - **Copy this adjustment to every photo in the folder** applies the same
     correction to the batch, for a rig that is consistently off.

   Adjusting corners does not change the output size, so millimetres-per-pixel
   stays identical across the set.
4. **Step 3 - Labels and run.** One row per photo, each showing its label bar
   enlarged. Tray numbers and end depths come pre-filled wherever the label
   could be read; type in the rest. You only enter the start depth of the first
   tray - every later tray starts where the previous one ended.

   Two rows sharing a tray number are that tray's dry and wet shots. If a tray
   was reshot, correct the tray numbers and nothing downstream shifts. Dry/Wet
   is decided from brightness, with a dropdown to override it.

   Press **Check** to catch typos, then **Process and save all folders**.

Anything filled in by machine shows in **amber** until you pass over the field,
and the app warns before saving if any amber is left.

**PHOTO_SOP.md** is worth reading before your next shift at the core shed. Most
of the quality is decided by how the photos are taken, not by this tool.

## What it does

**Finds the tray** two ways and keeps whichever gives a better-shaped box: on
the CIELAB b\* channel, where a dark teal tray is cool against a warm concrete
floor, and by flooding inwards from the frame edge through bright pixels, which
works when the tray is not teal or the bench is covered.

**Straightens** on a trimmed line fit to the tray's bottom rail. The top rail is
hidden behind the label bar.

**Crops** from the top of the label bar to the bottom rail. The bar is loose and
shifts between photos, so it is located on every frame - a fixed offset either
clips the bar or leaves a strip of floor above it.

**Keeps millimetres-per-pixel constant** across a batch, so trays are directly
comparable, and locks every output in a batch to one canvas size.

**Corrects exposure and white balance** against the white tape measure - the
same physical object in every photo, so any variation in it is camera or
lighting rather than geology. It never references the core itself; that would
flatten real lithological brightness differences. Contrast and saturation are
untouched.

**Decides dry vs wet** by comparing a tray's two shots: the darker core is wet.
Shooting order is not used - photographers sometimes shoot wet first. Close
calls are flagged in the manifest.

**Groups photos by the tray number**, not by adjacency, so one reshoot does not
shift the depths of every tray after it.

**Validates** before writing: tray numbers consecutive, depths increasing,
intervals plausible, two photos per tray.

## Reading the label bar

There is no OCR engine to install. The label characters are a fixed set of
physical stencil tiles - the same glyphs in every photo - so the app carries its
own digit reader (`corephoto/glyphs.py`, about 15 KB of templates). Recognising
one known alphabet is a far easier problem than general OCR, and on this
material it is much better at it: over 72 real labels Tesseract read 38%
correctly and 6% **wrongly**, against 93% correct and **0% wrong**.

Everything about it prefers an empty box to a guess:

- a character is accepted only if it is clearly closer to one digit than to any
  other;
- the depth sequence **vetoes** a reading that goes backwards or jumps
  implausibly - it never repairs one. An earlier version did repair them, and
  turned an obviously-wrong 7.5 into a plausible-looking 67.5, which is far
  harder to catch;
- several slices of the bar are read and a field is dropped if they disagree;
- nothing is auto-accepted - it all arrives amber.

A blank box means "read this one yourself". It costs ten seconds; a wrong depth
in a filename does not announce itself.

## If your photos are different

Most differences are handled automatically - camera height, tilt, tray drift,
bar position, a covered bench. Three are not:

- **A differently coloured tray.** `tray_mask` in `core.py` is the only function
  to change. The brightness-based second method often copes as it is.
- **A tray that does not span most of the frame.** The coverage profiles in
  `band()` assume it does.
- **A different stencil kit**, which makes depths come up blank. Retrain the
  digit reader from photos you have already processed correctly:

      python tools/train_glyphs.py path/to/processed -o glyph_templates.npz

  It takes each character's label from the depth in the filename, refuses to
  learn from photos it cannot segment confidently, and reports how many examples
  it found per digit. Copy the result over `corephoto/glyph_templates.npz`.

Grey bars at the left or right edge of an output mean the tray ran past the
frame edge in the original. Nothing recovers that; it is flagged in the
manifest. Fix it at the camera.

## For whoever maintains this

    corephoto/core.py      detection, deskew, crop, colour normalisation
    corephoto/barfind.py   label-bar location: stencil tiles, darkening gradient
    corephoto/glyphs.py    built-in digit reader: locate, segment, classify
    corephoto/ocr.py       reader dispatch and depth-sequence veto
    corephoto/naming.py    grouping, dry/wet, depth chaining, filenames
    corephoto/app.py       tkinter GUI
    corephoto/logfile.py   rolling log in the user's AppData folder
    tools/train_glyphs.py  retrain the digit reader
    tests/test_logic.py    pure-logic and image checks
    tests/test_gui.py      window checks, run under a display

Run from source:

    pip install -r requirements.txt
    python run_app.py

Pushing to `main` builds `CorePhotoTool.exe` on GitHub's Windows runners;
pushing a `v*` tag publishes it as a Release. Both run the tests first.

Every fix here came from a real batch that went wrong, and each one has a test
that was checked to fail on the previous version - a test that passes both ways
measures nothing. The commit log says what each version changed and what it was
measured at.
