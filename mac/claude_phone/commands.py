"""Spoken slash commands: "slash compact" -> "/compact", "slash code review high" -> "/code-review high".

Spoken names are matched against the commands and skills installed for Claude Code, so multi-word names
get their hyphens back and plugin skills get their plugin prefix ("slash sprint summary" ->
"/treyd-toolkit:sprint-summary"). When "slash" opens the transcript, an unknown name falls back to the first
spoken word.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

BUILTIN = [
    "add-dir", "agents", "clear", "code-review", "compact", "config", "context", "cost", "doctor", "exit",
    "export", "help", "hooks", "init", "mcp", "memory", "model", "new", "permissions", "plan", "reset",
    "resume", "review", "rewind", "security-review", "simplify", "status", "usage",
]  # fmt: skip

# "slash" / "forward slash" as a word, or "/" at the start of a word (not inside paths like src/main.py)
_SLASH = re.compile(r"\b(?:forward\s+)?slash\b[\s,.:;!?-]*|(?<!\S)/(?=\w)", re.IGNORECASE)
_MAX_LEAD_IN_WORDS = 4  # "Hi Claude, …", "Okay so …"
_MAX_NAME_WORDS = 6


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class CommandIndex:
    def __init__(self, names: Iterable[str] = ()):
        self._by_key: dict[str, str] = {}
        for name in names:
            self.add(name)

    def add(self, name: str) -> None:
        self._by_key.setdefault(_squash(name), name)
        if ":" in name:  # plugin skills can be spoken without the plugin name
            self._by_key.setdefault(_squash(name.split(":", 1)[1]), name)

    def lookup(self, spoken: str) -> str | None:
        return self._by_key.get(_squash(spoken))

    def __len__(self) -> int:
        return len(set(self._by_key.values()))

    @classmethod
    def discover(cls, project_dirs: Iterable[Path] = (), claude_home: Path | None = None) -> CommandIndex:
        home = claude_home or Path.home() / ".claude"
        index = cls(BUILTIN)
        for name in _user_and_project_names(home, project_dirs):
            index.add(name)
        for name in _plugin_names(home / "plugins"):
            index.add(name)
        return index


def to_command(text: str, index: CommandIndex) -> str | None:
    """The slash command in a transcript, else None.

    "Slash" (or "/") may come first, or after a short lead-in like "Hi Claude," as long as a known command
    follows. Words before it are dropped; words after the command name become its arguments.
    """
    for match in _SLASH.finditer(text):
        lead_in = len(text[: match.start()].split())
        if lead_in > _MAX_LEAD_IN_WORDS:
            return None
        command = _command_at(text[match.end() :].split(), index, allow_unknown=lead_in == 0)
        if command:
            return command
    return None


def _command_at(words: list[str], index: CommandIndex, allow_unknown: bool) -> str | None:
    if not words:
        return None
    for count in range(min(len(words), _MAX_NAME_WORDS), 0, -1):
        name = index.lookup("".join(words[:count]))
        if name:
            break
    else:
        if not allow_unknown:
            return None
        name, count = re.sub(r"[^a-z0-9:_-]", "", words[0].lower()), 1
    if not name:
        return None
    args = " ".join(words[count:]).strip(" .!?,;:")
    return f"/{name} {args}".rstrip()


def _skill_name(skill_md: Path) -> str:
    """The `name:` from SKILL.md frontmatter, else the skill's folder name."""
    try:
        lines = skill_md.read_text(errors="replace").splitlines()
    except OSError:
        return skill_md.parent.name
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            key, _, value = line.partition(":")
            if key.strip() == "name" and value.strip():
                return value.strip().strip("'\"")
    return skill_md.parent.name


def _user_and_project_names(home: Path, project_dirs: Iterable[Path]) -> list[str]:
    names = []
    skill_roots = [home / "skills", *(Path(d).expanduser() / ".claude" / "skills" for d in project_dirs)]
    command_roots = [home / "commands", *(Path(d).expanduser() / ".claude" / "commands" for d in project_dirs)]
    for root in filter(Path.is_dir, skill_roots):
        names.extend(_skill_name(md) for md in root.rglob("SKILL.md") if ".trash" not in md.parts)
    for root in filter(Path.is_dir, command_roots):
        names.extend(md.stem for md in root.rglob("*.md"))
    return names


def _plugin_names(plugins_dir: Path) -> list[str]:
    """`plugin:skill` and `plugin:command` for plugins synced or cached on this machine."""
    names = []
    for sub in ("synced", "cache"):
        root = plugins_dir / sub
        if not root.is_dir():
            continue
        for manifest in root.rglob(".claude-plugin/plugin.json"):
            plugin_dir = manifest.parent.parent
            try:
                plugin = json.loads(manifest.read_text()).get("name") or plugin_dir.name
            except (OSError, ValueError):
                plugin = plugin_dir.name
            for skill_md in plugin_dir.glob("skills/*/SKILL.md"):
                names.append(f"{plugin}:{_skill_name(skill_md)}")
            for command_md in plugin_dir.glob("commands/*.md"):
                names.append(f"{plugin}:{command_md.stem}")
    return names
