"""Reading a sheet by header name, which is the only way this repo reads one.

Column letters are not stable here: the Chapters List has been restructured,
`Interested in` has moved, and the resource map was inserted in the middle. So
every read and write resolves through the header row, and these are the three
helpers that do it.

They existed twice, and the two copies had made different safety choices rather
than the same one:

* `cell` — one copy guarded against a non-string cell, the other called
  `.strip()` on whatever was there and would raise on a number.
* `header_index` — one copy aborted when a header appeared twice, the other
  silently resolved to the first of them. A duplicated header is exactly the
  state in which "resolve by name" stops being safe, because the two columns
  mean different things and the reader cannot see which one it got.

For `header_index` this module keeps the stricter of the two: a duplicate now
aborts everywhere. For `cell` it keeps the more forgiving one, and that is a
deliberate trade rather than the same choice twice — every read in this repo
goes through `values.batchGet` with the default `FORMATTED_VALUE` render, which
returns strings, so the non-string branch is a guard against a shape that does
not occur rather than a live data path. **If a caller ever passes
`valueRenderOption=UNFORMATTED_VALUE`, revisit it**: numbers would then read as
blank, and a blank resource cell means "nobody has looked yet" to the sync
engine, which proposes and writes over it.

The dict form and the list form both survive because callers genuinely want
both shapes; they share one lookup.
"""

import sys


def cell(row, i):
    """Column `i` of `row` as a stripped string, or `""` past the end.

    A short row is normal — the Sheets API truncates trailing empties — and a
    non-string cell (a number, a bool) reads as `""` rather than raising,
    because a report should say "blank" about a cell it cannot use, not die.
    """
    if i < 0 or i >= len(row):
        return ""
    return row[i].strip() if isinstance(row[i], str) else ""


def _lookup(headers, sheet, names):
    """Resolve each name to its column index, aborting if that is ambiguous."""
    out = []
    for name in names:
        if headers.count(name) > 1:
            sys.exit("ABORT: %r appears twice on %s — reads would be ambiguous."
                     % (name, sheet))
        if name not in headers:
            sys.exit("ABORT: column %r not found on %s — sheet layout changed?"
                     % (name, sheet))
        out.append(headers.index(name))
    return out


def header_index(headers, sheet, *names):
    """Column indexes for `names`, in the order asked, as a list.

    For the positional style: `i_city, i_country = header_index(h, tab, "City",
    "Country")`.
    """
    return _lookup(headers, sheet, names)


def header_map(headers, sheet, *names):
    """The same lookup keyed by column name, for the `idx["City"]` style."""
    return dict(zip(names, _lookup(headers, sheet, names)))


def col_letter(i):
    """0-based column index -> A1 letter (`0` -> `A`, `26` -> `AA`).

    Every write target is derived from the header row through this, so a column
    reorder moves the writes with it instead of stranding them.
    """
    if i < 0:
        raise ValueError("col_letter: negative column index %d" % i)
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return s
