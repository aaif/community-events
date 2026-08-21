"""Minimal Luma public-API client + pure payload builders.

API: https://docs.luma.com/reference (base https://public-api.luma.com,
auth header `x-luma-api-key`, Luma Plus required). Keys are scoped to ONE
calendar — events are created on the calendar the key belongs to.

LIVE-WRITE CONTRACT: nothing in this module writes on import or by accident;
`create_event` / `update_event` / `add_host` / `upload_image` hit the live API
and must only be reached behind an explicit user-approved --create / --apply
flag in the calling script.

The payload builders (`event_times`, `event_payload`, `diff_payload`,
`slug_of_url`) are pure and unit-tested without network.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

from aaif_events import tracker

BASE = "https://public-api.luma.com"
KEYCHAIN_SERVICE = "luma-api-key"
_TRANSIENT_HTTP = (429, 500, 502, 503, 504)
_IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp", ".gif": "image/gif"}


class LumaError(RuntimeError):
    status = None   # HTTP status when the API answered; None for network failures


class NotAnEventUrl(LumaError):
    """The URL/slug resolves to a non-event entity (e.g. a chapter calendar)."""


# ---------------------------------------------------------------------------
# Auth + transport
# ---------------------------------------------------------------------------
def api_key():
    """LUMA_API_KEY env var, else the macOS keychain item `luma-api-key`."""
    k = os.environ.get("LUMA_API_KEY", "").strip()
    if k:
        return k
    try:
        r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                           capture_output=True, text=True)
    except OSError:   # no `security` CLI (non-macOS) — no keychain to read
        r = None
    if r is not None and r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    hint = ""
    if r is not None and r.returncode != 0:
        err = (r.stderr or "").strip()
        # item-not-found is the normal "no key stored" case; anything else
        # (locked keychain, denied ACL) must not masquerade as "no key"
        if err and "could not be found" not in err:
            hint = "\nKeychain lookup failed (not just missing): %s" % err.splitlines()[-1]
    raise LumaError(
        "No Luma API key found. Create one in the calendar's settings (Luma Plus, "
        "keys are per-calendar), then either export LUMA_API_KEY or store it with:\n"
        "  security add-generic-password -s %s -a aaif -w THE_KEY%s"
        % (KEYCHAIN_SERVICE, hint))


def available():
    """True if a Luma API key is configured (env or keychain). Skills use this
    to decide: connected -> propose/do the Luma step; not connected -> skip it
    and hand the user the manual instructions instead. Never raises."""
    try:
        api_key()
        return True
    except LumaError:
        return False


def call(method, path, params=None, body=None, retries=4):
    """One API call. GETs retry transient HTTP/network errors with backoff;
    writes (POST) are never blind-retried — a timed-out create/update may have
    gone through on the server, so a retry could duplicate the event or
    double-send guest notifications — except on 429, which Luma sends before
    doing any work."""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"x-luma-api-key": api_key(), "accept": "application/json"}
    if data is not None:
        headers["content-type"] = "application/json"
    write_hint = ("" if method == "GET" else
                  " — the write may still have gone through; check Luma before retrying")
    retries = max(1, retries)
    for i in range(retries):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                text = r.read().decode("utf-8") or "{}"
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # A proxy or outage page can answer 200 with HTML; a bare
                # JSONDecodeError mid-run names neither the request nor the fact
                # that the body was the problem.
                raise LumaError("%s %s answered 200 but the body was not JSON "
                                "(starts %r)" % (method, path, text[:120]))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:300]
            retryable = e.code == 429 or (method == "GET" and e.code in _TRANSIENT_HTTP)
            if retryable and i < retries - 1:
                print("luma: HTTP %d on %s %s — retry %d/%d in %ds"
                      % (e.code, method, path, i + 1, retries - 1, 2 * (i + 1)),
                      file=sys.stderr)
                time.sleep(2 * (i + 1))
                continue
            err = LumaError("%s %s -> HTTP %d: %s%s"
                            % (method, path, e.code, detail,
                               write_hint if e.code in _TRANSIENT_HTTP and e.code != 429
                               else ""))
            err.status = e.code
            raise err
        except (urllib.error.URLError, TimeoutError) as e:
            if method == "GET" and i < retries - 1:
                print("luma: %s %s unreachable — retry %d/%d in %ds"
                      % (method, path, i + 1, retries - 1, 2 * (i + 1)), file=sys.stderr)
                time.sleep(2 * (i + 1))
                continue
            raise LumaError("Luma unreachable (%s %s): %s%s"
                            % (method, path, e, write_hint))


# ---------------------------------------------------------------------------
# Endpoints (thin)
# ---------------------------------------------------------------------------
def get_calendar():
    return call("GET", "/v1/calendars/get")


def get_event(event_id):
    return call("GET", "/v1/events/get", params={"event_id": event_id})


def lookup_slug(slug):
    return call("GET", "/v1/entities/lookup", params={"slug": slug})


def create_event(payload):
    return call("POST", "/v1/events/create", body=payload)


def update_event(payload):
    return call("POST", "/v1/events/update", body=payload)


def add_host(event_id, email, name=None, access_level=None):
    body = {"event_id": event_id, "email": email}
    if name:
        body["name"] = name
    if access_level:
        body["access_level"] = access_level   # "manager" (default) or "check-in"
    return call("POST", "/v1/events/hosts/add", body=body)


def upload_image(path):
    """Upload a local image to the Luma CDN; returns the file_url for cover_url."""
    ext = os.path.splitext(path)[1].lower()
    mime = _IMAGE_MIME.get(ext)
    if not mime:
        raise LumaError("unsupported cover image type %r (use %s)"
                        % (ext, "/".join(sorted(_IMAGE_MIME))))
    res = call("POST", "/v1/images/create-upload-url", body={"content_type": mime})
    with open(path, "rb") as f:
        data = f.read()
    # The upload URL is pre-signed — no API key header, plain PUT of the bytes.
    req = urllib.request.Request(res["upload_url"], data=data, method="PUT",
                                 headers={"content-type": mime})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if r.status not in (200, 201, 204):
                raise LumaError("image upload failed: HTTP %d" % r.status)
    except (urllib.error.URLError, TimeoutError) as e:   # HTTPError included
        raise LumaError("image upload failed: %s" % e)
    return res["file_url"]


def resolve_event_id(url_or_slug):
    """Resolve a luma.com event URL (or bare slug) to an evt- id via entity lookup."""
    slug = slug_of_url(url_or_slug)
    ent = lookup_slug(slug).get("entity") or {}
    if ent.get("type") != "event":
        raise NotAnEventUrl("slug %r is a %s, not an event (chapter calendar links "
                            "can't be synced — pass the event page URL)"
                            % (slug, ent.get("type") or "unknown entity"))
    ev = ent.get("event") or {}
    event_id = ev.get("id") or ev.get("api_id")
    if not event_id:
        raise LumaError("entity lookup for %r returned no event id (keys: %s)"
                        % (slug, ", ".join(sorted(ev))))
    return event_id


# ---------------------------------------------------------------------------
# Pure payload builders (no network)
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"\b(\d{1,2}):(\d{2})(?:\s*([ap])\.?m\.?)?", re.I)
_URL_RE = re.compile(r"https?://\S+|(?:www\.|lu\.ma/|luma\.com/)\S+", re.I)


def slug_of_url(url_or_slug):
    """Last path segment of a luma.com / lu.ma URL; bare slugs pass through."""
    s = url_or_slug.strip().rstrip("/")
    s = re.sub(r"^https?://", "", s)
    return s.rsplit("/", 1)[-1].split("?")[0]


def _hour24(h, m, ampm):
    """(hour, minute) from a _TIME_RE match; honors an AM/PM marker if present."""
    h = int(h)
    if ampm:
        if ampm.lower() == "p" and h != 12:
            h += 12
        elif ampm.lower() == "a" and h == 12:
            h = 0
    return h, int(m)


def event_times(date_text, tz_name, duration_hours=3.0):
    """DATE & TIME cell -> (start, end) timezone-aware datetimes.

    The date comes from tracker.parse_event_date (requires a 4-digit year); the
    start time is the first HH:MM in the text — required, so a placeholder cell
    can't silently become a midnight event. An AM/PM marker is honored ("6:00 PM"
    is 18:00, not 06:00). A second HH:MM ("17:30 — 20:30") is the end time;
    otherwise end = start + duration_hours ("18:00 — late").
    """
    d = tracker.parse_event_date(date_text)
    tz = ZoneInfo(tz_name)
    times = _TIME_RE.findall(date_text)
    if not times:
        raise ValueError("no HH:MM start time in DATE & TIME: %r" % date_text)
    h, m = _hour24(*times[0])
    start = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
    if len(times) > 1:
        h, m = _hour24(*times[1])
        end = dt.datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
        if end <= start:   # "21:00 — 00:30" crosses midnight
            end += dt.timedelta(days=1)
    else:
        end = start + dt.timedelta(hours=duration_hours)
    return start, end


def iso_utc(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _capacity_of(text):
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


def event_payload(view, tz_name, duration_hours=3.0, description_md=None,
                  cover_url=None, slug=None):
    """Build the events/create payload from a tracker event view (read_event/
    view_event dict). In-person trackers map VENUE + LOCATION / CITY to a manual
    address; series trackers map STREAM / JOIN LINK to meeting_url. Placeholder
    text flows through as-is — the proposal is reviewed by a human before create.

    Consumes only view["details"], reading these labels: EVENT TITLE,
    DATE & TIME, VENUE, LOCATION / CITY, STREAM / JOIN LINK, CAPACITY / RSVPS.
    """
    det = view["details"]
    name = (det.get("EVENT TITLE") or "").strip()
    if not name:
        raise ValueError("tracker event has no EVENT TITLE")
    start, end = event_times(det.get("DATE & TIME", ""), tz_name, duration_hours)
    payload = {"name": name, "start_at": iso_utc(start), "end_at": iso_utc(end),
               "timezone": tz_name, "visibility": "public"}
    venue = (det.get("VENUE") or "").strip()
    city = (det.get("LOCATION / CITY") or "").strip()
    if venue or city:
        payload["geo_address_json"] = {"type": "manual",
                                       "address": ", ".join(x for x in (venue, city) if x)}
    join = (det.get("STREAM / JOIN LINK") or "").strip()
    m = _URL_RE.search(join)
    if m:
        u = m.group(0)
        payload["meeting_url"] = u if u.startswith("http") else "https://" + u
    cap = _capacity_of(det.get("CAPACITY / RSVPS"))
    if cap:
        payload["max_capacity"] = cap
    if description_md:
        payload["description_md"] = description_md
    if cover_url:
        payload["cover_url"] = cover_url
    if slug:
        payload["slug"] = slug
    return payload


def _norm(key, value):
    """Normalize one field for live-vs-desired comparison."""
    if value is None:
        return None
    if key in ("start_at", "end_at"):
        # compare as instants at second granularity; live returns fractional
        # seconds ("...:00.673Z") that would make every diff look dirty
        s = str(value).replace("Z", "+00:00")
        try:
            return dt.datetime.fromisoformat(s).astimezone(dt.timezone.utc).replace(microsecond=0)
        except ValueError:
            return str(value)
    if isinstance(value, str):
        return value.strip()
    return value


def diff_payload(live, desired):
    """Fields of `desired` that differ from the `live` event, as
    {field: (live_value, desired_value)}. geo_address_json compares only the
    keys `desired` sets (live may carry extra provider fields); description_md
    is treated as changed whenever provided and textually different (Luma's
    Spark round-trip isn't byte-stable, so only pass it when pushing new copy).
    """
    changes = {}
    for key, want in desired.items():
        have = live.get(key)
        if key == "geo_address_json" and isinstance(want, dict) and isinstance(have, dict):
            if all(_norm(None, have.get(k)) == _norm(None, v) for k, v in want.items()):
                continue
        elif _norm(key, have) == _norm(key, want):
            continue
        changes[key] = (have, want)
    return changes
