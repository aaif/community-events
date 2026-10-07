"""Which chapter a city belongs to, for the chapters that absorbed other cities.

Japan and New Zealand are country chapters (2026-10): their organizers pooled
and host in any city. The form's city dropdown still offers Tokyo, so an
applicant keeps arriving under a city that has no chapter row.

Every engine that reads a city off an intake row goes through `chapter_for` —
directly, or via `sync_chapters.resolve_city`, which calls it — because a fold
applied by some readers and not others puts one person in two chapters. Add a
city here and those engines follow.

The one exception is `aaif-clean-data`, which is zippable and cannot import
this: it keeps `COUNTRY_CHAPTER_FOLDS` for the sheet's Extracted City, and
`test_extract_city.py` asserts the two tables agree. Change both together.
"""
import re
import unicodedata

#: Folded city -> the chapter that absorbed it.
CITY_FOLDS = {"tokyo": "Japan", "wellington": "New Zealand", "auckland": "New Zealand"}


def _key(text):
    """The same accent- and punctuation-blind key as `sync_chapters.fold_city`."""
    s = "".join(c for c in unicodedata.normalize("NFKD", text or "")
                if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", s)).strip()


def chapter_for(city):
    """`city`, or the country chapter that absorbed it. Empty stays empty.

    "Tokyo, Japan" folds as well as "Tokyo": a city typed with its own country
    is still that city. Anything else comes back exactly as given.
    """
    key = _key(city)
    if key in CITY_FOLDS:
        return CITY_FOLDS[key]
    for folded, chapter in CITY_FOLDS.items():
        if key == "%s %s" % (folded, _key(chapter)):
            return chapter
    return city
