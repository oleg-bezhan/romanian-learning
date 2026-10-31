#!/usr/bin/env python3
"""Import a finished batch into Anki over AnkiConnect: tags, audio, read-back.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/anki_add.py preflight
    runtime/python/python .agents/skills/add-romanian-words/scripts/anki_add.py meta my-cards/YYYY-MM-DD_import.csv --from entries.json
    runtime/python/python .agents/skills/add-romanian-words/scripts/anki_add.py add  my-cards/YYYY-MM-DD_import.csv [--commit] [--force]
    runtime/python/python .agents/skills/add-romanian-words/scripts/anki_add.py delete <nid> ... [--commit] [--force]
    runtime/python/python .agents/skills/add-romanian-words/scripts/anki_add.py create-deck "<name>"
    runtime/python/python .agents/skills/add-romanian-words/scripts/anki_add.py retag <tags.json> [--commit] [--force]

preflight  Read-only readiness probe. The skill runs it before any other work,
           so a closed Anki or a broken .env stops a batch before it costs anything.
meta       Merge tag entries (a JSON object from --from FILE, or stdin) into the
           batch manifest my-cards/YYYY-MM-DD_meta.json, validating them on the way in:
               {"<romanian headword>": {"gram": "<tag>", "pos": "<pos>"}, ...}
           Keys may be written any way normalize() folds together -- "a merita,
           merit", "merita" and "a merit<u>a</u>" are one key. Optional per entry:
           "new_sense": true (see below), and "also": [tags] -- more constructions
           the card's examples realize, each one more gram:: tag. Re-sending a
           headword replaces its entry.
add        Dry by default: validates everything and prints the plan. --commit
           backs up the deck (first write of the day), then per row journals,
           calls addNote and reads the note back, then syncs AnkiWeb. --force
           skips only the reviewer-open guard.

Each note gets Front = CSV column 1 (the translation), Back = column 2
(Romanian, with its [sound:] tags), and three tags: gram::<tag> pos::<pos>
batch::<date> (plus one gram:: per "also" entry). The date comes from the
CSV's filename, gram and "also" against the Tag
column of grammar-profile.md (blocked constructions refused), pos against POS
below. The field names are ANKI_FRONT_FIELD / ANKI_BACK_FIELD (default Front,
Back); the note type must have both.

Idempotent, which is also how an interrupted import resumes: a row whose exact
Romanian field is already a live note is skipped, and so is a row whose
normalized headword already exists -- unless its manifest entry says
"new_sense": true (same lemma, clearly different meaning, kept on purpose).
Anki's own duplicate check is switched off: it compares the FIRST field, which
is the Ukrainian gloss, and two Romanian synonyms may legitimately share one.

Grammar-drill cards go in their own file, my-cards/YYYY-MM-DD_grammar.csv, with their
own manifest my-cards/YYYY-MM-DD_grammar_meta.json. They are text-only by the
learner's choice: every row must carry NO [sound:] tag and every entry must be
pos "grammar" -- and pos "grammar" is refused in an _import.csv.

This script never edits an existing note, and there is no undo command: addNote
bypasses Anki's undo queue, so my-cards/logs/import-YYYY-MM-DD.jsonl records every
created nid, and recovery is a Browse search on the batch:: tag.

delete     Removes the given notes -- ONLY on the learner's explicit request, after
           they confirmed the exact nids. Dry by default (prints each note);
           --commit refuses notes outside ANKI_IMPORT_DECK, takes the day's
           backup, journals each note's full fields and tags to today's import
           journal, calls deleteNotes, checks they are gone, then syncs. Media
           files are kept.

retag      Adds gram:: tags to existing notes -- ONLY on the learner's request
           (the one-off retag of 2026-10-30 gave older cards the richer grammar
           tags new cards get). Input: {"tags": {"<nid>": ["<tag>", ...]}} or
           just the inner object; tags without the gram:: prefix. Never removes
           or edits anything else. Dry by default (prints what each note gains);
           --commit refuses notes outside ANKI_IMPORT_DECK and any tag that is
           not an allowed/learning row of the grammar profile, takes the day's
           backup, journals each note's tags before the change, calls addTags,
           reads every note back, then syncs. Re-running adds nothing twice.

create-deck  Creates an empty deck -- for the setup skill, when a new learner
           wants their cards in a deck of their own. Nothing else is touched.

Env (.env): ANKI_IMPORT_DECK, ANKI_MODEL, ANKI_CONNECT_URL, [ANKI_CONNECT_KEY],
ANKI_MEDIA_DIR, [ANKI_FRONT_FIELD, ANKI_BACK_FIELD], ELEVENLABS_API_KEY,
ELEVENLABS_VOICE_ID (the last two are only checked for presence, by preflight
-- never printed).
"""
import io, os, re, sys, json, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ankilib import (ROOT, die, load_env, ac, quote_term, preflight, backup,
                     ensure_media, Unreachable)
from config import LOGS, fields, rel
from known import normalize, live_load, nfc, plain
from tts import split_row, SOUND_RE
from profile import load_rows
import validate

sys.stdout.reconfigure(encoding="utf-8")

ACTIONS_USED = ["version", "apiReflect", "deckNames", "modelNames", "modelFieldNames",
                "findNotes", "notesInfo", "addNote", "storeMediaFile",
                "getMediaFilesNames", "guiReviewActive", "exportPackage", "sync",
                "deleteNotes", "createDeck", "addTags"]
# Follows the normalization table: verb rows -> verb, noun rows -> noun, bare
# adjective -> adjective, "phrase / idiom" -> phrase. `other` is for single
# function words (pronoun, preposition, conjunction, numeral, interjection).
# `grammar` is for text-only grammar-drill cards, and only in a _grammar.csv.
POS = ("verb", "noun", "adjective", "adverb", "phrase", "other", "grammar")
FRONT, BACK = fields()
NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(import|grammar)\.csv$")


def batch_date(csv_path):
    m = NAME.match(os.path.basename(csv_path))
    if not m:
        die("expected a batch CSV named YYYY-MM-DD_import.csv or YYYY-MM-DD_grammar.csv,"
            " got %s" % csv_path)
    return m.group(1)


def is_grammar(csv_path):
    batch_date(csv_path)
    return NAME.match(os.path.basename(csv_path)).group(2) == "grammar"


def meta_path(csv_path):
    return os.path.join(os.path.dirname(os.path.abspath(csv_path)),
                        "%s_%smeta.json" % (batch_date(csv_path),
                                            "grammar_" if is_grammar(csv_path) else ""))


def gram_tags():
    """tag -> status, straight from grammar-profile.md's Tag column."""
    return {r["tag"]: r["status"] for r in load_rows()}


def check_entry(key, e, grams, grammar):
    if not isinstance(e, dict):
        return ["%r: entry must be an object {gram, pos}" % key]
    p = []
    extra = set(e) - {"gram", "pos", "new_sense", "also"}
    if extra:
        p.append("%r: unknown field(s) %s" % (key, ", ".join(sorted(extra))))
    g = e.get("gram")
    if g not in grams:
        p.append("%r: gram %r is not a Tag in grammar-profile.md" % (key, g))
    elif grams[g] == "blocked":
        p.append("%r: gram %r is a BLOCKED construction -- it must never be on a card"
                 % (key, g))
    if e.get("pos") not in POS:
        p.append("%r: pos %r is not one of %s" % (key, e.get("pos"), ", ".join(POS)))
    elif grammar and e.get("pos") != "grammar":
        p.append("%r: a _grammar.csv takes only pos \"grammar\"" % key)
    elif not grammar and e.get("pos") == "grammar":
        p.append("%r: pos \"grammar\" belongs in a _grammar.csv" % key)
    if "new_sense" in e and not isinstance(e["new_sense"], bool):
        p.append("%r: new_sense must be true or false" % key)
    also = e.get("also", [])
    if not isinstance(also, list):
        p.append("%r: also must be a list of tags" % key)
        also = []
    for t in also:
        if t not in grams:
            p.append("%r: also %r is not a Tag in grammar-profile.md" % (key, t))
        elif grams[t] == "blocked":
            p.append("%r: also %r is a BLOCKED construction -- it must never be on a card"
                     % (key, t))
    return p


def by_key(cards):
    """-> ({normalized key: (written key, entry)}, [collision problems])"""
    out, problems = {}, []
    for k, e in cards.items():
        n = normalize(k)
        if not n:
            problems.append("%r normalizes to nothing" % k)
        elif n in out:
            problems.append("%r and %r are the same headword (%s)" % (out[n][0], k, n))
        else:
            out[n] = (k, e)
    return out, problems


def load_meta(path):
    if not os.path.exists(path):
        return None
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except ValueError as e:
        die("%s is not valid JSON: %s" % (rel(path), e))


def read_csv(path):
    rows = []
    for n, line in enumerate(io.open(path, encoding="utf-8").read().split("\n"), 1):
        if not line.strip():
            continue
        ua, ro = split_row(line)            # structure already checked by validate
        rows.append({"line": n, "ua": nfc(ua), "ro": nfc(ro), "key": normalize(ro),
                     "clips": SOUND_RE.findall(ro)})
    return rows


def target():
    deck = os.environ.get("ANKI_IMPORT_DECK", "").strip()
    model = os.environ.get("ANKI_MODEL", "").strip()
    if not deck:
        die("ANKI_IMPORT_DECK not set in .env (the deck new cards go to, e.g. Default)")
    if not model:
        die("ANKI_MODEL not set in .env (the note type, e.g. Basic)")
    decks = ac("deckNames")
    if deck not in decks:
        die("deck %r not in the collection. Available: %s" % (deck, ", ".join(decks)))
    models = ac("modelNames")
    if model not in models:
        die("note type %r not in the collection. Available: %s" % (model, ", ".join(models)))
    have = ac("modelFieldNames", modelName=model)
    if FRONT not in have or BACK not in have:
        die("note type %r has fields %s; it needs %r and %r (ANKI_FRONT_FIELD / "
            "ANKI_BACK_FIELD)" % (model, have, FRONT, BACK))
    return deck, model


def journal(j, entry):
    entry = dict(ts=round(time.time(), 3), **entry)
    j.write(json.dumps(entry, ensure_ascii=False) + "\n")
    j.flush()
    os.fsync(j.fileno())


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_preflight():
    preflight(ACTIONS_USED, writing=False)
    url = os.environ.get("ANKI_CONNECT_URL", "http://127.0.0.1:8765")
    print("AnkiConnect v%s at %s -- all %d actions available"
          % (ac("version"), url, len(ACTIONS_USED)))
    deck, model = target()
    print("deck   %-10s %5d notes   (ANKI_IMPORT_DECK)"
          % (deck, len(ac("findNotes", query="deck:%s" % quote_term(deck)))))
    print("model  %-10s fields %s, %s   (ANKI_MODEL)" % (model, FRONT, BACK))
    print("collection: %d notes" % len(ac("findNotes", query="deck:*")))
    problems = []
    for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        if not os.environ.get(k, "").strip():
            problems.append("%s not set in .env" % k)
    media = os.environ.get("ANKI_MEDIA_DIR", "").strip()
    if media and not os.path.isdir(media):
        problems.append("ANKI_MEDIA_DIR does not exist: %s" % media)
    print("tts: credentials %s" % ("present" if not problems else "INCOMPLETE"))
    if ac("guiReviewActive"):
        print("WARNING: the Anki reviewer is open -- the import step will refuse until it is closed.")
    for p in problems:
        print("ERROR: %s" % p)
    print("NOT READY" if problems else "READY")
    return 2 if problems else 0


def cmd_meta(csv_path, src=None):
    if src:
        text = io.open(src, encoding="utf-8-sig").read()
    else:
        sys.stdin.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
    try:
        new = json.loads(text)
    except ValueError as e:
        die("the manifest entries are not valid JSON: %s" % e)
    if not isinstance(new, dict) or not new:
        die('entries must be a non-empty JSON object {"<headword>": {"gram": ..., "pos": ...}}')
    return merge_meta(csv_path, new)


def merge_meta(csv_path, new):
    """Validate entries and merge them into the day's manifest. -> exit code"""
    date = batch_date(csv_path)
    path = meta_path(csv_path)
    old = load_meta(path) or {"date": date, "cards": {}}
    if old.get("date") != date:
        die("%s has date %r, expected %s" % (rel(path), old.get("date"), date))

    grams = gram_tags()
    _, problems = by_key(new)
    for k, e in new.items():
        if e is not None:                   # null = remove the entry (card replaced)
            problems += check_entry(k, e, grams, is_grammar(csv_path))
    if problems:
        for p in problems:
            print("REFUSED  %s" % p)
        print("%d problem(s) -- %s untouched." % (len(problems), rel(path)))
        return 2

    merged = dict(old.get("cards") or {})
    index = {normalize(k): k for k in merged}
    for k, e in new.items():
        n = normalize(k)
        if e is None:
            if n not in index:
                die("cannot remove %r: no such entry in %s" % (k, rel(path)))
            del merged[index.pop(n)]
            continue
        if n in index and index[n] != k:
            del merged[index[n]]            # re-tagging a headword replaces its entry
        merged[k] = e
        index[n] = k
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"date": date, "cards": merged}, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)
    print("%s: %d entries (%d written now)" % (rel(path), len(merged), len(new)))

    if os.path.exists(csv_path):
        keys = {normalize(split_row(l)[1]) for l in io.open(csv_path, encoding="utf-8")
                .read().split("\n") if l.strip() and split_row(l)}
        for k in new:
            if new[k] is not None and normalize(k) not in keys:
                print("WARNING: %r matches no row of %s (yet)" % (k, rel(csv_path)))
    return 0


def plan_rows(csv_path, date):
    """All refusals happen here, before anything is written. -> rows or exit 2"""
    if not os.path.exists(csv_path):
        die("no such file: %s" % csv_path)
    if validate.main([csv_path]) != 0:
        die("validate.py found problems (above) -- nothing written.")
    rows = read_csv(csv_path)
    grammar = is_grammar(csv_path)
    problems = []
    if not rows:
        problems.append("%s has no rows" % rel(csv_path))
    for r in rows:
        want = r["ro"].count("</b>") + r["ro"].count("</i>")
        if grammar and r["clips"]:
            problems.append("line %d: grammar cards are text-only -- remove the [sound:] tags"
                            % r["line"])
        elif not grammar and (not want or len(r["clips"]) != want):
            problems.append("line %d: audio incomplete (%d of %d clips) -- run tts.py add first"
                            % (r["line"], len(r["clips"]), want))
        if not r["key"]:
            problems.append("line %d: headword normalizes to nothing" % r["line"])

    mpath = meta_path(csv_path)
    meta = load_meta(mpath)
    if meta is None:
        problems.append("no manifest %s -- write it with `anki_add.py meta`" % rel(mpath))
    else:
        if meta.get("date") != date:
            problems.append("%s has date %r, expected %s" % (rel(mpath), meta.get("date"), date))
        cards, coll = by_key(meta.get("cards") or {})
        problems += coll
        grams = gram_tags()
        for k, e in cards.values():
            problems += check_entry(k, e, grams, grammar)
        seen = {}
        for r in rows:
            if r["key"] in seen:
                problems.append("lines %d and %d: same headword %r"
                                % (seen[r["key"]], r["line"], r["key"]))
            seen[r["key"]] = r["line"]
            if r["key"] not in cards:
                problems.append("line %d: %r has no manifest entry" % (r["line"], r["key"]))
                continue
            e = cards[r["key"]][1]
            if isinstance(e, dict):
                grams_ = [e.get("gram")] + [t for t in e.get("also") or []
                                            if t != e.get("gram")]
                r["tags"] = ["gram::%s" % g for g in dict.fromkeys(grams_)] + \
                    ["pos::%s" % e.get("pos"), "batch::%s" % date]
                r["new_sense"] = e.get("new_sense") is True
        for n, (k, _) in cards.items():
            if n not in seen:
                problems.append("manifest entry %r matches no CSV row" % k)
    if problems:
        for p in problems:
            print("REFUSED  %s" % p)
        print("\n%d problem(s) -- nothing written." % len(problems))
        sys.exit(2)
    return rows


def readback(nid, r, deck, model):
    """-> list of mismatches between the live note and what we sent."""
    note = ac("notesInfo", notes=[nid])[0]
    bad = []
    if note.get("modelName") != model:
        bad.append("note type %r" % note.get("modelName"))
    f = note.get("fields", {})
    if f.get(FRONT, {}).get("value") != r["ua"]:
        bad.append("%s differs" % FRONT)
    if f.get(BACK, {}).get("value") != r["ro"]:
        bad.append("%s differs" % BACK)
    if {t.lower() for t in note.get("tags", [])} != {t.lower() for t in r["tags"]}:
        bad.append("tags are %s" % note.get("tags"))
    if not note.get("cards"):
        bad.append("note has no cards")
    if ac("findNotes", query="nid:%d deck:%s" % (nid, quote_term(deck))) != [nid]:
        bad.append("not in deck %s" % deck)
    for c in r["clips"]:
        if c not in ac("getMediaFilesNames", pattern=c):
            bad.append("clip missing from collection.media: %s" % c)
    return bad, len(note.get("cards") or [])


def cmd_add(csv_path, commit, force):
    return run_add(csv_path, commit, force)[0]


def run_add(csv_path, commit, force, quiet=False):
    """-> (exit code, result). quiet: one summary line instead of the per-row
    plan and per-note read-back lines (refusals and skips are always shown)."""
    res = {"created": [], "adds": 0, "present": 0, "dups": 0, "backup": None,
           "sync": "not attempted", "journal": None, "aborted": None}
    date = batch_date(csv_path)
    rows = plan_rows(csv_path, date)
    deck, model = target()
    live = live_load()

    for r in rows:
        if r["ro"] in live["exact"]:
            r["action"], r["nid"] = "present", live["exact"][r["ro"]]
        elif r["key"] in live["head"] and not r["new_sense"]:
            r["action"], r["nid"] = "duplicate", live["head"][r["key"]]
        else:
            r["action"] = "add"

    print("\n%s -> deck %r, note type %r" % (rel(csv_path), deck, model))
    for r in rows:
        head = plain(r["ro"])[:34]
        if r["action"] == "add":
            if quiet:
                continue
            what = "ADD   %s%s" % (" ".join(r["tags"]), "  (new sense)" if r["new_sense"] else "")
        elif r["action"] == "present":
            if quiet:
                continue
            what = "SKIP  already imported (nid %d)" % r["nid"]
        else:
            what = 'SKIP  headword already in Anki: nid %d "%s" -- not touched' % (
                r["nid"], live["title"].get(r["nid"], "")[:40])
        print("  line %-3d %-34s %s" % (r["line"], head, what))
    adds = [r for r in rows if r["action"] == "add"]
    dups = [r for r in rows if r["action"] == "duplicate"]
    res.update(adds=len(adds), dups=len(dups),
               present=sum(r["action"] == "present" for r in rows))
    print("%d to add, %d already imported, %d duplicate headword(s)"
          % (len(adds), res["present"], len(dups)))
    if dups:
        print("WARNING: dedup should have caught the duplicate(s) above. If the card is a\n"
              "different sense kept on purpose, mark it \"new_sense\": true in the manifest.")

    if not commit:
        print("\nDRY RUN -- nothing written. Add --commit to import.")
        return 0, res
    if not adds:
        print("\nnothing to import -- every row is already in Anki.")
        return 0, res

    preflight(ACTIONS_USED, writing=not force)
    backed = backup([deck])
    res["backup"] = backed
    print("backup: %s" % backed)
    have = set(ac("getMediaFilesNames", pattern="ro-*.mp3"))
    os.makedirs(LOGS, exist_ok=True)
    jpath = os.path.join(LOGS, "import-%s.jsonl" % date)
    created, aborted = [], None
    with io.open(jpath, "a", encoding="utf-8", newline="\n") as j:
        for r in adds:
            try:
                for c in r["clips"]:
                    ensure_media(c, have)
                journal(j, {"event": "add", "csv": rel(csv_path), "line": r["line"],
                            "key": r["key"], "deck": deck, "model": model,
                            "front": r["ua"], "back": r["ro"], "tags": r["tags"],
                            "clips": r["clips"]})
                nid = ac("addNote", note={
                    "deckName": deck, "modelName": model,
                    "fields": {FRONT: r["ua"], BACK: r["ro"]},
                    "tags": r["tags"], "options": {"allowDuplicate": True}})
                bad, ncards = readback(nid, r, deck, model)
                if bad:
                    journal(j, {"event": "mismatch", "key": r["key"], "nid": nid, "bad": bad})
                    raise RuntimeError(
                        "nid %d: read-back mismatch (%s). The note exists -- inspect it in\n"
                        "Browse (tag batch::%s). If it is open in the Browse editor, close\n"
                        "it: the editor reverts writes on blur." % (nid, "; ".join(bad), date))
                journal(j, {"event": "ok", "key": r["key"], "nid": nid, "cards": ncards})
                created.append(nid)
                if not quiet:
                    print("  added nid %d  read-back ok  %s" % (nid, plain(r["ro"])[:50]))
            except Unreachable as e:
                aborted = "Anki stopped answering mid-import -- re-run to resume.\n  %s" % e
                break
            except RuntimeError as e:
                aborted = str(e)
                break

        sync = "not attempted (nothing created)"
        if created:
            try:
                ac("sync")
                sync = "synced with AnkiWeb"
            except RuntimeError as e:
                sync = "WARNING: sync failed (%s) -- sync manually in Anki" % e
            journal(j, {"event": "sync", "result": sync})

    if aborted:
        print("\nABORTED at line %d: %s" % (r["line"], aborted))
    print("\nimported %d of %d note(s) into %r%s: %s"
          % (len(created), len(adds), deck, ", each read back ok" if quiet and created else "",
             ", ".join(str(n) for n in created) or "none"))
    print("sync: %s" % sync)
    print("journal: %s" % rel(jpath))
    res.update(created=created, sync=sync, journal=rel(jpath), aborted=aborted)
    return (1 if aborted else 0), res


def cmd_delete(nids, commit, force):
    """Only on the learner's explicit, confirmed request -- see the module doc."""
    deck, _ = target()
    try:
        want = sorted({int(n) for n in nids})
    except ValueError:
        die("note ids must be numbers: %s" % " ".join(nids))
    info = {n["noteId"]: n for n in ac("notesInfo", notes=want) if n.get("noteId")}
    in_deck = set(ac("findNotes", query="deck:%s" % quote_term(deck)))
    problems = ["nid %d: no such note" % n for n in want if n not in info]
    problems += ["nid %d: not in deck %s -- refusing" % (n, deck)
                 for n in want if n in info and n not in in_deck]
    print("\ndelete from deck %r:" % deck)
    for n in want:
        if n in info:
            f = info[n]["fields"]
            print("  nid %d  %-40s | %s  [%s]" % (
                n, plain(f[BACK]["value"])[:40], plain(f[FRONT]["value"])[:40],
                " ".join(info[n].get("tags", []))))
    if problems:
        for p in problems:
            print("REFUSED  %s" % p)
        print("\n%d problem(s) -- nothing deleted." % len(problems))
        return 2
    if not commit:
        print("\nDRY RUN -- nothing deleted. Add --commit to delete.")
        return 0

    preflight(ACTIONS_USED, writing=not force)
    print("backup: %s" % backup([deck]))
    os.makedirs(LOGS, exist_ok=True)
    jpath = os.path.join(LOGS, "import-%s.jsonl" % time.strftime("%Y-%m-%d"))
    with io.open(jpath, "a", encoding="utf-8", newline="\n") as j:
        for n in want:      # the full note first, so it can be re-created by hand
            journal(j, {"event": "delete", "nid": n, "deck": deck,
                        "model": info[n].get("modelName"),
                        "front": info[n]["fields"][FRONT]["value"],
                        "back": info[n]["fields"][BACK]["value"],
                        "tags": info[n].get("tags", [])})
        ac("deleteNotes", notes=want)
        left = [n for n in want if ac("findNotes", query="nid:%d" % n)]
        journal(j, {"event": "deleted", "nids": [n for n in want if n not in left],
                    "left": left})
        if left:
            print("\nERROR: still present after deleteNotes: %s" % left)
            return 1
        try:
            ac("sync")
            sync = "synced with AnkiWeb"
        except RuntimeError as e:
            sync = "WARNING: sync failed (%s) -- sync manually in Anki" % e
        journal(j, {"event": "sync", "result": sync})
    print("\ndeleted %d note(s): %s" % (len(want), ", ".join(map(str, want))))
    print("media kept -- use tts.py orphans / Tools > Check Media")
    print("sync: %s" % sync)
    print("journal: %s" % rel(jpath))
    return 0


def cmd_retag(path, commit, force):
    """Add gram:: tags to existing notes. See the module doc."""
    deck, _ = target()
    try:
        data = json.load(io.open(path, encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        die("cannot read %s: %s" % (path, e))
    want = data.get("tags", data) if isinstance(data, dict) else None
    if not isinstance(want, dict) or not want:
        die('expected {"tags": {"<nid>": ["<tag>", ...]}}')
    grams = gram_tags()
    try:
        nids = sorted({int(n) for n in want})
    except ValueError:
        die("note ids must be numbers")
    lists = {int(k): v for k, v in want.items()}
    info = {n["noteId"]: n for n in ac("notesInfo", notes=nids) if n.get("noteId")}
    in_deck = set(ac("findNotes", query="deck:%s" % quote_term(deck)))
    problems, plan = [], []
    for n in nids:
        tags = lists[n]
        if n not in info:
            problems.append("nid %d: no such note" % n)
        elif n not in in_deck:
            problems.append("nid %d: not in deck %s -- refusing" % (n, deck))
        elif not isinstance(tags, list):
            problems.append("nid %d: tags must be a list" % n)
        else:
            bad = [t for t in tags if grams.get(t) in (None, "blocked")]
            if bad:
                problems.append("nid %d: not an allowed/learning Tag of the profile: %s"
                                % (n, ", ".join(map(str, bad))))
                continue
            have = {t.lower() for t in info[n].get("tags", [])}
            add = [t for t in dict.fromkeys("gram::%s" % t for t in tags) if t.lower() not in have]
            if add:
                plan.append((n, add))
    print("\nretag in deck %r:" % deck)
    for n, add in plan:
        print("  nid %d  %-34s + %s" % (n, plain(info[n]["fields"][BACK]["value"])[:34],
                                        " ".join(add)))
    print("%d note(s) gain %d tag(s); %d already have every tag they would get"
          % (len(plan), sum(len(a) for _, a in plan), len(nids) - len(plan) - len(problems)))
    if problems:
        for p in problems:
            print("REFUSED  %s" % p)
        print("\n%d problem(s) -- nothing written." % len(problems))
        return 2
    if not commit:
        print("\nDRY RUN -- nothing written. Add --commit to add the tags.")
        return 0
    if not plan:
        print("\nnothing to add.")
        return 0

    preflight(ACTIONS_USED, writing=not force)
    print("backup: %s" % backup([deck]))
    os.makedirs(LOGS, exist_ok=True)
    jpath = os.path.join(LOGS, "import-%s.jsonl" % time.strftime("%Y-%m-%d"))
    with io.open(jpath, "a", encoding="utf-8", newline="\n") as j:
        for n, add in plan:          # the tags before, so the change can be undone by hand
            journal(j, {"event": "retag", "nid": n, "tags_before": info[n].get("tags", []),
                        "add": add})
            ac("addTags", notes=[n], tags=" ".join(add))
        after = {x["noteId"]: {t.lower() for t in x.get("tags", [])}
                 for x in ac("notesInfo", notes=[n for n, _ in plan])}
        missing = [(n, [t for t in add if t.lower() not in after.get(n, set())])
                   for n, add in plan]
        missing = [(n, m) for n, m in missing if m]
        journal(j, {"event": "retagged", "notes": len(plan), "missing": missing})
        if missing:
            for n, m in missing:
                print("ERROR: nid %d lacks %s after addTags" % (n, " ".join(m)))
            return 1
        try:
            ac("sync")
            sync = "synced with AnkiWeb"
        except RuntimeError as e:
            sync = "WARNING: sync failed (%s) -- sync manually in Anki" % e
        journal(j, {"event": "sync", "result": sync})
    print("\nretagged %d note(s), every tag read back" % len(plan))
    print("sync: %s" % sync)
    print("journal: %s" % rel(jpath))
    return 0


def cmd_create_deck(name):
    name = name.strip()
    if not name:
        die("a deck needs a name")
    if name in ac("deckNames"):
        print("deck %r already exists -- nothing to do" % name)
        return 0
    ac("createDeck", deck=name)
    print("created deck %r" % name)
    return 0


def main(argv):
    load_env()
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    src = None
    if "--from" in rest[:-1]:
        i = rest.index("--from")
        src, rest = rest[i + 1], rest[:i] + rest[i + 2:]
    pos = [a for a in rest if not a.startswith("--")]
    if cmd == "preflight":
        return cmd_preflight()
    if cmd == "retag" and len(pos) == 1:
        return cmd_retag(pos[0], "--commit" in rest, "--force" in rest)
    if cmd == "create-deck" and len(pos) == 1:
        return cmd_create_deck(pos[0])
    if cmd == "meta" and len(pos) == 1:
        return cmd_meta(pos[0], src)
    if cmd == "add" and len(pos) == 1:
        return cmd_add(pos[0], "--commit" in rest, "--force" in rest)
    if cmd == "delete" and pos:
        return cmd_delete(pos, "--commit" in rest, "--force" in rest)
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Unreachable as e:
        die(str(e))
