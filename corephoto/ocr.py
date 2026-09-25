"""
Label-bar reading. Pre-fill only - never trusted, never auto-accepted.

Two readers, tried in order:

1. `glyphs` - the built-in digit classifier. Trained on the project's own
   stencil tiles, needs no installation, ships inside the app.
2. Tesseract - only if it happens to be installed. A general OCR engine, and on
   this material a poor one.

Measured over the same 72 real DD26ZOP labels:

    Tesseract, best configuration ... 38% correct, 6% CONFIDENTLY WRONG
    built-in classifier ............. 93% correct,  7% blank, 0% wrong

The depth sequence is used to **veto** a reading, never to invent one. An
earlier version tried to repair a dropped digit from the sequence; on the single
bad read in the set it turned an obviously-wrong 7.5 into a plausible-looking
67.5, which is far harder for a human to spot. Rejecting is the safer failure.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import cv2

from . import glyphs

DIGITS = "0123456789."


# --------------------------------------------------------- Tesseract (opt.)
def _bundled_tesseract():
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for name in ("tesseract.exe", "tesseract"):
            p = os.path.join(base, name)
            if os.path.exists(p):
                return p
    return None


# The Windows installer often does not put Tesseract on PATH, so shutil.which()
# misses it even where it is installed.
_WIN_CANDIDATES = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR",
                 "tesseract.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Tesseract-OCR", "tesseract.exe"),
    os.path.join(os.environ.get("PROGRAMFILES", ""), "Tesseract-OCR", "tesseract.exe"),
)


def _registry_tesseract():
    if os.name != "nt":
        return None
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for key in (r"SOFTWARE\Tesseract-OCR", r"SOFTWARE\WOW6432Node\Tesseract-OCR"):
            for value in ("InstallDir", "Path", ""):
                try:
                    with winreg.OpenKey(root, key) as k:
                        d = winreg.QueryValueEx(k, value)[0]
                    p = os.path.join(d, "tesseract.exe")
                    if os.path.exists(p):
                        return p
                except OSError:
                    continue
    return None


def search_paths():
    out = ["the copy bundled in the app", "PATH"]
    if os.name == "nt":
        out += [p for p in _WIN_CANDIDATES if p]
        out.append("the Windows registry (SOFTWARE\\Tesseract-OCR)")
    return out


def tesseract_path():
    p = _bundled_tesseract()
    if p:
        return p
    try:
        from . import settings
        saved = settings.get("tesseract_path")
    except Exception:
        saved = None
    if saved and os.path.exists(saved):
        return saved
    p = shutil.which("tesseract")
    if p:
        return p
    if os.name == "nt":
        for c in _WIN_CANDIDATES:
            if c and os.path.exists(c):
                return c
        return _registry_tesseract()
    return None


def tesseract_available():
    return tesseract_path() is not None


def set_manual_path(path):
    from . import settings
    if not path or not os.path.exists(path):
        return False
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ok = r.returncode == 0 or "tesseract" in (r.stdout + r.stderr).lower()
    except Exception:
        return False
    if ok:
        settings.put("tesseract_path", path)
    return ok


def tesseract_version():
    p = tesseract_path()
    if not p:
        return None
    try:
        r = subprocess.run([p, "--version"], capture_output=True, text=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return (r.stdout or r.stderr).splitlines()[0].strip()
    except Exception:
        return None


def _tess_run(img, psm, whitelist):
    exe = tesseract_path()
    if not exe:
        return ""
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    env = dict(os.environ)
    base = getattr(sys, "_MEIPASS", None)
    if base and os.path.isdir(os.path.join(base, "tessdata")):
        env["TESSDATA_PREFIX"] = base
    try:
        cv2.imwrite(tmp.name, img)
        p = subprocess.run([exe, tmp.name, "stdout", "--psm", str(psm),
                            "-c", "tessedit_char_whitelist=" + whitelist],
                           capture_output=True, text=True, timeout=30, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.stdout
    except Exception:
        return ""
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _tess_depth(bar):
    crop = glyphs.depth_region(bar)
    if crop is None:
        return None
    crop = cv2.resize(crop, (crop.shape[1] * 3, crop.shape[0] * 3),
                      interpolation=cv2.INTER_CUBIC)
    crop = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    crop = cv2.copyMakeBorder(crop, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    for psm in (8, 7, 13, 6):
        s = "".join(re.findall(r"\d", _tess_run(crop, psm, DIGITS)))
        if len(s) >= 3:
            s = s[:5]
            try:
                return float(s[:-2] + "." + s[-2:])
            except ValueError:
                pass
    return None


# ------------------------------------------------------------------ public
def available():
    return glyphs.available() or tesseract_available()


def reader_name():
    if glyphs.available():
        return "built-in digit reader"
    if tesseract_available():
        return "Tesseract (fallback)"
    return None


def read_depth(bar):
    """End depth from a label bar, or None. Built-in reader first."""
    bars = bar if isinstance(bar, (list, tuple)) else [bar]
    v = glyphs.read_label(bars).get("depth") if glyphs.available() else None
    if v is None and tesseract_available():
        v = _tess_depth(bars[0])
    return v


def veto_depth(value, prev_end, min_interval=0.3, max_interval=8.0, trays_since=1):
    """Drop a reading that cannot be right given the previous tray's end depth.

    `trays_since` is how many trays back that previous depth came from. It
    matters more than it looks: the loop only advances `prev_end` when a tray
    is actually read, so after one unread tray the comparison is against a
    depth two trays back, and the window has to widen to match.

    Without it, ONE missed tray poisoned the whole hole. On DD_ZOP_015 tray 4
    was not read; every tray from 6 to 24 was then measured against tray 3's
    12.90 m, every gap exceeded the 8 m ceiling, and 21 correctly-read depths
    were thrown away. Three trays were filled out of twenty-four, and the
    reader looked broken when it was not.

    Only ever rejects. Inventing a correction produces plausible-looking wrong
    numbers, which is worse than an empty box.
    """
    if value is None or prev_end is None:
        return value
    n = max(int(trays_since), 1)
    gap = value - prev_end
    return value if (min_interval * n) <= gap <= (max_interval * n) else None


def read_label(bars):
    """Tray number and end depth from the label bar.

    `bars` may be several slices of the same bar; the reader drops any field
    the slices disagree on. Returns {"tray": int|None, "depth": float|None}.

    The HOLE number is deliberately not read. It can be segmented off the bar,
    but on the UG26ZOP set it came back confidently wrong on five photos of
    nineteen - reading the depth digits as a hole number when the region finder
    only located one group. The hole is typed once per folder, so that risk
    buys nothing.
    """
    if not glyphs.available():
        return {"tray": None, "depth": None}
    lab = glyphs.read_label(bars)
    return {"tray": lab.get("tray"), "depth": lab.get("depth")}
