#!/usr/bin/env python3
"""Verify that the DeepSeek/Deep Code port is structurally complete.

This is the port's own guard. It is intentionally independent of the legacy
Claude layout: it looks only at `.deepcode/`, `DEEPSEEK.md`, and `AGENTS.md`.

Checks:
  * every `.deepcode/skills/*/SKILL.md` has valid frontmatter with a `name`
    matching its directory and a non-empty `description` under 1024 chars
  * every required workflow skill is present
  * no runtime skill points at `.claude/` or `CLAUDE.md`
  * no runtime skill says it will use an Agent/subagent tool as if available
  * `.deepcode/settings.json` is valid JSON, has the expected shape, and does
    not contain an API key
  * the profile and workspace instructions exist

Usage: python3 tools/verify_deepseek_port.py [--quiet]
Exit 0 = the port is complete; exit 1 = at least one check failed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".deepcode" / "skills"
SETTINGS = ROOT / ".deepcode" / "settings.json"
PROFILE = ROOT / "DEEPSEEK.md"
AGENTS = ROOT / "AGENTS.md"

REQUIRED_SKILLS = {
    "job-application-assistant",
    "setup",
    "scrape",
    "rank",
    "apply",
    "interview",
    "outcome",
    "gmail-sync",
    "notion-sync",
    "html-report",
    "dashboard",
    "ui",
    "expand",
    "upskill",
    "add-template",
    "add-portal",
    "reset",
    "posting-fetcher",
    "posting-scorer",
    "application-reviewer",
}

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
DESCRIPTION_MAX = 1024


def parse_frontmatter(text: str) -> dict[str, str] | None:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields[key.strip()] = value.strip()
    return fields


def check_skills() -> list[str]:
    errors: list[str] = []
    if not SKILLS.is_dir():
        return [f"missing skills directory: {SKILLS.relative_to(ROOT)}"]

    found: set[str] = set()
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        folder = skill_md.parent.name
        rel = skill_md.relative_to(ROOT)
        text = skill_md.read_text(encoding="utf-8")
        fields = parse_frontmatter(text)
        if fields is None:
            errors.append(f"{rel}: missing YAML frontmatter")
            continue
        name = fields.get("name", "")
        description = fields.get("description", "")
        if not name:
            errors.append(f"{rel}: missing 'name'")
        elif not NAME_RE.match(name):
            errors.append(f"{rel}: invalid name {name!r} (lowercase letters, digits, hyphens)")
        elif name != folder:
            errors.append(f"{rel}: name {name!r} does not match folder {folder!r}")
        if not description:
            errors.append(f"{rel}: missing 'description'")
        elif len(description) > DESCRIPTION_MAX:
            errors.append(f"{rel}: description is {len(description)} chars (max {DESCRIPTION_MAX})")
        elif ": " in description:
            errors.append(f"{rel}: quote the description or remove the ': ' sequence (invalid YAML plain scalar)")
        found.add(name or folder)

        # Runtime paths must not fall back to the legacy Claude tree.
        for needle in (".claude/", "CLAUDE.md"):
            if needle in text:
                errors.append(f"{rel}: still references legacy path {needle!r}")

        # The runtime mapping explains why these names appear in prose; a line
        # that *dispatches* an agent is the problem. Match the upstream
        # dispatch phrases, not the mapping's "no Agent tool" sentence.
        for pattern in (
            r"via the \*\*Agent tool\*\*",
            r"Use the \*\*Agent tool\*\*",
            r"subagent_type:",
        ):
            if re.search(pattern, text):
                errors.append(f"{rel}: contains an upstream subagent dispatch ({pattern})")

    missing = sorted(REQUIRED_SKILLS - found)
    if missing:
        errors.append("missing required skills: " + ", ".join(missing))

    queries = SKILLS / "scrape" / "search-queries.md"
    if not queries.is_file():
        errors.append("missing .deepcode/skills/scrape/search-queries.md")

    core = SKILLS / "job-application-assistant"
    for number in range(1, 10):
        matches = sorted(core.glob(f"{number:02d}-*.md"))
        if not matches:
            errors.append(f"missing core methodology file {number:02d}-*.md")
    return errors


def check_settings() -> list[str]:
    errors: list[str] = []
    if not SETTINGS.is_file():
        return ["missing .deepcode/settings.json"]
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f".deepcode/settings.json: invalid JSON ({exc})"]
    if not isinstance(data, dict):
        return [".deepcode/settings.json: top-level value must be an object"]

    raw = SETTINGS.read_text(encoding="utf-8")
    if "API_KEY" in raw or re.search(r"sk-[A-Za-z0-9]", raw):
        errors.append(".deepcode/settings.json: must not contain an API key")

    if data.get("model") not in {"deepseek-v4-pro", "deepseek-flash", None}:
        errors.append(".deepcode/settings.json: model should be deepseek-v4-pro or deepseek-flash")

    permissions = data.get("permissions")
    if not isinstance(permissions, dict):
        errors.append(".deepcode/settings.json: permissions must be an object")
        return errors
    for key in ("allow", "ask", "deny"):
        value = permissions.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            errors.append(f".deepcode/settings.json: permissions.{key} must be a list of strings")
    if permissions.get("defaultMode") not in {"allowAll", "askAll", None}:
        errors.append(".deepcode/settings.json: permissions.defaultMode must be allowAll or askAll")
    if "network" not in permissions.get("allow", []):
        errors.append(".deepcode/settings.json: permissions.allow should include 'network' for portal and web fetches")
    return errors


def check_docs() -> list[str]:
    errors: list[str] = []
    for path in (PROFILE, AGENTS):
        if not path.is_file():
            errors.append(f"missing {path.name}")
        elif path.stat().st_size < 500:
            errors.append(f"{path.name} looks too small to be a real document")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the DeepSeek/Deep Code port")
    parser.add_argument("--quiet", action="store_true", help="only print failures")
    args = parser.parse_args()

    errors = check_skills() + check_settings() + check_docs()
    if errors:
        print("verify_deepseek_port: FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1

    count = len(list(SKILLS.glob("*/SKILL.md")))
    if not args.quiet:
        print(f"verify_deepseek_port: OK ({count} skills)")
        print(f"  settings: {SETTINGS.relative_to(ROOT)}")
        print(f"  profile:  {PROFILE.relative_to(ROOT)}")
        print(f"  agents:   {AGENTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
