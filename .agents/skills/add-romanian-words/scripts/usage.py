#!/usr/bin/env python3
"""Token usage of a card batch, read from the AI tool's own session record.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/usage.py [--session ID] [--since ISO-TIME]
    runtime/python/python .agents/skills/add-romanian-words/scripts/usage.py history

The first form prints the current session's totals: model requests ("turns"),
input tokens split into uncached / cache-read / cache-write, output tokens, the
context size at the first and the largest request, and the reviewer subagents.

`history` summarizes my-cards/logs/usage.jsonl -- one row per committed batch,
appended by `batch.py commit` (record()). A second batch in the same session
is recorded as the DELTA since the previous record, so per-batch numbers stay
honest even when a session runs several batches -- which it should not: input
tokens are roughly requests x context size, and a long session re-sends its
whole history on every request (2026-10-25: three batches, context 52k -> 233k).

Claude Code only, for now: its transcripts live in
~/.claude/projects/<project path with every non-alphanumeric as ->/<session>.jsonl,
subagents under <session>/subagents/. The session id comes from
CLAUDE_CODE_SESSION_ID. Another tool, or no transcript, records the batch with
tokens null -- the batch counts are still useful.
"""
import io, os, re, sys, glob, json, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ROOT, LOGS, rel

USAGE_LOG = os.path.join(LOGS, "usage.jsonl")
KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
        "output_tokens")


def transcript_dir():
    base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
    want = re.sub(r"[^A-Za-z0-9]", "-", ROOT).lower()
    if os.path.isdir(base):
        for d in os.listdir(base):
            if d.lower() == want:
                return os.path.join(base, d)
    return None


def session_id():
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if sid:
        return sid
    d = transcript_dir()
    files = sorted(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime) if d else []
    return os.path.basename(files[-1])[:-6] if files else None


def scan(path, since=None):
    """-> per-request usage dicts (deduplicated by message id; a streamed
    message is logged several times, the last copy carries the final count),
    plus the models and the number of tool calls."""
    by, models, tools = {}, set(), 0
    for line in io.open(path, encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        m = d.get("message")
        if d.get("type") != "assistant" or not isinstance(m, dict) or not m.get("usage"):
            continue
        if since and (d.get("timestamp") or "") <= since:
            continue
        mid = m.get("id") or d.get("uuid")
        prev = by.get(mid)
        if prev is None or (m["usage"].get("output_tokens") or 0) >= (prev.get("output_tokens") or 0):
            by[mid] = m["usage"]
        if m.get("model"):
            models.add(m["model"])
        tools += sum(1 for c in m.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "tool_use")
    return list(by.values()), models, tools


def totals(usages):
    t = {k: sum(u.get(k) or 0 for u in usages) for k in KEYS}
    ctx = [sum(u.get(k) or 0 for k in KEYS[:3]) for u in usages]
    t.update(turns=len(usages), first_context=ctx[0] if ctx else 0,
             peak_context=max(ctx) if ctx else 0)
    return t


def session_usage(sid, since=None):
    """-> dict, or None when there is no transcript for this session."""
    d = transcript_dir()
    path = os.path.join(d, sid + ".jsonl") if d and sid else None
    if not path or not os.path.exists(path):
        return None
    usages, models, tools = scan(path, since)
    out = totals(usages)
    out.update(session=sid, models=sorted(models), tool_calls=tools)
    subs = []
    for p in sorted(glob.glob(os.path.join(d, sid, "subagents", "*.jsonl"))):
        u, m, _ = scan(p, since)
        if u:
            subs.append(dict(totals(u), models=sorted(m)))
    out["subagents"] = {
        "count": len(subs),
        "turns": sum(s["turns"] for s in subs),
        "input": sum(s["input_tokens"] + s["cache_read_input_tokens"] +
                     s["cache_creation_input_tokens"] for s in subs),
        "output": sum(s["output_tokens"] for s in subs),
        "models": sorted({m for s in subs for m in s["models"]}),
    }
    return out


def input_total(u):
    return u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]


def describe(u):
    if not u:
        return "tokens: not available (no Claude Code transcript for this session)"
    s = u["subagents"]
    return ("%d requests, %d tool calls, input %.2fM (%.0f%% cache reads), output %dk, "
            "context %dk -> %dk; reviewers: %d run(s), %d requests, %dk in / %dk out; model %s"
            % (u["turns"], u["tool_calls"], input_total(u) / 1e6,
               100.0 * u["cache_read_input_tokens"] / max(1, input_total(u)),
               u["output_tokens"] // 1000, u["first_context"] // 1000,
               u["peak_context"] // 1000, s["count"], s["turns"], s["input"] // 1000,
               s["output"] // 1000, ", ".join(u["models"] + [m for m in s["models"]
                                                              if m not in u["models"]]) or "?"))


def last_record(sid):
    if not sid or not os.path.exists(USAGE_LOG):
        return None
    last = None
    for line in io.open(USAGE_LOG, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("session") == sid:
            last = r
    return last


def record(row):
    """Append one batch to usage.jsonl with this session's usage since its
    previous record. -> (the row as written, one-line description)"""
    sid = session_id()
    prev = last_record(sid)
    u = session_usage(sid, prev["ts_iso"] if prev else None) if sid else None
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    row = dict(row, ts_iso=now + ".999Z", session=sid, tool="claude-code" if u else None,
               earlier_batches_in_session=bool(prev))
    if u:
        cards = max(1, row.get("cards") or 0)
        row.update(requests=u["turns"], tool_calls=u["tool_calls"],
                   tokens_in=input_total(u), tokens_cache_read=u["cache_read_input_tokens"],
                   tokens_out=u["output_tokens"], peak_context=u["peak_context"],
                   models=u["models"], reviewer=u["subagents"],
                   tokens_in_per_card=input_total(u) // cards)
    os.makedirs(LOGS, exist_ok=True)
    with io.open(USAGE_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row, describe(u)


def cmd_history():
    if not os.path.exists(USAGE_LOG):
        print("no batches recorded yet (%s)" % rel(USAGE_LOG))
        return 0
    print("%-10s %-8s %5s %8s %9s %8s %8s %8s" % ("date", "mode", "cards", "requests",
                                                 "input", "per card", "output", "peak ctx"))
    for line in io.open(USAGE_LOG, encoding="utf-8"):
        r = json.loads(line)
        if r.get("tokens_in") is None:
            print("%-10s %-8s %5s   (no token data)" % (r.get("date"), r.get("mode"), r.get("cards")))
            continue
        print("%-10s %-8s %5d %8d %8.2fM %7dk %7dk %7dk" % (
            r.get("date"), r.get("mode"), r.get("cards") or 0, r["requests"],
            r["tokens_in"] / 1e6, r["tokens_in_per_card"] // 1000, r["tokens_out"] // 1000,
            r["peak_context"] // 1000))
    return 0


def main(argv):
    if argv[:1] == ["history"]:
        return cmd_history()
    sid = argv[argv.index("--session") + 1] if "--session" in argv[:-1] else session_id()
    since = argv[argv.index("--since") + 1] if "--since" in argv[:-1] else None
    u = session_usage(sid, since) if sid else None
    print("session %s" % (sid or "?"))
    print(describe(u))
    return 0 if u else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
