import json

import pytest

from claude_phone.commands import BUILTIN, CommandIndex, to_command

INDEX = CommandIndex([*BUILTIN, "treyd-toolkit:sprint-summary", "deep-research"])


@pytest.mark.parametrize(
    "heard, sent",
    [
        ("Slash compact.", "/compact"),
        ("slash clear", "/clear"),
        ("Slash reset", "/reset"),
        ("/compact", "/compact"),
        ("Forward slash model", "/model"),
        ("Slash, compact, focus on the tests.", "/compact focus on the tests"),
        ("Slash code review high.", "/code-review high"),
        ("Slash code-review", "/code-review"),
        ("Slash sprint summary.", "/treyd-toolkit:sprint-summary"),
        ("Slash deep research on phone ringers", "/deep-research on phone ringers"),
        ("Slash foo bar baz", "/foo bar baz"),  # unknown: first word is the command
        # after a short lead-in, as long as a known command follows
        ("Hi Claude, slash compact, this conversation is quite long", "/compact this conversation is quite long"),
        ("Okay so slash clear.", "/clear"),
        ("Hey Claude, /compact.", "/compact"),
        ("Hi Claude, slash sprint summary please", "/treyd-toolkit:sprint-summary please"),
    ],
)
def test_to_command(heard, sent):
    assert to_command(heard, INDEX) == sent


@pytest.mark.parametrize(
    "heard",
    [
        "Fix the slash in the URL",
        "please run the tests",
        "Slash",
        "Slash ...",
        "Hi Claude, slash foo bar",  # unknown name after a lead-in
        "please look at src/main.py and fix it",  # path, not a command
        "I think we should add a slash compact style option",  # too far into the sentence
    ],
)
def test_not_a_command(heard):
    assert to_command(heard, INDEX) is None


def test_discover_finds_user_project_and_plugin_skills(tmp_path):
    home = tmp_path / "claude"
    (home / "skills" / "loop").mkdir(parents=True)
    (home / "skills" / "loop" / "SKILL.md").write_text("---\nname: loop\ndescription: x\n---\n")
    (home / "skills" / ".trash" / "1" / "old").mkdir(parents=True)
    (home / "skills" / ".trash" / "1" / "old" / "SKILL.md").write_text("---\nname: old-skill\n---\n")
    (home / "commands").mkdir()
    (home / "commands" / "ship-it.md").write_text("ship")
    plugin = home / "plugins" / "synced" / "org" / "toolkit"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "toolkit"}))
    (plugin / "skills" / "release-log").mkdir(parents=True)
    (plugin / "skills" / "release-log" / "SKILL.md").write_text("---\nname: release-log\n---\n")
    project = tmp_path / "project"
    (project / ".claude" / "commands").mkdir(parents=True)
    (project / ".claude" / "commands" / "deploy-staging.md").write_text("deploy")

    index = CommandIndex.discover([project], claude_home=home)
    assert index.lookup("loop") == "loop"
    assert index.lookup("ship it") == "ship-it"
    assert index.lookup("release log") == "toolkit:release-log"
    assert index.lookup("deploy staging") == "deploy-staging"
    assert index.lookup("old skill") is None
    assert index.lookup("compact") == "compact"
