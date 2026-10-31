#!/usr/bin/env python3
"""Eval suite for the Romanian cards skill: change the skill, run this, compare.

Usage (from the project root):
    runtime/python/python .agents/skills/add-romanian-words/evals/run.py unit
    runtime/python/python .agents/skills/add-romanian-words/evals/run.py skill [--ref GIT_REF] [--label NAME]
                                                   [--mode quality|balanced|lean] [--model M] [--no-judge]
                                                   [--against LABEL] [--tool claude|agy]
                                                   [--reviewer-model inherit|flash|pro]
                                                   [--learner A0..C1] [--cases cases-low.json]
    runtime/python/python .agents/skills/add-romanian-words/evals/run.py history
    runtime/python/python .agents/skills/add-romanian-words/evals/run.py snapshot

unit      Offline script tests (unit.py): seconds, free. Run after every script edit.
skill     The real skill, end to end, headless (`claude -p`), on the fixed words in
          cases.json, in a throwaway copy of the project (the working tree, or
          --ref: any commit, e.g. the version before a change):
            - Anki and ElevenLabs are local fakes (fake_services.py): nothing
              reaches the real collection, no audio is billed. DEX is the real
              dexonline (read-only);
            - the deck is a snapshot of the real one (fixtures/collection.json),
              so dedup and the vocabulary check behave as they do for real.
          Then it grades what came out -- cards, headwords and stress against the
          answer key, tags, validation, the batch log, counters, a blind reviewer
          that never saw the scheduled grammar -- and has a separate blind judge
          (Sonnet, the rubric only) score every example sentence. Tokens and
          requests come from the run's own transcript. Each run is appended to
          results.jsonl and compared with the previous one: a check that passed
          before and fails now is a REGRESSION.
          Cost: one real batch of ~7 words, plus one short judge call.
          --tool agy runs it in Antigravity instead (headless `agy -p`, default
          model gemini-3.8-flash-high, the reviewer inheriting it unless
          --reviewer-model says otherwise); the judge stays the same.
          --learner A1 swaps the project's own learner for a NEW one at that
          level: learner.md from the template and a grammar profile from the
          level's preset, as the setup page writes them. The judge then also
          scores level fit. --cases picks another word list from evals/
          (cases-low.json: A1-A2 words for exactly that).
history   every recorded run, one line each.
snapshot  refreshes fixtures/collection.json from the live Anki (read-only).
"""
import io, os, re, sys, json, glob, time, shutil, socket, tempfile, subprocess

sys.stdout.reconfigure(encoding="utf-8")
EVALS = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(EVALS)
sys.path.insert(0, os.path.join(SKILL, "scripts"))
sys.path.insert(0, EVALS)
import config                                                    # noqa: E402
from config import ROOT, rel                                     # noqa: E402
from known import normalize                                      # noqa: E402
from fake_services import FakeServices                           # noqa: E402
import usage                                                     # noqa: E402

CASES = os.path.join(EVALS, "cases.json")
COLLECTION = os.path.join(EVALS, "fixtures", "collection.json")
RESULTS = os.path.join(EVALS, "results.jsonl")
RUNS = os.path.join(EVALS, "runs")
# What a project copy needs: the skills, the reviewer, the learner-owned policy.
# (Before 2026-10-31 -- older --ref versions -- the learner's files and the
# shared policy lived in v2/ and the batches in v3/; see dirs().)
COPY = (".claude/skills/", ".claude/agents/", ".agents/", "AGENTS.md",
        "my-cards/learner.md", "my-cards/grammar-profile.md",
        "v2/policies.md", "v2/grammar-profile.md", "v2/naturalness-rubric.md", "v2/learner.md",
        "v2/tts-config.json")
# Python may run the skill's scripts only (old versions keep them under .claude/):
# an ad-hoc `python -c` could reach the REAL Anki on its default port 8765,
# which is open on this machine (2026-10-30: a Flash run read from it).
# Since 2026-10-31 the skill runs the project-local runtime/python/python; older
# versions (--ref) still say python. Claude Code on Windows may use either shell.
TOOLS = ["Bash(runtime/python/python .agents/skills/add-romanian-words/scripts/*)",
         "PowerShell(runtime/python/python .agents/skills/add-romanian-words/scripts/*)",
         "Bash(python .agents/skills/add-romanian-words/scripts/*)",
         "Bash(python .claude/skills/add-romanian-words/scripts/*)",
         "Bash(python3 .agents/skills/add-romanian-words/scripts/*)",
         "Read", "Write", "Edit", "Glob", "Grep",
         "Agent", "Task", "Skill", "WebFetch(domain:dexonline.ro)"]
# The parent session's identity must not leak into the child run, and neither
# may the real .env (config loads it into this process): no real key, no real
# Anki, no real folders.
DROP_ENV = re.compile(r"^(CLAUDECODE|CLAUDE_CODE_.*|CLAUDE_PID|AI_AGENT|ELEVENLABS_.*|ANKI_.*"
                      r"|RO_.*|TTS_.*)$")


AGY_DEFAULT_MODEL = "gemini-3.8-flash-high"
# One session's permissions (never saved): python and dexonline. On Windows a
# plain prefix rule like command(python) never matches -- agy cannot split the
# command line into words there, so it needs the regex form (verified 2026-10-30).
# --mode accept-edits approves writes inside the throwaway project.
# Headless agy ends the whole run at the first refused command, while a learner
# would simply click "allow" -- so harmless read-only commands (listing, reading,
# searching, cd) are allowed too. Nothing that deletes, moves or writes.
_READ_ONLY = ("Get-ChildItem|gci|ls|dir|Get-Content|gc|cat|type|Select-Object|select|"
              "Select-String|sls|Test-Path|Get-Item|Get-Location|pwd|Measure-Object|echo|"
              "Write-Output|Write-Host|head|tail|wc|grep|findstr|cd|Set-Location")
_SCRIPTS = r"\.agents[/\\]skills[/\\]add-romanian-words[/\\]scripts[/\\][a-z_]+\.py"
_PY = r"(runtime[/\\]python[/\\]python(\.exe)?|python|python3|py)"
AGY_SETTINGS = {"permissions": {"allow": [
    "command(regex:%s %s .*)" % (_PY, _SCRIPTS),
    "command(regex:%s %s)" % (_PY, _SCRIPTS),
    "command(regex:(%s) .*)" % _READ_ONLY, "command(regex:(%s))" % _READ_ONLY,
    "read_url(dexonline.ro)"]}}
# The allowlist above still ended most Flash runs: Flash keeps finding new
# command shapes (dir, pwsh -Command ..., python -c). So agy runs auto-approve
# (--dangerously-skip-permissions) like a learner clicking "allow", with two
# guards: the real Anki must be CLOSED, so the fake takes its port 8765 and a
# stray AnkiConnect call hits the fake; and deny rules -- which apply in every
# mode -- block deleting, moving, writing and network tools. A command hidden
# inside a nested shell (pwsh -Command "...") is not inspected.
ANKI_PORT = 8765
_DESTRUCTIVE = ("Remove-Item|ri|rm|rmdir|rd|del|erase|Move-Item|mi|mv|move|Rename-Item|ren|"
                "Copy-Item|cp|copy|Set-Content|Add-Content|Clear-Content|Out-File|New-Item|ni|"
                "curl|wget|Invoke-WebRequest|iwr|Invoke-RestMethod|irm|Start-BitsTransfer|git|"
                "pip|npm|winget|choco|Start-Process|Stop-Process|taskkill|shutdown|reg|format|"
                "Set-ItemProperty|Remove-ItemProperty")
AGY_GUARD = {"permissions": {"deny": ["command(regex:(%s) .*)" % _DESTRUCTIVE,
                                      "command(regex:(%s))" % _DESTRUCTIVE]}}


def port_free(port):
    """Nothing answers on the port AND we could bind it ourselves. Both, because
    on Windows a bind can succeed next to a listener on another address."""
    c = socket.socket()
    c.settimeout(1)
    try:
        if c.connect_ex(("127.0.0.1", port)) == 0:
            return False
    finally:
        c.close()
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def wait_for_closed_anki(minutes=30):
    if port_free(ANKI_PORT):
        return
    print("waiting for port %d: close Anki -- the eval's fake takes its place ..." % ANKI_PORT,
          flush=True)
    end = time.time() + minutes * 60
    while time.time() < end:
        time.sleep(5)
        if port_free(ANKI_PORT):
            print("port %d is free -- starting" % ANKI_PORT, flush=True)
            return
    sys.exit("Anki still open after %d minutes -- eval not run" % minutes)


def agy_bin():
    for c in (os.environ.get("AGY_BIN"), shutil.which("agy"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe")):
        if c and os.path.exists(c):
            return c
    sys.exit("cannot find the agy CLI: set AGY_BIN to its path")


def agy_parse(stream):
    """Antigravity's stream-json -> (result, reviewer prompts, metrics). Steps
    other than user input and the agent's own text are tool calls; a step that
    mentions the reviewer subagent is a reviewer call."""
    events = []
    for line in stream.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    steps, final = {}, {}
    for e in events:
        if e.get("event") == "result":
            final = e.get("result") or {}
        su = e.get("step_update")
        if su and su.get("step_type") not in ("user_input", "agent_response", None):
            steps.setdefault(su.get("step_index"), []).append(su)

    def strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for v in o.values():
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)

    reviews, kinds = [], {}
    for idx, ups in steps.items():
        kind = ups[-1].get("step_type")
        kinds[kind] = kinds.get(kind, 0) + 1
        text = "\n".join(s for u in ups for s in strings(u) if len(s) > 30)
        if "naturalness-reviewer" in json.dumps(ups) and "subagent" in json.dumps(ups).lower():
            reviews.append(text)
    u = final.get("usage") or {}
    res = {"is_error": final.get("status") not in ("SUCCESS", None) or not final,
           "result": final.get("response", "")}
    metrics = {"requests": final.get("num_turns"), "tool_calls": len(steps),
               "input_tokens": u.get("input_tokens"), "cache_read": u.get("cache_read_tokens"),
               "output_tokens": (u.get("output_tokens") or 0) + (u.get("thinking_tokens") or 0),
               "subagents": sum(1 for r in reviews), "step_kinds": kinds}
    return res, reviews, metrics


def claude_bin():
    for c in (os.environ.get("CLAUDE_BIN"), shutil.which("claude"),
              os.environ.get("CLAUDE_CODE_EXECPATH")):
        if c and os.path.exists(c):
            return c
    sys.exit("cannot find the claude CLI: set CLAUDE_BIN to its path")


# --------------------------------------------------------------------------
# project copy
# --------------------------------------------------------------------------

def wanted(path):
    p = path.replace("\\", "/")
    return (any(p == c or p.startswith(c) for c in COPY)
            and "/evals/" not in p and "__pycache__" not in p)


def build_project(dest, ref):
    if ref:
        names = subprocess.run(["git", "-C", ROOT, "ls-tree", "-r", "--name-only", ref],
                               capture_output=True, text=True, check=True).stdout.split("\n")
        for n in filter(wanted, filter(None, names)):
            data = subprocess.run(["git", "-C", ROOT, "show", "%s:%s" % (ref, n)],
                                  capture_output=True, check=True).stdout
            os.makedirs(os.path.dirname(os.path.join(dest, n)), exist_ok=True)
            io.open(os.path.join(dest, n), "wb").write(data)
    else:
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "media", "logs")]
            for f in files:
                r = os.path.relpath(os.path.join(base, f), ROOT).replace("\\", "/")
                if wanted(r):
                    os.makedirs(os.path.dirname(os.path.join(dest, r)), exist_ok=True)
                    shutil.copyfile(os.path.join(ROOT, r), os.path.join(dest, r))
    # The project-local Python the skill runs on. A copy, never a junction: the
    # project copy is deleted with rmtree afterwards.
    if not os.path.exists(os.path.join(ROOT, "runtime", "python", "python.exe")):
        sys.exit("no runtime/python yet -- run .agents/skills/setup/bootstrap.ps1 once")
    shutil.copytree(os.path.join(ROOT, "runtime", "python"), os.path.join(dest, "runtime", "python"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    shim(dest)


def dirs(proj):
    """-> (learner's files, batches, audio cache) of a project copy: all in
    my-cards/ since 2026-10-31; older versions used v2/ and v3/."""
    if os.path.exists(os.path.join(proj, "v2", "grammar-profile.md")):
        return (os.path.join(proj, "v2"), os.path.join(proj, "v3"),
                os.path.join(proj, "v2", "media"))
    d = os.path.join(proj, "my-cards")
    return d, d, os.path.join(d, "media")


def new_learner(dest, level):
    """--learner: a NEW learner at that level instead of the project's own --
    learner.md from the template, a grammar profile from the level's preset
    (setup's grammar.json), no history -- what the setup page writes."""
    import setup, profile
    if level not in setup.levels():
        sys.exit("--learner is one of %s" % ", ".join(setup.levels()))
    text = io.open(os.path.join(SKILL, "templates", "learner.example.md"), encoding="utf-8").read()
    for k, v in (("LANGUAGE_CODE", "uk"), ("CHAT_LANGUAGE", "English"), ("LANGUAGE", "Ukrainian"),
                 ("LEVEL", level), ("MODE", "quality"), ("TOPICS", setup.DEFAULT_TOPICS)):
        text = text.replace("{{%s}}" % k, v)
    learner_dir = dirs(dest)[0]
    io.open(os.path.join(learner_dir, "learner.md"), "w", encoding="utf-8", newline="\n").write(text)
    gp = os.path.join(learner_dir, "grammar-profile.md")
    os.remove(gp)
    answers = os.path.join(dest, "answers.json")
    json.dump(setup.suggested(level, [r["tag"] for r in profile.load_rows(profile.SEED)]),
              io.open(answers, "w", encoding="utf-8"))
    from contextlib import redirect_stdout
    with redirect_stdout(io.StringIO()):
        if profile.cmd_init(gp, answers, False):
            sys.exit("could not write the %s grammar profile" % level)
    os.remove(answers)


def shim(dest):
    """Older versions hardcode the backup folder and the ElevenLabs address.
    Point both at the eval's own, or refuse to run: a fake backup written into
    the real backup folder would be reused as the day's real restore point."""
    # os.walk, not glob: glob's ** skips dot-folders, i.e. .claude/ and .agents/
    # where every script lives (2026-10-30: the first --ref run hit the real API).
    pys = [os.path.join(b, f) for b, _, fs in os.walk(dest) for f in fs if f.endswith(".py")]
    if not pys:
        sys.exit("refusing to run: no scripts found in the project copy")
    for p in pys:
        s = io.open(p, encoding="utf-8").read()
        t = s.replace('BACKUPS = "C:/tmp/anki-backups"', 'BACKUPS = os.environ["ANKI_BACKUP_DIR"]')
        t = t.replace('API = "https://api.elevenlabs.io/v1"', 'API = os.environ["ELEVENLABS_API_URL"]')
        if t != s:
            io.open(p, "w", encoding="utf-8", newline="\n").write(t)
        if "C:/tmp/anki-backups" in t or ("api.elevenlabs.io" in t and "ELEVENLABS_API_URL" not in t):
            sys.exit("refusing to run: %s still reaches a real backup folder or ElevenLabs" % p)


# --------------------------------------------------------------------------
# transcript
# --------------------------------------------------------------------------

def transcript(sid):
    hits = glob.glob(os.path.join(os.path.expanduser("~"), ".claude", "projects", "*",
                                  sid + ".jsonl"))
    return hits[0] if hits else None


def reviewer_calls(path):
    """-> (prompts, replies) of every naturalness-reviewer subagent in the run"""
    out, ids, replies = [], set(), {}
    for line in io.open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        for c in m.get("content") or []:
            if not isinstance(c, dict):
                continue
            if (d.get("type") == "assistant" and c.get("type") == "tool_use"
                    and (c.get("input") or {}).get("subagent_type") == "naturalness-reviewer"):
                out.append(c["input"].get("prompt", ""))
                ids.add(c.get("id"))
            elif c.get("type") == "tool_result" and c.get("tool_use_id") in ids:
                body = c.get("content")
                body = body if isinstance(body, str) else "\n".join(
                    b.get("text", "") for b in body or [] if isinstance(b, dict))
                replies[c["tool_use_id"]] = background_reply(path, body) or body
    return out, list(replies.values())


def background_reply(path, launch):
    """A reviewer started in the background answers later, in a notification;
    its tool result is only the launch notice. Its own transcript
    (<session>/subagents/agent-<id>.jsonl) has the real reply: its last text."""
    m = re.search(r"agentId: (\w+)", launch) if launch.startswith("Async agent launched") else None
    sub = m and os.path.join(path[:-6], "subagents", "agent-%s.jsonl" % m.group(1))
    if not sub or not os.path.exists(sub):
        return None
    last = None
    for line in io.open(sub, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = d.get("message")
        if d.get("type") == "assistant" and isinstance(msg, dict):
            texts = [c.get("text", "") for c in msg.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text"]
            if any(texts):
                last = "\n".join(texts)
    return last


def has_pick_contract(proj):
    """Only versions whose reviewer is asked for PICK lines are checked for them."""
    p = os.path.join(proj, ".claude", "agents", "naturalness-reviewer.md")
    return os.path.exists(p) and "PICK" in io.open(p, encoding="utf-8").read()


def run_usage(path):
    u, models, tools = usage.scan(path)
    t = usage.totals(u)
    subs = [usage.totals(usage.scan(p)[0])
            for p in glob.glob(os.path.join(path[:-6], "subagents", "*.jsonl"))]
    return {"requests": t["turns"], "tool_calls": tools,
            "input_tokens": usage.input_total(t), "cache_read": t["cache_read_input_tokens"],
            "output_tokens": t["output_tokens"], "first_context": t["first_context"],
            "peak_context": t["peak_context"], "models": sorted(models),
            "subagents": len(subs), "subagent_input": sum(usage.input_total(s) for s in subs),
            "subagent_output": sum(s["output_tokens"] for s in subs)}


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------

def split_row(line):
    parts = line.split('";"')
    return (parts[0][1:], parts[1][:-1]) if len(parts) == 2 else None


def strip(s):
    return re.sub(r"<[^>]+>", "", re.sub(r"\[sound:[^\]]*\]", "", s)).strip()


def case_keys(c):
    return {normalize(h) for h in c.get("headword", []) + c.get("plain", []) + [c["input"]]}


def grade(proj, cases, fake, original_profile, reviews, leak_words=True):
    checks, notes = {}, []
    rows = []
    learner_dir, data, media = dirs(proj)
    for p in glob.glob(os.path.join(data, "*_import.csv")):
        for line in io.open(p, encoding="utf-8").read().split("\n"):
            r = split_row(line) if line.strip() else None
            if r:
                rows.append({"front": r[0], "back": r[1], "key": normalize(r[1])})
    added = {normalize(n["fields"].get("Back", "")): n for n in fake.state["added"]}

    for c in cases:
        name = c["input"]
        keys = case_keys(c)
        row = next((r for r in rows if r["key"] in keys
                    or any(r["key"].startswith(k) for k in keys)), None)
        if c.get("duplicate"):
            checks["%s: skipped as already carded" % name] = row is None
            continue
        checks["%s: carded" % name] = row is not None
        if not row:
            continue
        head = re.search(r"<b>(.*?)</b>", row["back"], re.S)
        head = re.sub(r"\[sound:[^\]]*\]", "", head.group(1)) if head else ""
        if c.get("headword"):
            ok = head in c["headword"]
            checks["%s: headword + stress" % name] = ok
            if not ok:
                notes.append("%s: got %r, want %r" % (name, head, c["headword"][0]))
        if c.get("plain"):
            ok = strip(head) in c["plain"]
            checks["%s: headword form" % name] = ok
            if not ok:
                notes.append("%s: got %r" % (name, strip(head)))
        note = added.get(row["key"])
        checks["%s: imported" % name] = note is not None
        if note and c.get("pos"):
            ok = "pos::%s" % c["pos"] in note.get("tags", [])
            checks["%s: pos" % name] = ok
            if not ok:
                notes.append("%s: tags %s" % (name, note.get("tags")))

    # whole batch
    csvs = glob.glob(os.path.join(data, "*_import.csv"))
    if csvs:
        out = subprocess.run([sys.executable, os.path.join(SKILL, "scripts", "validate.py"),
                              csvs[0], "--media", media],
                             capture_output=True, text=True, encoding="utf-8")
        checks["batch: validate.py (current rules) clean"] = out.returncode == 0
        if out.returncode:
            notes += ["validate: " + l for l in out.stdout.splitlines() if l.startswith("line")]
    else:
        checks["batch: validate.py (current rules) clean"] = False
    checks["batch: every card tagged gram/pos/batch"] = bool(added) and all(
        sum(t.startswith("gram::") for t in n.get("tags", [])) >= 1 and
        all(sum(t.startswith(p) for t in n.get("tags", [])) == 1 for p in ("pos::", "batch::"))
        for n in added.values())
    logs = glob.glob(os.path.join(data, "logs", "*-batch.md"))
    # Versions format the log differently: headwords with or without <u>, a score
    # as `"…" — 5`, `"…" 5`, `(5)`, `5/5` or `score 5`. Compare plain text.
    log = re.sub(r"<[^>]+>|`", "", io.open(logs[0], encoding="utf-8").read()) if logs else ""
    checks["batch: log has every card"] = bool(log) and all(
        strip(re.search(r"<b>(.*?)</b>", r["back"]).group(1)).split(",")[0] in log for r in rows)
    checks["batch: log records candidate scores"] = bool(re.search(
        r"[\"”»]\s*(?:—|–|-|:)?\s*\(?[1-5](?:/5)?\)?(?!\d)|\bscore:?\s*[1-5]\b", log))
    profile = io.open(os.path.join(learner_dir, "grammar-profile.md"), encoding="utf-8").read()
    checks["batch: grammar counters updated"] = profile != original_profile
    checks["review: naturalness reviewer used"] = bool(reviews)
    tags = set(re.findall(r"\| ([a-z]+(?:-[a-z]+)+) \|", original_profile))
    leaks = [t for t in tags if any(t in p for p in reviews)]
    # agy's stream may echo the reviewer's own description ("never include the
    # scheduled grammar construction"), so there only the tag names count.
    if leak_words:
        leaks += [w for w in ("scheduled", "construction") if any(w in p.lower() for p in reviews)]
    checks["review: blind (no scheduled grammar in its prompt)"] = bool(reviews) and not leaks
    if leaks:
        notes.append("reviewer prompt mentions: %s" % ", ".join(sorted(set(leaks))))
    examples = []
    for r in rows:
        ro = [strip(x) for x in re.findall(r"<i>(.*?)</i>", r["back"], re.S)]
        uk = [strip(x) for x in re.findall(r"<i>(.*?)</i>", r["front"], re.S)]
        head = strip(re.search(r"<b>(.*?)</b>", r["back"]).group(1))
        for i, s in enumerate(ro):
            examples.append({"lemma": head, "ro": s, "tr": uk[i] if i < len(uk) else "",
                             "core": i == 0})
    core_len = [len(e["ro"].split()) for e in examples if e["core"]]
    gram_tags = [sum(t.startswith("gram::") for t in n.get("tags", [])) for n in added.values()]
    metrics = {"cards": len(rows), "examples": len(examples),
               "gram_tags_per_card": round(sum(gram_tags) / len(gram_tags), 2) if gram_tags else 0,
               "core_outside_4_11_words": sum(not 4 <= n <= 11 for n in core_len),
               "tts_chars": fake.state["tts_chars"], "reviewer_calls": len(reviews)}
    return checks, notes, metrics, examples


def judge(examples, learner, model, fit=False):
    """fit (--learner runs): also a 1-5 level-fit score per sentence -- are its
    grammar and supporting words right for a learner at that level?"""
    if not examples:
        return None
    rubric = io.open(config.RUBRIC, encoding="utf-8").read()
    lines = "\n".join("%d. [%s] %s — %s" % (i, e["lemma"], e["ro"], e["tr"])
                      for i, e in enumerate(examples, 1))
    answer = ("`<n>: <score>`" if not fit else
              "`<n>: <score> <fit>`, where <fit> (1-5) says how well the sentence's grammar and "
              "its words other than the [target] suit a Romanian %s learner (5 = all within "
              "reach, 1 = far beyond)" % learner["level"])
    prompt = ("You are a blind judge of Romanian learner flashcard sentences. Learner: native %s "
              "speaker, Romanian %s. Score every numbered sentence with this rubric (the "
              "[bracketed] word is the target; the text after — is the translation):\n\n%s\n\n"
              "Sentences:\n%s\n\nAnswer with exactly one line per sentence, %s, "
              "and nothing else. Do not use any tools."
              % (learner["language"], learner["level"], rubric, lines, answer))
    t = tempfile.mkdtemp(prefix="rcj-")
    try:
        out = subprocess.run([claude_bin(), "-p", "--output-format", "json", "--model", model,
                              "--strict-mcp-config"], input=prompt, cwd=t, env=child_env({}),
                             capture_output=True, text=True, encoding="utf-8", timeout=600)
        res = json.loads(out.stdout or "{}").get("result", "")
    finally:
        shutil.rmtree(t, ignore_errors=True)
    found = re.findall(r"^\s*(\d+)\s*:\s*([1-5])(?:\s+([1-5]))?", res, re.M)
    scores = {int(a): int(b) for a, b, _ in found}
    fits = {int(a): int(c) for a, _, c in found if c}
    if not scores:
        return {"error": res[:200]}
    vals = list(scores.values())
    extra = {}
    if fit and fits:
        extra = {"fit_mean": round(sum(fits.values()) / len(fits), 2),
                 "fit_low": ["%s (%d)" % (examples[n - 1]["ro"], s) for n, s in fits.items()
                             if s < 4 and n <= len(examples)]}
    return dict(extra, **{
        "model": model, "mean": round(sum(vals) / len(vals), 2), "min": min(vals),
        "below_4": sum(v < 4 for v in vals), "scored": len(vals),
        "low": ["%s (%d)" % (examples[n - 1]["ro"], s) for n, s in scores.items()
                if s < 4 and n <= len(examples)],
        "per_sentence": [{"score": scores.get(i), "fit": fits.get(i), "lemma": e["lemma"],
                          "ro": e["ro"], "core": e["core"]} for i, e in enumerate(examples, 1)]})


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def child_env(extra):
    env = {k: v for k, v in os.environ.items() if not DROP_ENV.match(k)}
    env.update(extra)
    return env


def cmd_skill(args):
    def opt(flag, default=None):
        return args[args.index(flag) + 1] if flag in args[:-1] else default
    ref, mode, model, tool = opt("--ref"), opt("--mode"), opt("--model"), opt("--tool", "claude")
    if tool not in ("claude", "agy"):
        sys.exit("--tool is claude or agy")
    if tool == "agy":
        model = model or AGY_DEFAULT_MODEL
    level = (opt("--learner") or "").upper()
    label = opt("--label") or "-".join(filter(None, [ref or "worktree", tool if tool != "claude"
                                                     else None, model, mode, level]))
    spec = json.load(io.open(os.path.join(EVALS, opt("--cases", "cases.json")), encoding="utf-8"))
    cases = spec["cases"]
    if not os.path.exists(COLLECTION):
        sys.exit("no fixtures/collection.json -- run `run.py snapshot` once (Anki open)")

    proj = tempfile.mkdtemp(prefix="rce-")
    build_project(proj, ref)
    if level:
        new_learner(proj, level)
        spec["learner"] = dict(spec["learner"], level=level)
    if mode:
        lp = os.path.join(dirs(proj)[0], "learner.md")
        if not os.path.exists(lp):
            sys.exit("--mode needs a version with learner.md")
        s = io.open(lp, encoding="utf-8").read()
        io.open(lp, "w", encoding="utf-8", newline="\n").write(
            re.sub(r"^mode:.*$", "mode: %s" % mode, s, flags=re.M))
    profile_path = os.path.join(dirs(proj)[0], "grammar-profile.md")
    original_profile = io.open(profile_path, encoding="utf-8").read()
    for d in (os.path.join(proj, "anki-media"), os.path.join(proj, "backups"), dirs(proj)[2]):
        os.makedirs(d, exist_ok=True)
    if tool == "agy":
        wait_for_closed_anki()
    fake = FakeServices(COLLECTION, os.path.join(proj, "anki-media"),
                        port=ANKI_PORT if tool == "agy" else 0)
    url = fake.start()
    env = child_env({
        "ANKI_CONNECT_URL": url, "ELEVENLABS_API_URL": url + "/v1",
        "ELEVENLABS_API_KEY": "eval-fake-key", "ELEVENLABS_VOICE_ID": "evalvoice",
        "ANKI_IMPORT_DECK": "Default", "ANKI_MODEL": "Basic",
        "ANKI_MEDIA_DIR": os.path.join(proj, "anki-media"),
        "ANKI_BACKUP_DIR": os.path.join(proj, "backups"), "TTS_CONFIRM_CHARS": "1000000"})
    words = "; ".join(c["input"] for c in cases)
    prompt = ("Use the add-romanian-words skill to add these Romanian words to my Anki deck: "
              "%s" % words)
    stream = None
    if tool == "claude":
        cmd = [claude_bin(), "-p", prompt, "--output-format", "json", "--strict-mcp-config",
               "--permission-mode", "acceptEdits", "--allowedTools"] + TOOLS
        if model:
            cmd += ["--model", model]
    else:
        agent = os.path.join(proj, ".agents", "agents", "naturalness-reviewer.md")
        if os.path.exists(agent):           # Pro may not be on the account: default inherit
            s = io.open(agent, encoding="utf-8").read()
            io.open(agent, "w", encoding="utf-8", newline="\n").write(re.sub(
                r"^model:.*$", "model: %s" % opt("--reviewer-model", "inherit"), s, flags=re.M))
        cmd = [agy_bin(), "-p", prompt, "--output-format", "stream-json", "--model", model,
               "--mode", "accept-edits", "--dangerously-skip-permissions",
               "--settings", json.dumps(AGY_GUARD)]
    print("running %s with %s in %s (fake services at %s) ..." % (label, tool, proj, url))
    t0 = time.time()
    try:
        out = subprocess.run(cmd, cwd=proj, env=env, capture_output=True, text=True,
                             encoding="utf-8", timeout=3600)
    finally:
        fake.stop()
    wall = round(time.time() - t0)
    if tool == "claude":
        try:
            res = json.loads(out.stdout)
        except ValueError:
            res = {"is_error": True, "result": (out.stdout or out.stderr)[-500:]}
        tpath = transcript(res.get("session_id", "")) if res.get("session_id") else None
        reviews, replies = reviewer_calls(tpath) if tpath else ([], [])
        run_metrics = run_usage(tpath) if tpath else {}
    else:
        stream = out.stdout
        res, reviews, run_metrics = agy_parse(stream)
        if not res.get("result") and out.stderr:
            res["result"] = out.stderr[-500:]
    checks, notes, metrics, examples = grade(proj, cases, fake, original_profile, reviews,
                                             leak_words=tool == "claude")
    checks["run: finished without error"] = not res.get("is_error") and out.returncode == 0
    if tool == "claude" and has_pick_contract(proj):
        picks = len(re.findall(r"(?m)^\s*\**PICK\s+\d+", replies[0])) if replies else 0
        checks["review: names a PICK for every card"] = picks >= metrics["cards"] > 0
    metrics.update(run_metrics)
    metrics.update(wall_seconds=wall, cost_usd=res.get("total_cost_usd"))
    learner = spec["learner"]
    j = None if "--no-judge" in args else judge(examples, learner, "sonnet", fit=bool(level))

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"[^\w.-]+", "_", label)
    keep = os.path.join(RUNS, run_id)
    os.makedirs(keep, exist_ok=True)
    data = dirs(proj)[1]
    for p in glob.glob(os.path.join(data, "*.csv")) + \
            glob.glob(os.path.join(data, "*_meta.json")) + \
            glob.glob(os.path.join(data, "logs", "*-batch.md")):
        shutil.copy(p, keep)
    io.open(os.path.join(keep, "final-message.md"), "w", encoding="utf-8").write(
        res.get("result") or "")
    if stream:
        io.open(os.path.join(keep, "stream.jsonl"), "w", encoding="utf-8").write(stream)
    record = {"id": run_id, "label": label, "ref": ref or "worktree", "mode": mode,
              "learner": level or None, "cases": opt("--cases", "cases.json"),
              "tool": tool, "model": model, "words": [c["input"] for c in cases],
              "passed": sum(checks.values()), "total": len(checks), "checks": checks,
              "notes": notes, "metrics": metrics, "judge": j, "kept": rel(keep)}
    prev = last_result(opt("--against"))
    with io.open(RESULTS, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    report(record, prev)
    shutil.rmtree(proj, ignore_errors=True)
    return 0 if record["passed"] == record["total"] else 1


def last_result(label=None):
    """The run to compare with: the latest one, or the latest with that label."""
    if not os.path.exists(RESULTS):
        return None
    runs = [json.loads(l) for l in io.open(RESULTS, encoding="utf-8").read().splitlines()
            if l.strip()]
    runs = [r for r in runs if not label or r["label"] == label]
    return runs[-1] if runs else None


def fmt(v):
    if isinstance(v, float):
        return "%.2f" % v
    if isinstance(v, int) and v >= 100000:
        return "%.2fM" % (v / 1e6)
    return str(v)


def report(r, prev):
    print("\n%s: %d/%d checks passed" % (r["label"], r["passed"], r["total"]))
    for k, v in r["checks"].items():
        was = prev["checks"].get(k) if prev else None
        tag = "ok  " if v else ("FAIL" if was is not True else "REGRESSION")
        if not v or was is False:
            print("  %-10s %s%s" % (tag, k, "   (fixed since last run)" if v and was is False else ""))
    for n in r["notes"]:
        print("  note: %s" % n)
    keys = ["cards", "examples", "gram_tags_per_card", "requests", "tool_calls", "input_tokens", "output_tokens",
            "peak_context", "subagents", "reviewer_calls", "core_outside_4_11_words",
            "wall_seconds", "cost_usd"]
    head = "\n  %-24s %14s" % ("metric", r["label"][:14])
    if prev:
        head += " %14s %8s" % (prev["label"][:14], "change")
    print(head)
    for k in keys:
        a = r["metrics"].get(k)
        line = "  %-24s %14s" % (k, fmt(a) if a is not None else "-")
        if prev:
            b = prev["metrics"].get(k)
            ch = ("%+.0f%%" % (100.0 * (a - b) / b)) if isinstance(a, (int, float)) and \
                isinstance(b, (int, float)) and b else ""
            line += " %14s %8s" % (fmt(b) if b is not None else "-", ch)
        print(line)
    j, pj = r.get("judge") or {}, (prev or {}).get("judge") or {}
    if j:
        print("  %-24s %14s%s" % ("judge mean (1-5)", j.get("mean", j.get("error")),
                                  " %14s" % pj.get("mean", "-") if prev else ""))
        print("  %-24s %14s%s" % ("judge sentences < 4", j.get("below_4", "-"),
                                  " %14s" % pj.get("below_4", "-") if prev else ""))
        for low in j.get("low", []):
            print("  judge low: %s" % low)
        if "fit_mean" in j:
            print("  %-24s %14s" % ("level fit (1-5)", j["fit_mean"]))
            for low in j.get("fit_low", []):
                print("  level fit low: %s" % low)
    print("\n  cards and log kept in %s" % r["kept"])


def cmd_history():
    if not os.path.exists(RESULTS):
        print("no runs yet")
        return 0
    print("%-34s %7s %9s %9s %8s %7s %6s" % ("run", "checks", "requests", "input", "output",
                                           "judge", "cost$"))
    for l in io.open(RESULTS, encoding="utf-8").read().splitlines():
        r = json.loads(l)
        m, j = r["metrics"], r.get("judge") or {}
        print("%-34s %3d/%-3d %9s %9s %8s %7s %6s" % (
            r["id"][:34], r["passed"], r["total"], m.get("requests") or "-",
            fmt(m.get("input_tokens") or 0), fmt(m.get("output_tokens") or 0),
            j.get("mean", "-"), fmt(m.get("cost_usd")) if m.get("cost_usd") else "-"))
    return 0


def cmd_snapshot():
    from ankilib import ac, notes_info
    decks = ac("deckNames")
    deck_of = {}
    for d in decks:
        for n in ac("findNotes", query='deck:"%s"' % d.replace('"', '\\"')):
            deck_of[n] = d
    notes = []
    for n in notes_info(ac("findNotes", query="deck:*")):
        notes.append({"nid": n["noteId"], "deck": deck_of.get(n["noteId"], "Default"),
                      "model": n.get("modelName"), "tags": n.get("tags", []),
                      "fields": {k: re.sub(r"\[sound:[^\]]*\]", "", v["value"])
                                 for k, v in sorted(n["fields"].items(),
                                                    key=lambda kv: kv[1]["order"])}})
    models = {m: ac("modelFieldNames", modelName=m) for m in {n["model"] for n in notes}}
    os.makedirs(os.path.dirname(COLLECTION), exist_ok=True)
    json.dump({"taken": time.strftime("%Y-%m-%d"), "decks": decks, "models": models,
               "notes": notes}, io.open(COLLECTION, "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    print("snapshot: %d notes, %d decks -> %s" % (len(notes), len(decks), rel(COLLECTION)))
    return 0


def main(argv):
    cmd = argv[0] if argv else ""
    if cmd == "unit":
        return subprocess.call([sys.executable, os.path.join(EVALS, "unit.py")] + argv[1:])
    if cmd == "skill":
        return cmd_skill(argv[1:])
    if cmd == "history":
        return cmd_history()
    if cmd == "snapshot":
        return cmd_snapshot()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
