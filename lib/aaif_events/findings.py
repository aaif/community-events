"""The structured half of an engine's report: what it measured and what it found.

Every ops engine prints a text report a person reads in the run log. This is
the same report as data, written beside it when the engine is given
`--json-out PATH`, so the sync run's page can open on **the state of the
estate** — stat tiles and finding tables per subject — rather than on a
transcript of what each engine printed.

One shape for every engine, so the renderer needs no per-engine knowledge:

    {
      "format": 1,
      "step": "crm",                     # the runner's name for the step
      "mode": "report" | "write",
      "summary": "317 people across 77 chapters; 2 workbooks would change",
      "measured": [                      # stat tiles, in the order given
        {"label": "people synced", "value": 317},
        {"label": "chapters", "value": 77, "tone": "ok"},
        {"label": "held under central approval", "value": 56, "tone": "warn"}
      ],
      "findings": [                      # one row per thing a human looks at
        {"kind": "new row", "subject": "Boston", "detail": "1 person to add",
         "severity": "warn", "action": "apply with --write"}
      ],
      "written": false                   # True only after an applied write
    }

Rules, because the page is read as the truth about the estate:

* **Only what the engine measured.** A number here is one the text report also
  prints; nothing is estimated or derived for the page.
* **A finding's subject is never a person.** `subject` is the chapter,
  channel, tab or row number. `detail` may carry a name or an address where
  the text report already prints one — through the same `--redact` — because
  "audit this grant" is not actionable without knowing whose it is. The file
  lands in the private run directory beside the logs; the discipline is about
  what a page ranks by, not about hiding what the log already says.
* **Severity is the engine's call**: `bad` (a failure or a broken row), `warn`
  (drift, a proposal, a gate needing a human), `info` (worth seeing, nothing to
  do). `ok` is for measured tiles only; a finding is never `ok`.
* **Free text from a sheet or form stays out of `detail`.** It is untrusted;
  the text report wraps it, the page should not need to.

`Report` collects; `write()` lands it 0600 and atomically, like the caches.
Portable skills (`clean.py`, `intake.py`) carry a copy of the writer rather
than import this — the shape is the contract, not the module.
"""

import json
import os
import tempfile

FORMAT = 1
SEVERITIES = ("bad", "warn", "info")
TONES = ("ok", "warn", "bad", None)


class Report:
    """Collects one engine's measured tiles and findings, then writes them."""

    def __init__(self, step, mode="report"):
        self.step = step
        self.mode = mode
        self.summary = ""
        self.measured = []
        self.findings = []
        self.written = False

    def measure(self, label, value, tone=None):
        """One stat tile. `value` is a number or a short string like '75 / 96'."""
        if tone not in TONES:
            raise ValueError("tone must be one of %r, not %r" % (TONES, tone))
        self.measured.append({"label": str(label), "value": value,
                              **({"tone": tone} if tone else {})})
        return self

    def find(self, kind, subject, detail="", severity="warn", action=""):
        """One finding row. `subject` is a chapter, channel, tab or row, never an address."""
        if severity not in SEVERITIES:
            raise ValueError("severity must be one of %r, not %r" % (SEVERITIES, severity))
        self.findings.append({"kind": str(kind), "subject": str(subject),
                              "detail": str(detail), "severity": severity,
                              "action": str(action)})
        return self

    def to_dict(self):
        return {"format": FORMAT, "step": self.step, "mode": self.mode,
                "summary": self.summary, "measured": list(self.measured),
                "findings": list(self.findings), "written": bool(self.written)}

    def write(self, path):
        """Land the report 0600, atomically. No-op when `path` is falsy, so an
        engine can call this unconditionally with its `--json-out` argument."""
        if not path:
            return None
        write(path, self.to_dict())
        return path


def write(path, doc):
    """0600 from creation, then os.replace: the file is complete or absent."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".findings-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read(path):
    """The dict back, or None when the file is absent or not this format."""
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        return None
    return doc


def add_flag(parser):
    """The one argparse flag every engine adds: `--json-out PATH`."""
    parser.add_argument("--json-out", metavar="PATH", default=None,
                        help="also write the report's measured counts and findings "
                             "as JSON to PATH (the sync runner passes this; the "
                             "file names chapters and rows, so keep it private)")
    return parser
