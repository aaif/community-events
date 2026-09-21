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


def test_read_is_none_only_for_an_absent_file(tmp_path):
    assert findings.read(str(tmp_path / "nope.json")) is None
    good = tmp_path / "good.json"
    findings.Report("s").write(str(good))
    assert findings.read(str(good))["step"] == "s"


def test_read_raises_for_a_present_but_unusable_file(tmp_path):
    other = tmp_path / "other.json"
    other.write_text('{"format": 99}', encoding="utf-8")
    with pytest.raises(findings.FindingsError, match="format 99"):
        findings.read(str(other))
    broken = tmp_path / "broken.json"
    broken.write_text('{"format": 1, "fin', encoding="utf-8")
    with pytest.raises(findings.FindingsError, match="not valid JSON"):
        findings.read(str(broken))
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"format": 1, "findings": [{"kind": "k", "severity": "ok"}]}),
                   encoding="utf-8")
    with pytest.raises(findings.FindingsError, match="severity"):
        findings.read(str(odd))


def test_mode_and_written_are_tied():
    with pytest.raises(ValueError):
        findings.Report("x", mode="wrote")
    with pytest.raises(ValueError):
        findings.Report("")
    r = findings.Report("x")           # report mode
    r.written = True
    with pytest.raises(ValueError, match="written=True"):
        r.to_dict()
    w = findings.Report("x", mode="write")
    w.written = True
    assert w.to_dict()["written"] is True


def test_tiles_are_scalars_and_findings_have_subjects():
    r = findings.Report("x")
    with pytest.raises(ValueError):
        r.measure("l", [1, 2])
    with pytest.raises(ValueError):
        r.measure("l", True)
    with pytest.raises(ValueError):
        r.find("k", "  ")
    r.measure("l", "75 / 96").find("k", "Boston")


def test_named_shows_a_few_and_counts_the_rest():
    assert findings.named([]) == ""
    assert findings.named(["Ada"]) == "Ada"
    assert findings.named(["Ada", "Bo", "Cy"]) == "Ada, Bo, Cy"
    assert findings.named(["Ada", "Bo", "Cy", "Di"]) == "Ada, Bo, Cy and 1 other"
    assert findings.named(["Ada", "Bo", "Cy", "Di", "Ed"]) == "Ada, Bo, Cy and 2 others"
    assert findings.named(["", "Ada", " "], show=1) == "Ada"
    assert findings.named(iter(["Ada", "Bo", "Cy", "Di"])) == "Ada, Bo, Cy and 1 other"


def test_add_flag_is_the_documented_flag():
    ap = findings.add_flag(argparse.ArgumentParser())
    assert ap.parse_args([]).json_out is None
    assert ap.parse_args(["--json-out", "/x/y.json"]).json_out == "/x/y.json"
