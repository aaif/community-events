"""findings: the shape every engine writes and the runner's page reads."""
import argparse
import json
import os
import stat

import pytest

from aaif_events import findings


def test_report_collects_in_order_and_writes_the_documented_shape(tmp_path):
    r = findings.Report("crm", mode="report")
    r.summary = "2 workbooks would change"
    r.measure("people synced", 317).measure("held", 56, tone="warn")
    r.find("new row", "Boston", "1 person to add", action="apply with --write")
    out = tmp_path / "crm.json"
    assert r.write(str(out)) == str(out)
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc == {
        "format": 1, "step": "crm", "mode": "report",
        "summary": "2 workbooks would change",
        "measured": [{"label": "people synced", "value": 317},
                     {"label": "held", "value": 56, "tone": "warn"}],
        "findings": [{"kind": "new row", "subject": "Boston", "detail": "1 person to add",
                      "severity": "warn", "action": "apply with --write"}],
        "written": False,
    }
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


def test_write_is_a_no_op_without_a_path():
    r = findings.Report("x")
    assert r.write(None) is None
    assert r.write("") is None


def test_severity_and_tone_are_closed_sets():
    r = findings.Report("x")
    with pytest.raises(ValueError):
        r.find("k", "s", severity="ok")
    with pytest.raises(ValueError):
        r.measure("l", 1, tone="info")


def test_read_returns_none_for_absent_or_foreign_files(tmp_path):
    assert findings.read(str(tmp_path / "nope.json")) is None
    other = tmp_path / "other.json"
    other.write_text('{"format": 99}', encoding="utf-8")
    assert findings.read(str(other)) is None
    good = tmp_path / "good.json"
    findings.Report("s").write(str(good))
    assert findings.read(str(good))["step"] == "s"


def test_named_shows_a_few_and_counts_the_rest():
    assert findings.named([]) == ""
    assert findings.named(["Ada"]) == "Ada"
    assert findings.named(["Ada", "Bo", "Cy"]) == "Ada, Bo, Cy"
    assert findings.named(["Ada", "Bo", "Cy", "Di"]) == "Ada, Bo, Cy and 1 other"
    assert findings.named(["Ada", "Bo", "Cy", "Di", "Ed"]) == "Ada, Bo, Cy and 2 others"
    assert findings.named(["", "Ada", " "], show=1) == "Ada"


def test_add_flag_is_the_documented_flag():
    ap = findings.add_flag(argparse.ArgumentParser())
    assert ap.parse_args([]).json_out is None
    assert ap.parse_args(["--json-out", "/x/y.json"]).json_out == "/x/y.json"
