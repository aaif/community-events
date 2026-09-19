#!/usr/bin/env python3
"""Resolve a chapter or online series to its `Event Tracker.docx`, download it to
a private temp directory, and print the path — plus, with `--event`, the tracker
fields the content skills need.

Every content skill in this repo (`aaif-announcement-post`, `aaif-recap-post`,
`aaif-speaker-bio`, `aaif-luma-description`, `aaif-carousel-copy`,
`aaif-attendee-reminder`, `aaif-dayof-slides`) opens on the same question: what
does the tracker say about this event? Each SKILL.md used to answer it in prose
— "find the folder, then its tracker, then read the entry" — which left the
agent to hand-compose two `gws drive files list` calls with nested single-quote
escaping, every time, in eight different skills. That is the shape the
skill-authoring guidance calls out: logic the agent reinvents each run belongs
in a script.

Read-only. It never writes to Drive, and never writes to the tracker.

The download lands in a directory created 0700 under the system temp dir, NOT in
the repo: a tracker holds organizer, speaker and venue details, and `.gitignore`
is a weaker guarantee than a path with nothing to commit it to. The caller is
responsible for deleting it; `--print-cleanup` emits the command.
"""
import argparse
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "lib"))
from aaif_events import gws, office, tracker  # noqa: E402
from aaif_events.redact import (add_redact_flag, redact_email, redact_name,  # noqa: E402
                                redact_text, set_redaction)

#: AAIF's own Drive folders. A chapter lives under Chapters, an online series
#: under Online. Which one a name lives in is the mode — there is no flag for it.
CHAPTERS_FOLDER = "1IQ1K7aVOKUUkxAcfLuNjdETEnmavvtjx"
ONLINE_FOLDER = "1g2vHrqDHfh9wBkDJryJIl8wqXA4J-d4i"

TRACKER_NAME = "Event Tracker.docx"

FOLDER_MIME = "application/vnd.google-apps.folder"

#: Every Drive read in this repo carries these. Without them a listing against a
#: shared drive comes back empty, and an empty listing is indistinguishable from
#: "no such folder" — the lookup would report a chapter that exists as missing.
DRIVE_SCOPE = {"supportsAllDrives": True, "includeItemsFromAllDrives": True}

#: The detail rows the content skills ask for, in the order a draft reads them.
#: Keys are the tracker's own row labels; a tracker missing one reports it blank
#: rather than dropping the line, so a missing field is visible to the agent
#: instead of looking like a field that was never asked for.
FIELDS = ("EVENT TITLE", "SERIES", "THEME", "DATE & TIME", "VENUE", "CITY",
          "LUMA URL", "SPEAKERS", "HOSTS", "DEMOS")

#: Detail rows that carry a real person's contact details. They are read (a
#: draft sometimes needs to know a speaker was confirmed) but never printed:
#: the public-copy rule in every content SKILL.md forbids publishing them, and
#: the reliable way to keep an address out of a post is to keep it out of the
#: agent's context. `--all-fields` overrides for a human debugging a tracker.
CONTACT_FIELDS = ("SPEAKER EMAIL", "HOST EMAIL", "CONTACT", "PHONE",
                  "VENUE CONTACT", "DOOR CODE", "ACCESS")


def _quote(s):
    """Escape a value for a Drive `q` string literal."""
    return s.replace("\\", "\\\\").replace("'", "\\'")


def find_folder(name):
    """Return (folder_id, mode) for a chapter or online series, by exact name.

    Chapters are tried first because there are ~100 of them and two online
    series. A name in neither parent raises rather than guessing at a fuzzy
    match — picking the wrong chapter writes the wrong city into a public post.
    """
    for parent, mode in ((CHAPTERS_FOLDER, "chapter"), (ONLINE_FOLDER, "series")):
        q = ("name = '%s' and '%s' in parents and mimeType = '%s' "
             "and trashed = false" % (_quote(name), parent, FOLDER_MIME))
        out = gws.json_out("drive", "files", "list",
                           params={"q": q, "fields": "files(id,name)", **DRIVE_SCOPE},
                           what="folder %r under %s" % (name, mode))
        files = out.get("files", [])
        if len(files) > 1:
            raise SystemExit("! %d folders named %r under %s — rename one"
                             % (len(files), name, mode))
        if files:
            return files[0]["id"], mode
    raise SystemExit(
        "! no chapter or online series folder named %r.\n"
        "  The name must match the Drive folder exactly (e.g. 'New York City',\n"
        "  not 'NYC'). `aaif-sync-chapters`' resource map holds the folder URL\n"
        "  for every chapter on the Chapters List." % name)


def find_tracker(folder_id):
    """Return the folder's `Event Tracker.docx` file id."""
    q = ("name = '%s' and '%s' in parents and trashed = false"
         % (_quote(TRACKER_NAME), folder_id))
    out = gws.json_out("drive", "files", "list",
                       params={"q": q, "fields": "files(id,name)", **DRIVE_SCOPE},
                       what="%s in folder" % TRACKER_NAME)
    files = out.get("files", [])
    if not files:
        raise SystemExit("! no %s in that folder. A chapter created by "
                         "`aaif-create-chapter` always has one — if this folder "
                         "does not, it was built by hand." % TRACKER_NAME)
    return files[0]["id"]


def download(file_id, workdir):
    """Download the tracker into `workdir` and return its path."""
    path = os.path.join(workdir, "tracker.docx")
    gws.download(file_id, path)
    os.chmod(path, 0o600)
    return path


def event_fields(path, event, all_fields=False):
    """Return one event's detail rows as a plain dict, or raise LookupError.

    `event` accepts a title, a unique substring of one, or `next` / `latest`.
    Ambiguity raises rather than resolving — `tracker._select` refuses a
    substring matching two titles, because a draft written for the wrong event
    reads as a correct draft.
    """
    root = office.read_document(path)
    ev = tracker.read_event(root, event)
    details = ev["details"]
    keys = list(details) if all_fields else FIELDS
    out = {"title": ev["title"], "date": str(ev["date"]) if ev["date"] else None}
    for k in keys:
        if not all_fields and k in CONTACT_FIELDS:
            continue
        out[k] = details.get(k, "")
    if not all_fields:
        held = sorted(k for k in details if k in CONTACT_FIELDS and details[k])
        if held:
            out["_withheld"] = held
    return out


def _mask(fields):
    """Apply `--redact` to the fields that carry a person. Names and addresses
    only — a venue or a talk title is not a person, and masking it would make
    the digest useless without protecting anyone."""
    masked = {}
    for k, v in fields.items():
        if not isinstance(v, str) or not v:
            masked[k] = v
        elif k in ("SPEAKERS", "HOSTS", "DEMOS"):
            masked[k] = redact_name(v)
        elif "@" in v:
            masked[k] = redact_email(v)
        else:
            masked[k] = redact_text(v)
    return masked


def main():
    ap = argparse.ArgumentParser(
        description="Download a chapter or series Event Tracker.docx and, with "
                    "--event, print its fields. Read-only.")
    ap.add_argument("name", help="chapter or online-series name, exactly as the "
                                 "Drive folder spells it")
    ap.add_argument("--event", help="event title, a unique substring of one, or "
                                    "'next' / 'latest'")
    ap.add_argument("--json", action="store_true",
                    help="emit the fields as JSON instead of a key: value block")
    ap.add_argument("--all-fields", action="store_true",
                    help="include every detail row, contact details included. "
                         "For debugging a tracker, never for drafting copy.")
    ap.add_argument("--keep", metavar="DIR",
                    help="download into DIR instead of a fresh temp directory")
    ap.add_argument("--print-cleanup", action="store_true",
                    help="print the rm command for the temp directory")
    add_redact_flag(ap, masks="speaker/host names (first initial) and addresses")
    a = ap.parse_args()
    set_redaction(a.redact)

    workdir = a.keep or tempfile.mkdtemp(prefix="aaif-tracker-")
    os.makedirs(workdir, mode=0o700, exist_ok=True)

    folder_id, mode = find_folder(a.name)
    path = download(find_tracker(folder_id), workdir)
    print("%s: %s" % (mode, a.name))
    print("tracker: %s" % path)

    if a.event:
        try:
            fields = event_fields(path, a.event, all_fields=a.all_fields)
        except LookupError as e:
            raise SystemExit("! %s" % e)
        if fields is None:
            raise SystemExit("! no event matching %r in that tracker." % a.event)
        fields = _mask(fields)
        print()
        if a.json:
            print(json.dumps(fields, indent=2, ensure_ascii=False))
        else:
            for k, v in fields.items():
                if k == "_withheld":
                    print("(withheld, contact details: %s — never publish these)"
                          % ", ".join(v))
                else:
                    print("%-12s %s" % (k + ":", v))

    if a.print_cleanup:
        print("\ncleanup: rm -rf %s" % workdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
