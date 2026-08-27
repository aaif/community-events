#!/usr/bin/env python3
"""Self-tests for the design-token extractor.

Standalone (not pytest) to match the repo's other `scripts/test_*.py`, which CI
runs directly.

The failure this guards against is silent by construction: a bad extraction
still writes a file, `--check` then compares the committed copy against the same
bad output and reports "up to date" forever, and every report renders in
fallback colours with nothing anywhere saying why. So the assertions are about
the extractor *refusing* rather than about what it produces.

Fixtures are synthetic CSS, not the real bundle.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import extract_design_tokens as ed  # noqa: E402

FAILS = []

TOKENS = "".join("  %s: x;\n" % t for t in ed.REQUIRED)
FALLBACK = ("@font-face {\n  font-family: 'Instrument Sans Fallback';\n"
            "  src: local('Arial');\n}")
GOOD = ":root {\n%s}\n\n%s" % (TOKENS, FALLBACK)


def check(label, got, want):
    if got != want:
        FAILS.append("%s\n     got:  %r\n     want: %r" % (label, got, want))


def check_raises(label, fn, needle=""):
    try:
        fn()
    except SystemExit as exc:
        if needle and needle not in str(exc):
            FAILS.append("%s: aborted, but message lacked %r:\n     %s"
                         % (label, needle, exc))
        return
    FAILS.append("%s: expected SystemExit, none raised" % label)


def bundle(css):
    """A minimal self-extracting bundle carrying `css`."""
    markup = "<html><head><style>%s</style></head><body></body></html>" % css
    return ('<html><script type="__bundler/template">%s</script></html>'
            % json.dumps(markup))


def test_extracts_the_token_block_and_fallback():
    out = ed.extract(GOOD)
    check("tokens present", "--ink:" in out, True)
    check("fallback face carried", "Instrument Sans Fallback" in out, True)
    check("generated banner present", "DO NOT EDIT" in out.upper(), True)


def test_braces_balance():
    """The property that decides whether the file is CSS at all."""
    out = ed.extract(GOOD)
    check("balanced braces", out.count("{"), out.count("}"))


def test_root_nested_in_a_media_query_is_not_taken():
    """An unanchored search grabs the media-scoped :root AND its dangling brace.

    That writes a brace-unbalanced file which --check then blesses permanently.
    """
    css = "@media print{\n:root {\n  --a: 1;\n}\n}\n\n%s" % GOOD
    out = ed.extract(css)
    check("took the top-level block, not the nested one",
          "--a: 1" in out, False)
    check("still balanced", out.count("{"), out.count("}"))


def test_missing_root_aborts():
    check_raises("no :root at all", lambda: ed.extract("body{color:red}\n"),
                 "no :root block")


def test_missing_required_token_aborts():
    """A design-system rename must fail loudly, not fall through to fallbacks."""
    css = GOOD.replace("--danger: x;", "--destructive: x;")
    check_raises("renamed neutral", lambda: ed.extract(css), "--danger")


def test_missing_fallback_face_aborts():
    check_raises("fallback face removed",
                 lambda: ed.extract(":root {\n%s}\n" % TOKENS),
                 "Instrument Sans Fallback")


def test_template_css_reads_the_json_encoded_block():
    check("css lifted out of the bundle",
          "--ink:" in ed.template_css_from_text(bundle(GOOD)), True)


def test_bundle_without_template_aborts():
    check_raises("not a design bundle",
                 lambda: ed.template_css_from_text("<html></html>"),
                 "__bundler/template")


def test_bundle_without_style_aborts():
    empty = ('<html><script type="__bundler/template">%s</script></html>'
             % json.dumps("<html><body>hi</body></html>"))
    check_raises("no <style> in the template",
                 lambda: ed.template_css_from_text(empty), "<style>")


def test_committed_tokens_are_current():
    """The same assertion CI makes, so a stale file fails here too."""
    if not os.path.exists(ed.BUNDLE):
        return
    with open(ed.OUT, encoding="utf-8") as fh:
        check("design/aaif-tokens.css matches the bundle", fh.read(),
              ed.extract(ed.template_css(ed.BUNDLE)))


def main():
    MIN_TESTS = 10
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                ran += 1
            except BaseException as exc:   # SystemExit too: an unexpected abort is a failure
                FAILS.append("%s raised %s: %s" % (name, type(exc).__name__, exc))
    if ran < MIN_TESTS:
        print("FAIL: only %d tests ran, expected at least %d" % (ran, MIN_TESTS))
        return 1
    if FAILS:
        print("FAIL (%d)" % len(FAILS))
        for f in FAILS:
            print("  - %s" % f)
        return 1
    print("extract_design_tokens: all %d checks passed" % ran)
    return 0


if __name__ == "__main__":
    sys.exit(main())
