#!/usr/bin/env python3
"""Tests for check_workflows.py — every rule, positive and negative, on synthetic YAML.

Run:  python3 scripts/test_check_workflows.py   (plain script, exit 1 on failure)
Needs PyYAML, like the linter itself.
"""
import glob, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_workflows as cw  # noqa: E402

SHA = "0" * 40
CHECKOUT = "      - uses: actions/checkout@%s\n        with:\n          persist-credentials: false\n" % SHA


def wf(on="workflow_dispatch:", job_extra="", steps="      - run: python x.py\n", env_block=""):
    return ("name: t\non:\n  %s\n%sjobs:\n  j:\n    runs-on: ubuntu-latest\n%s    steps:\n%s%s"
            % (on, env_block, job_extra, CHECKOUT, steps))


def errs(text):
    return cw.check_text(text)


def has(text, needle):
    return any(needle in e for e in errs(text))


class Parsing(unittest.TestCase):
    def test_quoted_on_key_and_flow_style_are_still_seen(self):
        t = '"on": {push: {branches: [main]}, pull_request_target: {}}\njobs:\n  j:\n    runs-on: x\n    steps: []\n'
        self.assertTrue(has(t, "forbidden trigger `pull_request_target`"))

    def test_list_and_scalar_on(self):
        self.assertTrue(has("on: [push, issue_comment]\njobs:\n  j:\n    steps: []\n", "issue_comment"))
        self.assertTrue(has("on: workflow_run\njobs:\n  j:\n    steps: []\n", "workflow_run"))

    def test_four_space_indent(self):
        self.assertTrue(has("on:\n    pull_request_target:\njobs:\n  j:\n    steps: []\n", "forbidden trigger"))

    def test_comments_do_not_trigger_rules(self):
        t = wf() + "      # pull_request_target --i-have-approval ${{ secrets.X }}\n"
        self.assertEqual(errs(t), [])

    def test_quoted_run_scalar_with_hash_still_checked(self):
        # In plain YAML ` #` starts a comment (GitHub sees the same truncation);
        # inside a quoted scalar it does not, and the secret must still be found.
        t = wf(job_extra="    environment: ops\n",
               steps="      - run: 'echo \"build #1\" ${{ secrets.T }}'\n")
        self.assertTrue(has(t, "secret interpolated into a run step"))

    def test_unparseable_fails_closed(self):
        self.assertTrue(has("on: [\n", "cannot parse"))
        self.assertTrue(has("jobs:\n  j:\n    steps: []\n", "cannot parse"))
        self.assertTrue(has("on: push\n", "cannot parse"))


class SecretTriggers(unittest.TestCase):
    def secret_wf(self, on, **kw):
        return wf(on=on, job_extra="    environment: ops\n",
                  steps="      - run: python x.py\n        env:\n          T: ${{ secrets.T }}\n", **kw)

    def test_pull_request_with_secrets_is_refused(self):
        self.assertTrue(has(self.secret_wf("pull_request:"), "touches secrets but triggers on ['pull_request']"))

    def test_dispatch_with_secrets_is_fine(self):
        self.assertEqual(errs(self.secret_wf("workflow_dispatch:")), [])

    def test_push_needs_main_branch_filter(self):
        self.assertTrue(has(self.secret_wf("push:"), "without `branches: [main]`"))
        self.assertTrue(has(self.secret_wf("push:\n    branches: [feature/*]"), "without `branches: [main]`"))
        self.assertEqual(errs(self.secret_wf("push:\n    branches: [main]")), [])

    def test_github_token_alone_is_not_a_secret(self):
        t = wf(on="pull_request:", steps="      - run: python x.py\n        env:\n          G: ${{ secrets.GITHUB_TOKEN }}\n")
        self.assertEqual(errs(t), [])

    def test_bracket_case_and_tojson_forms_count(self):
        for form in ("${{ secrets['T'] }}", "${{ SECRETS.T }}", "${{ toJSON(secrets) }}",
                     "${{ secrets[format('T{0}', 'X')] }}"):
            t = wf(on="pull_request:", steps="      - run: python x.py\n        env:\n          T: %s\n" % form)
            self.assertTrue(has(t, "touches secrets but triggers"), form)

    def test_secrets_inherit_is_refused(self):
        t = "on: workflow_dispatch\njobs:\n  j:\n    uses: ./.github/workflows/x.yml\n    secrets: inherit\n"
        self.assertTrue(has(t, "secrets: inherit"))

    def test_workflow_call_plus_pull_request_is_refused(self):
        t = ("on:\n  workflow_call:\n    secrets:\n      T: {required: true}\n  pull_request:\n"
             "jobs:\n  j:\n    runs-on: x\n    steps: []\n")
        self.assertTrue(has(t, "touches secrets but triggers on ['pull_request']"))


class JobEnvironment(unittest.TestCase):
    def test_missing_environment(self):
        t = wf(steps="      - run: python x.py\n        env:\n          T: ${{ secrets.T }}\n")
        self.assertTrue(has(t, "job `j` touches secrets without an `environment:`"))

    def test_environment_is_per_job(self):
        t = ("on: workflow_dispatch\njobs:\n  a:\n    runs-on: x\n    environment: ops\n    steps: []\n"
             "  b:\n    runs-on: x\n    steps:\n      - run: python x.py\n        env:\n          T: ${{ secrets.T }}\n")
        self.assertTrue(has(t, "job `b` touches secrets without an `environment:`"))
        self.assertFalse(has(t, "job `a`"))

    def test_workflow_level_env_counts_for_every_job(self):
        t = wf(env_block="env:\n  T: ${{ secrets.T }}\n")
        self.assertTrue(has(t, "job `j` touches secrets without an `environment:`"))


class Exfil(unittest.TestCase):
    def secret_job(self, steps):
        return wf(job_extra="    environment: ops\n",
                  steps="      - run: python x.py\n        env:\n          T: ${{ secrets.T }}\n" + steps)

    def test_upload_artifact_and_github_script(self):
        self.assertTrue(has(self.secret_job("      - uses: actions/upload-artifact@%s\n" % SHA), "artifacts"))
        self.assertTrue(has(self.secret_job("      - uses: actions/github-script@%s\n" % SHA), "github-script"))

    def test_step_summary(self):
        self.assertTrue(has(self.secret_job('      - run: echo hi >> "$GITHUB_STEP_SUMMARY"\n'), "step summary"))

    def test_env_dump(self):
        for cmd in ("env", "printenv", "set", "cat /proc/self/environ", "python x.py; env"):
            self.assertTrue(has(self.secret_job("      - run: %s\n" % cmd), "dumps the environment"), cmd)
        self.assertFalse(has(self.secret_job("      - run: set -euo pipefail\n"), "dumps the environment"))

    def test_pii_path_print(self):
        self.assertTrue(has(self.secret_job("      - run: cat nightly-reports/a.log\n"), "PII output path"))
        self.assertTrue(has(self.secret_job("      - run: |\n          tail -n5 .slack-audit-cache/users.json\n"),
                            "PII output path"))
        self.assertFalse(has(self.secret_job("      - run: rm -rf .slack-audit-cache backups/*\n"), "PII output path"))

    def test_artifacts_fine_without_secrets(self):
        self.assertEqual(errs(wf(steps="      - uses: actions/upload-artifact@%s\n" % SHA)), [])


class RunAndApproval(unittest.TestCase):
    def test_secret_inline_in_run_with_line_number(self):
        t = wf(job_extra="    environment: ops\n", steps="      - run: ./x --token ${{ secrets.T }}\n")
        e = [x for x in errs(t) if "secret interpolated" in x]
        self.assertEqual(len(e), 1)
        self.assertTrue(e[0].startswith("%d:" % (t.split("\n").index("      - run: ./x --token ${{ secrets.T }}") + 1)))

    def test_secret_on_continuation_line_any_indent(self):
        t = ("on: workflow_dispatch\njobs:\n  j:\n    runs-on: x\n    environment: ops\n    steps:\n"
             "        - run: |\n            a\n            b ${{ secrets.T }}\n")
        self.assertTrue(has(t, "secret interpolated into a run step"))

    def test_env_after_run_block_is_not_inside_it(self):
        t = wf(job_extra="    environment: ops\n",
               steps="      - run: |\n          python x.py\n        env:\n          T: ${{ secrets.T }}\n")
        self.assertEqual(errs(t), [])

    def test_i_have_approval(self):
        self.assertTrue(has(wf(steps="      - run: python s.py --write --i-have-approval\n"), "--i-have-approval"))


class Pinning(unittest.TestCase):
    def test_tag_and_short_sha_rejected(self):
        for ref in ("v4", "0123456"):
            self.assertTrue(has(wf(steps="      - uses: a/b@%s\n" % ref), "not pinned"))

    def test_full_sha_local_and_digest_accepted(self):
        t = wf(steps="      - uses: a/b@%s\n      - uses: ./local\n      - uses: docker://img@sha256:%s\n" % (SHA, "0" * 64))
        self.assertEqual(errs(t), [])
        self.assertTrue(has(wf(steps="      - uses: docker://img:3\n"), "digest"))

    def test_checkout_persist_credentials(self):
        t = wf().replace("          persist-credentials: false\n", "          fetch-depth: 1\n")
        self.assertTrue(has(t, "persist-credentials"))
        t2 = wf().replace("        with:\n", "        with:\n          fetch-depth: 1\n          ref: main\n")
        self.assertEqual(errs(t2), [])

    def test_reusable_workflow_uses_at_job_level(self):
        t = "on: workflow_dispatch\njobs:\n  j:\n    uses: a/b/.github/workflows/x.yml@v1\n"
        self.assertTrue(has(t, "not pinned"))


class Main(unittest.TestCase):
    def test_real_workflows_pass(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        files = glob.glob(os.path.join(root, ".github/workflows/*.yml*"))
        self.assertTrue(files)
        for f in files:
            self.assertEqual(cw.check(f), [], f)

    def test_no_files_is_a_failure(self):
        cwd = os.getcwd()
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        try:
            self.assertEqual(cw.main(["x"]), 1)
        finally:
            os.chdir(cwd)

    def test_missing_path_is_reported_not_raised(self):
        self.assertTrue(any("cannot read" in e for e in cw.check("/nonexistent/x.yml")))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=1).result
    sys.exit(0 if r.wasSuccessful() else 1)
