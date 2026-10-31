#!/usr/bin/env python3
"""Dedup + supporting-vocabulary check against the LIVE Anki collection.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/known.py check <word|phrase> ...
    runtime/python/python .agents/skills/add-romanian-words/scripts/known.py check --files <word|phrase> ...

Output, one line per candidate:
    DUPLICATE  nid:<id> "<headword>"    exact headword match after normalization
    KNOWN      nid:<id> "...context..." hit at a word start in some note's Romanian side
    UNKNOWN                             no trace in the collection
and, appended to any verdict when files and deck disagree:
    [FILE-ONLY <file>:<line>]           a project CSV has this headword, Anki does not

The collection is the source of truth: every note in every deck, Romanian side
only (the field without Cyrillic; every Latin-script field when that is
ambiguous). The project's batch files are still read, but only to report where
they disagree with Anki -- a batch whose import never finished, or a note the
user deleted or re-worded in Anki. --files gives the file-only verdicts
instead, for comparison. Anki must be running: without it this
exits 2. There is deliberately no silent fallback to files -- that would
quietly reintroduce the drift live mode exists to remove.

Normalization order is load-bearing (two historic dedup bugs, see v2/logs/2026-08-21
and 2026-08-23): split off examples on <br> BEFORE stripping tags; strip gender
markers (n)/(f)/(m)/(pl); then prefixes/articles/reflexive markers.

Third class, added with audio: [sound:...] is NOT an HTML tag, so the <[^>]*>
strip does not touch it. A comma-less headword ("oricum", "a pune la punct" --
every adjective, phrase and plural-only noun, 36 of 112 v2 rows) has no comma
for step 3 to amputate, so the tag would ride into the key and never match.
The KNOWN fallback cannot cover that: it searches the corpus, and the corpus
used to keep its <u> tags, which made 75 of 112 v2 headwords unfindable
("afumat" is not a substring of "afum<u>a</u>t"). Both are fixed here -- the
corpus is normalized on load, not merely lowercased.

Fourth, added in v3: KNOWN matches only at the START of a word. A bare
substring test found "merit" inside "nedumerit" (v2/logs/2026-09-28). Prefix
matching is kept on purpose -- querying a stem ("aștept") must still find its
inflections ("așteptarea").

Fifth, v3: a formula's open slot ("Te felicităm cu ocazia...", "…" or "...")
is stripped, or a query without the dots misses the card (2026-09-29, batch 2).

normalize() began as a copy of v2/scripts/known.py's and v3 extends it; v2's
stays frozen. It is the single key for dedup, the tag manifest and
anki_add.py's idempotency check -- all computed here, so they stay consistent.
"""
import io, os, re, sys, glob, html, unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ankilib import load_env, ac, notes_info, romanian_field, die, Unreachable
from validate import cyr
from config import ROOT, DATA_DIR

sys.stdout.reconfigure(encoding="utf-8")
SOUND = re.compile(r"\[sound:[^\]]*\]")
# Line-level boundaries: live notes from the old export use div/table markup
# where the CSVs use <br>. Words must never fuse across any of them.
BLOCK = re.compile(r"<(?:br|/?(?:div|p|li|tr|td|th|table|tbody|ul|ol))\b[^>]*>", re.I)


def corpus_text(text: str) -> str:
    """Strip the deck down to bare words for the KNOWN substring fallback.
    Tags must go to EMPTY (afum<u>a</u>t -> afumat), field/example/line
    boundaries to a newline, so words never fuse across them."""
    t = SOUND.sub("", text)
    t = t.replace('";"', "\n")
    t = BLOCK.sub("\n", t)
    t = re.sub(r"<[^>]*>", "", t)
    return t.replace("&nbsp;", " ").replace("\xa0", " ").lower()


def normalize(s: str) -> str:
    s = s.split("<br>")[0]                              # 1. examples off FIRST
    s = re.sub(r"<[^>]*>", "", s)                       # 2. tags (incl. <u> stress marks)
    s = SOUND.sub("", s)                                # 2b. Anki media tags (not HTML!)
    s = s.replace("&nbsp;", " ").replace("\xa0", " ")
    s = s.split(",")[0]                                 # 3. drop 1sg / plural half
    s = s.split("/")[0]                                 # 3b. slash-plural variants (pârjoală/e)
    s = re.sub(r"\s*\((?:n|f|m|pl)\.?\)", "", s)        # 4. gender markers ((pl) and (pl.))
    s = re.sub(r"^a-(?:și|ți|i)\s+", "", s)             # 5. verb prefixes...
    s = re.sub(r"^a\s+(?:se\s+)?", "", s)               # bare "a se X" == "a (se) X" (2026-10-10)
    s = re.sub(r"^(?:un|o|doi|două)\s+", "", s)         # ...articles...
    s = s.replace("(se) ", "").replace("(mă) ", "").replace("(-și) ", "")  # ...markers
    s = re.sub(r"\s*(?:\.\.\.|…)\s*$", "", s.strip())   # 6. open slot: "cu ocazia..."
    return s.strip().lower()                            # 7. trim


def plain(s: str) -> str:
    """First line of a field as readable text, for reports only."""
    s = BLOCK.split(s)[0]
    s = SOUND.sub("", s)
    s = html.unescape(re.sub(r"<[^>]*>", "", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def find(n, corpus):
    """-> [(where, context, whole)] for every corpus entry where n starts a
    word. Whole-word hits come first: "apă" must be reported from a card that
    says "apă", not from the first "apăra" in the deck."""
    if not n:
        return []
    whole_rx = re.compile(r"(?<!\w)" + re.escape(n) + r"(?!\w)")
    prefix_rx = re.compile(r"(?<!\w)" + re.escape(n))
    hits = []
    for where, txt in corpus:
        m = whole_rx.search(txt) or prefix_rx.search(txt)
        if m:
            a, b = max(0, m.start() - 18), min(len(txt), m.end() + 18)
            hits.append((where, re.sub(r"\s*\n\s*", " / ", txt[a:b]).strip(),
                         m.re is whole_rx))
    hits.sort(key=lambda h: not h[2])
    return hits


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

def sources():
    """This project's batch files: translation first, Romanian in column 2.
    (The v1/v2 files and original_import.txt were removed on 2026-10-30; their
    cards are all in Anki, which is what the checks use.)"""
    for pattern in ("*_import.csv", "*_grammar.csv"):
        for p in sorted(glob.glob(os.path.join(DATA_DIR, pattern))):
            yield p, "uafirst"


def load():
    """File corpus: -> (headwords {norm: (rel, line)}, [(rel, corpus_text)])."""
    headwords, raw = {}, []
    for path, kind in sources():
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        text = io.open(path, encoding="utf-8", errors="replace").read()
        raw.append((rel, corpus_text(text)))
        for i, line in enumerate(text.split("\n"), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if kind == "orig":
                ro = line.split("\t")[0]
            else:
                parts = re.split(r'";"|","', line)
                if len(parts) < 2:
                    continue
                ro = (parts[1] if kind == "uafirst" else parts[0]).strip('"')
            n = normalize(ro)
            if n and n not in headwords:
                headwords[n] = (rel, i)
    return headwords, raw


def live_load():
    """Live corpus from every note in the collection:
        head   {norm headword: nid}       first note wins
        title  {nid: plain headword}      for reports
        corpus [(nid, corpus_text)]       Romanian side only
        exact  {NFC Romanian field: nid}  anki_add.py's "already imported" test
    """
    nids = ac("findNotes", query="deck:*")
    live = {"head": {}, "title": {}, "corpus": [], "exact": {}, "notes": len(nids)}
    for note in notes_info(nids):
        nid = note["noteId"]
        fld, _ = romanian_field(note)
        values = [f["value"] for f in note["fields"].values()]
        if fld:
            ro = note["fields"][fld]["value"]
            heads, texts = [ro], [ro]
            live["exact"].setdefault(nfc(ro), nid)
        else:
            # Ambiguous note: every Latin-script field may be the Romanian one,
            # and mixed ones still carry findable Romanian words.
            heads, texts = [v for v in values if not cyr(v)], values
        for v in heads:
            n = normalize(v)
            if n and n not in live["head"]:
                live["head"][n] = nid
                live["title"][nid] = plain(v)
        live["corpus"].append((nid, corpus_text("\n".join(texts))))
    return live


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def check_files(words):
    headwords, raw = load()
    for w in words:
        n = normalize(w)
        if n in headwords:
            f, i = headwords[n]
            print("%-32s DUPLICATE  %s:%d" % (w, f, i))
            continue
        hits = find(n, raw)
        if hits:
            extra = " (+%d more)" % (len(hits) - 1) if len(hits) > 1 else ""
            print("%-32s KNOWN      %s%s" % (w, hits[0][0], extra))
        else:
            print("%-32s UNKNOWN" % w)


def verdict(w, live, files):
    """-> (DUPLICATE|KNOWN|UNKNOWN, report line) for one word against the live
    collection. batch.py's prep shares this with `check`."""
    n = normalize(w)
    if n in live["head"]:
        nid = live["head"][n]
        return "DUPLICATE", '%-32s DUPLICATE  nid:%d "%s"' % (
            w, nid, live["title"].get(nid, "")[:60])
    hits = find(n, live["corpus"])
    if hits:
        extra = " (+%d more)" % (len(hits) - 1) if len(hits) > 1 else ""
        kind = "KNOWN"
        line = '%-32s KNOWN      nid:%d "...%s..."%s' % (w, hits[0][0], hits[0][1], extra)
        if not hits[0][2]:
            line += "   (prefix only -- judge from the context)"
    else:
        kind, line = "UNKNOWN", "%-32s UNKNOWN" % w
    if n in files:
        line += "   [FILE-ONLY %s:%d]" % files[n]
    return kind, line


def check_live(words):
    live = live_load()
    files, _ = load()
    for w in words:
        print(verdict(w, live, files)[1])
    print("(live: %d notes in the collection)" % live["notes"])


def main(argv):
    load_env()
    if len(argv) < 2 or argv[0] != "check":
        print(__doc__)
        return 2
    words = [a for a in argv[1:] if a != "--files"]
    if not words:
        print(__doc__)
        return 2
    if "--files" in argv[1:]:
        check_files(words)
    else:
        check_live(words)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Unreachable as e:
        die(str(e))
