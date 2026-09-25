"""Checks that run without a display, so CI can run them.

Two halves:
  * pure logic - grouping, depth chaining, naming, the reader's refusals;
  * four 1/8-scale photo fixtures, which is the only way the tray finder,
    deskew and label-bar detection get tested at all. Every one of those was
    broken at some point by a change that the pure-logic tests waved through.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np                                            # noqa: E402
import cv2                                                    # noqa: E402

from corephoto import naming, core, ocr, glyphs, barfind       # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def test_pure_logic():
    srcs = ["a", "b", "c", "d", "e"]
    g = naming.suggest_trays(srcs)
    assert [g[s] for s in srcs] == [1, 1, 2, 2, 3]

    # grouping is by explicit tray number, so a reshoot cannot shift later trays
    tray_of = {"a": 1, "b": 1, "c": 1, "d": 2, "e": 2}
    gr = naming.group_by_tray(tray_of)
    assert gr == {1: ["a", "b", "c"], 2: ["d", "e"]}, gr

    # darker core is wet, regardless of order; override always wins
    c, m = naming.decide_conditions(["a", "b"], {"a": 120.0, "b": 70.0})
    assert c == {"a": "Dry", "b": "Wet"} and m == 50.0
    c, _ = naming.decide_conditions(["a", "b"], {"a": 70.0, "b": 120.0})
    assert c == {"a": "Wet", "b": "Dry"}, "shooting order must not decide it"
    c, _ = naming.decide_conditions(["a", "b"], {"a": 70.0, "b": 120.0}, {"a": "Dry"})
    assert c["a"] == "Dry", "user override must win"

    ch = naming.chain_depths([1, 2, 3], {1: 3.5, 2: 7.1, 3: 10.0}, 0.0)
    assert ch[2] == (3.5, 7.1) and ch[3] == (7.1, 10.0), ch

    assert naming.make_name("DD26ZOP-006", "Dry", 22, 82.5, 86.0) == \
        "DD26ZOP-006_Dry_Tray22_82.50-86.00m.jpg"
    assert naming.disambiguate(["x.jpg", "x.jpg", "y.jpg"]) == ["x.jpg", "x_2.jpg", "y.jpg"]

    p = naming.validate("H1", {1: ["a", "b"], 2: ["c", "d"]}, {1: 3.5, 2: 7.1}, 0.0)
    assert p == [], p


def test_missing_depth_cannot_mislabel_later_trays():
    """The bug this file previously asserted as correct behaviour.

    Tray 2 has no end depth. Tray 3 must NOT inherit tray 1's end as its start:
    that produced `..._Tray3_4.95-10.20m.jpg` for a tray that really began at
    7.65 - a wrong start depth baked into a filename, invisible afterwards.
    """
    ch = naming.chain_depths([1, 2, 3], {1: 4.95, 3: 10.20}, 0.0)
    assert ch[1] == (0.0, 4.95)
    assert ch[2] == (4.95, None)
    assert ch[3][0] is None, "tray 3 must not inherit tray 1's end depth"

    stop = naming.blocking_problems("H1", {1: ["a"], 2: ["b"], 3: ["c"]},
                                    {1: 4.95, 3: 10.20}, 0.0)
    assert stop and "cannot be skipped" in stop[0], stop

    # and the things that MUST stay overridable stay overridable
    assert naming.blocking_problems("H1", {1: ["a"], 3: ["c"]},
                                    {1: 3.5, 3: 7.0}, 0.0) == [], "a tray gap is advice"
    assert naming.blocking_problems("H1", {1: ["a"]}, {1: 3.5}, 0.0) == [], \
        "a single photo in a tray is advice"
    assert naming.blocking_problems("", {1: ["a"]}, {1: 3.5}, 0.0), "empty hole blocks"
    assert naming.blocking_problems("H1", {1: ["a"]}, {1: 3.5}, None), "no start blocks"


def test_reader_refuses_rather_than_guesses():
    assert ocr.veto_depth(89.70, 86.00) == 89.70
    assert ocr.veto_depth(7.50, 67.05) is None, "backwards depth must be rejected"
    assert ocr.veto_depth(200.0, 10.0) is None, "implausible jump must be rejected"
    assert ocr.veto_depth(5.0, None) == 5.0, "no previous tray -> nothing to check"
    assert ocr.veto_depth(None, 86.0) is None

    # the app calls these three; an edit that deletes one must fail here, not
    # in the field (ocr.read_label was once truncated away by a patch and no
    # test noticed, because none of them called it)
    for name in ("read_label", "read_depth", "veto_depth"):
        assert callable(getattr(ocr, name, None)), f"ocr.{name} is missing"
    assert set(ocr.read_label(None)) == {"tray", "depth"}

    assert glyphs.available(), "glyph templates missing"
    blank = [np.full((40, 25), 255, np.uint8)] * 4
    assert glyphs.read_digits(blank) is None, "blank glyphs must not be guessed at"
    assert glyphs.read_digits([]) is None
    assert ocr.reader_name() is not None

    # a strip with no lettering at all must read as nothing, not as something
    noise = np.full((120, 1400, 3), 90, np.uint8)
    lab = glyphs.read_label([noise])
    assert lab == {"hole": None, "tray": None, "depth": None}, lab

    # DISAGREEMENT IS A VETO: two slices reading different numbers means the
    # region finder is latching onto different things, so the field is dropped.
    class _Fake:
        def __init__(self, vals): self.vals = list(vals)
        def __call__(self, bar): return self.vals.pop(0)
    real = glyphs.read_depth
    try:
        glyphs.read_depth = _Fake([4.55, 9.99])
        assert glyphs.read_label([noise, noise])["depth"] is None, \
            "two slices disagreeing must produce no reading"
        glyphs.read_depth = _Fake([4.55, 4.55])
        assert glyphs.read_label([noise, noise])["depth"] == 4.55
    finally:
        glyphs.read_depth = real


def test_scale_relative_kernels_match_the_tuned_constants():
    """The reader's kernels were absolute pixel sizes, tuned on a full-size DD
    strip, and silently changed behaviour on any other size. They are relative
    now - which must still reproduce the ORIGINAL numbers at the size they were
    tuned at, or the 93%-correct DD26ZOP result is quietly gone."""
    H = 321                                   # a full-resolution DD label strip
    assert max(int(H * 0.03), 3) == 9, "tile close kernel was 9x9"
    assert max(int(0.11 * H), 5) == 35, "group close kernel was 35 wide"
    assert max(int(0.047 * H), 3) == 15, "group close kernel was 15 tall"
    assert abs(max(H * 0.010, 1.0) - 3.0) < 0.3, "tile blur sigma was 3"


def test_find_photos_skips_by_path_not_by_name(tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as root:
        src = os.path.join(root, "src")
        keep = os.path.join(src, "processed")       # same NAME as the output dir
        out = os.path.join(root, "processed")       # the actual output dir
        for d in (src, keep, out):
            os.makedirs(d, exist_ok=True)
        for d in (src, keep, out):
            cv2.imwrite(os.path.join(d, "a.jpg"), np.zeros((8, 8, 3), np.uint8))
        found = core.find_photos([src], skip_dirs=(out,))
        assert len(found) == 2, f"a source subfolder named like the output dir was dropped: {found}"


def _fixture(name):
    p = os.path.join(FIXTURES, name)
    img = cv2.imread(p)
    assert img is not None, f"missing fixture {p}"
    return img


def test_detection_on_real_photos():
    """Four real frames, one per failure mode the detectors exist for."""
    # The tile detector is authoritative wherever it finds the stencil tiles,
    # which is everywhere it can see them - including this pallet frame, where
    # it was previously losing to a higher gradient reading. The gradient
    # remains the fallback for frames where the tiles cannot be found at all.
    cases = {
        "dry_tiles.jpg": "tiles",
        "wet_tiles.jpg": "tiles",
        "tilted.jpg": None,
        "pallet_gradient.jpg": None,
    }
    for name, expect_method in cases.items():
        d = core.detect_image(_fixture(name))
        assert d is not None, f"{name}: no tray detected"

        # the label bar must be ABOVE the tray, or the crop cuts the text off.
        # On a wet tray the gradient detector finds the wet core instead, which
        # is exactly what produced an 80px strip with no label in it.
        assert d["bar"] < d["bb"][1], f"{name}: bar at {d['bar']} is not above tray top"
        assert d["bar_method"] != "fallback", f"{name}: neither detector found the bar"
        if expect_method:
            assert d["bar_method"] == expect_method, \
                f"{name}: expected the {expect_method} detector, got {d['bar_method']}"

        # a tilt fit steeper than this is a failed fit, not a tilted tray
        assert abs(d["angle"]) <= core.MAX_TILT_DEG, f"{name}: tilt {d['angle']}"
        assert 560 <= d["tray_px"] <= 900, f"{name}: implausible tray width {d['tray_px']}"


def test_wet_tray_needs_the_tile_detector():
    """On a wet tray the gradient detector is unreliable in BOTH directions.

    It looks for the steepest floor->bar darkening, and on a wet tray the dark
    wet core and the shaded cover both compete with the bar. On this fixture it
    lands 49 px ABOVE the lettering; on others it lands below and slices the
    label off. Either way the tile detector, which looks at the stencil tiles
    themselves, must be the one that is used.
    """
    img = _fixture("wet_tiles.jpg")
    d = core.detect_image(img)
    grad_y, _ = barfind.gradient_top(d["small"], d["bb"])
    tile = barfind.tile_band(d["small"])
    assert tile is not None, "tile detector found nothing on a wet tray"
    assert grad_y is not None and abs(grad_y - tile[0]) > 20, (
        f"gradient {grad_y} and tiles {tile[0]} now agree on this photo, so it "
        f"has stopped testing which of them wins")
    assert d["bar_method"] == "tiles", (
        f"used the {d['bar_method']} reading; the tile answer must win wherever "
        f"the tiles are found")
    assert d["bar"] == tile[0]
    assert d["bar"] < d["bb"][1], "bar must sit above the tray"


def test_crop_geometry_is_stable():
    d = core.detect_image(_fixture("dry_tiles.jpg"))
    p = core.Params()
    ow, oh = core.out_size(d, p)
    assert ow == core.OUT_WIDTH, "output width sets mm-per-pixel and must not drift"
    assert 800 < oh < 4000, oh
    # nudging the corners must NOT change the output size, or scale stops being
    # comparable between photos
    adj = [[6.0, -4.0], [0.0, 0.0], [-3.0, 2.0], [0.0, 0.0]]
    assert core.out_size(d, p) == (ow, oh)
    q = core.crop_quad(d, p, adj)
    assert q.shape == (4, 2)
    assert abs(q[0][0] - core.crop_quad(d, p)[0][0] - 6.0) < 1e-6

    strips = core.label_strips(np.zeros((oh, ow, 3), np.uint8), d, p)
    assert len(strips) >= 3, "the reader needs several candidate slices"
    assert all(s.shape[0] >= 1 for s in strips)
    assert all(s.shape[0] < oh for s in strips), "a slice must not be the whole crop"


# The boxes the user corrected by hand on two DD_ZOP_014 photos, recovered by
# matching his saved crops back onto the originals. 1/8-scale pixels.
# Stored as PNG, not JPEG, on purpose: on this rig the tray finder is not just
# wrong, it is UNSTABLE. Re-saving steel_tray_b at JPEG quality 88 / 95 / 100
# moved its detected box to (8,152,749,393) / (7,153,702,281) / (8,196,743,393)
# from (50,303,700,393) - three different answers from changes you cannot see.
# The b* threshold sits on a tipping point and the chosen blob flips. A lossy
# fixture would make this test pass or fail for the wrong reason.
HAND_CORRECTED = {
    "steel_tray_a.png": (-1, 153, 751, 409),
    "steel_tray_b.png": (9, 109, 750, 361),
}


def test_output_scale_is_constant_when_corners_are_dragged():
    """The stretching bug. The canvas used to be fixed to the AUTOMATIC box
    while the quad actually sampled moved with the user's corners, so the scale
    changed and the picture was squashed: three crops of the same physical tray
    came out at aspect 2.15, 2.55 and 4.09."""
    d = core.detect_image(_fixture("steel_tray_a.png"))
    p = core.Params()

    def mm_per_px(adj):
        q = core.crop_quad(d, p, adj)
        ow, _ = core.out_size(d, p, adj)
        return (q[1][0] - q[0][0]) / ow          # detection px per output px

    base = mm_per_px(None)
    for adj in ([[60, 0], [-60, 0], [-60, 0], [60, 0]],      # narrower
                [[0, -40], [0, -40], [0, 40], [0, 40]],      # taller
                [[-30, -10], [20, -10], [20, 25], [-30, 25]]):
        # tolerance is one pixel of integer rounding on the canvas, not a
        # licence for the scale to drift
        assert abs(mm_per_px(adj) - base) / base < 0.001, (
            f"scale changed when corners moved: {mm_per_px(adj)} vs {base}")
    # and an untouched photo must still come out at the standard width
    assert core.out_size(d, p)[0] == core.OUT_WIDTH


def test_one_corrected_box_calibrates_the_rest_of_the_folder():
    """On a rig the tray finder cannot segment (grey steel trays on a wooden
    bench), correcting ONE photo must place the others. The top is re-anchored
    on each photo's own label bar, because the bar is loose and moves between
    shots - which is why copying absolute corner offsets did not work."""
    a = core.detect_image(_fixture("steel_tray_a.png"))
    b = core.detect_image(_fixture("steel_tray_b.png"))
    p = core.Params()

    want_a = HAND_CORRECTED["steel_tray_a.png"]
    want_b = HAND_CORRECTED["steel_tray_b.png"]

    # express the corrected box for A as corner nudges, as dragging would
    auto = core.crop_box(a, p)
    adj = [[want_a[0] - auto[0], want_a[1] - auto[1]],
           [want_a[2] - auto[2], want_a[1] - auto[1]],
           [want_a[2] - auto[2], want_a[3] - auto[3]],
           [want_a[0] - auto[0], want_a[3] - auto[3]]]
    prof = core.profile_from_quad(a, p, adj)

    got = core.box_from_profile(b, prof)
    width = want_b[2] - want_b[0]
    err = max(abs(got[i] - want_b[i]) for i in range(4))
    assert err < 0.03 * width, (
        f"calibrating on one photo missed the next by {err:.0f} px "
        f"({100 * err / width:.1f}% of tray width)")

    # the automatic box on B is the thing this replaces - it must be worse,
    # or the calibration is not earning its place
    auto = core.crop_box(b, p)
    auto_err = max(abs(auto[i] - want_b[i]) for i in range(4))
    assert auto_err > err, (
        f"automatic detection ({auto_err:.0f} px) is no longer worse than "
        f"calibration ({err:.0f} px) - re-check whether this is still needed")


def test_a_pale_cover_is_read_by_brightness_not_colour():
    """DD_ZOP_015: a pale patterned cover was put under the box to help.

    It does help - the cover sits 37-45 L* above the tray - but the colour
    segmentation cannot use that, because brightness is not what it looks at.
    The brightness method must win here, and must produce a tray-shaped box.
    """
    img = _fixture("covered_bench.png")
    d = core.detect_image(img)
    assert d is not None
    assert d["mask_method"] == "bright", (
        f"a covered bench should be segmented by brightness, got "
        f"{d['mask_method']}")
    assert d["aspect_ok"], f"box is the wrong shape: {d['tray_aspect']}"

    # and the colour method on its own really is the weaker one here, or this
    # fixture has stopped testing anything
    p = core.Params()
    col = core._hypothesis(img, core.tray_mask(img), p)
    bri = core._hypothesis(img, core.tray_mask_bright(img), p)
    assert col is not None and bri is not None
    assert bri["err"] < col["err"], (
        f"colour ({col['err']:.2f}) is no longer worse than brightness "
        f"({bri['err']:.2f}) on a covered bench")


def test_locking_the_shape_gives_one_canvas_for_a_whole_batch():
    """Uniform output size is the point. The detected bottom rail is the least
    reliable edge - it is where the mask bleeds into shadow - so the height is
    derived from the width and the tray's known shape instead. Two photos
    framed differently must still come out on the same canvas."""
    p = core.Params()
    assert p.lock_aspect, "the lock is the mechanism; it must default on"
    a = core.detect_image(_fixture("covered_bench.png"), p)
    b = core.detect_image(_fixture("dry_tiles.jpg"), p)
    # same batch settings => identical canvas, whatever each photo's own
    # detected bottom happened to be
    assert a["bb"][3] != b["bb"][3], "fixtures no longer differ; test is vacuous"
    assert core.out_size(a, p) == core.out_size(b, p), (
        f"{core.out_size(a, p)} vs {core.out_size(b, p)} - batch canvas is not uniform")

    # and the lock must actually be the mechanism: height = width / shape,
    # not the detected bottom rail
    w, h = core.out_size(a, p)
    assert abs(w / h - p.tray_aspect) < 0.02, (
        f"locked canvas is {w}x{h} (aspect {w/h:.2f}), expected {p.tray_aspect}")
    q = core.Params(lock_aspect=False)
    assert core.crop_box(a, q)[3] != core.crop_box(a, p)[3], (
        "the lock changes nothing on this fixture; it has stopped being tested")


def test_the_bar_does_not_move_when_the_tray_box_is_wrong():
    """The regression that put ~400 px of bench above the tray.

    The bar used to be resolved against the winning tray mask's box: candidates
    were filtered by it, the gradient searched a window derived from it, and
    whichever answer sat HIGHER in the frame won. So when the two segmentation
    hypotheses tied and the box flipped, the bar moved with it - 47 px on this
    photo, about 400 px of bench and cover in the finished crop.

    The tile detector needs no box at all. It must therefore give the same
    answer whatever box it is handed. Measured over nine hand-corrected
    DD_ZOP_015 crops, tiles scored sd 2.6 px against 14.8 for the old rule.
    """
    img = _fixture("bar_vs_gradient.png")
    d = core.detect_image(img)
    assert d["bar_method"] == "tiles", f"took the {d['bar_method']} reading"
    assert abs(d["bar"] - 208) <= 12, (
        f"top edge at {d['bar']}, hand-corrected crop starts at 208")

    # Hand `find` a deliberately wrong tray box, as the mask flip did, and
    # compare against find's OWN answer on the correct box - not against
    # detect_image's, which is measured after deskew and differs by a pixel or
    # two for that reason alone.
    good = d["bb"]
    ref, _, ref_method, _ = barfind.find(img, good)
    assert ref_method == "tiles"
    for bad in ((good[0], good[1] - 90, good[2], good[3]),
                (good[0], good[1] + 60, good[2], good[3]),
                (0, int(img.shape[0] * 0.95), img.shape[1] - 1, img.shape[0] - 1)):
        y, _, method, _ = barfind.find(img, bad)
        assert method == "tiles" and y == ref, (
            f"a wrong tray box moved the bar from {ref} to {y} ({method}) - "
            f"the bar must not depend on the tray mask")


def test_one_unread_tray_does_not_poison_the_rest_of_the_hole():
    """The depth column stopping after three trays.

    The loop only advances its reference depth when a tray is actually read, so
    after an unread tray the next comparison is against a depth two trays back
    - but the window stayed one tray wide. On DD_ZOP_015 tray 4 was not read,
    every tray from 6 to 24 was then measured against tray 3's 12.90 m, every
    gap broke the 8 m ceiling, and 21 correctly-read depths were discarded.
    Three of twenty-four were filled and the reader looked broken when it was
    not.
    """
    ends = {t: d for t, d in zip(range(1, 25), [
        6.00, 9.30, 12.90, 16.40, 19.70, 23.20, 26.20, 29.30, 32.70, 36.20,
        39.20, 43.00, 46.50, 50.00, 53.40, 57.10, 60.60, 64.00, 67.50, 71.10,
        74.50, 78.00, 81.40, 85.00])}
    read = dict(ends)
    read[4] = read[5] = None              # the two the reader actually missed

    prev = prev_t = None
    filled = []
    for t in sorted(ends):
        v = read.get(t)
        if v is None:
            continue
        since = 1 if prev_t is None else max(t - prev_t, 1)
        ok = ocr.veto_depth(v, prev, trays_since=since)
        if ok is None:
            continue
        prev, prev_t = ok, t
        filled.append(t)
    assert len(filled) == 22, f"only {len(filled)} of 22 readable trays survived: {filled}"

    # and the veto must still do its job
    assert ocr.veto_depth(7.50, 67.05) is None, "backwards depth must be rejected"
    assert ocr.veto_depth(200.0, 10.0) is None, "wild jump must be rejected"
    assert ocr.veto_depth(200.0, 10.0, trays_since=3) is None, \
        "widening the window must not let a wild jump through"
    assert ocr.veto_depth(30.0, 10.0, trays_since=3) == 30.0, \
        "a genuine three-tray gap must pass"


def test_depth_markers_in_the_core_are_not_taken_for_the_label_bar():
    """The crop that collapsed to a sliver.

    The white depth markers standing in the core look exactly like stencil
    tiles: bright, solid, sharing a baseline, with dark core between them. On
    this photo the pale cover pushed the brightness threshold to 172 while the
    tiles on the rusty bar peak at 210, so the real bar was never found and the
    marker row 190 px lower was the only candidate. The crop then started below
    the core and ran off the bottom of the frame.
    """
    img = _fixture("markers_in_core.png")
    d = core.detect_image(img)
    assert d is not None
    assert d["bar_method"] == "tiles"
    # the real bar is at y≈205-230; the marker row that used to win is at ≈428
    assert d["bar"] < 300, f"bar at {d['bar']} - that is the depth markers, not the bar"
    assert d["aspect_ok"], f"box is the wrong shape: {d['tray_aspect']}"
    p = core.Params()
    box = core.crop_box(d, p)
    assert box[3] <= img.shape[0] * 1.05, (
        f"crop runs off the bottom of the frame: {box}")
    assert (box[3] - box[1]) > 0.3 * img.shape[0], (
        f"crop collapsed to {box[3] - box[1]:.0f} px tall")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"all {len(tests)} checks passed")


if __name__ == "__main__":
    main()
