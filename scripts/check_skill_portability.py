#!/usr/bin/env python3
"""Enforce client-neutral wording and portable skill-relative paths."""

import sys
from pathlib import Path

BANNED = {
    "${CLAUDE_SKILL_DIR}": "use `<skill-root>` and resolve it from SKILL.md",
    ".claude/skills/": "use a skill-relative path",
    "claude-in-chrome": "describe the browser or web-page capability",
    "get_page_text": "describe the web-page reading capability",
    "AskUserQuestion": "describe the user-input capability",
}
PATH_NOTE = (
    "Paths in this skill are relative to this skill directory. Resolve `<skill-root>`"
)


def check_skill(skill_dir: Path) -> list[str]:
    errors = []
    markdown = sorted(skill_dir.rglob("*.md"))
    texts = {}
    for path in markdown:
        text = path.read_text(encoding="utf-8")
        texts[path] = text
        for needle, replacement in BANNED.items():
            if needle in text:
                errors.append(f"{path}: client-specific `{needle}`; {replacement}")

    entry = skill_dir / "SKILL.md"
    entry_text = texts.get(entry, "")
    if any("<skill-root>" in text for text in texts.values()) and PATH_NOTE not in entry_text:
        errors.append(f"{entry}: define `<skill-root>` before using it")
    return errors


def check_repository(root: Path) -> list[str]:
    skills = root / "skills"
    return [error for skill in sorted(skills.iterdir()) if skill.is_dir()
            for error in check_skill(skill)]


def main(argv: list[str]) -> int:
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[1]
    errors = check_repository(root)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("check_skill_portability: skill instructions are client-neutral.")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
