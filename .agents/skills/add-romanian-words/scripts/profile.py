#!/usr/bin/env python3
"""Read and update grammar-profile.md -- the grammar scheduler's state.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/profile.py queue [--cards N]
    runtime/python/python .agents/skills/add-romanian-words/scripts/profile.py sched --date YYYY-MM-DD <tag|name> ...
    runtime/python/python .agents/skills/add-romanian-words/scripts/profile.py bump  --date YYYY-MM-DD "<tag|name>:N" ...
    runtime/python/python .agents/skills/add-romanian-words/scripts/profile.py init --from answers.json [--force]
    add --file <path> to any command to work on a copy (tests)

queue  the scheduling order, so dates are never compared by hand. Allowed AND
       learning rows by the later of Last used / Last scheduled, oldest first
       ("never" first of all); learning rows break ties on fewer uses. Then the
       batch's learning cap (1 per 20 cards, rounded up) and the blocked rows.
       (Learning was least-used first until 2026-10-30, which let a 0-use row
       that kept failing take every slot.)
sched  records that a construction was SCHEDULED on that date, whether or not
       it survived review. Without this, a construction the naturalness gate
       keeps rejecting never ages and stays first in the queue forever -- the
       literary future did exactly that for six weeks.
bump   counts constructions actually REALIZED: Uses += N, Last used = date.
init   writes a NEW learner's profile from templates/grammar-profile.seed.md
       (counters empty) with the statuses chosen in the setup interview:
           {"<tag>": "allowed" | "learning" | "blocked", ...}
       Tags left out keep the seed's status. Refuses to overwrite an existing
       profile unless --force, and then keeps the old one as a .bak copy.
set_statuses() (no command; the setup page calls it) changes only the Status
       cells of an existing profile and keeps every counter.

Rows are matched by Tag (the gram:: slug) or by exact Construction name. Any
unmatched argument aborts with exit 1 and the file untouched.

load_rows() is imported by anki_add.py: the Tag column is the single source of
truth for gram:: tags.
"""
import io, os, re, sys, json, math, time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PROFILE, TEMPLATES

SEED = os.path.join(TEMPLATES, "grammar-profile.seed.md")
ROW = re.compile(r"^\| (.+?) \| ([a-z0-9-]+) \| (.*?) \| (allowed|learning|blocked) "
                 r"\| (.*?) \| (.*?) \| (.*?) \|$")
FIELDS = ("construction", "tag", "example", "status", "last_used", "last_sched", "uses")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse(line):
    m = ROW.match(line)
    return dict(zip(FIELDS, (g.strip() for g in m.groups()))) if m else None


def render(r):
    return "| %s |" % " | ".join(r[f] for f in FIELDS)


def when(s):
    return s if DATE.match(s) else ""       # "–" = never, sorts before any date


def uses(r):
    return int(r["uses"]) if r["uses"].isdigit() else 0


def activity(r):
    return max(when(r["last_used"]), when(r["last_sched"]))


def load_rows(path=None):
    path = path or PROFILE
    rows = [r for r in (parse(l) for l in io.open(path, encoding="utf-8").read().split("\n")) if r]
    seen = set()
    for r in rows:
        if r["tag"] in seen:
            raise ValueError("duplicate Tag %r in %s" % (r["tag"], path))
        seen.add(r["tag"])
    if not rows:
        raise ValueError("no construction rows parsed from %s" % path)
    return rows


def queue_lines(path, cards):
    rows = load_rows(path)
    allowed = sorted((r for r in rows if r["status"] == "allowed"), key=activity)
    learning = sorted((r for r in rows if r["status"] == "learning"),
                      key=lambda r: (activity(r), uses(r)))
    blocked = [r for r in rows if r["status"] == "blocked"]
    out = ["ALLOWED -- in order; each card takes the first one that fits it naturally,",
           "and a construction once picked moves to the back for the rest of the batch:"]
    for i, r in enumerate(allowed, 1):
        out.append("  %2d. %-21s %-52s last %-10s uses %d"
                   % (i, r["tag"], r["construction"][:52], activity(r) or "never", uses(r)))
    cap = "%d" % math.ceil(cards / 20) if cards else "1 per 20 cards (rounded up)"
    out.append("\nLEARNING -- at most %s example(s) this batch across ALL of these;" % cap)
    out.append("tried longest ago first, and only where it fits naturally:")
    for r in learning:
        out.append("      %-21s %-52s uses %d  last %s"
                   % (r["tag"], r["construction"][:52], uses(r), activity(r) or "never"))
    out.append("\nBLOCKED -- never on a card: %s"
               % (", ".join(r["tag"] for r in blocked) or "none"))
    return out


def cmd_queue(path, cards):
    print("\n".join(queue_lines(path, cards)))
    return 0


def cmd_init(path, answers_path, force):
    """A new learner's profile from the seed, statuses from the interview."""
    try:
        answers = json.load(io.open(answers_path, encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        print("cannot read %s: %s" % (answers_path, e))
        return 2
    if not isinstance(answers, dict):
        print('%s must be a JSON object {"<tag>": "allowed|learning|blocked"}' % answers_path)
        return 2
    seed = load_rows(SEED)
    tags = {r["tag"] for r in seed}
    bad = ["%s: no such construction tag" % k for k in answers if k not in tags]
    bad += ["%s: status %r is not allowed/learning/blocked" % (k, v)
            for k, v in answers.items() if v not in ("allowed", "learning", "blocked")]
    if bad:
        print("\n".join(bad) + "\nnothing written.")
        return 2
    if os.path.exists(path):
        if not force:
            print("%s already exists -- a profile with history is never overwritten "
                  "without --force." % path)
            return 1
        bak = "%s.%s.bak" % (path, time.strftime("%Y%m%d-%H%M%S"))
        os.replace(path, bak)
        print("kept the old profile as %s" % bak)
    out = []
    for line in io.open(SEED, encoding="utf-8").read().split("\n"):
        r = parse(line)
        if r:
            r["status"] = answers.get(r["tag"], r["status"])
            line = render(r)
        out.append(line.replace("{{DATE}}", time.strftime("%Y-%m-%d")))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    rows = load_rows(path)
    print("wrote %s: %s" % (path, ", ".join(
        "%d %s" % (sum(r["status"] == s for r in rows), s)
        for s in ("allowed", "learning", "blocked"))))
    return 0


def set_statuses(path, statuses):
    """Change only the Status cells of an existing profile -- the setup page's
    re-runs. Last used, Last scheduled and Uses stay, so the scheduler keeps its
    history (`init --force` starts from the seed and loses it). -> (exit code,
    report lines); the file is untouched on any problem."""
    rows = {r["tag"]: r for r in load_rows(path)}
    bad = ["%s: no such construction tag" % k for k in statuses if k not in rows]
    bad += ["%s: status %r is not allowed/learning/blocked" % (k, v)
            for k, v in statuses.items() if v not in ("allowed", "learning", "blocked")]
    if bad:
        return 2, bad + ["nothing written."]
    changed, out = [], []
    for line in io.open(path, encoding="utf-8").read().split("\n"):
        r = parse(line)
        if r and statuses.get(r["tag"], r["status"]) != r["status"]:
            changed.append("%s: %s -> %s" % (r["tag"], r["status"], statuses[r["tag"]]))
            r["status"] = statuses[r["tag"]]
            line = render(r)
        out.append(line)
    if changed:
        io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    return 0, changed or ["no status changed"]


def update(path, mode, date, args):
    """sched/bump -> (exit code, report lines). The file is untouched unless
    every argument matched a row."""
    want = {}
    for a in args:
        if mode == "bump":
            k, _, n = a.rpartition(":")
            # a negative N corrects an earlier over-count (card replaced after import)
            if not k or not n.lstrip("-").isdigit():
                return 2, ['bad bump arg (want "tag:N" or "Name:N"; N may be negative): %s' % a]
            want[k] = want.get(k, 0) + int(n)
        else:
            want[a] = 0
    text = io.open(path, encoding="utf-8").read()
    out, hit = [], {}
    for line in text.split("\n"):
        r = parse(line)
        if r:
            for k in (r["tag"], r["construction"]):
                if k in want:
                    if mode == "bump":
                        r["uses"] = str(max(0, uses(r) + want[k]))
                        if want[k] > 0:
                            r["last_used"] = date
                    else:
                        r["last_sched"] = date
                    hit[k] = r["tag"]
                    line = render(r)
                    break
        out.append(line)
    missing = set(want) - set(hit)
    if missing:
        return 1, ["NO MATCH for: %s -- file untouched" % ", ".join(sorted(missing))]
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out))
    lines = []
    for k in sorted(hit):
        if mode == "bump":
            lines.append("bumped     %-21s %+d  (last used %s)" % (hit[k], want[k], date))
        else:
            lines.append("scheduled  %-21s     (last scheduled %s)" % (hit[k], date))
    return 0, lines


def cmd_update(path, mode, date, args):
    rc, lines = update(path, mode, date, args)
    print("\n".join(lines))
    return rc


def main(argv):
    rest = list(argv)

    def opt(flag):
        if flag in rest:
            i = rest.index(flag)
            if i + 1 >= len(rest):
                return None
            v = rest[i + 1]
            del rest[i:i + 2]
            return v
        return None

    path = opt("--file") or PROFILE
    date = opt("--date")
    cards = opt("--cards")
    answers = opt("--from")
    if not rest:
        print(__doc__)
        return 2
    cmd, args = rest[0], rest[1:]
    if cmd == "queue":
        return cmd_queue(path, int(cards) if cards and cards.isdigit() else None)
    if cmd == "init" and answers:
        return cmd_init(path, answers, "--force" in args)
    if cmd in ("bump", "sched"):
        if not date or not DATE.match(date) or not args:
            print(__doc__)
            return 2
        return cmd_update(path, cmd, date, args)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
