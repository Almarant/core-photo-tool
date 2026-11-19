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

from . import barfind

RED = 8            # detection decode factor (cv2.IMREAD_REDUCED_COLOR_8)
OUT_WIDTH = 5952   # output width in pixels; sets the mm-per-pixel for everything

# Every genuine tilt measured across the DD26ZOP and UG26ZOP sets is under
# 1.2 degrees - the rig is on a flat floor. A steeper answer means the rail fit
# latched onto something that is not the rail, and rotating by it swings the
# label bar out of the crop (IMG_0142 came back at 3.36 and lost its label).
MAX_TILT_DEG = 2.0


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
    # The finished crop - label bar down to the tray's bottom rail - has a
    # fixed shape, because it is the same physical tray in the same framing.
    # Measured at 2.94-2.98 on three different rigs (teal trays on concrete,
    # steel trays on a bare bench, steel trays on a pale cover), so it is a
    # reliable way to tell a good segmentation from a bad one.
    tray_aspect: float = 2.95
    aspect_tol: float = 0.45
    # With this on, the crop's HEIGHT is derived from its width and the tray's
    # known shape instead of from the detected bottom rail. The bottom rail is
    # the least reliable edge - it is where the mask bleeds into shadow - and
    # letting it set the height is why one batch came out at aspects 2.15,
    # 2.55 and 4.09. Locking costs a few pixels of accuracy on the odd photo
    # (median error measured at 0-3 px) and buys an identical canvas for every
    # photo in a batch, which is what makes a set usable side by side.
    lock_aspect: bool = True
    # Margin, in 1/8-scale px, added around a crop that was fitted to this
    # photo's own tray (`fit_tray`). The fitted edges already take in about
    # 1 px (8 full-size px) of cover; 0 keeps them exactly as found.
    fit_pad: float = 0.0

    @property
    def scale(self):
        return OUT_WIDTH / ((self.tray_width + 2 * self.pad_x) * RED)

    def to_dict(self):
        return asdict(self)


PAD_COLOR = (105, 105, 105)   # honest fill where the tray ran past the frame edge


# --------------------------------------------------------------- loading
_ORIENT = {}


def _orientation_flag(path):
    """IMREAD_IGNORE_ORIENTATION when the camera's rotation tag is wrong.

    The camera points straight down at the tray, and its orientation sensor
    cannot tell which way round it is held from straight above. DD26ZOP-027
    IMG_1726 came out tagged "rotate 90" on perfectly ordinary landscape
    pixels; OpenCV obeys the tag, the tray arrives standing on end, and the
    crop and the label reading both fail outright.

    A tray photo is always landscape. So the tag is obeyed only when it leaves
    the image landscape; when it would turn landscape pixels into a portrait
    image it is ignored. Decided once per file, at 1/8 scale.
    """
    if path not in _ORIENT:
        flag = 0
        a = cv2.imread(path, cv2.IMREAD_REDUCED_COLOR_8)
        if a is not None and a.shape[0] > a.shape[1]:
            b = cv2.imread(path, cv2.IMREAD_REDUCED_COLOR_8 | cv2.IMREAD_IGNORE_ORIENTATION)
            if b is not None and b.shape[1] > b.shape[0]:
                flag = cv2.IMREAD_IGNORE_ORIENTATION
        _ORIENT[path] = flag
    return _ORIENT[path]


def imread(path, flag=cv2.IMREAD_COLOR):
    """cv2.imread with the orientation rule above. Every photo goes through it."""
    return cv2.imread(path, flag | _orientation_flag(path))


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


def tray_mask_bright(small):
    """Tray by what the BACKGROUND cannot reach.

    `tray_mask` segments on colour and needs the tray to differ in hue from
    whatever it sits on. Put a pale cover under the box and that stops being
    the useful signal - brightness becomes it: measured on DD_ZOP_015 the cover
    runs 37-45 L* above the tray, against 19 L* for a bare wooden bench.

    Thresholding "dark = tray" still fails, because pale core is as bright as
    the cover. But the cover is one bright region that touches the frame edge
    and surrounds the box, so flood-filling inwards from the border through
    bright pixels marks the background, and whatever it cannot reach is the
    tray - pale core included.
    """
    lab = cv2.cvtColor(cv2.GaussianBlur(small, (7, 7), 0), cv2.COLOR_BGR2LAB)
    L = lab[:, :, 0]
    _, bright = cv2.threshold(L, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    h, w = L.shape
    ff = bright.copy()
    pad = np.zeros((h + 2, w + 2), np.uint8)
    for x in range(0, w, 8):
        for y in (0, h - 1):
            if ff[y, x] == 255:
                cv2.floodFill(ff, pad, (x, y), 128)
    for y in range(0, h, 8):
        for x in (0, w - 1):
            if ff[y, x] == 255:
                cv2.floodFill(ff, pad, (x, y), 128)
    tray = 255 - (ff == 128).astype(np.uint8) * 255
    tray = cv2.morphologyEx(tray, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 5)))
    # The measuring tape is bright, spans the tray and touches both ends, so
    # the flood fill runs straight through it and cuts the tray in half. Bridge
    # it with a tall kernel.
    kv = max(int(0.10 * h), 9)
    tray = cv2.morphologyEx(tray, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (5, kv)))
    n, labi, st, _ = cv2.connectedComponentsWithStats(tray, 8)
    if n < 2:
        return None
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    cs = cv2.findContours(np.uint8(labi == i) * 255, cv2.RETR_EXTERNAL,
                          cv2.CHAIN_APPROX_SIMPLE)[0]
    if not cs:
        return None
    out = np.zeros(L.shape, np.uint8)
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


def _band_at(m, frac):
    h, w = m.shape
    r = _longest_run((m > 0).sum(1) >= frac * w)
    if r is None:
        return None
    y0, y1 = r
    c = _longest_run((m[y0:y1 + 1] > 0).sum(0) >= frac * (y1 - y0 + 1))
    return None if c is None else (c[0], y0, c[1], y1)


def band(m, frac=0.5):
    """Tray extent from row/column coverage profiles - more robust than
    minAreaRect, which bleeds into shadows and into hoses or boots lying nearby.

    Retries at lower coverage because an ALREADY-CROPPED photo has the tray
    running off both edges, and a half-empty tray drops below 50% coverage;
    both make the strict profile stop short of the real tray ends. IMG_0142
    lost its END DEPTH that way.
    """
    bb = None
    for f in (frac, 0.38, 0.28, 0.20, 0.12):
        b = _band_at(m, f)
        if b:
            bb = b
        if b and (b[2] - b[0]) > 0.80 * m.shape[1]:
            break
    if bb is None:
        return None
    cols = (m > 0).sum(0)
    nz = np.nonzero(cols > 0.05 * m.shape[0])[0]
    if nz.size and (nz[-1] - nz[0]) > 1.25 * (bb[2] - bb[0]):
        bb = (int(nz[0]), bb[1], int(nz[-1]), bb[3])
    return bb


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
    ang = float(np.degrees(np.arctan(np.polyfit(xs, ys, 1)[0])))
    if abs(ang) > MAX_TILT_DEG:
        return 0.0          # failed fit, not a tilted tray - see MAX_TILT_DEG
    return ang


def bar_top(s, bb):
    """Top edge of the label bar. See corephoto/barfind.py for why this needs
    two detectors rather than one."""
    y, y_bot, method, strength = barfind.find(s, bb)
    return y, y_bot, strength, method


# Weight on solidity in the hypothesis score. Shape alone leaves the two
# methods nearly tied on some photos, and then an invisible change - re-saving
# the same frame as JPEG - flips which one wins and moves the crop. Solidity
# breaks those ties: a mask that has bled into the background fills its own
# bounding box less densely than one that has actually found the tray.
SOLIDITY_WEIGHT = 3.0


def _hypothesis(s, m, p: Params):
    """Score one candidate segmentation by the crop it implies: the right
    SHAPE for a core tray, and a mask that actually fills that shape."""
    if m is None:
        return None
    bb = band(m)
    if bb is None:
        return None
    y_top, _, _, _ = barfind.find(s, bb)
    top = min(y_top, bb[1])
    aspect = (bb[2] - bb[0]) / max(bb[3] - top, 1)
    x0, y0, x1, y1 = bb
    solid = float((m[y0:y1 + 1, x0:x1 + 1] > 0).mean())
    err = abs(aspect - p.tray_aspect) + SOLIDITY_WEIGHT * (1.0 - solid)
    return dict(mask=m, bb=bb, aspect=aspect, solid=solid, err=err,
                shape_err=abs(aspect - p.tray_aspect))


BENCH_L = 165       # L* (0-255) above which a column/row counts as pale bench cover


def _first_run(arr, cond, start, step, n):
    """Index of the first of `n` consecutive entries satisfying `cond`,
    walking from `start` in direction `step`; None if there is none."""
    i = start
    while 0 <= i < len(arr):
        if all(0 <= i + k * step < len(arr) and cond(arr[i + k * step]) for k in range(n)):
            return i
        i += step
    return None


def _lum(img, k=1):
    return cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32),
                            (5, 5) if k > 1 else (3, 3), 0)


BAR_STEP = 30.0     # L* drop from the cover onto the rusty bar that counts as its edge
BAR_STEP_WEAK = 20.0  # ...where the cover is in shadow (DD26ZOP-026 IMG_1679-1683)


def bar_edge(img, bb, bar, k=1):
    """The top edge of the label bar, fitted as a straight line.

    `barfind` places the bar from its stencil tiles, padded upward by 3/4 of a
    tile, so its answer sits about 13 px (1/8 scale) - 100 px at full size -
    ABOVE the bar. That margin of cover is what made the top of every crop
    look loose. And the tray is levelled on its bottom rail, which on
    DD26ZOP-028 differed from the bar by up to 1.1 deg, so the bar ran visibly
    downhill across the crop.

    Where the bar sits against something brighter than itself (a pale cover)
    its own top edge can be measured directly: the bright-to-dark steps
    below the tile reading in ~120 columns, the straight line through most of
    them, then a trimmed fit to the strongest step along that line. `img` is at `k` times the detection scale (k=2 for a
    1/4-scale decode); `bb` and `bar` are in detection px, and so is `top`.

    Returns dict(slope, top, n, resid) or None. `slope` is in this frame;
    `top` is where the highest stretch of the edge lies, less half a detection
    px.
    """
    H, W = img.shape[:2]
    L = _lum(img, k)
    xs = np.unique(np.linspace(max(bb[0] * k + 10, 2), min(bb[2] * k - 10, W - 3),
                               120).astype(int))
    if len(xs) < 40:
        return None
    C = (L[:, xs - 1] + L[:, xs] + L[:, xs + 1]) / 3.0
    cs = np.vstack([np.zeros((1, C.shape[1]), np.float32), np.cumsum(C, 0)])
    a4, a1, b1, b5 = 4 * k, k, k, 5 * k
    ya = max(int((bar - 6) * k), a4 + 1)
    yb = min(int((bar + 27.5) * k), H - b5 - 2)
    if yb - ya < 4:
        return None
    ys = np.arange(ya - 1, yb + 1)
    up = (cs[ys - a1 + 1] - cs[ys - a4 + 1]) / (a4 - a1)        # rows y-4k .. y-k
    dn = (cs[ys + b5] - cs[ys + b1]) / (b5 - b1)                # rows y+k .. y+5k
    sc = np.where(dn < 135, up - dn, -1e9)

    def peak(i, j):
        """Sub-pixel position of the step peak at row index i, column j."""
        a, b, c = sc[i - 1, j], sc[i, j], sc[i + 1, j]
        den = a - 2 * b + c
        off = 0.5 * (a - c) / den if (den < 0 and a > -1e8 and c > -1e8) else 0.0
        return ys[i] + float(np.clip(off, -0.5, 0.5))

    def line(pts):
        if len(pts) < 40:
            return None
        P = np.array(pts, float)
        px, py = P[:, 0], P[:, 1]
        keep = np.ones(len(P), bool)
        for _ in range(4):
            a = np.polyfit(px[keep], py[keep], 1)
            r = py - np.polyval(a, px)
            keep = np.abs(r) < max(k, 2.5 * float(np.median(np.abs(r[keep]))))
            if keep.sum() < 40:
                return None
        a = np.polyfit(px[keep], py[keep], 1)
        return a, px, py, py - np.polyval(a, px), keep

    # Find the edge first as the straight line through the most columns'
    # step peaks - every peak, not one per column. One peak per column does
    # not work: the strongest is often the bottom of a white stencil tile
    # (DD26ZOP-021, where the tiles sit high on the bar), and the first is
    # often a leaf printed on the cover (DD26ZOP-026 IMG_1681, 1683). The
    # bar's own top edge is the one line that runs the whole width.
    tmax = np.tan(np.radians(MAX_TILT_DEG))

    def ransac(thr):
        pk = (sc[1:-1] > thr) & (sc[1:-1] >= sc[:-2]) & (sc[1:-1] > sc[2:])
        cand = [(j, x, peak(i + 1, j)) for j, x in enumerate(xs)
                for i in np.nonzero(pk[:, j])[0]]
        if len(cand) < 40:
            return None
        cj = np.array([c[0] for c in cand]); cx = np.array([c[1] for c in cand], float)
        cy = np.array([c[2] for c in cand])
        pairs = np.random.default_rng(0).integers(0, len(cand), size=(400, 2))
        best = None
        for p, q in pairs:
            if cx[p] == cx[q]:
                continue
            sl = (cy[q] - cy[p]) / (cx[q] - cx[p])
            if abs(sl) > tmax:
                continue
            inl = np.abs(cy - (cy[p] + sl * (cx - cx[p]))) <= 1.0 * k
            key = (len(np.unique(cj[inl])), -(cy[p] + sl * (0.5 * W - cx[p])))
            if best is None or key > best[0]:
                best = (key, inl)
        if best is None or best[0][0] < 40:
            return None
        return cj, cx, cy, best[1]

    # Weak steps only where there are not enough strong ones: a weak step is
    # as often the shading across the cover as the bar's edge.
    got = ransac(BAR_STEP) or ransac(BAR_STEP_WEAK)
    if got is None:
        return None
    cj, cx, cy, inl = got
    # That line is only the seed: it may follow one stretch of a bar that is
    # not quite straight. The trimmed fit through each column's peak nearest
    # to it, within 4k, follows the whole bar.
    a0 = np.polyfit(cx[inl], cy[inl], 1)
    d0 = np.abs(cy - np.polyval(a0, cx))
    near = {}
    for j, x, y, dd in zip(cj, cx, cy, d0):
        if dd <= 4 * k and (j not in near or dd < near[j][2]):
            near[j] = (x, y, dd)
    first = line([(x, y) for x, y, _ in near.values()])
    if first is None:
        return None
    # Then the strongest step within 2k of that line, for the precise points.
    a0 = first[0]
    pts = []
    for j, x in enumerate(xs):
        yl = np.polyval(a0, x)
        lo = max(int(np.searchsorted(ys, yl - 2 * k)), 1)
        hi = min(int(np.searchsorted(ys, yl + 2 * k)) + 1, len(ys) - 1)
        if hi - lo < 1:
            continue
        i = lo + int(np.argmax(sc[lo:hi, j]))
        pts.append((x, peak(i, j), sc[i, j]))
    strong = [(x, y) for x, y, v in pts if v > BAR_STEP]
    fit = line(strong if len(strong) >= 60 else [(x, y) for x, y, v in pts if v > BAR_STEP_WEAK])
    if fit is None:
        return None
    a, px, py, r, keep = fit
    if px[keep].max() - px[keep].min() < 0.6 * (bb[2] - bb[0]) * k:
        return None
    # The edge points themselves, not the line, set the top: a bar that bows
    # or kinks (DD26ZOP-028 IMG_1798 rises 15 px, 1/4 scale, mid-length) must
    # not lose its highest stretch to a straight line through the rest.
    # So: the highest step peak in each column from 4k above the line to k
    # below it, and the 5th percentile of those.
    rc = cy - np.polyval(a, cx)
    w_ = (rc >= -4 * k) & (rc <= k)
    hi_ = {}
    for j, y in zip(cj[w_], cy[w_]):
        hi_[j] = min(y, hi_.get(j, y))
    top = (float(np.percentile(list(hi_.values()), 5)) - 0.5 * k) / k
    return dict(slope=float(a[0]), top=top, n=int(keep.sum()),
                resid=float(np.median(np.abs(r[keep]))) / k)


def _wall_edge(band, lo, hi, right, k):
    """Where the tray's outer wall meets the cover, when the cover is in shade.

    The bright-run test needs cover above BENCH_L; in shadow at the edge of
    the frame it is not (L* 135 beside DD26ZOP-028 IMG_1805). Take instead the
    strongest step from the wall up onto anything cover-like."""
    n = 3 * k
    best, at = 0.0, None
    for i in range(max(lo, 0), min(hi, len(band)) - n):
        inner, outer = (band[i], band[i + n]) if right else (band[i + n], band[i])
        if outer >= 125 and inner < 135 and outer - inner > best:
            best, at = float(outer - inner), i + n / 2.0
    return at if best >= 20 else None


def fit_tray(s, bb, bar, k=1, strict=False):
    """This photo's own tray edges, where the tray sits on a pale cover.

    The crop used one width for the whole batch - the median of the detected
    widths - centred on each photo's tray. That assumes the camera height never
    changes. On DD26ZOP-026 it did: the tray measured 651-735 px across the
    batch (12% spread), so every crop was either loose, showing floor and the
    cover at the sides and bottom, or tight. The tray finder's own box was no
    substitute, out by a median 6 px and up to 62 px per edge on that hole.

    On a pale cover the edges can be found directly: walk out from the middle
    of the tray along the rows below the bar until the cover starts, and down
    past the tray's bottom rail until it starts again. `s` is at `k` times
    detection scale; `bb` and `bar` (the top of the bar) are in detection px.
    Returns (x0, x1, y1) in detection px, each taking in about 8 full-size px
    of cover, or None when no cover is found on a side that does not run off
    the frame - on those rigs (teal trays on concrete, DD_ZOP_014/015) the
    batch-width crop is kept, as before.

    The bottom used to be the first pale rows below a fixed fraction of the
    tray height. Pale core reaches that far: on DD26ZOP-028 IMG_1796, 1803,
    1805 and 1806 the walk stopped inside the tray. It now starts from the
    darkest row below the last core row - the shadow under the tray's inner
    rail - and no core can sit below that.

    A side whose cover is in shade, or that runs off the frame, is accepted
    only when the bar's own top edge was measured (`bar_edge`), which needs a
    pale surface behind the bar. `strict` says it was not: then both sides
    must find bright cover, as in 1.7.1, or a teal tray on concrete (UG_ZOP)
    fits by accident on a third of its photos.
    """
    H, W = s.shape[:2]
    w0 = max(bb[2] - bb[0], 1) * k
    top = bar * k
    ya, yb = int(top + 0.027 * w0), int(min(H - 1, top + 0.26 * w0))
    if yb - ya < 20 * k:
        return None
    L = _lum(s, k)
    band = np.median(L[ya:yb], axis=0)
    xc = int(0.5 * (bb[0] + bb[2]) * k)
    bright = lambda v: v > BENCH_L
    r = _first_run(band, bright, xc, +1, 6 * k)
    lft = _first_run(band, bright, xc, -1, 6 * k)
    x0 = lft + 1 - k if lft is not None else None
    x1 = r + k if r is not None else None
    if strict and (x0 is None or x1 is None):
        return None
    if x0 is None:
        e = _wall_edge(band, 0, 40 * k, False, k)
        x0 = e - 0.5 * k if e is not None else None
    if x1 is None:
        e = _wall_edge(band, W - 40 * k, W, True, k)
        x1 = e + 0.5 * k if e is not None else None
    # A side that found no cover must genuinely run off the frame: the tray
    # box reaches the edge, or the frame's own edge column is still tray wall
    # (DD26ZOP-028 IMG_1807, where the box stopped 15 px short of it).
    off_l = bb[0] <= 2 or float(np.mean(band[:2 * k])) < 125
    off_r = bb[2] >= W / k - 3 or float(np.mean(band[-2 * k:])) < 125
    if (x1 is None and not off_r) or (x0 is None and not off_l):
        return None
    if x0 is None and x1 is None:
        return None
    x0 = max(x0, 0) if x0 is not None else 0
    x1 = min(x1, W) if x1 is not None else W
    if not 0.85 <= (x1 - x0) / w0 <= 1.15:
        return None
    rows = np.median(L[:, int(x0) + 20 * k:int(x1) - 20 * k], axis=1)
    s0 = int(top + 0.29 * (x1 - x0))
    if s0 + 30 * k >= H:
        return None
    m = s0 + int(np.argmin(rows[s0:s0 + 30 * k]))
    y1 = H
    for y in range(m, min(H - 2 * k, m + 45 * k)):
        if np.all(rows[y:y + 2 * k] > 145):
            y1 = y + 0.5 * k
            break
    return (float(x0) / k, float(x1) / k, float(y1) / k)


def _anchor(d):
    """Top of the tray as measured: the bar's own edge where it was found."""
    return d["edge"] if d.get("edge") is not None else d["bar"]


def settle_fits(dets, tol=0.10):
    """Batch rule for the per-photo fits. Returns the tray shape to lock to.

    A fit whose shape (width / height from the bar down to the bottom rail) is
    off the batch median by more than `tol` has caught something that is not
    the tray - a paintbrush beside it (026 IMG_1674) or a rail it stopped short
    of (IMG_1684) - and is dropped; that photo keeps the old batch-width box
    and its shape warning. The lock uses the TALLEST good shape, so no photo
    loses its bottom rail; the others take in a sliver of cover below it.
    Returns None when the batch is not a pale-cover rig (see below).
    """
    shapes = {k: (d["fit"][1] - d["fit"][0]) / max(d["fit"][2] - _anchor(d), 1)
              for k, d in dets.items() if d.get("fit")}
    # A rig with a pale cover fits on most photos (42-48 of 44-56 on DD26ZOP
    # 021/026/027); a teal tray on concrete fits only by accident (2 of 28 on
    # DD_ZOP_014, 10 of 48 on 015). Mixing the two in one batch would put the
    # accidental shape on every photo, so below half the batch none is used.
    if len(shapes) < max(3, 0.5 * len(dets)):
        for d in dets.values():
            d.pop("fit", None)
        return None
    med = float(np.median(list(shapes.values())))
    good = []
    for k, a in shapes.items():
        if abs(a - med) > tol:
            dets[k].pop("fit", None)
        else:
            good.append(a)
            dets[k]["aspect_ok"] = True     # the box is this photo's own tray
    return min(good) if good else None


def detect_image(s, p: Params = None, fine=None):
    """Detection on an already-decoded 1/8-scale frame.

    Two segmentations are tried, because no single one covers the rigs this is
    used on: colour (`tray_mask`, a teal tray against concrete) and brightness
    (`tray_mask_bright`, any tray on a pale cover). Whichever implies a crop
    closer to the tray's known shape wins. If NEITHER lands within
    `aspect_tol`, the photo is marked `aspect_ok=False` rather than quietly
    cropped wrong, and the app tells the user to calibrate the folder from one
    corrected photo.

    Split out from `detect` so the tests can ship 1/8-scale fixtures - a few
    tens of KB each instead of a few hundred - and still exercise the real tray
    finder, deskew and bar detection rather than only the pure logic.

    `fine`, the same photo decoded at 1/4 scale, is optional: with it the bar
    angle, the bar edge and the tray edges are measured at twice the
    resolution. At 1/8 the bar angle comes out within about 0.2 deg of the
    1/4-scale answer; that is 20 full-size px from one end of the bar to the
    other, which is visible, so `detect` always passes it.
    """
    if s is None:
        return None
    p = p or Params()
    cands = [(name, _hypothesis(s, fn(s), p))
             for name, fn in (("colour", tray_mask), ("bright", tray_mask_bright))]
    cands = [(n, c) for n, c in cands if c is not None]
    if not cands:
        return None
    mask_method, best = min(cands, key=lambda t: t[1]["err"])
    aspect_ok = best["shape_err"] <= p.aspect_tol
    m, bb = best["mask"], best["bb"]
    s0, m0 = s, m
    ang = tilt(m, *bb)
    # Find the bar BEFORE rotating as well as after: rotating the frame can
    # hide the tile row from the finder (IMG_0140 and IMG_0149 both lost their
    # label that way), so the pre-rotation answer has to stay in play.
    pre_y, pre_bot, pre_method, pre_strength = barfind.find(s, bb)
    if abs(ang) > 0.05:
        s, m, bb = _rotated(s0, m0, ang, bb)
    else:
        ang = 0.0
    bt, bbot, strength, method = bar_top(s, bb)
    # Same rule as in barfind.find: a tile reading beats a gradient one, and
    # "whichever sits higher" is not a tie-break, it is how a bad reading wins.
    if method != "tiles" and pre_method == "tiles":
        bt, bbot, method, strength = pre_y, pre_bot, pre_method, pre_strength
    edge, level, fine_r, k = None, "rail", None, 1
    if method in ("tiles", "gradient"):
        # Level on the label bar where its edge can be measured (see
        # `bar_edge`), on the finer decode when there is one.
        k = 2 if fine is not None else 1
        src = fine if fine is not None else s0
        # A bar that is not quite straight changes which points the trimmed
        # fit keeps once the frame turns, so one pass can leave 0.6 deg on it
        # (IMG_1798); repeat until the bar is level in the frame it is cut from.
        rail = (s, m, bb)
        a_try, e = ang, None
        for _ in range(4):
            f_try = _rot(src, a_try)
            e = bar_edge(f_try, bb, bt, k)
            if e is None or e["resid"] > 1.5:
                e = None
                break
            d_a = float(np.degrees(np.arctan(e["slope"])))
            if abs(d_a) < 0.03:
                break
            a_try += d_a
            if abs(a_try) > MAX_TILT_DEG:
                e = None
                break
            s, m, bb = _rotated(s0, m0, a_try, bb)
        else:
            f_try = _rot(src, a_try)
            e = bar_edge(f_try, bb, bt, k)
        if e is not None:
            ang, level, fine_r = a_try, "bar", f_try
            if abs(e["top"] - bt) < 27:
                edge = e["top"]
        else:
            s, m, bb = rail
    x0, y0, x1, y1 = bb
    fit = None
    if method in ("tiles", "gradient"):
        top = edge if edge is not None else bt
        if fine_r is None and fine is not None:
            fine_r = _rot(fine, ang)
        fit = (fit_tray(fine_r, bb, top, k, strict=edge is None) if fine_r is not None
               else fit_tray(s, bb, top, strict=edge is None))
    return dict(small=s, bb=bb, angle=ang, bar=bt, bar_bot=bbot,
                bar_strength=strength, bar_method=method, tray_px=int(x1 - x0),
                mask_method=mask_method, tray_aspect=round(best["aspect"], 2),
                aspect_ok=bool(aspect_ok), fit=fit, edge=edge, level=level,
                cut_left=int(x0 <= 2), cut_right=int(x1 >= s.shape[1] - 3))


def _rot(img, ang):
    if abs(ang) < 1e-6:
        return img
    h, w = img.shape[:2]
    return cv2.warpAffine(img, cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0), (w, h))


def _rotated(s, m, ang, bb):
    """Frame and tray mask deskewed by `ang`, and the tray box found again."""
    h, w = m.shape
    R = cv2.getRotationMatrix2D((w / 2, h / 2), ang, 1.0)
    s = cv2.warpAffine(s, R, (w, h))
    m = cv2.warpAffine(m, R, (w, h), flags=cv2.INTER_NEAREST)
    return s, m, (band(m) or bb)


def detect(path, p: Params = None):
    """Returns dict(small, bb, angle, bar, bar_method, bar_strength,
    tray_px, cut_left, cut_right) or None."""
    return detect_image(imread(path, cv2.IMREAD_REDUCED_COLOR_8), p,
                        fine=imread(path, cv2.IMREAD_REDUCED_COLOR_4))


def manual_detect(path):
    """A placeholder detection for a photo the tray finder could not read.

    Without this the photo simply vanishes from the run - logged as "skipped"
    and never seen again. Here it gets a default box covering most of the frame
    so the corner handles can be dragged onto the tray by hand.
    """
    s = imread(path, cv2.IMREAD_REDUCED_COLOR_8)
    if s is None:
        return None
    h, w = s.shape[:2]
    bb = (int(0.04 * w), int(0.22 * h), int(0.96 * w), int(0.80 * h))
    return dict(small=s, bb=bb, angle=0.0, bar=int(0.16 * h),
                bar_bot=int(0.22 * h), bar_strength=0.0,
                bar_method="manual", tray_px=int(bb[2] - bb[0]),
                cut_left=0, cut_right=0, auto_failed=True)


def crop_box(d, p: Params):
    """Auto-detected crop rectangle, in detection-pixel coordinates.

    With a per-photo fit (`fit_tray`) the box is this photo's own tray: from
    the top edge of the label bar to the cover under the bottom rail, wall to
    wall. Its height is the tray's own, not the batch shape - those boxes
    have already been checked against the batch in `settle_fits`, and locking
    them to the tallest shape is what left cover showing under most trays.

    Otherwise the batch width is centred on the detected tray, as before, with
    the height from the batch shape when `lock_aspect` is on.
    """
    x0, y0, x1, y1 = d["bb"]
    top = d["edge"] - p.fit_pad if d.get("edge") is not None else d["bar"] - p.pad_top
    if d.get("fit"):
        fx0, fx1, fy1 = d["fit"]
        return (fx0 - p.fit_pad, top, fx1 + p.fit_pad, fy1 + p.fit_pad)
    xc = 0.5 * (x0 + x1)
    left = xc - p.tray_width / 2 - p.pad_x
    right = xc + p.tray_width / 2 + p.pad_x
    if p.lock_aspect and p.tray_aspect > 0:
        return (left, top, right, top + (right - left) / p.tray_aspect)
    return (left, top, right, y1 + p.pad_bottom)


def _scale(d, p: Params, prof=None):
    """Output pixels per detection pixel for this photo.

    A fitted photo is scaled so ITS tray fills the canvas width: the tray is
    the same physical object in every shot, so that is what keeps
    millimetres-per-pixel constant when the camera height changes. Everything
    else uses the batch scale, as before.
    """
    if prof is None and d.get("fit"):
        l, _, r, _ = crop_box(d, p)
        return OUT_WIDTH / (max(r - l, 1) * RED)
    return p.scale


def profile_from_quad(d, p: Params, adj=None):
    """Turn one corrected crop into a rule that fits the REST of the folder.

    The tray finder segments on the b* channel and assumes a teal plastic tray
    against warm concrete. On a rig with grey steel trays on a wooden bench
    that separation is simply gone - measured on DD_ZOP_014, the tray sits at
    b* = -11.5 and the bench at -10.3 - and the box comes out wrong by up to
    13% of the tray width, on every photo, in a different way each time. No
    statistic I could find tells the two situations apart, so guessing between
    them is not honest.

    What IS reliable is the label bar, and the camera does not move. So: take
    the box the user fixed by hand, keep its horizontal span and its height,
    and re-anchor the top on each photo's OWN detected bar - the bar is loose
    and shifts between shots, which is why copying absolute pixel offsets
    (the old "copy to every photo") could not follow it.

    Measured against four hand-corrected DD_ZOP_014 crops, calibrating on any
    one of them places the other three to within 1.2% of the tray width.
    Automatic detection on the same four was out by 13%.
    """
    q = crop_quad(d, p, adj)
    x0 = float(min(q[0][0], q[3][0])); x1 = float(max(q[1][0], q[2][0]))
    y0 = float(min(q[0][1], q[1][1])); y1 = float(max(q[2][1], q[3][1]))
    return dict(x0=x0, x1=x1, dy_top=y0 - d["bar"], h=y1 - y0)


def box_from_profile(d, prof):
    """The calibrated box for this photo, anchored on its own label bar."""
    y0 = d["bar"] + prof["dy_top"]
    return (prof["x0"], y0, prof["x1"], y0 + prof["h"])


def crop_quad(d, p: Params, adj=None, prof=None):
    """The four corners actually used for the crop: tl, tr, br, bl.

    `adj` is four (dx, dy) offsets in detection pixels, one per corner, from
    dragging the handles in the app. A quad rather than a rectangle because a
    photo taken slightly off-centre keystones the tray - opposite edges are not
    parallel - and no rectangle can sit on all four sides at once. Warping a
    quad onto a rectangle rectifies that.

    `prof` is a folder-wide calibration from `profile_from_quad`; it replaces
    the automatic box, and any `adj` is then a nudge on top of it.
    """
    bx0, by0, bx1, by1 = box_from_profile(d, prof) if prof else crop_box(d, p)
    q = np.array([[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]], np.float32)
    if adj:
        q = q + np.asarray(adj, np.float32)
    return q


def out_size(d, p: Params, adj=None, prof=None):
    """Output canvas at a CONSTANT millimetres-per-pixel.

    This used to be derived from the automatic box and then the (possibly very
    different) adjusted quad was warped onto it. That does the opposite of what
    it claimed: the canvas stayed fixed while the source region changed, so the
    scale changed and the picture was stretched. Three DD_ZOP_014 crops of the
    same physical tray came out at aspect 2.15, 2.55 and 4.09.

    Deriving the canvas from the quad that is actually being sampled keeps
    millimetres-per-pixel identical across the whole set, which is the property
    that was wanted all along, and nothing is distorted.
    """
    q = crop_quad(d, p, adj, prof)
    w = 0.5 * (np.hypot(*(q[1] - q[0])) + np.hypot(*(q[2] - q[3])))
    h = 0.5 * (np.hypot(*(q[3] - q[0])) + np.hypot(*(q[2] - q[1])))
    k = _scale(d, p, prof)
    return (max(int(round(w * RED * k)), 1),
            max(int(round(h * RED * k)), 1))


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
    (a* below -3); without it a near-empty tray is dominated by plastic and the
    dry/wet comparison collapses.

    It used to require warm rock as well (b* above -4). Core that photographs
    cool - the blue-grey to purple limestone of DD26ZOP-021 to -027 - then lost
    most of its pixels to the mask, and the "rock" median was taken over the
    rusty label bar and tape markings instead: the same 113.0 came back on six
    different photos. Measured on 114 trays from five holes, that mask got 28
    dry/wet calls wrong; a* alone gets 4. Only used now for trays with more
    or fewer than two photos - pairs are compared with `pair_score`."""
    h, w = crop.shape[:2]
    roi = crop[int(0.16 * h):int(0.97 * h), int(0.04 * w):int(0.96 * w)]
    roi = cv2.resize(roi, (max(roi.shape[1] // 4, 1), max(roi.shape[0] // 4, 1)),
                     interpolation=cv2.INTER_AREA)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)
    L, a, b = lab[:, :, 0], lab[:, :, 1] - 128, lab[:, :, 2] - 128
    rock = (a > -3) & (L > 15) & (L < 225)
    fill = float(rock.mean())
    if rock.sum() < 0.02 * L.size:
        rock = (L > 15) & (L < 225)
    return (float(np.median(L[rock])), float(np.median(np.sqrt(a * a + b * b)[rock])), fill)


PAIR_W = 160      # width of the brightness map two shots are compared on


def pair_map(crop):
    """Small, blurred L* map of the tray below the label bar, for `pair_score`."""
    h, w = crop.shape[:2]
    roi = crop[int(0.16 * h):int(0.97 * h), int(0.04 * w):int(0.96 * w)]
    roi = cv2.resize(roi, (PAIR_W, max(int(PAIR_W * roi.shape[0] / max(roi.shape[1], 1)), 1)),
                     interpolation=cv2.INTER_AREA)
    L = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    return cv2.GaussianBlur(L, (5, 5), 0)


def pair_score(a, b):
    """How much brighter shot `a` is than shot `b`, place by place.

    The two shots are the same tray in nearly the same framing, so instead of
    comparing one median brightness with another - which is what a single
    bright depth block or a strip of tray plastic can tip - each place in the
    tray is compared with itself, and the median of those differences is
    taken. Positive means `a` is the dry shot. Measured on 114 trays: 112
    right, against 110 for the best whole-photo median.
    """
    n = min(a.shape[0], b.shape[0])
    a, b = a[:n], b[:n]
    ok = (a > 6) & (b > 6) & (a < 250) & (b < 250)
    if ok.sum() < 50:
        return 0.0
    return float(np.median((a - b)[ok]))


_REDUCED_FLAG = {1: cv2.IMREAD_COLOR,
                 2: cv2.IMREAD_REDUCED_COLOR_2,
                 4: cv2.IMREAD_REDUCED_COLOR_4,
                 8: cv2.IMREAD_REDUCED_COLOR_8}


def render(path, d, p: Params, adj=None, reduce=1, prof=None):
    """The crop. Returns (image, gains).

    `reduce` decodes the JPEG at 1/2, 1/4 or 1/8 size. Scanning only needs the
    crop for dry/wet luminance, the label reading and a thumbnail, and doing
    that at half size is four times less pixel work per photo - on a 72-photo
    hole the scan was re-rendering every image at full 5952 px and then the run
    did it all over again. Saving always uses reduce=1.
    """
    if reduce not in _REDUCED_FLAG:
        reduce = 1
    OW, OH = out_size(d, p, adj, prof)
    OW, OH = max(OW // reduce, 1), max(OH // reduce, 1)
    quad = crop_quad(d, p, adj, prof) * (RED / float(reduce))
    dst = np.array([[0, 0], [OW, 0], [OW, OH], [0, OH]], np.float32)
    full = imread(path, _REDUCED_FLAG[reduce])
    if full is None:
        raise IOError("could not read " + path)
    fh, fw = full.shape[:2]
    R = np.vstack([cv2.getRotationMatrix2D((fw / 2, fh / 2), d["angle"], 1.0), [0, 0, 1]])
    P = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    crop = cv2.warpPerspective(full, P @ R, (OW, OH), flags=cv2.INTER_AREA,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=PAD_COLOR)
    gains = None
    if p.normalise:
        crop, gains = normalise(crop, p.target_tape_L)
    return crop, gains


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff")


def find_photos(sources, skip_dirs=()):
    """Expand a mix of folders and individual files into a photo list.

    Accepts a single path or a list, so the app can offer "pick a folder",
    "pick some files" and drag-and-drop through one code path.
    """
    if isinstance(sources, str):
        sources = [sources]
    # Resolved absolute paths, not basenames: matching on the folder NAME skips
    # any folder called e.g. "processed" anywhere in the tree, which silently
    # drops a source folder that happens to share the output folder's name.
    skip = {os.path.normcase(os.path.abspath(s)) for s in skip_dirs if s}
    out = []
    for src in sources:
        if os.path.isfile(src):
            if src.lower().endswith(IMAGE_EXT):
                out.append(src)
            continue
        for root, dirs, names in os.walk(src):
            dirs[:] = [d for d in dirs
                       if not d.startswith(("_", "."))
                       and os.path.normcase(os.path.abspath(os.path.join(root, d)))
                       not in skip]
            for n in sorted(names):
                if n.lower().endswith(IMAGE_EXT):
                    out.append(os.path.join(root, n))
    seen, uniq = set(), []
    for p in sorted(out):
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


def label_strip(crop, frac=0.16):
    """Top slice of a cropped tray - the label bar, for reading or display."""
    return crop[:max(int(crop.shape[0] * frac), 1), :]


def label_strips(crop, d=None, p: Params = None, adj=None, prof=None):
    """Several candidate slices of the label bar, for the reader to cross-check.

    Where to cut is the weak link. Too shallow clips the lettering; too deep
    lets the depth markers standing in the core join the baseline and the
    reader locks onto the wrong group. One fixed fraction cannot suit every
    frame, so hand the reader a few and let it drop anything they disagree on.
    """
    # Nine heights, not three. Where the lettering falls inside the crop moves
    # with how much bar the frame caught, and a given photo often reads at only
    # one or two of these - measured on hand-corrected DD_ZOP_015 crops, one
    # photo read only at 0.12 and another only at 0.26. Slices are cheap; the
    # reader still drops any field two of them disagree on, so more attempts
    # cannot turn into more guesses.
    fracs = [0.10, 0.12, 0.14, 0.16, 0.19, 0.22, 0.26, 0.30, 0.35]
    if d is not None and p is not None:
        q = crop_quad(d, p, adj, prof)
        by0 = float(min(q[0][1], q[1][1])); by1 = float(max(q[2][1], q[3][1]))
        span = max(by1 - by0, 1e-6)
        derived = (d.get("bar_bot", d["bb"][1]) - by0 + p.pad_top) / span
        if 0.04 < derived < 0.62:
            fracs.append(round(min(derived * 1.15, 0.62), 3))
    return [label_strip(crop, f) for f in sorted(set(fracs))]
