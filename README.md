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

## What changed in 1.7.0

Found by running 1.6.0 over all 28 photos of DD_ZOP_014, where the reader
filled in 6 tray numbers and 3 depths.

**The label reader was blind on most of that hole, and nothing said so.** On 22
of the 28 photos it found *no lettering at all* — not a misread, no read. The
pale cover behind the bar comes through as one or two blobs a thousand pixels
wide, and the region finder took the four **biggest** blobs as its sample of
the lettering and measured the baseline off them. The baseline then landed
between the cover and the text, and every real group failed the band test.
Size cannot tell a patch of cover from a word, so the finder now looks for what
lettering actually is: **several blobs of similar height sitting on one
baseline**. Every blob is tried as the seed of a row and the row with the most
members wins. Two patches of cover cannot outnumber the five groups of a label.

**It also tries three brightness thresholds instead of one.** The bar is lit
unevenly — on the UG26ZOP photos the bright right-hand end set a threshold the
dimmer left end could not reach, so only END DEPTH was ever found and the tray
number was invisible.

**The tray number is taken from the middle of the bar, not "the second group
from the left".** How many groups a bar breaks into is not fixed: END, DEPTH
and the number usually separate, TRAY sometimes splits from its digits, and the
hole is sometimes clipped off the crop. Counting from the left put "END" in the
tray column the moment the hole group was missed. Measured across both diamond
holes the hole sits at 0.13–0.20 of the bar width, the tray at 0.45–0.52 and
the END DEPTH run at 0.71–0.91.

**A tray number clipped short no longer vetoes the field.** One shallow slice
reading 5 where seven slices read 35 threw the whole reading away. A reading
that is a strict *suffix* of a longer one seen at least as often is now dropped
as a clipped read — a digit can be lost off the front of a group, it cannot be
invented there. Any other disagreement still vetoes.

Over three sets, with **no value read wrongly anywhere**:

| | tray, before | tray, now | depth, before | depth, now |
|---|---|---|---|---|
| DD_ZOP_014 (28) | 6 | **28** | 8 | 26 |
| DD_ZOP_015 (17) | 16 | **17** | 15 | 15 |
| UG26ZOP (19) | 6 | **9** | 15 | 15 |

**Tray numbers now carry across the photos that could not be read.** The table
starts with a guess from the photo order — 1, 1, 2, 2 — and where the reader
failed that guess stayed on screen in white, looking exactly like a confirmed
value. On a hole starting at tray 26 it was wrong on every row. The photos are
in order, so each read is compared with its own position guess and, if every
read implies the same offset, the offset is applied to the rest. Carried values
land amber. If the reads do not agree on one offset — a reshoot, a missing
photo, a misread — nothing is carried and the log says so.

End to end on DD_ZOP_014 the table now fills **28 of 28 tray numbers and 28 of
28 depths**, all correct, against 6 and 3 before.

## What changed in 1.6.0

Three things reported from a full 48-photo run of DD_ZOP_015, all of them in
the window rather than in the image work.

**The first photo of a scan came up blank.** You had to press Next and then
Back to see it. `_scan_done` runs on the main thread, but it is queued *before*
the callback that clears the busy flag — and the preview was guarded by that
same flag, so the one draw it asked for was thrown away. The guard now tests a
separate lock that `_scan_done` clears itself, the moment the new detection
results are in place. The tab is also selected and laid out before that first
draw: on an unmapped canvas `winfo_width()` returns **1 px**, so the photo was
being scaled to a fallback size and the crop handles sat away from the box.

**The wheel did nothing over the label table on step 3.** Only the canvas and
its frame were bound, and once the rows are built they cover the canvas
completely — the pointer is always over a child widget, so the event never
reached anything that could scroll. Every widget in the table is bound now.
That also fixes something worse and quieter: ttk binds the wheel on a combobox
to **change its value**, so a scroll that landed on the Dry/Wet column was
editing the data. The handler returns `break`, which stops that.

**Tray 1 starts at 0.00 m by itself.** If the lowest tray number on the table
is 1, the start depth is filled in. It only ever fills an empty field, so a
hole that carries on from a previous run keeps whatever you typed, and a hole
whose first tray is tray 5 is left alone.

Four window-level checks were added (`tests/test_gui.py`, run under a virtual
display) — each one fails on 1.5.0 and passes here.

## What changed in 1.5.0

Two bugs, both of which made the reader look far worse than it was.

**One unread tray was discarding every depth after it.** The depth column
stopped at tray 3 of 24. The sequence check compares each reading against the
previous tray's end depth, but it only advances that reference when a tray is
actually read — while still allowing a one-tray gap. Tray 4 was not read, so
trays 6 to 24 were all measured against tray 3's 12.90 m, every gap broke the
8 m ceiling, and **21 correctly-read depths were thrown away**. The window now
widens by however many trays were skipped. On the same data: 3 of 24 → **22 of
24**, with backwards jumps and wild values still rejected.

**The white depth markers standing in the core were being read as the label
bar.** They look exactly like stencil tiles — bright, solid, sharing a
baseline, dark between them. On IMG_0475 the pale cover pushed the brightness
threshold to 172 while the tiles on the rusty bar peak at 210, so the real bar
was never found and the marker row 190 px lower was the only candidate; the
crop then started below the core and ran off the bottom of the frame. The tile
finder now pools candidates from several thresholds and takes the topmost that
passes every test. The dark-background test is what keeps the extra sensitivity
safe — rows lying on the cover, on the tape or on pale core are still thrown
out.

Measured over the 17 DD_ZOP_015 photos checked here: **one output size**, shape
check failed on none, tray number 16/17, end depth filled 15/17 with nothing
read wrongly.

## What changed in 1.4.0

Found by running 1.3.0 over all 48 photos of DD_ZOP_015 and comparing the saved
crops against the boxes that had to be corrected by hand.

**The label bar was being resolved against the tray mask, and inherited its
mistakes.** Candidates were filtered by the mask's box, the gradient detector
searched a window derived from it, and whichever answer sat *higher* in the
frame won. So whenever the two segmentation hypotheses tied and the box
flipped, the bar moved with it — up to 47 px, which is roughly **400 px of
bench and cover above the tray** in the finished crop. That is the "large area
above the tray" on 12 of the 48.

The tile detector needs no box at all: it looks at the stencil tiles directly.
Measured against nine hand-corrected crops, it places the top edge with **sd
2.6 px** against 14.8 for the old rule, so it is now authoritative wherever the
tiles are found, and the gradient is strictly a fallback for frames where they
cannot be. Worst-corner error across those nine: **52 px → 11 px**.

**The depth reader now tries nine strip heights instead of three.** Where the
lettering falls inside the crop moves with how much bar the frame caught, and a
given photo often reads at only one or two heights — one read only at 0.12,
another only at 0.26. Slices are cheap, and two that disagree still veto each
other, so more attempts cannot become more guesses.

Measured on the photos checked here: end depth **6/48 → 9 of 12**, tray number
**11 of 12**, nothing read wrongly.

**Dry/wet on tray 3 fixed itself.** It was being decided on a crop that
included a strip of bench, which shifted the rock luminance. With the crop
right, the call is correct. Chroma was tested as a second cue and rejected — it
got tray 2 wrong.

One thing worth knowing: on IMG_0479 the bar physically reads `37·70` while
your corrected depth is 39.20. The reader was right and the label is wrong —
worth a look at that box.

## What changed in 1.3.0

**Put a pale cover under the core box.** It works, and the tool now uses it.

Measured on DD_ZOP_015, the cover sits **37–45 L\* above the tray**, against
19 L\* for a bare wooden bench. But 1.2.0 could not exploit that, because it
segments on *colour*, not brightness — which is why many of those photos came
out uncropped.

- **A second tray finder, by brightness.** The cover is one bright region that
  surrounds the box and touches the frame edge, so flood-filling inwards from
  the border marks the background, and whatever it cannot reach is the tray —
  pale core included. (Thresholding "dark = tray" does not work: pale core is
  as bright as the cover. The measuring tape also spans the tray and lets the
  fill straight through, cutting it in half, so it is bridged.)
- **The tool picks the method per photo, automatically.** A core tray crop has
  a fixed shape — measured at 2.94–2.98 across three different rigs — so
  whichever segmentation produces a tray-shaped, densely-filled box wins. On
  the covered batch brightness wins 11 of 12; on teal trays against concrete
  colour still wins 18 of 19.
- **Every crop in a batch now comes out the same size.** The tray's height is
  derived from its width and the batch's measured shape, rather than from the
  detected bottom rail — the least reliable edge, and the one that produced a
  set at aspects 2.15, 2.55 and 4.09. Verified: one canvas for all 12 covered
  photos, one for all 19 UG photos.
- **Photos whose box is the wrong shape are flagged, not silently cropped.**
  The scan log says how many and tells you to calibrate the folder from one
  corrected photo.

Knock-on: with correct crops, the label reader went from about a third of tray
numbers on this material to **10 of 12**.

**Best practice for photographers:** keep using the cover, and make sure it
extends past all four edges of the box in frame. That is what lets the tool
find the tray without anyone touching a slider.

## What changed in 1.2.0

Found by running 1.1.0 on DD_ZOP_014 — a hole shot on **different equipment**
(grey steel trays on a wooden bench, not teal plastic on concrete). Every crop
had to be corrected by hand.

**The tray finder cannot segment that rig, and neither could the old version.**
It thresholds the CIELAB b\* channel on the assumption that a cool teal tray
stands out against warm concrete. Measured on these photos the tray sits at
b\* = −11.5 and the bench at −10.3: no separation at all. The mask then covers
47–75% of the frame and the box is wrong by up to 13% of the tray width, in a
different way on each photo. It is also **unstable** — re-saving one frame at
three JPEG qualities moved its detected box to three completely different
places.

Nothing in the image reliably distinguishes "the mask worked" from "the mask
failed", so the app no longer pretends otherwise:

- **Fix one photo, calibrate the folder.** The old "copy this adjustment to
  every photo" copied absolute corner offsets, which cannot work: the label bar
  is loose and sits at a different height in every frame. The button is now
  **"Use this box for every photo in this folder"**, and it stores the corrected
  box as a rule — same width and height, top re-anchored on each photo's own
  detected bar. Measured against four hand-corrected DD_ZOP_014 crops,
  calibrating from any one of them placed the other three to within **1.2% of
  the tray width**. Automatic detection on the same four was out by **13%**.
- **The app now warns you** when the measured tray width wanders by more than
  8% across a batch, and tells you to calibrate. DD_ZOP_014 varies by 15%; a
  batch the finder handles varies by 4%.

**Stretching is fixed.** The output canvas was locked to the *automatic* box
while the quad actually being sampled followed your corners, so moving them
changed the scale and squashed the picture — three DD_ZOP_014 crops of the same
physical tray came out at aspect 2.15, 2.55 and 4.09. The canvas now follows the
quad at a constant millimetres-per-pixel, which is what "constant scale" was
always supposed to mean. After calibration those four crops come out identical
at 5920×2016.

**The label reader was never the problem on that hole.** It was reading a strip
cut from a bad crop. On correctly cropped DD_ZOP_014 photos it reads **4 of 4
depths and 3 of 4 tray numbers** correctly.

## What changed in 1.1.0

Field-tested on a second hole type (UG26ZOP, underground, 40 photos on a
different rig), which broke several things the original DD26ZOP set never
touched.

**Fixed, in order of how much damage each could do**

1. **A missing end depth used to mislabel the trays below it.** Leaving Tray 2
   blank produced `..._Tray3_4.95-10.20m.jpg` for a tray that actually began at
   7.65 m: the chain carried the last known depth forward. A wrong start depth
   baked into a filename is invisible afterwards. Missing depths now break the
   chain and **block** processing instead of warning.
2. **Wet trays lost their label bar.** The bar was found by the steepest
   darkening above the tray; on a wet tray the dark core is a stronger edge, so
   the crop came out with the text sliced off (6 of 42 photos). A second
   detector now looks for the stencil tiles themselves.
3. **A failed tilt fit rotated the label out of frame.** One photo measured
   3.36° when every real tilt is under 1.2°. Steep fits are now discarded.
4. **tkinter objects were being created and written from a worker thread.** It
   survives on a threaded Tcl build, which is why it never failed here, but it
   is the classic cause of an app that crashes on one colleague's machine only.
5. **The hole-ID guess was hard-coded to `DD26ZOP-`.** Removed.
6. **Output-folder skipping matched folder NAMES**, so any source subfolder
   called `processed` was silently dropped.
7. **Dragging a crop corner during a re-scan** could raise a KeyError.

**New**

- The label reader now fills in **tray numbers** as well as depths.
- **Tray width is measured from your photos** instead of assuming 744 px, so a
  different camera height no longer crops everything wrong until you find the
  slider.
- Photos where no tray is found are **kept**, with a default box you can drag,
  instead of being skipped and lost.
- **A log file** at `%LOCALAPPDATA%\CorePhotoTool\log.txt` — one line per photo
  with the detection numbers. Send it when something goes wrong.
- Scanning is **~2.5× faster** (15.5 s → 6.1 s for 19 photos): it no longer
  renders every photo at full resolution twice.
- Tests now include **four real photo fixtures**, so the tray finder, deskew and
  bar detection are actually covered. Every bug above passed the old test suite.
- PNG and TIFF are accepted, not only JPEG.
- The version is in the title bar and in every manifest row.

**Known limits**

- The reader fills about **two thirds of depths and a third of tray numbers** on
  the UG26ZOP set, with **nothing read wrongly**. It was 93% on DD26ZOP; the UG
  photos frame the bar differently. Blank means "look at it yourself", which is
  the intended failure.
- The hole number is deliberately **not** read off the bar. It can be, but on
  UG26ZOP it came back confidently wrong on 5 photos of 19, and you only type
  the hole once per folder.

## For geologists — just use it

1. Download the .exe using the button above and double-click it.
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

On the later UG26ZOP set (different rig, different framing) the built-in reader
gets about 68% of depths and 32% of tray numbers, still with nothing read
wrongly. The gap is region-finding, not character recognition: when the reader
can locate the number it almost always reads it correctly.

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
corephoto/barfind.py  label-bar location: stencil tiles + darkening gradient
corephoto/logfile.py  rolling log in the user's AppData folder
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

Tesseract is optional and only used as a depth fallback. Nothing needs it — the
built-in reader covers tray numbers and depths, and the hole ID is typed once
per folder. The app looks for it in the
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
