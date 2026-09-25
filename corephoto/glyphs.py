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
    """Bright stencil tiles. Every size here is relative to the strip height:
    absolute pixel sizes silently change behaviour when the caller passes a
    downscaled bar, which is exactly how a half-size strip ended up merging
    "DEPTH" and "4.55" into one unreadable group."""
    H = g.shape[0]
    sig = max(H * 0.010, 1.0)
    bl = cv2.GaussianBlur(g, (0, 0), sig)
    m = (bl > max(140, np.percentile(bl, 90) * 0.72)).astype(np.uint8) * 255
    k = max(int(H * 0.03), 3)
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))


def bar_slice(img, frac=0.14):
    return img[:max(int(img.shape[0] * frac), 1), :]


def text_groups(bar):
    """Every group of stencil tiles on the bar, left to right.

    Not simply "bright blobs": glare and the bright end of the bar also pass a
    brightness test. Real lettering shares a baseline and is densely filled, so
    filter on those two properties first. That took region-finding from ~80% to
    ~95% on the DD set.

    Returns a list of (x, image) in left-to-right order. The bar reads
    `<hole>  TRAY<n>  END DEPTH <d>`, so the first group is the hole ID, the
    last is the depth number, and the tray sits between them.
    """
    if bar is None or bar.size == 0:
        return []
    g = cv2.cvtColor(bar, cv2.COLOR_BGR2GRAY) if bar.ndim == 3 else bar
    H, W = g.shape
    kw, kh = max(int(0.11 * H), 5), max(int(0.047 * H), 3)
    big = cv2.morphologyEx(_tiles_mask(g), cv2.MORPH_CLOSE,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh)))
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
        return []
    ref = sorted(cand, key=lambda b: -b[4])[:4]
    yc = np.median([b[1] + b[3] / 2.0 for b in ref])
    hm = np.median([b[3] for b in ref])
    ok = [b for b in cand
          if abs((b[1] + b[3] / 2.0) - yc) < 0.40 * hm and 0.6 * hm < b[3] < 1.5 * hm]
    if not ok:
        return []
    out = []
    for x, y, w, h, _ in sorted(ok, key=lambda b: b[0]):
        q = int(0.25 * h)
        out.append((x, g[max(y - q, 0):min(y + h + q, H),
                         max(x - q, 0):min(x + w + q, W)]))
    return out


def depth_region(bar):
    """The END DEPTH number: the rightmost group of lettering on the bar."""
    gr = text_groups(bar)
    return gr[-1][1] if len(gr) >= MIN_GROUPS else None


def characters(reg, keep_x=False):
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
    return out if keep_x else [g for _, g in out]


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


def split_on_gap(chars_with_x, factor=2.5):
    """Split one group where the spacing jumps - i.e. between words.

    "END DEPTH 04.95" sometimes closes into a single blob and arrives as twelve
    characters, which the reader rejects as too long. The gap between DEPTH and
    the number is several times wider than the gap between two characters of
    the same word, so the run can be cut cleanly.

    The threshold is a multiple of the MEDIAN GAP, not of character width:
    these are stencil tiles, and the gap between two tiles is already about as
    wide as a character, so a width-based threshold cuts between every digit
    and leaves nothing to read.
    """
    if len(chars_with_x) < 2:
        return [chars_with_x]
    widths = [g.shape[1] for _, g in chars_with_x]
    gaps = [chars_with_x[i + 1][0] - (chars_with_x[i][0] + widths[i])
            for i in range(len(chars_with_x) - 1)]
    pos = [g for g in gaps if g > 0]
    if not pos:
        return [chars_with_x]
    med_g = float(np.median(pos))
    thr = max(factor * med_g, 0.9 * float(np.median(widths)))
    if max(gaps) <= thr:
        return [chars_with_x]
    runs, cur = [], [chars_with_x[0]]
    for i, g in enumerate(gaps):
        if g > thr:
            runs.append(cur); cur = []
        cur.append(chars_with_x[i + 1])
    runs.append(cur)
    return [r for r in runs if r]


def _classify_all(glyphs_):
    return [classify(g) for g in glyphs_]


def read_fixed_tail(glyphs_, count):
    """The last `count` characters, as digits, or None if any is uncertain.

    Used for the hole number, which is a fixed 3 digits behind a constant
    letter prefix (DD26ZOP-006, UG26ZOP-021). Taking a FIXED count matters:
    reading greedily from the right would swallow the "O" of ZOP, which is the
    one letter a digit classifier is happy to accept.
    """
    if len(glyphs_) < count:
        return None
    out = []
    for k, dist, margin in _classify_all(glyphs_[-count:]):
        if k is None or dist > MAX_DIST or margin < MIN_MARGIN:
            return None
        out.append(str(k))
    return "".join(out)


def read_tail_digits(glyphs_, max_len=2):
    """Trailing digits, stopping at the first character that is not confidently
    a digit. The tray number is 1 or 2 digits behind the word TRAY, and the kit
    is inconsistent about padding - "TRAY 1", "TRAY01" and "TRAY 12" all occur.
    """
    out = []
    for g in reversed(glyphs_[-max_len:] if max_len else glyphs_):
        k, dist, margin = classify(g)
        if k is None or dist > MAX_DIST or margin < MIN_MARGIN:
            break
        out.append(str(k))
    return "".join(reversed(out)) or None


# The bar reads `<hole>  TRAY<n>  END DEPTH <d>`, so a correct read finds at
# least three groups of lettering. Fewer means the region finder has locked
# onto something else - a single group, usually - and then "the first group"
# and "the last group" are the same thing. That is how the hole number got
# read as a depth (IMG_0136 -> 0.21 m) and depth digits got reported as a hole
# number on five photos. Anything below three groups is not read at all.
MIN_GROUPS = 3


def read_hole_digits(bar, count=3):
    """The hole number off the LEFT-hand group, e.g. 027.

    NOT USED by the app. Kept because it is occasionally useful from a script,
    but it proved unsafe as a pre-fill: on the UG26ZOP set it read the depth
    digits as a hole number on five of nineteen photos, confidently. The hole
    is typed once per folder anyway, so the risk buys nothing.
    """
    gr = text_groups(bar)
    if len(gr) < MIN_GROUPS:
        return None
    runs = split_on_gap(characters(gr[0][1], keep_x=True))
    return read_fixed_tail([g for _, g in runs[-1]], count)


def read_tray(bar):
    """The tray number off the middle group, or None."""
    gr = text_groups(bar)
    if len(gr) < MIN_GROUPS:
        return None          # need hole | tray | depth to know which is which
    runs = split_on_gap(characters(gr[1][1], keep_x=True))
    d = read_tail_digits([g for _, g in runs[-1]], 2)
    if not d:
        return None
    v = int(d)
    return v if 1 <= v <= 99 else None


def read_label(bars):
    """Everything the bar carries: {"hole": "027", "tray": 2, "depth": 5.90}.

    `bars` is one image or several slices of the same label at different
    heights. Where exactly to cut the strip is the weak link - too shallow and
    the lettering is clipped, too deep and depth markers in the core join in -
    so several cuts are tried.

    Two different answers from two cuts means the region finder is picking up
    different things, so the field is dropped. DISAGREEMENT IS A VETO: a field
    only survives if every cut that read it at all read it the same. That keeps
    the multi-try from turning into multi-guess.
    """
    if bars is None:
        return {"hole": None, "tray": None, "depth": None}
    if not isinstance(bars, (list, tuple)):
        bars = [bars]
    out = {"hole": None}
    for field, fn in (("tray", read_tray), ("depth", read_depth)):
        seen = []
        for b in bars:
            if b is None or b.size == 0:
                continue
            try:
                v = fn(b)
            except Exception:
                v = None
            if v is not None:
                seen.append(v)
        out[field] = seen[0] if (seen and len(set(seen)) == 1) else None
    return out


def read_depth(bar):
    """End depth in metres from a label-bar image, or None."""
    reg = depth_region(bar)
    if reg is None:
        return None
    runs = split_on_gap(characters(reg, keep_x=True))
    digits = read_digits([g for _, g in runs[-1]])
    if not digits or len(digits) < 3:
        return None
    try:
        v = float(digits[:-2] + "." + digits[-2:])
    except ValueError:
        return None
    return v if 0.30 <= v <= 400.0 else None
