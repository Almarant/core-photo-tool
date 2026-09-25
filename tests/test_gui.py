"""
Window-level checks, run under a virtual display.

Three defects reported from real use were all invisible to the pure-logic
suite because they live in tkinter's event and geometry plumbing:

* the first photo of a scan came up blank until you stepped past it and back;
* the wheel did nothing over the label table on step 3;
* tray 1 still needed its start depth typed in by hand.

Each one is a single assertion here. Needs a display: skipped without one.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="cpt_gui_")

SKIP = None
try:
    import tkinter as tk
    _probe = tk.Tk()
    _probe.destroy()
except Exception as exc:                                      # pragma: no cover
    SKIP = f"no display ({exc})"

if SKIP is None:
    from corephoto import app as appmod
    from corephoto import core


def _fake_scan(a, n=4):
    """Feed the window a scan result, built from a real fixture photo.

    _scan_done is the real method: it is what installs the data and draws the
    first preview, which is exactly where the blank-photo bug lived. The
    detection dicts come from core.detect on copies of a fixture, so they have
    every key the preview path reads - a hand-written dict silently skips the
    code under test the moment it is missing one.
    """
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "dry_tiles.jpg")
    hole = os.path.join(_TMP, "DD_ZOP_099")
    os.makedirs(hole, exist_ok=True)
    photos, det, coreL = [], {}, {}
    for i in range(n):
        dst = os.path.join(hole, f"IMG_{i:04d}.jpg")
        if not os.path.exists(dst):
            shutil.copyfile(src, dst)
        photos.append(dst)
        det[dst] = core.detect(dst, a.params)
        coreL[dst] = 100.0 + (i % 2) * 20.0
    a._scan_done(photos, det, coreL, {}, [], False)
    return photos


def _win():
    a = appmod.App()
    a.update()
    return a


def test_the_first_photo_is_drawn_as_soon_as_the_scan_finishes():
    """The blank preview.

    _scan_done runs on the main thread, but it is queued BEFORE the callback
    that clears _busy, so the preview it asked for was dropped by a guard that
    tested _busy. You saw an empty canvas until you pressed Next and Back.
    The guard now tests _det_lock, which _scan_done clears itself.
    """
    if SKIP:
        return
    a = _win()
    try:
        a._busy = True                        # exactly the state _scan_done runs in
        _fake_scan(a)
        assert a._img1 is not None, "no photo image was built"
        assert a.cv_orig.find_withtag("all"), "nothing was drawn on the preview canvas"
        assert a._pv is not None
    finally:
        a.destroy()


def test_the_preview_is_sized_from_a_mapped_canvas():
    """Drawn before the tab was laid out, the canvas reports a width of 1 and
    the photo is scaled to a fallback 560x400 - so the crop handles sit away
    from the box until the next redraw."""
    if SKIP:
        return
    a = _win()
    try:
        a._busy = True
        _fake_scan(a)
        assert a.cv_orig.winfo_width() > 100, (
            f"canvas still unmapped at draw time: {a.cv_orig.winfo_width()} px")
    finally:
        a.destroy()


def test_the_wheel_scrolls_the_label_table_from_every_widget_in_it():
    """Binding the canvas alone did nothing: the rows cover it, so the pointer
    is always over a child. The combobox is worse - ttk's own wheel binding
    CHANGES ITS VALUE, so a scroll over the Dry/Wet column edited the data."""
    if SKIP:
        return
    a = _win()
    try:
        _fake_scan(a)
        a._show_folder()
        a.update()
        rows = a.rowsf.winfo_children()
        assert rows, "no rows were built"

        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)

        combos = [w for w in walk(a.rowsf) if isinstance(w, appmod.ttk.Combobox)]
        assert combos, "no Dry/Wet combobox in the table"
        for w in walk(a.rowsf):
            assert w.bind("<MouseWheel>"), f"{w.winfo_class()} does not take the wheel"
        # and the handler must stop ttk's value-changing binding
        assert a._wheel() == "break"
    finally:
        a.destroy()


def test_tray_one_starts_at_zero_without_being_typed():
    if SKIP:
        return
    a = _win()
    try:
        _fake_scan(a)
        fs = a._cur()
        assert fs is not None
        assert min(int(r["tray"].get()) for r in fs.rows) == 1
        assert fs.first_start.get() == "0.00", (
            f"start depth left as {fs.first_start.get()!r}")

        # a hole that carries on from somewhere else must not be overwritten
        fs.first_start.set("48.60")
        a._seed_start(fs)
        assert fs.first_start.get() == "48.60"

        # nor may a hole whose first tray is not tray 1 be given 0.00
        fs.first_start.set("")
        for r in fs.rows:
            r["tray"].set(str(int(r["tray"].get()) + 4))
        a._seed_start(fs)
        assert fs.first_start.get() == "", (
            f"tray 5 was given a start depth of {fs.first_start.get()!r}")
    finally:
        a.destroy()


def main():
    if SKIP:
        print(f"  skipped: {SKIP}")
        return
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"all {len(tests)} window checks passed")


if __name__ == "__main__":
    main()
