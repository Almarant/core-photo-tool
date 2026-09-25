"""
Locating the label bar.

Two independent detectors, because each fails where the other works:

* `tile_band` looks for the white stencil tiles themselves - bright, solid,
  sharing one baseline, with DARK STEEL between them. That last test is what
  rejects the white measuring tape and the pale depth-marker blocks inside the
  tray, both of which also form bright rows on a shared baseline.

* `gradient_top` is the original method: the steepest floor->bar darkening
  above the tray. Fast and simple, but on a WET tray the dark wet core is a
  stronger edge than the bar, so it returns a line below the text and the crop
  comes out with the label sliced off. That cost 6 of 42 photos on UG26ZOP.

The tile method in turn fails when something pale sits directly behind the bar
- a wooden pallet, in practice. So `find` runs both and takes whichever sits
higher. Neither alone was enough on real material; together they found all 42.
"""
import cv2
import numpy as np


def tile_boxes(g, H, W, frac=0.80):
    """Candidate stencil tiles in a reduced-scale greyscale frame."""
    thr = max(110, float(np.percentile(g, 97)) * frac)
    m = (cv2.GaussianBlur(g, (0, 0), 1.2) > thr).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))
    n, _, st, _ = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if h < 0.010 * H or h > 0.09 * H:
            continue
        if w < 0.005 * W or w > 0.35 * W:
            continue
        if a / float(w * h) < 0.45:          # solid, not a wisp of glare
            continue
        if w / float(h) > 12:                # the measuring tape
            continue
        out.append((x, y, w, h, a))
    return out, m


def _rows_at(g, H, W, frac):
    """Baseline-sharing rows of tiles at one brightness threshold."""
    bx, tilemask = tile_boxes(g, H, W, frac)
    if len(bx) < 2:
        return []
    hm = float(np.median([b[3] for b in bx]))
    out, seen = [], set()
    for c in bx:
        yc = c[1] + c[3] / 2.0
        key = int(yc / max(hm * 0.5, 1))
        if key in seen:
            continue
        seen.add(key)
        grp = [b for b in bx if abs((b[1] + b[3] / 2.0) - yc) < 0.9 * hm]
        if len(grp) < 3:
            continue
        y0 = min(b[1] for b in grp); y1 = max(b[1] + b[3] for b in grp)
        x0 = min(b[0] for b in grp); x1 = max(b[0] + b[2] for b in grp)
        if x1 - x0 < 0.35 * W:
            continue
        sub_ = g[y0:y1, x0:x1]
        sm = tilemask[y0:y1, x0:x1]
        bg = sub_[sm == 0]
        if bg.size < 50:
            continue
        if float(np.median(bg)) > 135:   # white tape or bright core, not steel
            continue
        out.append((y0, y1, x0, x1, len(grp), x1 - x0))
    return out


def tile_band(small):
    """(y_top, y_bot, x_left, x_right, n_tiles) of the label bar, or None.

    Several brightness thresholds are tried, not one. A single threshold taken
    from the frame's own 97th percentile is set by the BRIGHTEST thing in shot,
    and once a pale cover went under the core box that stopped being the label
    tiles: on IMG_0475 the threshold landed at 172 while the tiles on the rusty
    bar peak at 210 against a cover peaking at 216, so the bar was not found at
    all. The only row that did qualify was the white depth markers standing in
    the core, 190 px lower, and the crop collapsed to a sliver.

    Pooling rows from several thresholds and taking the TOPMOST that passes
    every test finds the real bar. The dark-background test is what keeps the
    extra sensitivity safe: rows lying on the cover, on the tape or on pale
    core are still thrown out, whatever threshold found them.
    """
    if small is None or small.size == 0:
        return None
    g = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == 3 else small
    H, W = g.shape
    cands = []
    for frac in (0.80, 0.70, 0.60):
        cands += _rows_at(g, H, W, frac)
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], -c[5]))   # the bar sits above the core
    y0, y1, x0, x1, nt, _ = cands[0]
    pad = 0.75 * (y1 - y0)
    return (max(int(y0 - pad), 0), min(int(y1 + pad), H),
            max(int(x0 - 6), 0), min(int(x1 + 6), W), nt)


def gradient_top(s, bb):
    """Original method: steepest floor->bar darkening above the tray."""
    x0, y0, x1, y1 = bb
    L = cv2.cvtColor(s, cv2.COLOR_BGR2LAB)[:, :, 0].astype(np.float32)
    xa, xb = int(x0 + 0.12 * (x1 - x0)), int(x0 + 0.88 * (x1 - x0))
    if xb <= xa:
        return None, 0.0
    prof = cv2.GaussianBlur(np.median(L[:, xa:xb], axis=1).reshape(-1, 1),
                            (1, 7), 0).ravel()
    lo, hi = max(int(y0) - 78, 2), max(int(y0) - 6, 3)
    if hi - lo < 12:
        return None, 0.0
    gr = np.diff(prof[lo:hi + 1])
    k = int(np.argmin(gr))
    return lo + k + 1, float(-gr[k])


def find(small, bb):
    """Label bar position, in reduced-scale pixels.

    Returns (y_top, y_bot, method, strength).

    THE TILE DETECTOR WINS WHENEVER IT FINDS ANYTHING. It looks at the stencil
    tiles directly and needs no help from the tray mask; measured against nine
    hand-corrected DD_ZOP_015 crops it put the top edge within 9 px, sd 2.6.

    This used to take whichever candidate sat HIGHER in the frame, on the
    theory that cropping high is safer than clipping the label. It is not: a
    spurious gradient reading then beats a correct tile reading, and on
    IMG_0500 that put the top edge 47 px too high - roughly 400 px of bench and
    cover above the tray in the finished crop. Across the same nine photos that
    rule scored sd 14.8 against the tile detector's 2.6.

    The gradient stays as the fallback for frames where the tiles cannot be
    found at all (something pale directly behind the bar), which is the case it
    was always good at.
    """
    band = tile_band(small)
    if band is not None:
        y, y_bot = int(band[0]), int(band[1])
        _, strength = gradient_top(small, bb)
        return y, int(max(y_bot, y + 4)), "tiles", float(strength)
    grad_y, strength = gradient_top(small, bb)
    if grad_y is not None and 0 <= grad_y < bb[1]:
        return int(grad_y), int(bb[1]), "gradient", float(strength)
    return max(int(bb[1]) - 30, 0), int(bb[1]), "fallback", float(strength)
