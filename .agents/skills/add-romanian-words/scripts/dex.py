#!/usr/bin/env python3
"""DEXonline research for one Romanian word: senses + stress, in one call.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/dex.py <word> [--forms form1 form2 ...]
    runtime/python/python .agents/skills/add-romanian-words/scripts/dex.py <w1> <w2> <w3> ...
    runtime/python/python .agents/skills/add-romanian-words/scripts/dex.py <w1> --forms f1 f2 -- <w2> --forms f3 -- <w3>

Several words in one call: bare words, or groups separated by a lone `--`
when forms are passed. Each word's block starts with `== <word>`.

Prints:
    HEADWORD <word with <u>stress</u>>        from htmlRep tonic-accent spans
    1SG      <cited 1sg, stress-marked>       verbs: the entry's second citation form
    PL       <cited plural, stress-marked>    nouns (adjectives print none: cards
                                              cite the bare masculine only)
    SENSE    [source] <cleaned definition>    top definitions from distinct sources
    FORM     <form> -> <stress-marked form>   from /paradigma (only with --forms)

Rules baked in (see v2/logs/2026-09-01 for the regression this prevents):
- paradigm cells are matched by walking plain-text chars, PREFERRING cells that
  actually carry a tonic-accent inside the walked span — never bare prefix match;
- dangling <u> is closed, output verified tag-balanced;
- monosyllabic forms are reported as "do not mark", regardless of what DEX marks.
"""
import io, re, sys, json, urllib.request, urllib.parse

sys.stdout.reconfigure(encoding="utf-8")
UA = {"User-Agent": "Mozilla/5.0 (personal-vocab-tool)"}
VOWELS = "aeiouăâîAEIOUĂÂÎ"


def fetch(url):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "replace")


def syllables(form):
    return len(re.findall(r"[%s]+" % VOWELS, form))


def mark_spans(html):
    return re.sub(r'<span class="tonic-accent">(.*?)</span>', r"<u>\1</u>", html)


def clean_internal(rep, limit=400):
    t = rep.replace("&#039;", "'")
    t = re.sub(r"[@$#]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit] + ("…" if len(t) > limit else "")


def headword_and_senses(word):
    data = json.loads(fetch("https://dexonline.ro/definitie/%s/json" % urllib.parse.quote(word)))
    defs = data.get("definitions", [])
    head = sg = None
    seen_sources, senses = set(), []
    for d in defs:
        h = d.get("htmlRep") or ""
        if head is None:
            m = re.search(r'<b>([^<]*<span class="tonic-accent">[^<]*</span>[^<]*)', h)
            if m:
                head = mark_spans(m.group(1)).rstrip(",").strip().lower()
                head = re.sub(r",\s*-.*$", "", head)        # "frumos, -oasă" -> "frumos"
        ir = (d.get("internalRep") or "").replace("&#039;", "'")
        if sg is None:
            m = re.match(r"\s*@[^@]+@\s*\$([^$]+)\$(.{0,24})", ir)
            if m:
                tail = m.group(2)
                label = ("1SG" if re.search(r"\bvb\.", tail) else
                         "PL" if re.search(r"\bs\.\s*[fmn]\.", tail) else None)
                cand = m.group(1).strip().rstrip(",")
                if label is None:                            # adjective, adverb, ...:
                    sg = ""                                  # no second form worth citing
                elif re.match(r"^[a-zA-ZăâîșțĂÂÎȘȚ' ]+$", cand):
                    sg = re.sub(r"'([a-zA-ZăâîșțĂÂÎȘȚ])", r"<u>\1</u>", cand).lower()
        src = d.get("sourceName", "?")
        if src not in seen_sources and len(senses) < 3:
            seen_sources.add(src)
            senses.append((src, clean_internal(d.get("internalRep") or "")))
    print("HEADWORD %s" % (head or "(no stress span — monosyllable or no data; do not mark)"))
    if sg:
        note = "" if "<u>" in sg else "   (unmarked in entry — verify via --forms if used)"
        print("%-8s %s%s" % (label, sg, note))
    print("DEFS     %d definitions" % len(defs))
    for src, txt in senses:
        print("SENSE    [%s] %s" % (src, txt))


def paradigm_forms(word, forms):
    html = fetch("https://dexonline.ro/definitie/%s/paradigma" % urllib.parse.quote(word))
    # A cell lists its variants as <li> items -- "(să) v<u>a</u>dă", "v<u>a</u>ză" --
    # which run together once tags are stripped, so each item is its own candidate.
    cells = []
    for c in re.findall(r"<td[^>]*>(.*?)</td>", html, re.S):
        if "tonic-accent" in c:
            cells += [mark_spans(i) for i in re.findall(r"<li[^>]*>(.*?)</li>", c, re.S) or [c]]
    for form in forms:
        # Cells cite particles in brackets -- "(să) p<u>i</u>cure", "(a) picura" --
        # so they are stripped before matching. A form asked for WITH its
        # particle ("să picure") is matched without it and printed with it.
        pre = ""
        m = re.match(r"^(să|a)\s+(\S.*)$", form)
        if m:
            pre, form = m.group(1) + " ", m.group(2)
        if syllables(form) <= 1:
            print("FORM     %-16s -> %s   (monosyllable — do not mark)" % (pre + form, pre + form))
            continue
        best = None
        for cell in cells:
            marked = re.sub(r"<(?!/?u\b)[^>]+>", "", cell).strip()
            marked = re.sub(r"^\((?:să|a)\)\s*", "", marked)
            plain = re.sub(r"<[^>]+>", "", marked)
            if not plain.startswith(form):
                continue
            # The form must be a whole word of the cell: "vede" must not take its
            # stress from the long infinitive "ved<u>e</u>re" (2026-10-18).
            if len(plain) > len(form) and plain[len(form)].isalpha():
                continue
            out, n, open_u = "", 0, False
            for tok in re.finditer(r"<u>|</u>|.", marked):
                t = tok.group(0)
                if t == "<u>":
                    if n >= len(form):
                        break
                    out += t
                    open_u = True
                elif t == "</u>":
                    if open_u:
                        out += t
                        open_u = False
                else:
                    if n >= len(form):
                        break
                    out += t
                    n += 1
            if open_u:
                out += "</u>"
            if re.sub(r"</?u>", "", out) != form or out.count("<u>") != out.count("</u>"):
                continue
            if "<u>" in out:          # prefer a cell whose accent falls INSIDE the form
                best = out
                break
            best = best or out        # unmarked prefix hit: keep only as last resort
        if best and "<u>" in best:
            print("FORM     %-16s -> %s" % (pre + form, pre + best))
        else:
            print("FORM     %-16s -> (no stress found — leave unmarked, flag it)" % (pre + form))


def groups(argv):
    """-> [(word, forms)]. A chunk with --forms is one word and its forms;
    a chunk without is any number of bare words."""
    out, chunk = [], []
    for a in argv + ["--"]:
        if a != "--":
            chunk.append(a)
            continue
        if "--forms" in chunk:
            i = chunk.index("--forms")
            out += [(w, []) for w in chunk[:i - 1]] + [(chunk[i - 1], chunk[i + 1:])] if i else []
        else:
            out += [(w, []) for w in chunk]
        chunk = []
    return out


def main(argv):
    todo = groups(argv)
    if not todo:
        print(__doc__)
        return 2
    for word, forms in todo:
        if len(todo) > 1:
            print("== %s" % word)
        try:
            headword_and_senses(word)
        except Exception as e:
            print("HEADWORD (fetch failed: %s — use WebFetch fallback)" % e)
        if forms:
            try:
                paradigm_forms(word, forms)
            except Exception as e:
                print("FORMS    (paradigm fetch failed: %s)" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
