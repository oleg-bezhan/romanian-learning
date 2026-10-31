#!/usr/bin/env python3
"""Keep the per-assistant copies of the skills and the reviewer agent in step.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/adapters.py sync
    runtime/python/python .agents/skills/add-romanian-words/scripts/adapters.py check

The skills and the reviewer live ONCE, in .agents/ -- where Antigravity (the
Gemini subscription's assistant) looks for them:
    .agents/skills/<name>/SKILL.md          skills (same format in both tools)
    .agents/agents/naturalness-reviewer.md  the blind reviewer subagent
Claude Code only reads .claude/, so `sync` writes generated copies there:
    .claude/skills/<name>/SKILL.md          identical, plus a do-not-edit line and
                                            the commands it may run unprompted
    .claude/agents/naturalness-reviewer.md  same body, Claude's frontmatter
Edit the .agents/ files, then run `sync`. `check` (part of the unit evals)
fails when a copy is missing or stale.
"""
import io, os, re, sys, glob

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ROOT, rel

SKILLS = os.path.join(ROOT, ".agents", "skills")
AGENTS = os.path.join(ROOT, ".agents", "agents")
CLAUDE = os.path.join(ROOT, ".claude")
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
# Claude Code's frontmatter for each agent: model and tool names differ per tool.
CLAUDE_AGENT = {"naturalness-reviewer": {"model": "sonnet", "tools": "Read"}}
# Claude Code only: commands a skill may run without an "Allow?" prompt during the
# turn that invokes it (`allowed-tools`). Windows Claude Code runs either shell.
_PY = "runtime/python/python .agents/skills/add-romanian-words/scripts/*"
_BOOT = "powershell -NoProfile -ExecutionPolicy Bypass -File .agents/skills/setup/bootstrap.ps1"
_SHELLS = ["%s(%s)" % (sh, cmd) for cmd in (_PY,) for sh in ("Bash", "PowerShell")]
CLAUDE_SKILL_TOOLS = {
    "add-romanian-words": _SHELLS + ["WebFetch(domain:dexonline.ro)"],
    "setup": _SHELLS + ["Bash(%s)" % _BOOT, "PowerShell(%s)" % _BOOT],
}


def read(p):
    return io.open(p, encoding="utf-8").read().replace("\r\n", "\n")


def wanted():
    """-> {claude path: content} for every generated copy."""
    out = {}
    for src in sorted(glob.glob(os.path.join(SKILLS, "*", "SKILL.md"))):
        name = os.path.basename(os.path.dirname(src))
        text = read(src)
        m = FRONT.match(text)
        note = "<!-- Generated from %s by adapters.py sync -- edit that file, not this one. -->\n" \
            % rel(src)
        body = text[m.end():] if m else text
        tools = "".join("\n  - %s" % t for t in CLAUDE_SKILL_TOOLS.get(name, []))
        head = "---\n%s%s\n---\n" % (m.group(1), "\nallowed-tools:" + tools if tools else "") \
            if m else ""
        out[os.path.join(CLAUDE, "skills", name, "SKILL.md")] = head + note + body
    for src in sorted(glob.glob(os.path.join(AGENTS, "*.md"))):
        name = os.path.basename(src)[:-3]
        text = read(src)
        m = FRONT.match(text)
        desc = re.search(r"^description:\s*(.*)$", m.group(1), re.M).group(1) if m else ""
        extra = CLAUDE_AGENT.get(name, {})
        head = ["name: %s" % name, "description: %s" % desc] + \
            ["%s: %s" % kv for kv in extra.items()]
        out[os.path.join(CLAUDE, "agents", name + ".md")] = \
            "---\n%s\n---\n" % "\n".join(head) + (text[m.end():] if m else text)
    return out


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd not in ("sync", "check"):
        print(__doc__)
        return 2
    stale = []
    for path, content in wanted().items():
        have = read(path) if os.path.exists(path) else None
        if have == content:
            continue
        stale.append(rel(path))
        if cmd == "sync":
            os.makedirs(os.path.dirname(path), exist_ok=True)
            io.open(path, "w", encoding="utf-8", newline="\n").write(content)
    if cmd == "sync":
        print("updated: %s" % (", ".join(stale) if stale else "nothing -- all in step"))
        return 0
    for s in stale:
        print("STALE  %s -- run adapters.py sync" % s)
    print("adapters: %s" % ("in step" if not stale else "%d stale" % len(stale)))
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
