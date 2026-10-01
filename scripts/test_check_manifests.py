import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import check_manifests as chk  # noqa: E402


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def valid_tree(root: Path) -> None:
    portable = {
        "$schema": chk.PLUGIN_SCHEMA,
        "name": "aaif-events",
        "version": "1.2.3",
        "description": "Synthetic plugin.",
        "author": {"name": "AAIF"},
        "keywords": ["events"],
    }
    claude = {"name": "aaif-events", "version": "1.2.3"}
    marketplace = {"plugins": [{"name": "aaif-events", "source": "./"}]}
    write_json(root / "plugin.json", portable)
    write_json(root / ".claude-plugin" / "plugin.json", claude)
    write_json(root / ".claude-plugin" / "marketplace.json", marketplace)


class TestPortableManifest(unittest.TestCase):
    def test_valid_manifest(self):
        data = {"$schema": chk.PLUGIN_SCHEMA, "name": "aaif-events"}
        self.assertEqual(chk.validate_portable(Path("plugin.json"), data), [])

    def test_unknown_field_is_rejected_by_repository_policy(self):
        data = {"$schema": chk.PLUGIN_SCHEMA, "name": "aaif-events", "skills": "./skills"}
        self.assertTrue(any("unknown" in error for error in
                            chk.validate_portable(Path("plugin.json"), data)))

    def test_bad_name_is_rejected(self):
        data = {"$schema": chk.PLUGIN_SCHEMA, "name": "AAIF--events"}
        self.assertTrue(any("naming rules" in error for error in
                            chk.validate_portable(Path("plugin.json"), data)))


class TestRepositoryManifests(unittest.TestCase):
    def test_valid_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_tree(root)
            self.assertEqual(chk.validate_repository(root), [])

    def test_versions_must_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_tree(root)
            write_json(root / ".claude-plugin" / "plugin.json",
                       {"name": "aaif-events", "version": "9.9.9"})
            self.assertTrue(any("`version` must match" in error
                                for error in chk.validate_repository(root)))

    def test_marketplace_must_point_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_tree(root)
            write_json(root / ".claude-plugin" / "marketplace.json",
                       {"plugins": [{"name": "aaif-events", "source": "./plugin"}]})
            self.assertTrue(any("source must remain" in error
                                for error in chk.validate_repository(root)))

    def test_redundant_client_manifest_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_tree(root)
            (root / ".codex-plugin").mkdir()
            self.assertTrue(any("redundant client manifest" in error
                                for error in chk.validate_repository(root)))


if __name__ == "__main__":
    unittest.main()
