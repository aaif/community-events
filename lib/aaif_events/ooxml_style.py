"""Conform a .pptx/.docx/.xlsx to the AAIF design system.

The OOXML sibling of `report_style.py`, and it exists for the same reason: the
brand should live in exactly one place. Every deck and tracker in the Drive
estate was hand-authored before the design system existed, so the brand is
scattered through them as literal font names and hex values — Space Grotesk,
Manrope, Calibri, a navy `1E2761`, a warm-grey ramp that is half a shade off
`--line-2`. This module is the seam that ends that: **skill scripts never write
a font name or a colour of their own**, they call `restyle_part` and add a rule
here when the vocabulary is missing.

Stdlib only, so it runs from a plugin install with nothing to install.

Two things are worth knowing before editing the maps below.

**Colour is role-aware, and it has to be.** The same hex means different things
in different slots, and a blind find-and-replace gets it wrong in a way that is
invisible until someone opens the file. In the event trackers, `1e2761` is a
table-header *fill* — which becomes the design system's black plate — and forty
characters later in the same run of XML it is a cell *border*, which becomes a
hairline. One source value, two destinations, chosen by where it sits.

**The rewrite is minimal-diff and byte-preserving.** Only the `val=` / `w:fill=`
attribute of a colour, and the `typeface=` of a font, are touched; every other
byte of every part is passed through untouched. That is deliberate and not
merely tidy: these files carry embedded fonts, `mc:AlternateContent` fallbacks,
and relationship ids that an XML round-trip through ElementTree would reorder,
re-prefix, or drop. `create_chapter.rebrand_part` takes the same approach for
the same reason. The tokenizer below tracks the element stack so it can resolve
a role without ever reconstructing the document.
"""
import hashlib
import os
import re
import zipfile

#: `design/aaif-tokens.css`, which is itself generated from the design system
#: bundle by `scripts/extract_design_tokens.py`. Parsing it — rather than
#: writing hexes here — is what makes this module follow the design system
#: instead of merely resembling it: a token whose value changes upstream
#: changes here on the next `extract_design_tokens.py` run, with no edit.
_TOKENS_CSS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))), "design", "aaif-tokens.css")

_TOKEN_RE = re.compile(r"--([a-z0-9-]+)\s*:\s*#([0-9A-Fa-f]{6})\b")


def _load_tokens(path=_TOKENS_CSS):
    """`{"ink": "0A0A0A", ...}` — the plain six-digit hex tokens only.

    Tokens whose value is a gradient, an alias (`var(--ink)`), an rgba() or a
    length are skipped: this module only ever needs to name a solid colour, and
    a partial parse that silently yielded `var(--ink)` as a hex would write
    literal garbage into a slide.
    """
    with open(path, encoding="utf-8") as fh:
        return {m.group(1): m.group(2).upper() for m in _TOKEN_RE.finditer(fh.read())}


TOKENS = _load_tokens()


def token(name):
    """The six-digit uppercase hex for an AAIF token name, e.g. `token("ink")`.

    Raises rather than defaulting. A missing token means the design system was
    replaced and this module's vocabulary no longer matches it — that must stop
    a run loudly, not quietly write a wrong colour into 900 files.
    """
    try:
        return TOKENS[name]
    except KeyError:
        raise KeyError(
            "no --%s in design/aaif-tokens.css — the design system changed; "
            "update ooxml_style's maps rather than working around this" % name)


# --------------------------------------------------------------------- type --
#: One family, per the design system: "There is no second display face."
SANS = "Instrument Sans"
#: A PPTX cannot express a font *stack*, so the system names JetBrains Mono for
#: the metadata runs that resolve to `--font-mono` on the web. It stays — but
#: only for metadata. See `_MONO_IS_PROSE` below.
MONO = "JetBrains Mono"

#: Every face found across the estate that is not one of the two above. Calibri
#: and Arial arrive as Office defaults (mostly on `endParaRPr`, i.e. invisible
#: trailing-paragraph state) rather than as a design decision; Space Grotesk and
#: Manrope were the previous display faces and are the visible drift.
FONT_MAP = {
    "Space Grotesk": SANS,
    "Manrope": SANS,
    "Calibri": SANS,
    "Arial": SANS,
    "Georgia": SANS,
    "Times New Roman": SANS,
    "Helvetica": SANS,
    "Helvetica Neue": SANS,
    "Cambria": SANS,          # Word's stock heading face, via word/theme
    "Cambria Math": SANS,
    # A spreadsheet's monospace column (ids, slugs) is metadata, which is what
    # the system reserves mono for — so it becomes the embeddable mono rather
    # than the sans.
    "Consolas": MONO,
    "Courier New": MONO,
}
# A `typeface` of "+mj-lt"/"+mn-lt" is a *reference* to the theme's major/minor
# font, not a face. It is absent from FONT_MAP on purpose — such a run is
# already correct once the theme is, and rewriting it to a literal would break
# the indirection the theme exists to provide.

#: Parts where JetBrains Mono is carrying body prose rather than metadata, and
#: so must become Instrument Sans. The design system is explicit: "Mono carries
#: metadata, not prose." The trackers set 205 body runs in mono; a deck's mono
#: runs are eyebrows, dates and cities, which are exactly what it is for.
_MONO_IS_PROSE = ("word/document.xml",)


# ------------------------------------------------------------------- colour --
# Role-aware. Keys are the off-system values found in the estate; values name
# AAIF tokens per role. A source colour absent from this table is left alone and
# reported by `audit()` — silence would let new drift through unnoticed.
#
# FILL   a shape fill, a table-cell shading, a slide background
# STROKE a line, an outline, a cell border
# TEXT   a run colour
_ROLE_MAP = {
    # --- the warm grey ramp the decks drift across -------------------------
    "C9C6BF": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "C9C7C0": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "B9B7B0": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "9A978F": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "D7D3C9": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "D8D6CF": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "DDDBD4": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "E8E6DF": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "EDEBE4": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},

    # --- a cool grey ramp that belongs to no AAIF surface at all -----------
    "B9BFCB": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "9AA1AE": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "8E94A1": {"fill": "line-2", "stroke": "line-2", "text": "ink-3"},
    "4A5568": {"fill": "line-2", "stroke": "line-2", "text": "ink-3"},
    "D5DAE3": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    # A dark plate and the hairline drawn on it.
    "1A2332": {"fill": "void-2", "stroke": "ink", "text": "ink"},
    "252525": {"fill": "void-3", "stroke": "line-inv", "text": "ink"},

    # --- the trackers' navy scheme ----------------------------------------
    # The header band becomes the design system's black plate; its 1px navy
    # borders become hairlines, because the system is flat and the band is
    # already the only weight the table needs.
    "1E2761": {"fill": "void-2", "stroke": "line", "text": "ink"},
    "DCE6FB": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "ECECEF": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "CADCFC": {"fill": "paper-3", "stroke": "line", "text": "info"},
    "DCEFE0": {"fill": "paper-3", "stroke": "line", "text": "success"},
    "FBF0D8": {"fill": "paper-3", "stroke": "line", "text": "warning"},
    "555555": {"fill": "line-2", "stroke": "line-2", "text": "ink-3"},
    "7A7F88": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "1B7A48": {"fill": "paper-3", "stroke": "success", "text": "success"},
    "9A6A14": {"fill": "paper-3", "stroke": "warning", "text": "warning"},

    # --- Excel's stock conditional-format styles --------------------------
    # "Bad", "Neutral" and "Good": a saturated text colour on a pale tint. The
    # tints all become --paper-3 and the meaning is carried by the text colour,
    # which is what the design system does with status.
    "9C0006": {"fill": "paper-3", "stroke": "danger", "text": "danger"},
    "FFC7CE": {"fill": "paper-3", "stroke": "line", "text": "danger"},
    "9C6500": {"fill": "paper-3", "stroke": "warning", "text": "warning"},
    "FFEB9C": {"fill": "paper-3", "stroke": "line", "text": "warning"},
    "C6EFCE": {"fill": "paper-3", "stroke": "line", "text": "success"},
    # The CRM's own banding, borders and header tints.
    "D9DBEC": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "EAF0FD": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "F2F2F4": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},

    # --- the you-are-here dot on the network slide ------------------------
    # A 6px status dot is exactly, and only, what the design system reserves
    # the spectrum for. NOTE: create_chapter.GREEN and backfill_map_dots key on
    # this fill to tell the dot shape from its label — the three must agree.
    "14964A": {"fill": "spec-3", "stroke": "spec-3", "text": "spec-3"},

    # --- chart furniture --------------------------------------------------
    "888888": {"fill": "line-2", "stroke": "line-2", "text": "ink-4"},
    "DDDAD2": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},

    # --- Word's own stock styles ------------------------------------------
    # Heading blues and the two hyperlink blues (Word's #0000EE and the Google
    # Docs export's #1155CC). The design system sets --link to --ink and keeps
    # the underline, so a link is ink here too — it is not a coloured link
    # system, and leaving these blue is the single most visible drift in a
    # tracker.
    "1F4D78": {"fill": "paper-3", "stroke": "line", "text": "ink"},
    "2E74B5": {"fill": "paper-3", "stroke": "line", "text": "ink"},
    "0000EE": {"fill": "paper-3", "stroke": "line", "text": "ink"},
    "1155CC": {"fill": "paper-3", "stroke": "line", "text": "ink"},
    "0000FF": {"fill": "paper-3", "stroke": "line", "text": "ink"},   # Excel's
    "C7D2EC": {"fill": "paper-3", "stroke": "line", "text": "ink-3"},
    "666666": {"fill": "line-2", "stroke": "line-2", "text": "ink-3"},
    # Near-blacks that ride in on pasted content rather than from the template:
    # Slack's own ink, GitHub's, and a plain 222222. All three are "black" by
    # intent and become the system's ink or its dark surface accordingly.
    "1D1C1D": {"fill": "void-2", "stroke": "line", "text": "ink-2"},
    "222222": {"fill": "void-3", "stroke": "line-inv", "text": "ink-2"},
    "1F2328": {"fill": "void-3", "stroke": "line-inv", "text": "ink-2"},

    # --- the warm off-white, retired as a surface -------------------------
    # --paper-2 is section banding in the design system, not a background, and
    # the brand's editorial surface is white. A slide or page drawn on F6F5F1
    # becomes white; used as a hairline or a run colour it reads as a tint and
    # goes to --paper-3 so it stays visible against the white it now sits on.
    "F6F5F1": {"fill": "paper", "stroke": "paper-3", "text": "paper-3"},
}

#: Stock Office theme palette. These reach the files as `clrScheme` defaults
#: nobody chose, and any shape left on a theme colour inherits them.
_THEME_CLRS = {
    "dk1": "ink", "lt1": "paper", "dk2": "void-2", "lt2": "paper-2",
    "accent1": "spec-1", "accent2": "spec-2", "accent3": "spec-3",
    "accent4": "spec-4", "accent5": "spec-5", "accent6": "spec-6",
    "hlink": "ink", "folHlink": "ink-3",
}


def _mapped(value, role):
    """The AAIF hex for `value` in `role`, or None to leave it untouched."""
    rule = _ROLE_MAP.get(value.upper())
    return token(rule[role]) if rule else None


# ---------------------------------------------------------------- scanning --
_TAG = re.compile(r"<(/?)([A-Za-z_][\w.-]*(?::[\w.-]+)?)((?:[^>\"']|\"[^\"]*\"|'[^']*')*?)(/?)>", re.S)

#: Ancestors that make a colour a run colour rather than a fill.
_PPTX_TEXT_CTX = ("a:rPr", "a:defRPr", "a:endParaRPr")


def _pptx_role(stack):
    """Resolve a `<a:srgbClr>`'s role from its ancestors.

    Order matters. A run's colour sits inside `a:rPr` *and* an `a:solidFill`, so
    the text test has to win; an outline's colour sits inside `a:ln` and an
    `a:solidFill` too. Everything else — shape fills, backgrounds, table cell
    fills, gradient stops — is a fill.
    """
    if any(t in _PPTX_TEXT_CTX for t in stack):
        return "text"
    if "a:ln" in stack:
        return "stroke"
    return "fill"


def _rewrite_attr(attrs, name, new):
    """Replace one attribute's value inside a raw attribute string, in place."""
    return re.sub(r'(\b%s\s*=\s*")[^"]*(")' % re.escape(name),
                  lambda m: m.group(1) + new + m.group(2), attrs, count=1)


def _scan(xml, handler):
    """Walk `xml`, calling `handler(tag, attrs, stack) -> attrs or None` for
    every element start, and splice back any rewritten attribute string.

    Returns the rewritten XML. Untouched elements are copied byte-for-byte, so
    a part with nothing to change comes out identical to the input and the
    caller can skip re-uploading it.
    """
    out, last, stack = [], 0, []
    for m in _TAG.finditer(xml):
        closing, tag, attrs, selfclose = m.group(1), m.group(2), m.group(3), m.group(4)
        if closing:
            if stack and stack[-1] == tag:
                stack.pop()
            continue
        new = handler(tag, attrs, stack)
        if new is not None and new != attrs:
            out.append(xml[last:m.start()])
            out.append("<%s%s%s>" % (tag, new, selfclose))
            last = m.end()
        if not selfclose:
            stack.append(tag)
    if not out:
        return xml
    out.append(xml[last:])
    return "".join(out)


def _attr(attrs, name):
    m = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(name), attrs)
    return m.group(1) if m else None


# ------------------------------------------------------------------ pptx ----
def _restyle_pptx(xml, part_name):
    # Any theme part, not just a deck's: the workbook theme carries the same
    # clrScheme and was missing the wholesale palette swap entirely.
    is_theme = bool(re.match(r"(ppt|xl|word)/theme/theme\d+\.xml$", part_name))

    def handler(tag, attrs, stack):
        if tag in ("a:latin", "a:ea", "a:cs"):
            face = _attr(attrs, "typeface")
            new = FONT_MAP.get(face)
            return _rewrite_attr(attrs, "typeface", new) if new else None
        if tag == "a:srgbClr":
            val = _attr(attrs, "val")
            if not val:
                return None
            # In a theme's clrScheme the slot name is the parent element, and
            # the whole stock Office palette is replaced wholesale rather than
            # by value — mapping 4472C4 by hex would also hit a shape that
            # happens to use it.
            if is_theme and "a:clrScheme" in stack and len(stack) >= 1:
                slot = stack[-1].split(":")[-1]
                if slot in _THEME_CLRS:
                    return _rewrite_attr(attrs, "val", token(_THEME_CLRS[slot]))
                return None
            new = _mapped(val, _pptx_role(stack))
            return _rewrite_attr(attrs, "val", new) if new else None
        return None

    return _scan(xml, handler)


# ------------------------------------------------------------------ docx ----
#: Word carries the colour on the attribute, not on a child element, so the role
#: is the attribute's own name plus its context.
_DOCX_BORDER_CTX = ("w:tcBorders", "w:pBdr", "w:tblBorders")


def _restyle_docx(xml, part_name):
    mono_to_sans = part_name in _MONO_IS_PROSE

    def face(f):
        if f == MONO:
            return SANS if mono_to_sans else None
        return FONT_MAP.get(f)

    def handler(tag, attrs, stack):
        if tag == "w:rFonts":
            new = attrs
            for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                cur = _attr(new, a)
                if cur:
                    repl = face(cur)
                    if repl:
                        new = _rewrite_attr(new, a, repl)
            return new
        if tag == "w:shd":
            val = _attr(attrs, "w:fill")
            if val and val.lower() != "auto":
                new = _mapped(val, "fill")
                if new:
                    # Word writes shading lowercase; keep the file's own casing
                    # so a re-run produces byte-identical output.
                    return _rewrite_attr(attrs, "w:fill", new.lower())
            return None
        if tag == "w:color":
            val = _attr(attrs, "w:val")
            if val and val.lower() != "auto":
                new = _mapped(val, "text")
                if new:
                    return _rewrite_attr(attrs, "w:val", new.lower())
            return None
        # Border elements (w:top/w:bottom/w:left/w:right/w:insideH/...) carry
        # their colour on a w:color attribute of their own.
        if any(c in stack for c in _DOCX_BORDER_CTX):
            val = _attr(attrs, "w:color")
            if val and val.lower() != "auto":
                new = _mapped(val, "stroke")
                if new:
                    return _rewrite_attr(attrs, "w:color", new.lower())
        return None

    return _scan(xml, handler)


def _restyle_word_theme(xml):
    def handler(tag, attrs, stack):
        if tag in ("a:latin", "a:ea", "a:cs"):
            new = FONT_MAP.get(_attr(attrs, "typeface"))
            return _rewrite_attr(attrs, "typeface", new) if new else None
        if tag == "a:srgbClr" and "a:clrScheme" in stack and stack:
            slot = stack[-1].split(":")[-1]
            if slot in _THEME_CLRS:
                return _rewrite_attr(attrs, "val", token(_THEME_CLRS[slot]))
        return None

    return _scan(xml, handler)


# ------------------------------------------------------------------ xlsx ----
# A workbook is NOT DrawingML. `xl/styles.xml` is SpreadsheetML: fonts are
# `<font><name val="Calibri"/><color rgb="FF1E2761"/></font>`, fills are
# `<patternFill><fgColor rgb="..."/></patternFill>`, borders carry their own
# `<color>`. Handing it to the pptx pass — which looks for `a:latin` and
# `a:srgbClr` — silently changes nothing, and `audit()` then reports a workbook
# full of Calibri and navy as clean. Every CRM in the estate was in that state.
#
# Colours here are **ARGB**: eight hex digits, alpha first. The alpha pair is
# preserved and only the RGB half is remapped.
_XLSX_TEXT_CTX = ("font",)
_XLSX_FILL_CTX = ("patternFill",)
_XLSX_STROKE_CTX = ("border",)


def _argb(value):
    """(alpha_prefix, rrggbb) for an ARGB or RGB attribute value."""
    v = value.strip()
    if len(v) == 8:
        return v[:2], v[2:]
    if len(v) == 6:
        return "", v
    return None, None


def _restyle_xlsx(xml):
    def role(stack):
        if any(t in _XLSX_TEXT_CTX for t in stack):
            return "text"
        if any(t in _XLSX_FILL_CTX for t in stack):
            return "fill"
        if any(t in _XLSX_STROKE_CTX for t in stack):
            return "stroke"
        return None

    def handler(tag, attrs, stack):
        if tag == "name" and "font" in stack:
            face = _attr(attrs, "val")
            new = FONT_MAP.get(face)
            return _rewrite_attr(attrs, "val", new) if new else None
        if tag in ("color", "fgColor", "bgColor"):
            raw = _attr(attrs, "rgb")
            if not raw:
                return None            # theme= or indexed=, left to the theme
            alpha, rgb = _argb(raw)
            if not rgb:
                return None
            r = role(stack)
            if r is None:
                return None
            new = _mapped(rgb, r)
            return _rewrite_attr(attrs, "rgb", alpha + new) if new else None
        return None

    return _scan(xml, handler)


# ------------------------------------------------------------------- api ----
_PPTX_PARTS = re.compile(
    r"ppt/(slides|slideLayouts|slideMasters|notesSlides|notesMasters"
    r"|handoutMasters|charts|diagrams)/[^/]+\.xml$")
#: Deck-level parts that carry type but sit outside any of the folders above.
#: `presentation.xml` holds the default text style and `tableStyles.xml` the
#: table defaults — both name Arial, and both are inherited by anything a
#: designer has not overridden, so missing them leaves the drift live.
_PPTX_SINGLETONS = ("ppt/presentation.xml", "ppt/tableStyles.xml")


def restyle_part(part_name, data):
    """Return conformant bytes for one OOXML part, or `data` unchanged.

    Same contract as `create_chapter.rebrand_part`: parts this does not
    understand are returned byte-for-byte, so the caller can drive it straight
    through `_rewrite_zip` and use "did the bytes change" as the upload test.
    """
    try:
        xml = data.decode("utf-8")
    except UnicodeDecodeError:
        return data          # media, embedded fonts, anything not XML

    if (_PPTX_PARTS.match(part_name) or part_name in _PPTX_SINGLETONS
            or re.match(r"ppt/theme/theme\d+\.xml$", part_name)):
        out = _restyle_pptx(xml, part_name)
    elif part_name in ("word/document.xml", "word/styles.xml",
                       "word/header1.xml", "word/footer1.xml"):
        out = _restyle_docx(xml, part_name)
    elif re.match(r"word/(header|footer)\d+\.xml$", part_name):
        out = _restyle_docx(xml, part_name)
    elif re.match(r"word/theme/theme\d+\.xml$", part_name):
        out = _restyle_word_theme(xml)
    elif part_name == "xl/styles.xml":
        out = _restyle_xlsx(xml)
    elif re.match(r"xl/theme/theme\d+\.xml$", part_name):
        # The workbook THEME is DrawingML like any other, so its clrScheme and
        # fontScheme go through the same slot-wise swap the deck themes get.
        out = _restyle_pptx(xml, part_name)
    else:
        return data
    return out.encode("utf-8") if out != xml else data


# ----------------------------------------------------------------- audit ----
def _is_restyled_part(name):
    """Whether `restyle_part` would actually rewrite this part.

    The audit is scoped to exactly that set. Reporting drift in a part the
    sweep never touches — `word/numbering.xml`, `xl/worksheets/`, footnotes —
    gives the operator a REMAINS line they cannot act on and makes `--check`
    exit 1 forever. If one of those parts genuinely needs restyling, the answer
    is to handle it in `restyle_part`, which puts it back in the audit here.
    """
    return (_PPTX_PARTS.match(name) is not None
            or name in _PPTX_SINGLETONS
            or name == "xl/styles.xml"
            or re.match(r"(ppt|xl|word)/theme/theme\d+\.xml$", name) is not None
            or name in ("word/document.xml", "word/styles.xml")
            or re.match(r"word/(header|footer)\d+\.xml$", name) is not None)


def audit(path):
    """Every font and colour still off-system, as `(part, kind, value)`.

    This is the `--check` mode and the CI assertion. It reports what is left
    rather than what was changed, so a value nobody has written a rule for
    shows up as drift instead of passing silently.
    """
    hits = []
    known = set(TOKENS.values())
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if not _is_restyled_part(n):
                continue
            try:
                xml = z.read(n).decode("utf-8")
            except UnicodeDecodeError:
                continue
            # A theme's clrScheme is allowed to name any colour — it IS the
            # palette — so only its fonts are audited.
            in_theme = "/theme/" in n

            def note(tag, attrs, stack):
                if tag in ("a:latin", "a:ea", "a:cs"):
                    f = _attr(attrs, "typeface")
                    # "+mj-lt"/"+mn-lt" are theme references, not faces.
                    if f and f not in (SANS, MONO) and not f.startswith("+"):
                        hits.append((n, "font", f))
                elif tag == "w:rFonts":
                    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                        f = _attr(attrs, a)
                        if f and f not in (SANS, MONO):
                            hits.append((n, "font", f))
                # Colours are read from the elements that actually carry one.
                # Scanning every `val=` instead would report <a:alpha
                # val="100000"/> and <a:lumMod val="110000"/> as colours: both
                # are six characters and both are valid hex.
                elif tag == "a:srgbClr" and not (in_theme and "a:clrScheme" in stack):
                    v = _attr(attrs, "val")
                    if v and len(v) == 6:
                        hits.append((n, "colour", v.upper()))
                elif tag == "name" and "font" in stack:
                    f = _attr(attrs, "val")
                    if f and f not in (SANS, MONO):
                        hits.append((n, "font", f))
                elif tag in ("color", "fgColor", "bgColor"):
                    raw = _attr(attrs, "rgb")
                    _a, rgb = _argb(raw) if raw else (None, None)
                    if rgb:
                        hits.append((n, "colour", rgb.upper()))
                elif tag == "w:shd":
                    v = _attr(attrs, "w:fill")
                    if v and len(v) == 6:
                        hits.append((n, "colour", v.upper()))
                elif tag == "w:color" or any(c in stack for c in _DOCX_BORDER_CTX):
                    v = _attr(attrs, "w:val") if tag == "w:color" else _attr(attrs, "w:color")
                    if v and len(v) == 6:
                        hits.append((n, "colour", v.upper()))
                return None

            before = len(hits)
            _scan(xml, note)
            # Keep only what is genuinely off-system.
            hits[before:] = [h for h in hits[before:]
                             if h[1] == "font" or h[2] in _ROLE_MAP or h[2] not in known]
    return sorted(set(hits))


# ---------------------------------------------------- adding background slides --
#: Content-type defaults a plate may need. PNG is already declared by every deck
#: in the estate; GIF is not, and a part with no declared content type makes
#: PowerPoint report the file as corrupt rather than skipping the image.
_MEDIA_CT = {".png": "image/png", ".gif": "image/gif", ".jpg": "image/jpeg"}

_SLIDE_CT = ("application/vnd.openxmlformats-officedocument."
             "presentationml.slide+xml")
_IMAGE_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
              "relationships/image")
_SLIDE_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
              "relationships/slide")
_NOTES_REL = ("http://schemas.openxmlformats.org/officeDocument/2006/"
              "relationships/notesSlide")


def _full_bleed_embed(slide_xml, slide_w, slide_h):
    """The `r:embed` id of whatever provides this slide's full-bleed image.

    Two shapes have to be handled, and the estate uses the first one:

    * `<p:bg>` with a `blipFill` — the image is the slide BACKGROUND. This is
      what the hero decks do, and it is why looking only at `<p:pic>` finds
      nothing on a slide that plainly has a background image.
    * a `<p:pic>` sized to the slide — a picture used as a background.

    A picture is identified by geometry rather than by name or order: the decks
    name their images from a long-gone export ("Google Shape;62;p3",
    `descr="assets/logo.png"`), so filling the slide is the only trustworthy
    signal. Returns None when neither is present, and the caller then knows this
    slide has no plate to swap and must not be cloned.
    """
    bg = re.search(r"<p:bg>.*?</p:bg>", slide_xml, re.S)
    if bg:
        embed = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', bg.group(0))
        if embed:
            return embed.group(1)
    best = None
    for m in re.finditer(r"<p:pic>.*?</p:pic>", slide_xml, re.S):
        block = m.group(0)
        embed = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', block)
        ext = re.search(r'<a:ext cx="(\d+)" cy="(\d+)"', block)
        if not (embed and ext):
            continue
        if int(ext.group(1)) >= slide_w * 0.98 and int(ext.group(2)) >= slide_h * 0.98:
            best = embed.group(1)
    return best


#: The light-surface ink ramp mapped onto the design system's inverse ramp, for
#: text that is about to sit on a plate. The black-plate slide these are cloned
#: from sets several runs in the LIGHT ramp — its eyebrow, its subtitle, and the
#: wordmark of the host lockup are all near-black on near-black, which reads as
#: "absent" on flat black and as "muddy" the moment a gradient goes behind it.
#: Only text is remapped; the hairline rules are already light and correct.
_ON_DARK = {"ink": "ink-inv", "ink-2": "ink-inv", "ink-3": "ink-inv-2",
            "ink-4": "ink-inv-3", "void": "ink-inv", "void-2": "ink-inv",
            "void-3": "ink-inv-2"}


def to_on_dark(xml):
    """Rewrite a slide's TEXT colours from the light ramp to the inverse ramp."""
    ramp = {token(k): token(v) for k, v in _ON_DARK.items()}

    def handler(tag, attrs, stack):
        if tag != "a:srgbClr" or not any(t in _PPTX_TEXT_CTX for t in stack):
            return None
        val = (_attr(attrs, "val") or "").upper()
        return _rewrite_attr(attrs, "val", ramp[val]) if val in ramp else None

    return _scan(xml, handler)


#: Stamped into each generated slide's <p:cSld name="...">. It is what makes a
#: re-run a no-op: without a marker the only way to ask "has this deck already
#: got its plates?" is to compare image bytes, and a sweep that guessed wrong
#: would append six more slides to every deck in the estate on every run.
PLATE_MARK = "AAIF plate \u00b7 %s"


def plate_labels(path):
    """The plate labels already present in a deck."""
    out = set()
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if re.match(r"ppt/slides/slide\d+\.xml$", n):
                m = re.search(r'<p:cSld[^>]*\bname="AAIF plate \u00b7 ([^"]*)"',
                              z.read(n).decode("utf-8", "replace"))
                if m:
                    out.add(m.group(1))
    return out


_BLIP_BG = ('<p:bg><p:bgPr><a:blipFill><a:blip r:embed="%s"><a:alphaModFix/></a:blip>'
            '<a:stretch><a:fillRect/></a:stretch></a:blipFill></p:bgPr></p:bg>')


def add_plate_slides(path, plates, src_index=2):
    """Append one slide per plate, each a copy of slide `src_index` with its
    full-bleed background swapped for that plate's image.

    `plates` is `[(label, image_path), ...]`. Returns the labels added.

    Cloning the slide rather than authoring one keeps every piece of the layout
    the deck already has — the header rule, the type stack and its exact
    positions, the footer lockup — so an organizer picking a different
    background gets the same, already-approved composition underneath it.

    `src_index` defaults to the **black-plate** slide, not the image-backed one,
    because its layout and its logos suit a dark ground. Its TYPE does not:
    several runs — the eyebrow, the subtitle, the wordmark of the host lockup —
    are still in the light ink ramp, near-black on near-black. That is why the
    clone is put through `to_on_dark` below rather than copied as-is. The
    image-backed slide is a worse start regardless: it was coloured for one
    specific picture.

    Handles both background shapes — an existing `blipFill` has its target
    swapped, a `solidFill` is replaced by a `blipFill` with a new relationship.
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        parts = {n: z.read(n) for n in names}

    presentation_xml = parts["ppt/presentation.xml"].decode("utf-8")
    # Attribute ORDER is not fixed in OOXML — these decks write cy before cx —
    # so each is read by name rather than by position in one combined pattern.
    sz = re.search(r"<p:sldSz[^>]*/?>", presentation_xml)
    if not sz:
        raise RuntimeError("%s has no <p:sldSz> — cannot tell what full-bleed is" % path)
    dims = dict(re.findall(r'\b(cx|cy)="(\d+)"', sz.group(0)))
    if "cx" not in dims or "cy" not in dims:
        raise RuntimeError("%s has a <p:sldSz> with no cx/cy" % path)
    slide_w, slide_h = int(dims["cx"]), int(dims["cy"])

    src = "ppt/slides/slide%d.xml" % src_index
    src_rels = "ppt/slides/_rels/slide%d.xml.rels" % src_index
    if src not in parts:
        raise RuntimeError("%s has no %s" % (path, src))
    src_xml = parts[src].decode("utf-8")
    if not re.search(r"<p:bg>.*?</p:bg>", src_xml, re.S):
        raise RuntimeError("slide %d of %s has no <p:bg> to put a plate in"
                           % (src_index, path))
    # `embed` is None when the source's background is a SOLID FILL rather than
    # an image — which is the normal case for the black-plate slide, and the
    # slide worth cloning: its type and its logos are already the on-dark
    # variants, where the existing image-backed slide mixes on-light and
    # on-dark runs and leaves half of them unreadable on a new plate.
    embed = _full_bleed_embed(src_xml, slide_w, slide_h)

    rels_xml = parts[src_rels].decode("utf-8")
    ct = parts["[Content_Types].xml"].decode("utf-8")
    presentation_rels = parts["ppt/_rels/presentation.xml.rels"].decode("utf-8")

    next_slide = 1 + max(int(m.group(1)) for m in
                         (re.match(r"ppt/slides/slide(\d+)\.xml$", n) for n in names) if m)
    next_media = 1 + max([int(m.group(1)) for m in
                          (re.match(r"ppt/media/image(\d+)\.", n) for n in names) if m] or [0])
    next_rid = 1 + max(int(m) for m in re.findall(r'Id="rId(\d+)"', presentation_rels))
    next_sldid = 1 + max(int(m) for m in re.findall(r'<p:sldId id="(\d+)"', presentation_xml))

    have = plate_labels(path)
    plates = [(lab, img) for lab, img in plates if lab not in have]
    if not plates:
        return []                      # already plated; a re-run changes nothing

    added = []
    for label, img in plates:
        ext = os.path.splitext(img)[1].lower()
        if ext not in _MEDIA_CT:
            raise ValueError("unsupported plate type %s" % ext)
        media = "ppt/media/image%d%s" % (next_media, ext)
        with open(img, "rb") as fh:
            parts[media] = fh.read()
        if ('Extension="%s"' % ext[1:]) not in ct:
            ct = ct.replace("<Types ", '<Types ', 1).replace(
                "</Types>", '<Default ContentType="%s" Extension="%s"/></Types>'
                % (_MEDIA_CT[ext], ext[1:]))

        slide = "ppt/slides/slide%d.xml" % next_slide
        # Rebuild the rels: keep the layout and every non-background image, point
        # the background at the new plate, drop the notes rel (a notes part
        # cannot be shared between two slides, and a dangling one is a corrupt
        # file; a title card has no speaker notes anyway).
        new_rels, slide_xml = [], src_xml
        for rm in re.finditer(r"<Relationship\b[^>]*/>", rels_xml):
            r = rm.group(0)
            if 'Type="%s"' % _NOTES_REL in r:
                continue
            if embed and 'Id="%s"' % embed in r and 'Type="%s"' % _IMAGE_REL in r:
                r = re.sub(r'Target="[^"]*"', 'Target="../media/image%d%s"'
                           % (next_media, ext), r)
            new_rels.append(r)
        if not embed:
            # Solid-fill background: add a relationship of our own and turn the
            # <p:bg> into a blipFill pointing at it.
            used = [int(n) for n in re.findall(r'Id="rId(\d+)"', rels_xml)]
            local = "rId%d" % (max(used) + 1 if used else 1)
            new_rels.append('<Relationship Id="%s" Type="%s" '
                            'Target="../media/image%d%s"/>'
                            % (local, _IMAGE_REL, next_media, ext))
            slide_xml = re.sub(r"<p:bg>.*?</p:bg>", _BLIP_BG % local, src_xml,
                               count=1, flags=re.S)
        slide_xml = to_on_dark(slide_xml)
        # Stamp the marker. <p:cSld> may or may not already carry a name.
        mark = PLATE_MARK % label
        if re.search(r"<p:cSld\b[^>]*\bname=", slide_xml):
            slide_xml = re.sub(r'(<p:cSld\b[^>]*\bname=")[^"]*(")',
                               lambda m: m.group(1) + mark + m.group(2),
                               slide_xml, count=1)
        else:
            slide_xml = slide_xml.replace("<p:cSld>", '<p:cSld name="%s">' % mark, 1)
        parts[slide] = slide_xml.encode("utf-8")
        parts["ppt/slides/_rels/slide%d.xml.rels" % next_slide] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">%s</Relationships>' % "".join(new_rels)).encode("utf-8")

        ct = ct.replace("</Types>", '<Override ContentType="%s" PartName="/%s"/></Types>'
                        % (_SLIDE_CT, slide))
        presentation_rels = presentation_rels.replace(
            "</Relationships>",
            '<Relationship Id="rId%d" Type="%s" Target="slides/slide%d.xml"/>'
            "</Relationships>" % (next_rid, _SLIDE_REL, next_slide))
        presentation_xml = presentation_xml.replace("</p:sldIdLst>", '<p:sldId id="%d" r:id="rId%d"/></p:sldIdLst>'
                            % (next_sldid, next_rid))

        added.append(label)
        next_slide += 1
        next_media += 1
        next_rid += 1
        next_sldid += 1

    parts["[Content_Types].xml"] = ct.encode("utf-8")
    parts["ppt/presentation.xml"] = presentation_xml.encode("utf-8")
    parts["ppt/_rels/presentation.xml.rels"] = presentation_rels.encode("utf-8")

    tmp = path + ".new"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
            for n, data in parts.items():
                zo.writestr(n, data)
        with zipfile.ZipFile(tmp) as zt:
            if zt.testzip() is not None:
                raise RuntimeError("repackaged deck failed validation: " + path)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return added


# --------------------------------------------------- measured contrast repair --
def improve_contrast(path):
    """Move unreadable text onto the inverse ramp, but only where that helps.

    The failures in this estate are one shape: a slide drawn on a dark ground —
    the black plate, or a background image whose mean is dark — carrying runs
    still set in the LIGHT ink ramp. `to_on_dark` is exactly the remap those
    runs need, and it is already written; what was missing is deciding *which*
    slides get it.

    That decision is made by measurement, not by slide index. For each slide
    this re-scores the whole slide with the remap applied and keeps it only if
    **at least one run is materially rescued and none crosses from passing to
    failing, or from readable into the invisible band** — so a slide with light
    text on a light ground, or a plate with a bright region under some run, is
    left alone instead of being whitened into a new bug. Note that the AA
    failure COUNT need not drop: a rescue from 1.19 to 4.48 against a 4.50
    threshold is real and leaves that count unchanged.

    Returns `(rescued, before, after)`, where `before`/`after` count runs
    failing AA and `rescued` counts runs **materially improved** — an AA
    crossing, or an escape from the invisible band to at least `AA_LARGE`.

    Those are two different numbers on purpose. A slide can be genuinely
    repaired without any AA crossing: lifting footer text from 1.19 to 4.48
    against a 4.50 threshold leaves the AA count unchanged while making text
    readable that nobody could see before. Reporting only AA crossings would
    call that "no change" — and the caller uses `rescued` to decide whether to
    upload, so it would then quietly discard the repair it just made.
    """
    from aaif_events import contrast as ct

    before = [f for f in ct.check_pptx(path)
              if f.ratio is not None and f.ratio < f.threshold]
    if not before:
        return 0, 0, 0

    failing_parts = {f.part for f in before}

    def score(p):
        """{part: [ratio, ...]} in document order.

        Positional, NOT keyed by the run's text. Keying by text collapses the
        two runs that both read "Agentic AI" — the header lockup, which is fine,
        and the footer lockup, which is the invisible one — into a single entry,
        and the comparison then silently comes out wrong for exactly the slide
        that needs fixing.
        """
        out = {}
        for f in ct.check_pptx(p, include_passes=True):
            out.setdefault(f.part, []).append((f.ratio, f.threshold))
        return out

    base = score(path)
    keep = set()
    for part in sorted(failing_parts):
        # Try the remap on this part alone.
        trial = path + ".trial"
        _rewrite_zip_to(path, trial,
                        lambda n, d, _p=part: (to_on_dark(d.decode("utf-8")).encode("utf-8")
                                               if n == _p else d))
        try:
            after = score(trial)
            b, a = base.get(part, []), after.get(part, [])
            if len(b) != len(a):
                continue                       # runs moved; do not guess
            # A slide is kept when nothing crosses DOWN and something is
            # genuinely rescued. "Rescued" is deliberately two cases, because
            # the threshold alone is too blunt in both directions:
            #
            #   * Requiring that no ratio drop at all rejects the repair over
            #     noise — remapping --ink-4 to --ink-inv-3 on a black plate
            #     moves a run from 5.89 to 5.71, both far above AA, while the
            #     same remap takes the invisible footer wordmark to 19.80.
            #
            #   * Requiring an AA crossing rejects rescues that stop just short
            #     of it. On the old plate the remap lifts text from 1.19 to
            #     4.48 against a threshold of 4.50 — unreadable to plainly
            #     readable, missed by two hundredths — and an earlier version of
            #     this rule left that text at 1.19 because of it.
            #
            # So escaping the invisible band counts as a rescue in its own
            # right. Text nobody can see is a different problem from text that
            # is merely under AA, and it is worth fixing on its own terms.
            # The invisible band is first-class in BOTH directions. Escaping it
            # counts as a rescue, so falling into it has to count as a break —
            # an earlier version tested `broke` only on an AA crossing, which
            # meant a run already below its threshold could not be counted as
            # broken however far it fell. A label at 4.40 (failing, but plainly
            # legible) dropping to 1.10 (white on a bright disc, invisible)
            # scored as no harm done, and the slide was kept and uploaded.
            broke = fixed = 0
            for (x, tx), (y, ty) in zip(b, a):
                if x is None or y is None:
                    continue
                if x >= ct.INVISIBLE > y:
                    broke += 1
                elif x >= tx and y < ty:
                    broke += 1
                elif x < tx <= y:
                    fixed += 1
                elif x < ct.INVISIBLE and y >= ct.AA_LARGE:
                    fixed += 1
            if fixed and not broke:
                keep.add(part)
        finally:
            if os.path.exists(trial):
                os.remove(trial)

    if not keep:
        return 0, len(before), len(before)

    _rewrite_zip_to(path, path + ".new",
                    lambda n, d: (to_on_dark(d.decode("utf-8")).encode("utf-8")
                                  if n in keep else d))
    os.replace(path + ".new", path)

    # Count RUNS, positionally, against the same before-scores the decision
    # used. Counting events instead — AA crossings plus invisible escapes —
    # double-counts the run that does both, which is the common case: a
    # wordmark going from 1.00 to 19.80 is one run rescued, not two.
    final = score(path)
    rescued = 0
    for part, rows in base.items():
        for (x, tx), (y, _ty) in zip(rows, final.get(part, [])):
            if x is None or y is None:
                continue
            if (x < tx <= y) or (x < ct.INVISIBLE <= y and y >= ct.AA_LARGE):
                rescued += 1

    after_all = [f for f in ct.check_pptx(path)
                 if f.ratio is not None and f.ratio < f.threshold]
    return rescued, len(before), len(after_all)


def _rewrite_zip_to(src, dst, transform, drop=(), add=None):
    """`create_chapter._rewrite_zip`, but writing to a separate path so a trial
    can be scored without disturbing the original, and able to DROP and ADD
    members.

    `add` is `{name: bytes}` for parts that do not exist in `src`. Without it a
    caller can point a relationship at a new part and never write it, leaving a
    file whose rels reference a member that is not in the archive — which reads
    as a successful rewrite and fails when something opens it.

    Validates the repack with `testzip()` and removes `dst` if anything goes
    wrong, exactly as `_rewrite_zip` does. That is not defensive padding: the
    callers here `os.replace` this file over the good one and the sweep then
    uploads it to Drive, so a truncated repack would silently replace the
    original and ship it. An earlier version skipped both the check and the
    cleanup.
    """
    ok = False
    try:
        with zipfile.ZipFile(src) as zin, \
                zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
            for it in zin.infolist():
                if it.filename in drop:
                    continue
                data = zin.read(it.filename)
                try:
                    new = transform(it.filename, data)
                except UnicodeDecodeError:
                    new = data
                zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
                zi.compress_type = it.compress_type
                zi.external_attr = it.external_attr
                zout.writestr(zi, new)
            for name, data in sorted((add or {}).items()):
                zout.writestr(zipfile.ZipInfo(name), data)
        with zipfile.ZipFile(dst) as zt:
            if zt.testzip() is not None:
                raise RuntimeError("repackaged zip failed validation: " + dst)
        ok = True
    finally:
        if not ok and os.path.exists(dst):
            os.remove(dst)


# ------------------------------------------------- embedded Word fonts -------
#: A font entry is either self-closing or a container, and the SELF-CLOSING
#: alternative has to come first. With the container form first, `[^>]*>`
#: happily consumes the `/` of `<w:font w:name="Arial"/>` and then `.*?` runs on
#: to the NEXT entry's `</w:font>` — so one match swallows three entries and the
#: two in the middle are never rewritten. The `(?<!/)` on the container form
#: stops it matching a self-closing tag from the other direction.
_FONT_ENTRY = re.compile(
    r"<w:font\b[^>]*w:name=\"([^\"]+)\"[^>]*/>"
    r"|<w:font\b[^>]*w:name=\"([^\"]+)\"[^>]*(?<!/)>.*?</w:font>", re.S)
_EMBED = re.compile(r"<w:embed(?:Regular|Bold|Italic|BoldItalic)\b[^>]*/>")
_EMBED_ID = re.compile(r'r:id="([^"]+)"')


def prune_embedded_fonts(path):
    """Make `word/fontTable.xml` agree with the faces the document actually uses.

    Reconciled against USAGE, not just against the rename map: an entry is
    dropped when its face was renamed away *or* when no content part references
    it any more. Both happen here — `_MONO_IS_PROSE` rewrites mono body prose
    to the sans, so JetBrains Mono stops being used at all.

    Renaming every face to Instrument Sans leaves a tracker in a worse state
    than it started: the document references a font the file does not embed,
    the font table still declares the faces nobody uses any more, and ~760KB of
    embedded TTFs for those faces ride along in every copy.

    The embeds cannot simply be renamed with the entries — their BYTES are
    Manrope and Space Grotesk, so calling them "Instrument Sans" would make
    Word render the old face under the new name, which is worse than
    substituting. So the embeds go, the orphaned font parts go with them, and
    the table is deduplicated to the faces actually in use.

    That does mean the trackers no longer carry an embedded copy of their
    typeface. Instrument Sans is not embeddable from here — `assets/fonts` has
    woff2, which OOXML cannot use — so this is honest rather than complete:
    correct metadata and a smaller file, and the face resolves from the system
    (Google Docs, where these live, has it). See DESIGN.md.

    Returns (faces removed, parts dropped).
    """
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "word/fontTable.xml" not in names:
            return [], []
        table = z.read("word/fontTable.xml").decode("utf-8", "replace")
        rels_name = "word/_rels/fontTable.xml.rels"
        rels = z.read(rels_name).decode("utf-8", "replace") if rels_name in names else ""
        # What the document ACTUALLY asks for. Deduping by mapped name alone
        # leaves a face declared and embedded that nothing references: mono
        # body prose is rewritten to the sans by `_MONO_IS_PROSE`, so a tracker
        # kept JetBrains Mono in its table and shipped 443KB of it while using
        # it zero times. The table has to be reconciled against usage, which is
        # what this function's name has always claimed.
        in_use = set()
        for n in names:
            if not n.startswith("word/") or n.endswith("fontTable.xml"):
                continue
            if not n.endswith(".xml"):
                continue
            xml = z.read(n).decode("utf-8", "replace")
            for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
                in_use.update(re.findall(r'%s="([^"]+)"' % attr, xml))
            in_use.update(re.findall(r'<w:latin[^>]*w:typeface="([^"]+)"', xml))
            in_use.update(re.findall(r'<a:latin[^>]*typeface="([^"]+)"', xml))

    dropped_ids, removed, seen = set(), [], set()
    out, last = [], 0
    for m in _FONT_ENTRY.finditer(table):
        face = m.group(1) or m.group(2)
        block = m.group(0)
        new_face = FONT_MAP.get(face, face)
        out.append(table[last:m.start()])
        last = m.end()
        # Unreferenced faces go too, not only renamed ones. `in_use` is empty
        # only if the document has no font references at all, in which case
        # nothing is dropped for lack of evidence.
        unused = bool(in_use) and new_face not in in_use
        if new_face != face or unused:
            removed.append(face)
            for e in _EMBED.finditer(block):
                rid = _EMBED_ID.search(e.group(0))
                if rid:
                    dropped_ids.add(rid.group(1))
            block = _EMBED.sub("", block)
            block = block.replace('w:name="%s"' % face, 'w:name="%s"' % new_face)
        if unused:
            continue                       # nothing references it; drop it
        if new_face in seen:
            continue                       # collapsed onto an entry we kept
        seen.add(new_face)
        out.append(block)
    out.append(table[last:])
    new_table = "".join(out)

    parts, new_rels = [], rels
    for rm in re.finditer(r"<Relationship\b[^>]*/>", rels):
        rid = re.search(r'Id="([^"]+)"', rm.group(0))
        tgt = re.search(r'Target="([^"]+)"', rm.group(0))
        if rid and tgt and rid.group(1) in dropped_ids:
            parts.append(os.path.normpath(
                os.path.join("word", tgt.group(1))).replace(os.sep, "/"))
            new_rels = new_rels.replace(rm.group(0), "")

    if not removed and not parts:
        return [], []

    def tx(name, data):
        if name == "word/fontTable.xml":
            return new_table.encode("utf-8")
        if name == rels_name:
            return new_rels.encode("utf-8")
        if name == "[Content_Types].xml":
            ct = data.decode("utf-8", "replace")
            for part in parts:
                ct = re.sub(r'<Override[^>]*PartName="/%s"[^>]*/>' % re.escape(part),
                            "", ct)
            return ct.encode("utf-8")
        return data

    _rewrite_zip_to(path, path + ".new", tx, drop=set(parts))
    os.replace(path + ".new", path)
    return sorted(set(removed)), sorted(parts)


# ------------------------------------------------- retiring a legacy plate ----
def background_media(path):
    """{media part: [slide parts that use it as their background]}.

    Only `<p:bg>` blip fills. A `<p:pic>` — the world map on the network slide,
    a logo — is content, not a plate, and must not be swapped.
    """
    out = {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for n in sorted(names):
            if not re.match(r"ppt/slides/slide\d+\.xml$", n):
                continue
            xml = z.read(n).decode("utf-8", "replace")
            bg = re.search(r"<p:bg>.*?</p:bg>", xml, re.S)
            if not bg:
                continue
            embed = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', bg.group(0))
            if not embed:
                continue
            rels = "ppt/slides/_rels/%s.rels" % os.path.basename(n)
            if rels not in names:
                continue
            rx = z.read(rels).decode("utf-8", "replace")
            rel = re.search(r'<Relationship\b[^>]*Id="%s"[^>]*/>' % re.escape(embed.group(1)), rx)
            if not rel:
                continue
            tgt = re.search(r'Target="([^"]+)"', rel.group(0))
            if not tgt:
                continue
            part = os.path.normpath(os.path.join("ppt/slides", tgt.group(1)))
            out.setdefault(part.replace(os.sep, "/"), []).append(n)
    return out


def retire_plates(path, replacement, keep_digests):
    """Replace every background image that is NOT one of ours with `replacement`.

    The estate's decks carry one hand-made plate — a teal/magenta/ochre gradient
    that belongs to no AAIF palette — behind their colour title slide. Its text
    was coloured for it, so it is also where almost all of the remaining
    sub-AA contrast sits: recolouring the type can reach ~3.5:1 against it and
    no further, because the plate itself is the problem.

    Swapping the media part's BYTES retires it everywhere at once. Every slide
    that referenced it keeps its layout, its relationships and its ids, and
    simply has an AAIF plate behind it instead — no slide surgery, and the same
    file shared by several slides is fixed in one move.

    `keep_digests` are the SHA-256s of plates this toolkit generated, which must
    survive. Identifying the legacy plate positively (by hash, by name) would be
    fragile; identifying OURS and replacing whatever else is a background is
    not, and it degrades safely — an unknown plate is treated as legacy, which
    is what an unknown plate is.

    Returns the media parts replaced.
    """
    used = background_media(path)
    if not used:
        return []
    with zipfile.ZipFile(path) as z:
        legacy = [part for part in used
                  if hashlib.sha256(z.read(part)).hexdigest() not in keep_digests]
    if not legacy:
        return []
    # The bytes are swapped in place under the part's existing NAME, so the
    # replacement has to be the type that name declares. Writing a PNG into
    # ppt/media/imageN.jpeg leaves the part contradicting its content type —
    # PowerPoint may refuse it, and the contrast checker, which decodes only
    # .png and .gif, would quietly call the slide unreadable-background rather
    # than repairing it.
    wrong = [p for p in legacy if not p.lower().endswith(".png")]
    if wrong:
        raise RuntimeError(
            "cannot retire %s with PNG bytes: the part name declares another "
            "type. Rename the part and its content-type override, or supply a "
            "replacement of that type." % ", ".join(wrong))
    _rewrite_zip_to(path, path + ".new",
                    lambda n, d: replacement if n in legacy else d)
    os.replace(path + ".new", path)
    return sorted(legacy)


def update_plates(path, plates):
    """Refresh the artwork behind slides this toolkit already plated.

    `add_plate_slides` is idempotent by LABEL — a deck that already has
    `hero-gradient` is skipped — which is right for adding, and wrong for
    changing the art. Retirement is no help either: it would see six unfamiliar
    digests and replace all six with the single retirement plate.

    So this matches on the label each generated slide carries in its
    `<p:cSld name="AAIF plate · …">` and swaps that slide's background media
    for the current file of the same name. Slides the toolkit did not generate
    are untouched, and a deck whose art is already current comes back unchanged
    so the caller does not re-upload it.

    Returns the labels whose artwork changed.
    """
    by_label = dict(plates)
    changed, media_for, retarget = [], {}, {}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for slide in sorted(n for n in names
                            if re.match(r"ppt/slides/slide\d+\.xml$", n)):
            xml = z.read(slide).decode("utf-8", "replace")
            mark = re.search(r'<p:cSld[^>]*\bname="AAIF plate · ([^"]*)"', xml)
            if not mark or mark.group(1) not in by_label:
                continue
            label = mark.group(1)
            bg = re.search(r"<p:bg>.*?</p:bg>", xml, re.S)
            embed = re.search(r'<a:blip[^>]*r:embed="([^"]+)"', bg.group(0)) if bg else None
            if not embed:
                continue
            rels = "ppt/slides/_rels/%s.rels" % os.path.basename(slide)
            if rels not in names:
                continue
            rel = re.search(r'<Relationship\b[^>]*Id="%s"[^>]*/>'
                            % re.escape(embed.group(1)),
                            z.read(rels).decode("utf-8", "replace"))
            tgt = re.search(r'Target="([^"]+)"', rel.group(0)) if rel else None
            if not tgt:
                continue
            part = os.path.normpath(
                os.path.join("ppt/slides", tgt.group(1))).replace(os.sep, "/")
            with open(by_label[label], "rb") as fh:
                new = fh.read()
            if z.read(part) == new:
                continue                    # already current
            want_ext = os.path.splitext(by_label[label])[1].lower()
            if os.path.splitext(part)[1].lower() == want_ext:
                media_for[part] = new
            else:
                # The plate changed TYPE — a still became animated, or the
                # reverse. Bytes cannot be swapped under the old name, because
                # the part's extension and its content type declare what it is.
                # Point the relationship at a NEW part and drop the old one.
                retarget[(slide, rels, embed.group(1), part)] = (label, new, want_ext)
            changed.append(label)
    if not media_for and not retarget:
        return []

    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
    next_media = 1 + max([int(m.group(1)) for m in
                          (re.match(r"ppt/media/image(\d+)\.", n) for n in names)
                          if m] or [0])
    rel_edits, ct_adds, drop = {}, set(), set()
    for (slide, rels, rid, part), (_label, new, ext) in sorted(retarget.items()):
        newpart = "ppt/media/image%d%s" % (next_media, ext)
        next_media += 1
        media_for[newpart] = new
        rel_edits.setdefault(rels, []).append((rid, os.path.basename(newpart)))
        ct_adds.add(ext)
        drop.add(part)

    def tx(name, data):
        if name in media_for:
            return media_for[name]
        if name in rel_edits:
            xml = data.decode("utf-8", "replace")
            for rid, base in rel_edits[name]:
                xml = re.sub(
                    r'(<Relationship\b[^>]*Id="%s"[^>]*Target=")[^"]*(")'
                    % re.escape(rid),
                    lambda m: m.group(1) + "../media/" + base + m.group(2), xml)
            return xml.encode("utf-8")
        if name == "[Content_Types].xml" and ct_adds:
            xml = data.decode("utf-8", "replace")
            for ext in ct_adds:
                if ('Extension="%s"' % ext[1:]) not in xml:
                    xml = xml.replace(
                        "</Types>", '<Default ContentType="%s" Extension="%s"/>'
                                    "</Types>" % (_MEDIA_CT[ext], ext[1:]))
            return xml.encode("utf-8")
        return data

    # A dropped part may still be referenced by a slide this did not retarget —
    # the same plate image can back more than one slide. Count the references
    # that will REMAIN and keep any part something still points at.
    retargeted = {rid for edits in rel_edits.values() for rid, _b in edits}
    remaining = set()
    with zipfile.ZipFile(path) as z:
        for n in names:
            if not n.endswith(".rels"):
                continue
            for rel in re.finditer(r"<Relationship\b[^>]*/>",
                                   z.read(n).decode("utf-8", "replace")):
                rid = re.search(r'Id="([^"]+)"', rel.group(0))
                tgt = re.search(r'Target="\.\./media/([^"]+)"', rel.group(0))
                if not tgt:
                    continue
                if n in rel_edits and rid and rid.group(1) in retargeted:
                    continue          # this reference is being moved away
                remaining.add("ppt/media/" + tgt.group(1))
    new_parts = {k: v for k, v in media_for.items() if k not in names}
    _rewrite_zip_to(path, path + ".new", tx,
                    drop={d for d in drop if d not in remaining},
                    add=new_parts)
    os.replace(path + ".new", path)
    return sorted(set(changed))
