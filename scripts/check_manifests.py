#!/usr/bin/env python3
"""Validate the portable and Claude-specific plugin manifests together."""

import json
import re
import sys
from pathlib import Path

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
PLUGIN_FIELDS = {
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
}
AUTHOR_FIELDS = {"name", "email", "url"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")


def load_json(path: Path) -> tuple[dict | None, list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, [f"{path}: missing"]
    except json.JSONDecodeError as exc:
        return None, [f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})"]
    if not isinstance(value, dict):
        return None, [f"{path}: top level must be an object"]
    return value, []


def validate_portable(path: Path, data: dict) -> list[str]:
    errors = []
    unknown = sorted(set(data) - PLUGIN_FIELDS)
    if unknown:
        errors.append(f"{path}: unknown Agent Plugins field(s): {', '.join(unknown)}")
    if data.get("$schema") != PLUGIN_SCHEMA:
        errors.append(f"{path}: `$schema` must be {PLUGIN_SCHEMA}")
    name = data.get("name")
    if not isinstance(name, str) or not NAME_RE.fullmatch(name) or "--" in name or ".." in name:
        errors.append(f"{path}: `name` must satisfy Agent Plugins v1 naming rules")

    for field in ("version", "description", "homepage", "repository", "license"):
        if field in data and not isinstance(data[field], str):
            errors.append(f"{path}: `{field}` must be a string")

    author = data.get("author")
    if author is not None:
        if not isinstance(author, dict):
            errors.append(f"{path}: `author` must be an object")
        else:
            extra = sorted(set(author) - AUTHOR_FIELDS)
            if extra:
                errors.append(f"{path}: unknown author field(s): {', '.join(extra)}")
            if any(not isinstance(value, str) for value in author.values()):
                errors.append(f"{path}: every `author` value must be a string")

    keywords = data.get("keywords")
    if keywords is not None and (
        not isinstance(keywords, list) or any(not isinstance(item, str) for item in keywords)
    ):
        errors.append(f"{path}: `keywords` must be an array of strings")

    extensions = data.get("extensions")
    if extensions is not None and not isinstance(extensions, dict):
        errors.append(f"{path}: `extensions` must be an object")
    elif isinstance(extensions, dict):
        for namespace, value in extensions.items():
            if not isinstance(namespace, str) or not isinstance(value, dict):
                errors.append(f"{path}: every extension namespace must map to an object")
                break
    return errors


def validate_repository(root: Path) -> list[str]:
    portable_path = root / "plugin.json"
    claude_path = root / ".claude-plugin" / "plugin.json"
    marketplace_path = root / ".claude-plugin" / "marketplace.json"

    portable, errors = load_json(portable_path)
    claude, more = load_json(claude_path)
    errors.extend(more)
    marketplace, more = load_json(marketplace_path)
    errors.extend(more)
    if portable is None or claude is None or marketplace is None:
        return errors

    errors.extend(validate_portable(portable_path, portable))

    for field in ("name", "version"):
        if portable.get(field) != claude.get(field):
            errors.append(f"{portable_path} and {claude_path}: `{field}` must match")

    entries = marketplace.get("plugins")
    if not isinstance(entries, list):
        errors.append(f"{marketplace_path}: `plugins` must be an array")
    else:
        matches = [item for item in entries if isinstance(item, dict)
                   and item.get("name") == portable.get("name")]
        if len(matches) != 1:
            errors.append(
                f"{marketplace_path}: expected exactly one entry for {portable.get('name')!r}"
            )
        elif matches[0].get("source") != "./":
            errors.append(f"{marketplace_path}: plugin source must remain `./`")

    for dirname in (".codex-plugin", ".cursor-plugin"):
        if (root / dirname).exists():
            errors.append(
                f"{root / dirname}: redundant client manifest; use root plugin.json "
                "unless a documented client-specific requirement exists"
            )
    return errors


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[1]
    errors = validate_repository(root)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("check_manifests: portable, Claude, and marketplace manifests agree.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
