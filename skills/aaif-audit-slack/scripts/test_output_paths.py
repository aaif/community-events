#!/usr/bin/env python3
"""Every audit writes its cache and pages to the data folder, never the cwd.

From Goose or Claude Desktop the cwd is the operator's own, often synced,
folder, and `.slack-audit-cache` holds the whole member directory. Each
script's real argument parsing is run (stopped just after `parse_args`) with
the data folder and the session folder apart; in a checkout they coincide,
which is how dropping `type=datadir.resolve` from one flag would otherwise
pass. Run: python3 test_output_paths.py
"""

import argparse
import importlib.util
import os
import sys
import tempfile
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(SKILLS, "..", "lib"))
sys.path.insert(0, HERE)

SCRIPTS = [os.path.join(HERE, name + ".py") for name in (
    "audit_activity", "audit_members", "audit_organizers", "audit_topics",
    "summarize_audits")] + [
    os.path.join(SKILLS, "aaif-sync-chapters", "scripts", "chapter_health.py")]

FAILS = []


class _Parsed(Exception):
    pass


def parsed_args(path):
    spec = importlib.util.spec_from_file_location(
        "probe_" + os.path.basename(path)[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real = argparse.ArgumentParser.parse_args

    def stop(self, args=None, namespace=None):
        raise _Parsed(real(self, args, namespace))
    with mock.patch.object(argparse.ArgumentParser, "parse_args", stop), \
            mock.patch.object(sys, "argv", [path]):
        try:
            module.main()
        except _Parsed as done:
            return done.args[0]
    raise AssertionError("%s never parsed its arguments" % path)


def check(label, got, want):
    if got == want:
        print("ok   %s" % label)
    else:
        FAILS.append(label)
        print("FAIL %s: got %r, want %r" % (label, got, want))


with tempfile.TemporaryDirectory() as tmp:
    data, session = os.path.join(tmp, "data"), os.path.join(tmp, "session")
    os.makedirs(session)
    old = os.getcwd()
    os.chdir(session)
    try:
        with mock.patch.dict(os.environ, {"AAIF_DATA_DIR": data}):
            # Default paths are computed when each script is loaded, so load
            # them with the data folder already set.
            for path in SCRIPTS:
                a = parsed_args(path)
                name = os.path.basename(path)
                for flag in ("cache", "out"):
                    value = getattr(a, flag, None)
                    if value is None:
                        continue
                    check("%s --%s defaults into the data folder" % (name, flag),
                          os.path.commonpath([os.path.abspath(value), data]), data)
    finally:
        os.chdir(old)

if FAILS:
    sys.exit("%d failure(s)" % len(FAILS))
print("all output paths land in the data folder")
