"""
Core engine: tray detection, deskew, crop, colour normalisation, dry/wet stats.

No GUI, no I/O beyond reading/writing image files, so it can be used from the
app, from a script, or from a notebook.

The tray is segmented on the CIELAB b* channel: a dark teal tray is cool (low
b*) against a warm concrete floor (high b*). Plain greyscale thresholding does
NOT work - floor brightness overlaps the tray. If your trays are a different
colour, `tray_mask` is the one function to revisit.
"""
from dataclasses import dataclass, asdict
import os
import cv2
import numpy as np

RED = 8            # detection decode factor (cv2.IMREAD_REDUCED_COLOR_8)
OUT_WIDTH = 5952   # output width in pixels; sets the mm-per-pixel for everything


@dataclass
class Params:
    """Everything a different camera setup might need to change."""
    tray_width: int = 744    # tray width in 1/8-scale detection px
    pad_x: int = 6
    pad_top: int = 2         # above the label bar
    pad_bottom: int = 3      # below the tray's bottom rail
    normalise: bool = True
    target_tape_L: float = 200.0
    jpeg_quality: int = 92

    @property
    def scale(self):
        return OUT_WIDTH / ((self.tray_width + 2 * self.pad_x) * RED)

    def to_dict(self):
        return asdict(self)


PAD_COLOR = (105, 105, 105)   # honest fill where the tray ran past the frame edge


# --------------------------------------------------------------- detection
def tray_mask(small):
    b = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2LAB)[:, :, 2], (7, 7), 0)
    m = cv2.threshold(b, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    cs = cv2.findContours(np.uint8(lab == i) * 255, cv2.RETR_EXTERNAL,
                          cv2.CHAIN_APPROX_SIMPLE)[0]
    if not cs:
        return None
    out = np.zeros(small.shape[:2], np.uint8)
    cv2.drawContours(out, [max(cs, key=cv2.contourArea)], -1, 255, -1)
    return out


def _longest_run(flags):
    runs, cur = [], None
    for i, f in enumerate(flags):
        if f and cur is None:
            cur = i
        elif not f and cur is not None:
            runs.append((cur, i - 1)); cur = None
    if cur is not None:
        runs.append((cur, len(flags) - 1))
    return max(runs, key=lambda r: r[1] - r[0]) if runs else None


def band(m, frac=0.5):
    """Tray extent from row/column coverage profiles - more robust than
    minAreaRect, which bleeds into shadows and into hoses or boots lying nearby."""
    h, w = m.shape
    r = _longest_run((m > 0).sum(1) >= frac * w)
    if r is None:
        return None
    y0, y1 = r
    c = _longest_run((m[y0:y1 + 1] > 0).sum(0) >= frac * (y1 - y0 + 1))
    return None if c is None else (c[0], y0, c[1], y1)


def tilt(m, x0, y0, x1, y1):
    """Tilt from the tray's bottom rail; the top rail is occluded by the bar."""
    xs, ys = [], []
    step = max(1, (x1 - x0) // 120)
    for x in range(x0 + (x1 - x0) // 12, x1 - (x1 - x0) // 12, step):
        col = np.nonzero(m[y0:y1 + 1, x])[0]
        if col.size:
            xs.append(x); ys.append(y0 + col[-1])
    if len(xs) < 20:
        return 0.0
    xs, ys = np.array(xs, float), np.array(ys, float)
    for _ in range(3):                                  # trimmed least squares
        a, b0 = np.polyfit(xs, ys, 1)
        res = np.abs(ys - (a * xs + b0))
        keep = res <= max(np.percentile(res, 70), 1.0)
        if keep.sum() < 20:
            break
        xs, ys = xs[keep], ys[keep]
    return float(np.degrees(np.arctan(np.polyfit(xs, ys, 1)[0])))


def bar_top(s, bb):
    """Top edge of the label bar = steepest floor->bar darkening above the tray.
    The bar is a loose steel bar, so it shifts between photos and must be found
    per photo - a fixed offset either clips it or leaves floor above it."""
    x0, y0, x1, y1 = bb
    L = cv2.cvtColor(s, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    xa, xb = int(x0 + 0.12 * (x1 - x0)), int(x0 + 0.88 * (x1 - x0))
    prof = cv2.GaussianBlur(np.median(L[:, xa:xb], axis=1).reshape(-1, 1), (1, 7), 0).ravel()
    lo, hi = max(int(y0) - 78, 2), max(int(y0) - 6, 3)
    if hi - lo < 12:
        return int(y0) - 30, 0.0
    g = np.diff(prof[lo:hi + 1])
    k = int(np.argmin(g))
    return lo + k + 1, float(-g[k])


def detect(path):
    """Returns dict(small, bb, angle, bar, bar_strength) or None."""
    s = cv2.imread(path, cv2.IMREAD_REDUCED_COLOR_8)
    if s is None:
        return None
    m = tray_mask(s)
    if m is None:
        return None
    bb = band(m)
    if bb is None:
        return None
    ang = tilt(m, *bb)
    if abs(ang) > 0.05:
        h, w = m.shape
        R = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
        s = cv2.warpAffine(s, R, (w, h))
        m = cv2.warpAffine(m, R, (w, h), flags=cv2.INTER_NEAREST)
        bb = band(m) or bb
    else:
        ang = 0.0
    bt, strength = bar_top(s, bb)
    x0, y0, x1, y1 = bb
    return dict(small=s, bb=bb, angle=ang, bar=bt, bar_strength=strength,
                cut_left=int(x0 <= 2), cut_right=int(x1 >= s.shape[1] - 3))


def crop_box(d, p: Params):
    """Auto-detected crop rectangle, in detection-pixel coordinates."""
    x0, y0, x1, y1 = d["bb"]
    xc = 0.5 * (x0 + x1)
    return (xc - p.tray_width / 2 - p.pad_x, d["bar"] - p.pad_top,
            xc + p.tray_width / 2 + p.pad_x, y1 + p.pad_bottom)


def crop_quad(d, p: Params, adj=None):
    """The four corners actually used for the crop: tl, tr, br, bl.

    `adj` is four (dx, dy) offsets in detection pixels, one per corner, from
    dragging the handles in the app. A quad rather than a rectangle because a
    photo taken slightly off-centre keystones the tray - opposite edges are not
    parallel - and no rectangle can sit on all four sides at once. Warping a
    quad onto a rectangle rectifies that.
    """
    bx0, by0, bx1, by1 = crop_box(d, p)
    q = np.array([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], np.float32)
    if adj:
        q = q + np.asarray(adj, np.float32)
    return q


def out_size(d, p: Params):
    """Output canvas, derived from the AUTO box so millimetres-per-pixel stay
    constant no matter how the user nudges the corners."""
    bx0, by0, bx1, by1 = crop_box(d, p)
    return (int(round((bx1 - bx0) * RED * p.scale)),
            int(round((by1 - by0) * RED * p.scale)))


# ----------------------------------------------------------- normalisation
def normalise(im, target_L=200.0):
    """Exposure / white-balance correction referenced to the white tape measure.

    The tape is the same physical object in every photo, so variation in it is
    camera or lighting, not geology. Never reference the core itself - that
    flattens real lithological brightness differences.
    """
    q = cv2.resize(im, (max(im.shape[1] // 4, 1), max(im.shape[0] // 4, 1)),
                   interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(q, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, a, b = lab[:, :, 0], lab[:, :, 1] - 128, lab[:, :, 2] - 128
    tape = (L > 170) & (np.abs(a) < 6) & (np.abs(b) < 10)
    if tape.sum() < 0.004 * L.size:
        return im, None
    gains = [min(target_L / max(float(np.median(q[:, :, c][tape])), 1.0), 1.6)
             for c in range(3)]
    out = im.astype(np.float32)
    for c in range(3):
        out[:, :, c] *= gains[c]
    return np.clip(out, 0, 255).astype(np.uint8), gains


def core_stats(crop):
    """Median L* / chroma of the ROCK only. The mask excludes teal tray plastic
    and white tape; without it a near-empty tray is dominated by plastic and the
    dry/wet comparison collapses."""
    h, w = crop.shape[:2]
    roi = crop[int(0.16 * h):int(0.97 * h), int(0.04 * w):int(0.96 * w)]
    roi = cv2.resize(roi, (max(roi.shape[1] // 4, 1), max(roi.shape[0] // 4, 1)),
                     interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, a, b = lab[:, :, 0], lab[:, :, 1] - 128, lab[:, :, 2] - 128
    rock = (a > -3) & (b > -4) & (L > 15) & (L < 225)
    fill = float(rock.mean())
    if rock.sum() < 0.02 * L.size:
        rock = (L > 15) & (L < 225)
    return (float(np.median(L[rock])), float(np.median(np.sqrt(a * a + b * b)[rock])), fill)


def render(path, d, p: Params, adj=None):
    """Full-resolution crop. Returns (image, gains)."""
    OW, OH = out_size(d, p)
    quad = crop_quad(d, p, adj) * RED          # deskewed full-resolution coords
    dst = np.array([[0, 0], [OW, 0], [OW, OH], [0, OH]], np.float32)
    full = cv2.imread(path, cv2.IMREAD_COLOR)
    fh, fw = full.shape[:2]
    R = np.vstack([cv2.getRotationMatrix2D((fw / 2, fh / 2), d["angle"], 1.0), [0, 0, 1]])
    P = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    crop = cv2.warpPerspective(full, P @ R, (OW, OH), flags=cv2.INTER_AREA,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=PAD_COLOR)
    gains = None
    if p.normalise:
        crop, gains = normalise(crop, p.target_tape_L)
    return crop, gains


def find_photos(indir, skip_dirs=()):
    """All JPEGs under indir, skipping output folders and _/. folders."""
    skip = {os.path.basename(s).lower() for s in skip_dirs if s}
    out = []
    for root, dirs, names in os.walk(indir):
        dirs[:] = [d for d in dirs if not d.startswith(("_", ".")) and d.lower() not in skip]
        for n in sorted(names):
            if n.lower().endswith((".jpg", ".jpeg")):
                out.append(os.path.join(root, n))
    return sorted(out)


def label_strip(crop, frac=0.16):
    """Top slice of a cropped tray - the label bar, for reading or display."""
    return crop[:max(int(crop.shape[0] * frac), 1), :]
