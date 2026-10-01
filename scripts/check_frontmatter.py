#!/usr/bin/env python3
"""Validate SKILL.md files against Agent Skills plus this repo's policy.

A SKILL.md whose frontmatter fails to parse loads at runtime with *empty
metadata* — every field (including the `description` that drives auto-activation)
is silently dropped. `claude plugin validate` does NOT parse skill frontmatter,
so this hook is the guard — it runs both locally on commit and in CI (the
pre-commit job). Requires PyYAML (pulled in by pre-commit).
"""
import re
import sys
from pathlib import Path

import yaml

FRONTMATTER = re.compile(r"^---\n(.*?)\n---", re.S)
ALLOWED_FIELDS = {
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


def check(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = FRONTMATTER.match(text)
    if not m:
        return [f"{path}: no `---` YAML frontmatter block at the top of the file"]
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        first = str(e).splitlines()[0]
        return [f"{path}: frontmatter is not valid YAML ({first})"]
    if not isinstance(data, dict):
        return [f"{path}: frontmatter must be a YAML mapping"]
    errors = []
    for field in ("name", "description", "compatibility"):
        if not isinstance(data.get(field), str) or not data[field].strip():
            errors.append(f"{path}: frontmatter is missing a non-empty `{field}`")
    unknown = sorted(set(data) - ALLOWED_FIELDS)
    if unknown:
        errors.append(f"{path}: unsupported frontmatter field(s): {', '.join(unknown)}")

    name = data.get("name")
    if isinstance(name, str):
        if not NAME_RE.fullmatch(name) or "--" in name:
            errors.append(f"{path}: `name` does not satisfy Agent Skills naming rules")
        if name != Path(path).parent.name:
            errors.append(f"{path}: `name` must match its parent directory")

    description = data.get("description")
    if isinstance(description, str) and len(description) > 1024:
        errors.append(f"{path}: `description` exceeds 1024 characters")
    compatibility = data.get("compatibility")
    if isinstance(compatibility, str) and len(compatibility) > 500:
        errors.append(f"{path}: `compatibility` exceeds 500 characters")

    for field in ("license", "allowed-tools"):
        if field in data and (not isinstance(data[field], str) or not data[field].strip()):
            errors.append(f"{path}: `{field}` must be a non-empty string")

    metadata = data.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            errors.append(f"{path}: `metadata` must be a mapping")
        elif any(not isinstance(key, str) or not isinstance(value, str)
                 for key, value in metadata.items()):
            errors.append(f"{path}: `metadata` keys and values must all be strings")

    body = text[m.end():].strip()
    if not body:
        errors.append(f"{path}: Markdown body is empty")
    if len(text.splitlines()) > 500:
        errors.append(f"{path}: exceeds the repository limit of 500 lines")
    return errors


def main(argv: list[str]) -> int:
    problems = [err for path in argv for err in check(path)]
    for err in problems:
        print(err, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
