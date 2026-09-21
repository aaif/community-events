# `create_chapter.py` internals and maintenance

> Load this before editing the rebrand engine or the map projection, or when the
> template's world-map image changes and the projection must be refitted.

`scripts/create_chapter.py` is the engine. It rebrands at the paragraph level
(concatenate the text runs, transform, write back into the first run) so it is
robust to OOXML run-splitting. The `SF`-abbreviation casing is decided by the
surrounding words. The Drive layer uses `gws` (`files.copy`, `create`, `get`,
`update`).

The slide-5 map dot is placed by `reposition_map_marker` using a lat/lon → pixel
**Gall Stereographic** projection (`lon2x` linear in longitude; `lat2y` linear in
`(1 + √2/2)·tan(lat/2)`), fitted 2026-07-30 against Natural Earth 110m coastlines
composited over `image18.png` (mean residual 0.64 px). **If the template's map
image changes, refit**: composite the transparent PNG over `#F6F5F1`, build a
distance transform of the drawn pixels, and optimize (scale_x, scale_y, offset_x,
offset_y, central meridian) per candidate projection family by Nelder-Mead to
minimise the mean distance from projected coastline vertices (lat > -60; the map
omits Antarctica) to the nearest drawn pixel. `scripts/test_create_chapter.py`
covers the San Francisco calibration lock, the label offset, monotonicity/canvas
bounds, and the 2-shapes-or-raise guard.

To validate the engine after any edit, rebrand a throwaway copy of the template
and diff it against an existing chapter (the canonical end-state):
```bash
# --rebrand-local requires --lat/--lon: local mode is fully offline and refuses
# to geocode, so the coordinates must be passed explicitly.
python3 ${CLAUDE_SKILL_DIR}/scripts/create_chapter.py \
    --city "Los Angeles" --lat 34.05 --lon -118.24 --rebrand-local /path/to/template-copy
# then compare paragraph text against the real Los Angeles chapter, and open
# slide 5 of the rebranded Slides.pptx to confirm the dot moved (+map dot).
# EXPECTED: the dot sits ~8-15 px from the existing chapter's — old decks were
# placed by the pre-2026-07-30 anchors/overrides projection. Judge the dot
# against the COASTLINE, not the old deck, and never "fix" the projection back
# toward a hand-placed dot.
python3 ${CLAUDE_SKILL_DIR}/scripts/test_create_chapter.py   # unit tests
```

Constants (Chapters parent id, TemplateCity id) live at the top of the script.
The template must stay "clean": `San Francisco` contiguous (no run/paragraph
splits) and the slug normalized to `aaif-sanfrancisco`. If a future template edit
re-introduces a split, the paragraph-level engine still handles it, but the big
stacked title on Carousel slide 2 is intentionally a single adaptive line.
