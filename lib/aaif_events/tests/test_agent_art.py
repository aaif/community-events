"""Tests for the generated plates and the stdlib image codecs behind them.

The plates themselves are checked the only way that matters here — that every
colour in one is a colour the design system defines. Rendering is not tested:
it shells out to Chrome, and a test that needs a browser is a test that gets
skipped.

The GIF encoder and PNG reader ARE tested, closely. They are hand-rolled
because `lib` is stdlib-only, they are the kind of code whose bugs look like
"the file is slightly wrong" rather than an exception, and nothing downstream
would notice: a mis-encoded plate lands in 83 chapter decks looking fine in the
thumbnail.
"""
import struct
import zlib

import pytest

from aaif_events import agent_art as aa


# ------------------------------------------------------------- the plates ----

@pytest.mark.parametrize("kind", aa.PLATES)
@pytest.mark.parametrize("aspect", sorted(aa.ASPECTS))
def test_every_plate_is_drawn_only_in_design_system_colours(kind, aspect):
    stray = aa.offbrand_colours(aa.plate(kind, aspect))
    assert not stray, "%s/%s uses %s" % (kind, aspect, stray)


@pytest.mark.parametrize("kind", aa.PLATES)
def test_every_plate_renders_at_both_aspects(kind):
    for aspect, (w, h) in aa.ASPECTS.items():
        svg = aa.plate(kind, aspect)
        assert 'width="%d"' % w in svg and 'height="%d"' % h in svg


def test_an_unknown_plate_is_refused():
    with pytest.raises(ValueError):
        aa.plate("no-such-plate", "wide")


def test_animated_plates_actually_differ_between_frames():
    """A plate in ANIMATED that ignores `frame` would encode as N identical
    frames — a file several times larger than the PNG, for no motion."""
    for kind in aa.ANIMATED:
        assert aa.plate(kind, "wide", 0.0) != aa.plate(kind, "wide", 0.5), kind


def test_static_plates_ignore_the_frame():
    for kind in aa.PLATES:
        if kind not in aa.ANIMATED:
            assert aa.plate(kind, "wide", 0.0) == aa.plate(kind, "wide", 0.7), kind


def test_the_hue_helper_wraps_so_a_secondary_is_always_valid():
    # Secondaries derive as (primary + 5), which runs past --spec-10.
    assert aa.hue(11) == aa.hue(1)
    assert aa.hue(15) == aa.hue(5)


# ---------------------------------------------------------- the PNG reader ---

def _make_png(w, h, rows, filt=0, alpha=False):
    """A real PNG, filtered with `filt`, to read back."""
    ch = 4 if alpha else 3
    raw = b""
    prev = bytearray(w * ch)
    for row in rows:
        line = bytearray()
        for i, b in enumerate(row):
            if filt == 0:
                line.append(b)
            elif filt == 1:
                line.append((b - (row[i - ch] if i >= ch else 0)) & 0xFF)
            elif filt == 2:
                line.append((b - prev[i]) & 0xFF)
        raw += bytes([filt]) + bytes(line)
        prev = bytearray(row)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6 if alpha else 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


@pytest.mark.parametrize("filt", [0, 1, 2])
def test_the_png_reader_undoes_every_filter_it_claims_to(tmp_path, filt):
    """Getting one filter case wrong shears the image from that row down —
    visible in the GIF, invisible in any exception."""
    rows = [bytearray([10, 20, 30, 40, 50, 60]), bytearray([70, 80, 90, 1, 2, 3])]
    p = tmp_path / "x.png"
    p.write_bytes(_make_png(2, 2, rows, filt))
    w, h, got = aa.read_png(str(p))
    assert (w, h) == (2, 2)
    assert [bytes(r) for r in got] == [bytes(r) for r in rows]


def test_the_png_reader_drops_the_alpha_channel(tmp_path):
    rows = [bytearray([10, 20, 30, 255, 40, 50, 60, 128])]
    p = tmp_path / "a.png"
    p.write_bytes(_make_png(2, 1, rows, 0, alpha=True))
    _w, _h, got = aa.read_png(str(p))
    assert bytes(got[0]) == bytes([10, 20, 30, 40, 50, 60])


def test_a_non_png_is_refused(tmp_path):
    p = tmp_path / "n.png"
    p.write_bytes(b"not a png at all")
    with pytest.raises(ValueError, match="not a PNG"):
        aa.read_png(str(p))


# --------------------------------------------------------- the GIF encoder ---

def _frames(n, w=4, h=2, colours=((1, 2, 3), (4, 5, 6))):
    """`n` frames of flat colour, the pattern shifting by one pixel each frame."""
    out = []
    for f in range(n):
        row = b"".join(bytes(colours[(x + f) % len(colours)]) for x in range(w))
        out.append((w, h, [bytearray(row) for _ in range(h)]))
    return out


def test_the_gif_has_the_header_loop_and_trailer_it_needs(tmp_path):
    p = str(tmp_path / "a.gif")
    aa.write_gif(_frames(3), p)
    data = open(p, "rb").read()
    assert data.startswith(b"GIF89a")
    # A GIF loops forever only via the NETSCAPE2.0 application extension.
    assert b"NETSCAPE2.0" in data
    assert data.endswith(b"\x3B")
    assert data.count(b"\x21\xF9\x04") == 3      # one graphic-control per frame


def test_the_gif_round_trips_through_a_decoder(tmp_path):
    """The encoder's LZW is the part most likely to be subtly wrong, and a wrong
    stream still produces a file. Decode it and compare pixels."""
    tk = pytest.importorskip("tkinter")
    frames = _frames(2)
    p = str(tmp_path / "b.gif")
    aa.write_gif(frames, p)
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("no display for tkinter's GIF decoder")
    try:
        img = tk.PhotoImage(file=p, format="gif -index 0")
        w, h, rows = frames[0]
        assert (img.width(), img.height()) == (w, h)
        for x in range(w):
            got = img.get(x, 0)
            got = tuple(int(v) for v in got.split()) if isinstance(got, str) else tuple(got)
            assert got == tuple(rows[0][x * 3:x * 3 + 3])
    finally:
        root.destroy()


def test_frames_of_different_sizes_are_refused(tmp_path):
    frames = _frames(2)
    frames[1] = (8, 2, frames[1][2])
    with pytest.raises(ValueError, match="differ in size"):
        aa.write_gif(frames, str(tmp_path / "c.gif"))


def test_a_gradient_is_refused_as_an_animation(tmp_path):
    """The guard that keeps a smooth plate from shipping as a banded GIF. A
    256-entry palette cannot hold a full-frame gradient, and the failure is
    visible banding rather than an error."""
    w, h = 64, 64
    rows = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            row += bytes((x * 4 % 256, y * 4 % 256, (x * y) % 256))
        rows.append(row)
    with pytest.raises(ValueError, match="gradient, not flat art"):
        aa.write_gif([(w, h, rows)], str(tmp_path / "d.gif"))


def test_flat_art_is_accepted(tmp_path):
    """The other side of that guard: the plates that DO animate must pass it."""
    chosen, index, drift = aa._quantise(_frames(4))
    assert drift == 0.0
    assert len(chosen) == 2


# ---------------------------------------------------------- chapter agents ----

def test_fnv1a_matches_the_published_vectors():
    """Unsigned 32-bit arithmetic. A signed shift gives a different scene per
    platform, so a chapter would not render the same art twice."""
    assert aa.fnv1a("") == 0x811C9DC5
    assert aa.fnv1a("a") == 0xE40C292C
    assert aa.fnv1a("foobar") == 0xBF9CF968


def test_a_chapters_scene_is_stable_and_in_range():
    for name in ("Boston", "Tokyo", "Lagos", "Madison, WI", "Montréal"):
        once, twice = aa.chapter_scene(name), aa.chapter_scene(name)
        assert once == twice
        spec, sec, action, ridge, mirrored = once
        assert 1 <= spec <= 5                  # a PRIMARY leads
        assert sec == spec + 5                 # the secondary derives from it
        assert action in aa.ACTIONS
        assert 0 <= ridge <= 3
        assert isinstance(mirrored, bool)


def test_neighbouring_chapters_do_not_all_get_the_same_scene():
    names = ["Boston", "Berlin", "Tokyo", "Lagos", "Paris", "Madrid",
             "Seattle", "Denver", "Austin", "Chicago"]
    scenes = {aa.chapter_scene(n) for n in names}
    assert len(scenes) >= 8, scenes


@pytest.mark.parametrize("action", aa.ACTIONS)
def test_every_action_draws_and_stays_on_palette(action):
    svg = aa.agent_scene(3, 8, action, ridge=1, size=384)
    assert aa.offbrand_colours(svg) == []
    assert "<svg" in svg and "</svg>" in svg


def test_the_agent_blinks_and_bobs_within_the_loop():
    frames = [aa.agent_scene(3, 8, "flag", size=384, frame=f / 8.0) for f in range(8)]
    assert len(set(frames)) > 1, "the agent never moves"
    # The blink replaces the eye circles with flattened ellipses for one frame.
    assert any("<ellipse cx=\"19.8\"" in f for f in
               [aa.agent_scene(3, 8, "flag", size=384, frame=f / 100.0)
                for f in range(100)])


def test_mirroring_changes_the_drawing():
    a = aa.agent_scene(2, 7, "carry", ridge=1, mirrored=False, size=384)
    b = aa.agent_scene(2, 7, "carry", ridge=1, mirrored=True, size=384)
    assert a != b and "scale(-1,1)" in b
