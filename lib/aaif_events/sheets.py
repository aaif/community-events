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


def _lookup(headers, sheet, names, first_of=()):
    """Resolve each name to its column index, aborting if that is ambiguous.

    `first_of` names columns where a duplicate is known, benign and READ-ONLY:
    those resolve to the first match with a warning instead of aborting. It
    exists because a live sheet already carries one — the intake's
    `Run events before?` sits in two columns, a form-version artefact — and
    that column is only ever printed in a report, never written back. Aborting
    every run over a column nobody writes trades a real outage for a
    theoretical ambiguity.

    It is deliberately per-column and opt-in. The default stays fatal, because
    the case this guard exists for is a WRITE resolving to a different column
    than the read that fed it.
    """
    out = []
    for name in names:
        if headers.count(name) > 1 and name in first_of:
            at = [i for i, h in enumerate(headers) if h == name]
            print("  warning: %r appears %d times on %s (columns %s) — reading "
                  "the first. This column is read-only here; fix the sheet when "
                  "convenient."
                  % (name, len(at), sheet,
                     ", ".join(col_letter(i) for i in at)), file=sys.stderr)
            out.append(at[0])
            continue
        if headers.count(name) > 1:
            sys.exit("ABORT: %r appears twice on %s — reads would be ambiguous."
                     % (name, sheet))
        if name not in headers:
            sys.exit("ABORT: column %r not found on %s — sheet layout changed?"
                     % (name, sheet))
        out.append(headers.index(name))
    return out


def header_index(headers, sheet, *names, first_of=()):
    """Column indexes for `names`, in the order asked, as a list.

    For the positional style: `i_city, i_country = header_index(h, tab, "City",
    "Country")`. See `_lookup` for `first_of`.
    """
    return _lookup(headers, sheet, names, first_of)


def header_map(headers, sheet, *names, first_of=()):
    """The same lookup keyed by column name, for the `idx["City"]` style."""
    return dict(zip(names, _lookup(headers, sheet, names, first_of)))


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
