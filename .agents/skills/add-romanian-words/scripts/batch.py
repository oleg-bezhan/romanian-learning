#!/usr/bin/env python3
"""A whole batch in two calls: everything before writing, everything after.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/batch.py prep <word> ... [--no-policies]
    runtime/python/python .agents/skills/add-romanian-words/scripts/batch.py commit <batch.json> [--approved] [--dry-run]

Why: input tokens are roughly model requests x context size, and a batch used
to take 16-20 requests -- preflight, dedup, one DEX call per word, the queue,
then CSV, manifest, validate, audio, import, counters and the log, each its own
request. prep and commit fold those into one request each. The underlying
scripts are unchanged and still callable one by one for repairs.

prep    Read-only. Prints, in order: PREFLIGHT (stops with exit 2 if Anki,
        AnkiConnect or .env is not ready), BATCH (today's file, the token
        mode), DEDUP (one live verdict per word, plus words that collide with
        each other), DEX (senses + headword stress for every word that is not
        a duplicate), QUEUE (the grammar schedule for that many cards), and
        the texts the card writer needs every batch: LEARNER (learner.md) and
        POLICIES (policies.md; --no-policies skips them on a re-run).

commit  Takes the finished batch as one JSON file, written with the file tool
        (shell heredocs break on quotes):
            {"kind": "import",                       optional; "grammar" for drills
             "cards": [{"front": "<b>…</b><br><br><i>…</i>",
                        "back":  "<b>…</b><br><br><i>…</i>",
                        "gram": "<tag of the core example>",
                        "pos": "verb|noun|adjective|adverb|phrase|other",
                        "scheduled": "<tag scheduled for this card in step 3>",
                        "stretch": ["<primary tag of each stretch example>"],
                        "also": ["<every other listed construction realized in
                                  any example>"],  quality mode (see MODE)
                        "inherent": ["<grammar the target itself always
                                      carries>"],  tagged, never counted
                        "new_sense": true}],          optional, see anki_add.py
             "log": "<markdown: per card -- input, sense, scheduled vs realized,
                     every candidate with its score, rejection reasons, novelty;
                     then batch totals only the writer knows>"}
        Checks everything first and writes nothing on a refusal (exit 2).
        Spend gate: a batch billing TTS_CONFIRM_CHARS or more prints a preview
        and exits 3 -- show it to the learner, and re-run with --approved once
        they agree. Then: append to today's CSV, merge the tag manifest,
        validate, synthesize audio, import (backup, read-back, sync), update
        the grammar counters, append the batch log (the writer's text plus the
        audio, import, counter and cost lines), record usage.
        Idempotent: re-running the same command after a failure resumes it
        (exit 1 means "fix what it says, then re-run").
        --dry-run stops after the checks and the spend estimate.
"""
import io, os, re, sys, json, time, hashlib, tempfile
from collections import Counter
from contextlib import redirect_stdout

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from config import ROOT, DATA_DIR, LOGS, POLICIES, LEARNER, rel
from ankilib import ac, preflight, die, Unreachable
from known import live_load, verdict, load, normalize, plain, nfc
import anki_add
import profile
import tts
import usage
import validate

CARD_KEYS = {"front", "back", "gram", "pos", "scheduled", "stretch", "also", "inherent",
             "new_sense"}
TOP_KEYS = {"kind", "cards", "log"}


def section(name):
    print("\n== %s" % name)


def today():
    return time.strftime("%Y-%m-%d")


def csv_path(kind, date=None):
    return os.path.join(DATA_DIR, "%s_%s.csv" % (date or today(), kind))


def batches_in_log(date):
    path = os.path.join(LOGS, "%s-batch.md" % date)
    if not os.path.exists(path):
        return 0
    return sum(1 for l in io.open(path, encoding="utf-8") if l.startswith("# Batch"))


# --------------------------------------------------------------------------
# prep
# --------------------------------------------------------------------------

def cmd_prep(words, policies):
    learner = config.learner()
    section("PREFLIGHT")
    try:
        preflight(anki_add.ACTIONS_USED, writing=False)
        deck, model = anki_add.target()
    except Unreachable as e:
        print("ERROR: %s\nNOT READY" % e)
        return 2
    missing = [k for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
               if not os.environ.get(k, "").strip()]
    media = os.environ.get("ANKI_MEDIA_DIR", "").strip()
    if media and not os.path.isdir(media):
        missing.append("ANKI_MEDIA_DIR (folder not found)")
    if not learner["_exists"]:
        missing.append("learner.md (run the setup skill)")
    if missing:
        print("ERROR: not set in .env / missing: %s\nNOT READY" % ", ".join(missing))
        return 2
    reviewing = " -- WARNING: the Anki reviewer is open; the import will refuse until it is closed" \
        if ac("guiReviewActive") else ""
    print("READY  deck %r, note type %r, audio settings present%s" % (deck, model, reviewing))

    if not words:
        print("\nno words given -- nothing to prepare.")
        return 0

    section("BATCH")
    n = batches_in_log(today())
    print("date %s; batch %d of the day; cards go to %s%s"
          % (today(), n + 1, rel(csv_path("import")),
             "" if n == 0 else " (appending to the morning's file)"))
    print(config.mode_line(learner["mode"]))
    prev = usage.last_record(usage.session_id())
    if prev:
        print("NOTE: this conversation already committed a batch (%s). Every request re-sends\n"
              "the whole conversation, so tell the learner a fresh conversation for the next\n"
              "batch costs much less." % prev.get("date"))

    section("DEDUP")
    seen, survivors = {}, []
    for w in words:
        k = normalize(w)
        if k in seen:
            print("%-32s SAME HEADWORD as %r in this input -- one card" % (w, seen[k]))
            continue
        seen[k] = w
    live, files = live_load(), load()[0]
    for w in seen.values():
        kind, line = verdict(w, live, files)
        print(line)
        if kind != "DUPLICATE":
            survivors.append(w)
    print("(live: %d notes in the collection; %d of %d words to research)"
          % (live["notes"], len(survivors), len(seen)))

    import dex
    section("DEX")
    for w in survivors:
        key = normalize(w)
        print("-- %s" % w)
        try:
            dex.headword_and_senses(key)
        except Exception as e:
            print("HEADWORD (fetch failed: %s -- %s)" % (
                e, "a phrase often has no entry of its own: look up its key word with dex.py"
                if " " in key else "retry with dex.py, then WebFetch as a fallback"))

    section("QUEUE (%d cards)" % len(survivors))
    print("\n".join(profile.queue_lines(config.PROFILE, len(survivors))))

    if policies:
        for name, path in (("LEARNER", LEARNER), ("POLICIES", POLICIES)):
            section("%s (%s)" % (name, rel(path)))
            text = io.open(path, encoding="utf-8").read().strip()
            print(text.split("\n", 1)[1].strip() if text.startswith("# ") else text)
    return 0


# --------------------------------------------------------------------------
# commit
# --------------------------------------------------------------------------

def strip_sound(s):
    return tts.SOUND_RE.sub("", s)


def check_batch(b):
    """-> (kind, cards, problems). Pure: reads the profile, writes nothing."""
    p = []
    if not isinstance(b, dict):
        return None, [], ["the batch file must hold a JSON object"]
    extra = set(b) - TOP_KEYS
    if extra:
        p.append("unknown top-level field(s): %s" % ", ".join(sorted(extra)))
    kind = b.get("kind", "import")
    if kind not in ("import", "grammar"):
        p.append('kind must be "import" or "grammar"')
    cards = b.get("cards")
    if not isinstance(cards, list) or not cards:
        return kind, [], p + ["cards must be a non-empty list"]
    if not isinstance(b.get("log"), str) or len(b["log"].strip()) < 20:
        p.append("log is missing -- the batch log is the provenance record, write it")
    grams = anki_add.gram_tags()
    keys = {}
    for i, c in enumerate(cards, 1):
        if not isinstance(c, dict):
            p.append("card %d: must be an object" % i)
            continue
        where = "card %d" % i
        extra = set(c) - CARD_KEYS
        if extra:
            p.append("%s: unknown field(s) %s" % (where, ", ".join(sorted(extra))))
        for f in ("front", "back"):
            v = c.get(f)
            if not isinstance(v, str) or not v.strip():
                p.append("%s: %s is empty" % (where, f))
            elif "[sound:" in v:
                p.append("%s: never type a [sound:] tag -- the audio step adds them" % where)
            elif '";"' in v or "\n" in v:
                p.append("%s: %s contains a line break or the field separator" % (where, f))
        back = c.get("back") or ""
        key = normalize(back) if isinstance(back, str) else ""
        if not key:
            p.append("%s: the Romanian headword normalizes to nothing" % where)
        elif key in keys:
            p.append("%s: same headword as card %d (%s)" % (where, keys[key], key))
        keys[key] = i
        where = "card %d (%s)" % (i, key)
        entry = manifest_entry(c)
        for k in ("also", "inherent"):
            if k in c and not isinstance(c[k], list):
                p.append("%s: %s must be a list of tags" % (where, k))
        p += ["%s: %s" % (where, e.split(": ", 1)[-1])
              for e in anki_add.check_entry(key, entry, grams, kind == "grammar")]
        if kind == "import":
            examples = back.count("<i>")
            stretch = c.get("stretch", [])
            if not isinstance(stretch, list):
                p.append("%s: stretch must be a list of tags" % where)
                stretch = []
            if len(stretch) != max(0, examples - 1):
                p.append("%s: %d example(s) need %d stretch tag(s), got %d -- one primary "
                         "construction per stretch example" % (where, examples,
                                                               max(0, examples - 1), len(stretch)))
            for t in [c.get("scheduled")] + stretch:
                if t not in grams:
                    p.append("%s: %r is not a Tag in grammar-profile.md" % (where, t))
                elif grams[t] == "blocked":
                    p.append("%s: %r is a BLOCKED construction" % (where, t))
    return kind, cards, p


def manifest_entry(c):
    """The card's tag manifest entry. Every stretch example's construction and
    every `also` one becomes a gram:: tag beside the core's; the counters still
    count only the core and stretch ones (commit, below)."""
    e = {k: c[k] for k in ("gram", "pos", "new_sense") if k in c}
    lists = [c.get(k) if isinstance(c.get(k), list) else []
             for k in ("stretch", "also", "inherent")]
    also = [t for t in lists[0] + lists[1] + lists[2] if t != c.get("gram")]
    if also:
        e["also"] = list(dict.fromkeys(also))
    return e


def batch_id(cards):
    canon = json.dumps([[nfc(c["front"]), nfc(c["back"])] for c in cards], ensure_ascii=False)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


def existing_rows(path):
    """-> {key: (translation, romanian without sound tags)} of today's file"""
    out = {}
    if os.path.exists(path):
        for line in io.open(path, encoding="utf-8").read().split("\n"):
            row = tts.split_row(line) if line.strip() else None
            if row:
                out[normalize(row[1])] = (nfc(row[0]), nfc(strip_sound(row[1])))
    return out


def text_problems(rows, numbers):
    """validate.py's text rules on the new rows alone, before they touch
    today's file. numbers[i] is row i's card number. -> problem lines"""
    d = tempfile.mkdtemp()
    tmp = os.path.join(d, "%s_check.csv" % today())
    try:
        io.open(tmp, "w", encoding="utf-8", newline="\n").write("\n".join(rows) + "\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = validate.main([tmp, "--text"])
        out = []
        for l in buf.getvalue().splitlines() if rc else []:
            m = re.match(r"line (\d+): (.*)", l)
            if m:
                out.append("card %d: %s" % (numbers[int(m.group(1)) - 1], m.group(2)))
        return out
    finally:
        os.remove(tmp)
        os.rmdir(d)


def journal(date, event):
    os.makedirs(LOGS, exist_ok=True)
    with io.open(os.path.join(LOGS, "import-%s.jsonl" % date), "a", encoding="utf-8",
                 newline="\n") as j:
        j.write(json.dumps(dict(ts=round(time.time(), 3), **event), ensure_ascii=False) + "\n")
        j.flush()
        os.fsync(j.fileno())


def journaled(date, event, bid):
    path = os.path.join(LOGS, "import-%s.jsonl" % date)
    if not os.path.exists(path):
        return False
    for line in io.open(path, encoding="utf-8"):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == event and r.get("batch") == bid:
            return True
    return False


def preview(cards, est):
    print("\nSPEND GATE -- this batch bills %d characters (approval needed at %d)."
          % (est["chars"], config.tts_confirm_chars()))
    print("Show the learner this table and wait for one approval; it covers the card")
    print("text, the audio cost and the import. Then re-run with --approved.\n")
    print("| # | Front | Back | gram | pos |\n|---|---|---|---|---|")
    for i, c in enumerate(cards, 1):
        print("| %d | %s | %s | %s | %s |" % (i, plain(c["front"]), plain(c["back"]),
                                            c["gram"], c["pos"]))
    print("\n%d clips to synthesize, %d cached; ~%d ElevenLabs credits"
          % (est["billable"], est["cached"], round(est["chars"] * 0.375)))


def write_log(date, bid, n, text, lines):
    path = os.path.join(LOGS, "%s-batch.md" % date)
    marker = "<!-- batch-id: %s -->" % bid
    old = io.open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if marker in old:
        return False
    head = "# Batch log %s" % date if not old else "\n# Batch %d" % n
    body = "%s\n%s\n\n%s\n\n## Recorded by batch.py\n%s\n" % (
        head, marker, text.strip(), "\n".join("- %s" % l for l in lines))
    os.makedirs(LOGS, exist_ok=True)
    with io.open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return True


def cmd_commit(path, approved, dry):
    try:
        b = json.load(io.open(path, encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        die("cannot read %s: %s" % (path, e))
    kind, cards, problems = check_batch(b)
    # All problems in one report, so one repair round fixes them all -- the
    # checks below only need cards whose two sides are strings.
    if not cards or any(not isinstance(c, dict) or not isinstance(c.get("front"), str)
                        or not isinstance(c.get("back"), str) for c in cards):
        for p in problems:
            print("REFUSED  %s" % p)
        print("\n%d problem(s) -- nothing written. Fix the batch file and re-run." % len(problems))
        return 2
    date, csv = today(), csv_path(kind if kind == "grammar" else "import")
    bid = batch_id(cards)
    learner = config.learner()

    have = existing_rows(csv)
    new, resumed = [], 0
    for c in cards:
        key = normalize(c["back"])
        if key in have:
            if have[key] == (nfc(c["front"]), nfc(c["back"])):
                resumed += 1
                continue
            problems.append("%r is already in today's file with different text -- to replace a "
                            "card see .agents/skills/add-romanian-words/reference/corrections.md" % key)
        new.append(c)
    rows = ['"%s";"%s"' % (c["front"], c["back"]) for c in new]
    problems += text_problems(rows, [cards.index(c) + 1 for c in new]) if rows else []
    live = live_load()
    for c in new:
        key = normalize(c["back"])
        if key in live["head"] and not c.get("new_sense"):
            problems.append('%r is already in Anki: nid %d "%s" -- drop the card, or mark '
                            '"new_sense": true if it is a clearly different meaning'
                            % (key, live["head"][key], live["title"].get(live["head"][key], "")[:40]))
    if problems:
        for p in problems:
            print("REFUSED  %s" % p)
        print("\n%d problem(s) -- nothing written. Fix the batch file and re-run." % len(problems))
        return 2

    est = None
    if kind == "import":
        est, lines = tts.estimate([c["back"] for c in cards])
        print(lines[-1])
        if est["needs_approval"] and not approved:
            preview(cards, est)
            return 3
    if dry:
        print("DRY RUN ok -- %d new row(s), %d already in today's file; nothing written."
              % (len(new), resumed))
        return 0

    # ---- write: every step below is idempotent, so a re-run resumes -------
    if rows:
        os.makedirs(DATA_DIR, exist_ok=True)
        lead = ""
        if os.path.exists(csv):
            text = io.open(csv, encoding="utf-8").read()
            lead = "\n" if text and not text.endswith("\n") else ""
        with io.open(csv, "a", encoding="utf-8", newline="\n") as f:
            f.write(lead + "\n".join(rows) + "\n")
    entries = {}
    for c in cards:
        head = re.search(r"<b>(.*?)</b>", c["back"], re.S)
        entries[head.group(1) if head else c["back"]] = manifest_entry(c)
    if anki_add.merge_meta(csv, entries) != 0:
        return 1

    audio = None
    if kind == "import":
        rc, audio = tts.run_add(csv, dry=False, quiet=True)
        if rc != 0:
            print("\nSTOPPED at the audio step -- fix what it says, then re-run this command.")
            return 1
    rc, res = anki_add.run_add(csv, commit=True, force=False, quiet=True)
    if rc != 0:
        print("\nSTOPPED at the import step -- re-run this command to resume.")
        return 1

    counter_lines = []
    if kind == "import":
        if journaled(date, "counters", bid):
            counter_lines.append("counters: already updated by an earlier run of this batch")
        else:
            sched = sorted({c["scheduled"] for c in cards})
            # Built-in grammar is tagged but not counted: the target word carries it
            # whatever was scheduled, so it is no evidence of practice.
            bump = Counter(t for c in cards for t in [c["gram"]] + c.get("stretch", [])
                           if t not in (c.get("inherent") or []))
            for mode, args in (("sched", sched),
                               ("bump", ["%s:%d" % kv for kv in sorted(bump.items())])):
                rc, out = profile.update(config.PROFILE, mode, date, args)
                if rc != 0:
                    print("\n".join(out))
                    print("\nSTOPPED at the counters -- the cards are in Anki; fix the profile "
                          "and run profile.py %s by hand." % mode)
                    return 1
            journal(date, {"event": "counters", "batch": bid, "sched": sched,
                           "bump": dict(bump)})
            built_in = sorted({t for c in cards for t in c.get("inherent") or []})
            counter_lines.append("counters: scheduled %s; realized %s%s" % (
                ", ".join(sched), ", ".join("%s +%d" % kv for kv in sorted(bump.items())),
                "; built-in, not counted: %s" % ", ".join(built_in) if built_in else ""))

    n = batches_in_log(date) + 1
    examples = sum(c["back"].count("<i>") for c in cards)
    lines = ["Committed %d card(s), %d example(s), kind %s, mode %s."
             % (len(cards), examples, kind, learner["mode"])] + counter_lines
    if audio:
        lines.append("Audio: voice from .env ELEVENLABS_VOICE_ID, model %s; %d clips generated, "
                     "%d cached; %d characters billed."
                     % (audio["model_id"], audio["new"], audio["cached"], audio["chars"]))
    lines.append("Import: %s created%s; %d already in Anki. Backup: %s. Sync: %s. Journal: %s."
                 % ("notes " + ", ".join(map(str, res["created"])) if res["created"] else "no notes",
                    "" if not res["dups"] else ", %d duplicate(s) skipped" % res["dups"],
                    res["present"], res["backup"] or "not needed", res["sync"],
                    res["journal"] or "unchanged"))
    if not journaled(date, "usage", bid):
        _, cost = usage.record({"date": date, "batch": n, "batch_id": bid, "kind": kind,
                                "mode": learner["mode"], "cards": len(cards),
                                "examples": examples,
                                "tts_chars": audio["chars"] if audio else 0})
        journal(date, {"event": "usage", "batch": bid})
        lines.append("Cost (this batch, up to the commit): %s." % cost)
    logged = write_log(date, bid, n, b["log"], lines)

    print("\nCOMMITTED batch %d of %s" % (n if logged else n - 1, date))
    for l in lines:
        print("  " + l)
    print("  Log: %s%s" % (rel(os.path.join(LOGS, "%s-batch.md" % date)),
                           "" if logged else " (already written by an earlier run)"))
    return 0


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "prep":
        words = [a for a in rest if not a.startswith("--")]
        return cmd_prep(words, "--no-policies" not in rest)
    if cmd == "commit":
        files = [a for a in rest if not a.startswith("--")]
        if len(files) == 1:
            return cmd_commit(files[0], "--approved" in rest, "--dry-run" in rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Unreachable as e:
        die(str(e))
