#!/usr/bin/env python3
"""Validate a batch CSV before/after commit. Exit 0 = clean, 1 = problems.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/validate.py my-cards/YYYY-MM-DD_import.csv [--text] [--media DIR]

--text is the pre-audio check: every text rule, but no audio-coverage rule.
Without it, a second batch appended to a day's file could never pass before
tts.py runs -- the morning's clips put the whole file in audio mode.

Checks per row: exactly 2 ";"-separated quoted fields; column 1 the
translation, column 2 Romanian (no Cyrillic); no cedilla diacritics (ş ţ)
anywhere; <b>/<i>/<u> tags balanced and <u> only on the Romanian side.

Stress (see the skill's "Stress marking"): every <u> wraps exactly ONE vowel
letter, and an example sentence carries no more marked words than the
headword does -- only the target word is marked, never the whole sentence.

Language (learner.md `language_code`): for a Cyrillic translation language,
column 1 must contain Cyrillic (catches swapped columns); for Ukrainian, the
Russian-only letters ы э ъ ё are refused. Other languages get the generic
checks only.

Audio (see tts.py): if ANY row carries a [sound:] tag the file is in
audio mode, and then every row must be fully covered -- one clip anchored to
each </b> and each </i>, Romanian side only. Partial coverage means a tts run
died halfway and the batch must not be imported yet. anki_add.py runs this
same check before every import, and additionally demands audio on every row.

Stays offline on purpose (it reads learner.md, nothing else). Detecting text
that drifted away from its audio needs the voice profile, so it lives in
`tts.py check` instead.
"""
import io, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
RUS = set("ыэъёЫЭЪЁ")
CED = set("şţŞŢ")
SOUND = re.compile(r"\[sound:([^\]]*)\]")
ANCHORED = re.compile(r"</[bi]>\[sound:([^\]]*)\]")
# Deliberately broader than tts.py's own names, so a hand-placed clip passes.
# This is what keeps a filename out of validate's '";"' split, known.py's ','
# split, and Anki's media layer.
FNAME = re.compile(r"^[A-Za-z0-9._-]{1,120}\.mp3$")
STRESS = re.compile(r"<u>(.*?)</u>")
VOWEL = re.compile(r"^[aeiouăâîAEIOUĂÂÎ]$")


def cyr(s):
    return any("Ѐ" <= c <= "ӿ" for c in s)


def balanced(s, tag):
    return s.count("<%s>" % tag) == s.count("</%s>" % tag)


WORD_SPLIT = re.compile(r"[\s,;.!?:()…]+")
# Citation particles that carry no stress of their own: a, a-și, un, două, (se) ...
PARTICLE = re.compile(r"^(?:a|a-și|a-ți|a-i|un|o|doi|două|se|mă|-și|\.\.\.)$", re.I)


def marked_words(s):
    """Words carrying a <u> stress mark, in a field fragment."""
    return sum(1 for w in WORD_SPLIT.split(s) if "<u>" in w)


def head_words(s):
    """Words of a headword part that an example may mark: everything but the
    citation particles. A monosyllable counts too -- `a-și da interesul` marks
    nothing in `da`, but its example's `dădea` is rightly marked."""
    words = WORD_SPLIT.split(re.sub(r"<[^>]+>", "", s))
    return sum(1 for w in words if w and not PARTICLE.match(w))


def stress_problems(ro):
    """-> problem strings for the Romanian field's stress marks."""
    out = []
    for m in STRESS.finditer(ro):
        if not VOWEL.match(m.group(1)):
            out.append("<u>%s</u> must wrap exactly one vowel letter" % m.group(1))
    head = re.search(r"<b>(.*?)</b>", ro, re.S)
    cap = max([1] + [head_words(part) for part in head.group(1).split(",")]) if head else 1
    for ex in re.findall(r"<i>(.*?)</i>", ro, re.S):
        n = marked_words(SOUND.sub("", ex))
        if n > cap:
            out.append("%d stressed words in an example, the headword has %d -- mark the "
                       "target word only: %s" % (n, cap, re.sub(r"<[^>]+>", "", ex)[:50]))
    return out


def main(argv):
    media = config.MEDIA
    learner = config.learner()
    cyrillic, ukrainian = config.cyrillic_translation(learner), learner["language_code"] == "uk"
    rest = list(argv)
    if "--media" in rest:                   # drop the flag AND its value
        i = rest.index("--media")
        media = rest[i + 1]
        rest = rest[:i] + rest[i + 2:]
    args = [a for a in rest if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2

    text = io.open(args[0], encoding="utf-8").read()
    audio_mode = "[sound:" in text and "--text" not in rest   # once, for the whole file
    bad, rows = 0, 0

    for i, line in enumerate(text.split("\n"), 1):
        line = line.rstrip("\r")
        if not line:
            continue
        rows += 1
        parts = line.split('";"')
        if len(parts) != 2 or not line.startswith('"') or not line.endswith('"'):
            print("line %d: bad field structure" % i); bad += 1; continue
        ua, ro = parts[0][1:], parts[1][:-1]
        if cyrillic and not cyr(ua):
            print("line %d: column 1 has no Cyrillic (columns swapped?)" % i); bad += 1
        if cyr(ro):
            print("line %d: Cyrillic in Romanian column" % i); bad += 1
        if ukrainian and RUS & set(ua):
            print("line %d: Russian-specific letters %s" % (i, sorted(RUS & set(ua)))); bad += 1
        if CED & set(line):
            print("line %d: cedilla diacritics (use ș/ț)" % i); bad += 1
        if "<u>" in ua or "</u>" in ua:
            print("line %d: stress mark on the translation side" % i); bad += 1
        for p in stress_problems(ro):
            print("line %d: %s" % (i, p)); bad += 1
        for field, side in ((ua, "translation"), (ro, "Romanian")):
            for tag in ("b", "i", "u"):
                if not balanced(field, tag):
                    print("line %d: unbalanced <%s> tags on the %s side" % (i, tag, side))
                    bad += 1

        # --- audio ---------------------------------------------------------
        if "[sound:" in ua:
            print("line %d: sound tag on the translation side" % i); bad += 1
        tags = SOUND.findall(ro)
        if len(tags) != len(ANCHORED.findall(ro)):
            print("line %d: sound tag not anchored to </b> or </i>" % i); bad += 1
        if audio_mode:
            want = ro.count("</b>") + ro.count("</i>")
            if len(tags) != want:
                print("line %d: expected %d sound tags, found %d%s"
                      % (i, want, len(tags),
                         " (partial tts run -- re-run `tts.py add`)" if tags else
                         " (no audio -- mixed coverage)")); bad += 1
        if len(set(tags)) != len(tags):
            print("line %d: same clip used twice in one row" % i); bad += 1
        for name in tags:
            if not FNAME.match(name):
                print("line %d: unsafe media filename %r" % (i, name)); bad += 1
            elif not os.path.exists(os.path.join(media, name)):
                print("line %d: media file missing: %s" % (i, name)); bad += 1

    print("%s: %d rows, %s%s" % (args[0], rows, "audio, " if audio_mode else "",
                                 "CLEAN" if not bad else "%d PROBLEM(S)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
