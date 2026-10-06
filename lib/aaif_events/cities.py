"""Which chapter a city belongs to, for the chapters that absorbed other cities.

Japan and New Zealand are country chapters (2026-10): their organizers pooled
and host in any city. The form's city dropdown still offers Tokyo, so an
applicant keeps arriving under a city that has no chapter row.

One definition, because four readers resolve an intake row's city
independently — the chapters feed, the CRM and Drive-access engines, and the
Slack audit — and a fold applied to some of them places one person in two
chapters. Add a city here and every reader follows.
"""
import re

#: Folded city -> the chapter that absorbed it.
CITY_FOLDS = {"tokyo": "Japan", "wellington": "New Zealand", "auckland": "New Zealand"}


def chapter_for(city):
    """`city`, or the country chapter that absorbed it. Empty stays empty."""
    key = re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", city or "")).strip().casefold()
    return CITY_FOLDS.get(key, city)
