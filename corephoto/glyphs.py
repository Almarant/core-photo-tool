"""
Reads the END DEPTH number off the label bar, with no external OCR engine.

Why not Tesseract: the label characters are a fixed set of physical stencil
tiles, the same glyphs in every photo. A general OCR engine has to cope with any
font and does badly here - measured over 72 real DD26ZOP labels it read the end
depth correctly 38% of the time and produced a confidently wrong number 6% of
the time. Recognising one known alphabet is a much easier problem.

Measured on the same 72 labels, this module: 93% correct, 7% left blank,
0% wrong. The failure mode is "couldn't read it", which is safe - the field
stays empty and a human types it.

The templates in glyph_templates.npz are digit centroids trained on 282 glyphs
cut from those photos. Retrain with tools/train_glyphs.py if the label kit ever
changes.
"""
import os
import numpy as np
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "glyph_templates.npz")

# Tuned on the 72-label set. Across correct reads the worst decision margin was
# 10.4 and the worst distance 26.7; the misreads sat at margin 3.9-5.0 and
# distance 57-60. These thresholds therefore reject every misread that is
# detectable from confidence alone, without losing a single correct read.
MIN_MARGIN = 8.0     # gap between best and second-best squared distance
MAX_DIST = 30.0      # distance to the winning centroid

_T = None


def _templates():
    global _T
    if _T is None:
        if not os.path.exists(TEMPLATES):
            return None
        d = np.load(TEMPLATES)
        _T = (d["centroids"], int(d["gw"]), int(d["gh"]))
    return _T


def available():
    return _templates() is not None


# ------------------------------------------------------------------ locate
def _tiles_mask(g):
    bl = cv2.GaussianBlur(g, (0, 0), 3)
    m = (bl > max(140, np.percentile(bl, 90) * 0.72)).astype(np.uint8) * 255
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))


def bar_slice(img, frac=0.14):
    return img[:max(int(img.shape[0] * frac), 1), :]


def depth_region(bar):
    """The END DEPTH number on the label bar.

    Not simply "the rightmost bright group": glare and the bright end of the bar
    sit further right than the number. Real lettering shares a baseline and is
    densely filled, so filter on that before taking the rightmost.
    """
    if bar is None or bar.size == 0:
        return None
    g = cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY) if bar.ndim == 3 else bar
    H, W = g.shape
    big = cv2.morphologyEx(_tiles_mask(g), cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (35, 15)))
    n, _, st, _ = cv2.connectedComponentsWithStats(big, 8)
    # Thresholds scale with the strip, so a downscaled bar degrades gracefully
    # instead of silently matching nothing. (Absolute pixel limits here once made
    # the app find no digits at all, because it passed a 1/4-size strip.)
    min_h, min_w, min_a = 0.12 * H, 0.20 * H, 0.07 * H * H
    cand = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if a < min_a or h < min_h or w < min_w:
            continue
        if a / float(w * h) < 0.5:              # sparse blob = glare, not text
            continue
        cand.append((x, y, w, h, a))
    if not cand:
        return None
    ref = sorted(cand, key=lambda b: -b[4])[:4]
    yc = np.median([b[1] + b[3] / 2.0 for b in ref])
    hm = np.median([b[3] for b in ref])
    ok = [b for b in cand
          if abs((b[1] + b[3] / 2.0) - yc) < 0.40 * hm and 0.6 * hm < b[3] < 1.5 * hm]
    if not ok:
        return None
    x, y, w, h, _ = max(ok, key=lambda b: b[0])
    p = int(0.25 * h)
    return g[max(y - p, 0):min(y + h + p, H), max(x - p, 0):min(x + w + p, W)]


def characters(reg):
    """Individual printed characters: dark blobs inside the white tiles.

    Segmenting the white tiles instead does not work - adjacent tiles touch and
    merge. The printed characters are cleanly separated.
    """
    if reg is None or reg.size == 0:
        return []
    r = cv2.resize(reg, (reg.shape[1] * 2, reg.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
    bw = cv2.threshold(r, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (3, 9)))
    n, _, st, _ = cv2.connectedComponentsWithStats(bw, 8)
    H = r.shape[0]
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if h < 0.28 * H or h > 0.98 * H:        # digits span most of the tile
            continue
        if w < 0.04 * H or a < 40:              # the decimal dot, or noise
            continue
        if y < 0.02 * H and h > 0.9 * H:        # tile border
            continue
        out.append((x, r[y:y + h, x:x + w]))
    out.sort(key=lambda t: t[0])
    return [g for _, g in out]


# --------------------------------------------------------------- classify
def classify(glyph):
    """(digit, distance, margin) for one character image."""
    t = _templates()
    if t is None:
        return None, 1e9, 0.0
    cent, gw, gh = t
    g = cv2.resize(glyph, (gw, gh), interpolation=cv2.INTER_AREA).astype(np.float32)
    g = cv2.normalize(g, None, 0, 1, cv2.NORM_MINMAX).ravel()
    d = ((g[None, :] - cent) ** 2).sum(1)
    order = np.argsort(d)
    return int(order[0]), float(d[order[0]]), float(d[order[1]] - d[order[0]])


def read_digits(glyphs):
    """Digit string, or None if any character is not confidently recognised."""
    if not (3 <= len(glyphs) <= 6):
        return None
    out = []
    for g in glyphs:
        k, dist, margin = classify(g)
        if k is None or dist > MAX_DIST or margin < MIN_MARGIN:
            return None
        out.append(str(k))
    return "".join(out)


def read_depth(bar):
    """End depth in metres from a label-bar image, or None."""
    digits = read_digits(characters(depth_region(bar)))
    if not digits or len(digits) < 3:
        return None
    try:
        return float(digits[:-2] + "." + digits[-2:])
    except ValueError:
        return None
