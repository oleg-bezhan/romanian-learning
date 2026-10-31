#!/usr/bin/env python3
"""ElevenLabs audio for batch CSVs: one clip per headword, one per example.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/tts.py voices
    runtime/python/python .agents/skills/add-romanian-words/scripts/tts.py estimate --from romanian-fields.txt
    runtime/python/python .agents/skills/add-romanian-words/scripts/tts.py estimate  < romanian-fields.txt
    runtime/python/python .agents/skills/add-romanian-words/scripts/tts.py add my-cards/YYYY-MM-DD_import.csv [--dry-run] [--limit N]
    runtime/python/python .agents/skills/add-romanian-words/scripts/tts.py check my-cards/YYYY-MM-DD_import.csv
    runtime/python/python .agents/skills/add-romanian-words/scripts/tts.py orphans

estimate reads Romanian fields (from --from FILE, or stdin), one per line,
exactly as they will go into the CSV, and prints the characters a real run
would bill -- same text extraction, same cache lookup, no file written and
nothing sent. Its last line is the spend gate's verdict against
TTS_CONFIRM_CHARS (.env, default 3000): `GATE pass` or `GATE needs approval`.

--dry-run prints every string that would be spoken, with its target filename and
the billed character count, and sends nothing. --limit N stops after N NEW clips
(cache hits are free and do not count); the rest simply go untagged and a later
run fills them in. Always dry-run a batch before paying for it.

Audio is a MECHANICAL pass over an already-approved CSV: card text is never
changed and the model never types a [sound:] tag. Clips go inline in the
ROMANIAN field only -- one after </b>, one after each </i>.

Filenames are content-addressed: ro-<slug>-<sha1(profile+text)[:12]>.mp3.
That single decision buys everything else:
- re-running is free and byte-identical (cache hit on the filename);
- editing a sentence yields a new name, so the stale tag is replaced;
- `check` detects text that drifted away from its audio with zero API calls;
- `orphans` derives the referenced set from the CSVs and journals -- no manifest.

Work is per SEGMENT, not per row: a row whose headword succeeded and whose
sentence failed is repaired by re-running, instead of being skipped forever.

Credentials come from .env in the project root (real env vars win):
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID, ANKI_MEDIA_DIR
The key is never printed -- not in errors, not in --dry-run. Non-secret tuning
lives in tts-config.json (the skill's policy/ folder), which is refused if it contains a secret.
"""
import io, os, re, sys, json, time, html, shutil, hashlib, unicodedata
import urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import ROOT, MEDIA, DATA_DIR, LOGS, load_env, tts_confirm_chars, rel
from config import TTS_CONFIG as CONFIG
# Overridable so the evals can point it at a local fake that bills nothing.
API = os.environ.get("ELEVENLABS_API_URL", "").strip() or "https://api.elevenlabs.io/v1"

# Our own clip names only. A tag that does not match this is hand-placed and is
# never touched. Kept in sync with validate.py's filename charset check.
NAME_RE = re.compile(r"^ro-[a-z0-9-]{1,32}-[0-9a-f]{12}\.mp3$")
SOUND_RE = re.compile(r"\[sound:([^\]]*)\]")
# One segment = a <b>/<i> block plus any clip already attached to it.
SEG_RE = re.compile(r"<(b|i)>(.*?)</\1>(\[sound:[^\]]*\])?", re.S)

DEFAULTS = {
    "model_id": "eleven_multilingual_v2",
    "output_format": "mp3_44100_128",
    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75,
                       "style": 0.0, "use_speaker_boost": True},
    "language_code": None,
}


def die(msg):
    print("ERROR: %s" % msg)
    sys.exit(2)


class FatalError(RuntimeError):
    """401/403 (key) and 402 (plan/voice entitlement). Never per-segment: these
    are wrong for EVERY segment, so the run aborts instead of replaying the same
    rejection once per clip."""


# --------------------------------------------------------------------------
# credentials & config
# --------------------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        user = json.load(io.open(CONFIG, encoding="utf-8"))
        for k, v in user.items():
            if re.search(r"key|token|secret|password", k, re.I) or \
               (isinstance(v, str) and v.startswith("sk_")):
                die("%s must not contain secrets (%r) -- keep them in .env" % (CONFIG, k))
            cfg[k] = v
    return cfg


def profile_string(cfg, voice_id):
    """Everything that can change how a clip SOUNDS. Hashing the whole profile
    means a tweaked voice setting produces new clips instead of silently
    serving stale audio from the cache."""
    return "|".join([
        voice_id, cfg["model_id"], cfg["output_format"],
        json.dumps(cfg["voice_settings"], sort_keys=True, separators=(",", ":")),
        cfg.get("language_code") or "",
    ])


# --------------------------------------------------------------------------
# HTML -> speakable Romanian
# --------------------------------------------------------------------------

# (se)/(mă)/... is notation: the word is spoken, the brackets are not.
NOTATION = re.compile(r"\(\s*(se|mă|te|ne|vă|îmi|îți|își|-și|ez|esc)\s*\)", re.I)
# (pl) / (pers. 3) / (col.) is metadata about the word: not spoken at all.
META = re.compile(r"\s*\((?:pl|sg|n|f|m|pers[^)]*|col|fam|reg|înv|fig|arg|pop|"
                  r"vulg|rar|peior)\.?\)", re.I)
TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")   # gloss: (răcitură), (Mica Unire)


def tts_text(seg):
    """Order is load-bearing. Above all, <u> must strip to EMPTY, not to a
    space: pi<u>e</u>ptăn has to become pieptăn, or the voice reads markup."""
    t = SOUND_RE.sub("", seg)                       # 1. our tags first -- the
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.I)    #    hash derives from clean text
    t = html.unescape(t).replace("\xa0", " ")
    t = re.sub(r"<[^>]*>", "", t)                   # 2. remaining tags -> EMPTY
    t = NOTATION.sub(r"\1", t)                      # 3. keep the word, drop parens
    t = META.sub("", t)                             # 4. drop the metadata
    t = TRAILING_PAREN.sub("", t)                   # 5. drop a trailing gloss
    t = t.replace("/", ",").replace("+", ",")       # 6. mici / mititei -> mici, mititei
    t = t.replace("…", "").replace("...", "")
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s*,\s*", ", ", t)
    return t.strip(" ,")


def refuse(text):
    """Return a reason to skip, or None. Cheaper than paying for garbage audio."""
    if len(text) < 2:
        return "too short"
    if re.search(r"[<>\[\]]", text):
        return "markup survived extraction"
    return None


def slug(text, limit=32):
    s = unicodedata.normalize("NFKD", text.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limit].strip("-") or "x"


def clip_name(text, prof):
    h = hashlib.sha1((prof + "\0" + text).encode("utf-8")).hexdigest()[:12]
    return "ro-%s-%s.mp3" % (slug(text), h)


# --------------------------------------------------------------------------
# ElevenLabs
# --------------------------------------------------------------------------

def api_get(path, key):
    req = urllib.request.Request(API + path, headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        die("HTTP %s from %s: %s" % (e.code, path, e.read().decode("utf-8", "replace")[:300]))


def synthesize(text, cfg, voice_id, key):
    body = {"text": text, "model_id": cfg["model_id"],
            "voice_settings": cfg["voice_settings"]}
    if cfg.get("language_code"):
        body["language_code"] = cfg["language_code"]
    url = "%s/text-to-speech/%s?output_format=%s" % (API, voice_id, cfg["output_format"])
    delay = 2
    for attempt in range(4):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"xi-api-key": key, "Content-Type": "application/json",
                     "Accept": "audio/mpeg"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                ctype = r.headers.get("Content-Type", "")
                audio = r.read()
            # Verify BEFORE caching: a JSON error body written to foo.mp3 would
            # poison the cache permanently, since the cache is keyed on existence.
            if "audio" not in ctype:
                raise RuntimeError("unexpected Content-Type %r" % ctype)
            if not (audio[:3] == b"ID3" or audio[:2] in
                    (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa")):
                raise RuntimeError("not an MPEG stream (%d bytes)" % len(audio))
            return audio
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403, 402):
                raise FatalError("HTTP %s: %s" % (e.code, detail))
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(delay); delay *= 2; continue
            raise RuntimeError("HTTP %s: %s" % (e.code, detail))
        except Exception as e:
            if attempt < 3:
                time.sleep(delay); delay *= 2; continue
            raise RuntimeError(str(e))


def store(name, audio):
    os.makedirs(MEDIA, exist_ok=True)
    dst = os.path.join(MEDIA, name)
    tmp = dst + ".part"
    with io.open(tmp, "wb") as f:
        f.write(audio)
    os.replace(tmp, dst)        # a truncated download never becomes a cache entry


def install(name, anki_dir):
    """Copy into collection.media. Returns True if a copy happened."""
    if not anki_dir or not os.path.isdir(anki_dir):
        return False
    src, dst = os.path.join(MEDIA, name), os.path.join(anki_dir, name)
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        return False
    tmp = dst + ".part"                     # Anki scans this directory; a half-
    shutil.copyfile(src, tmp)               # written mp3 must never be visible
    os.replace(tmp, dst)
    return True


# --------------------------------------------------------------------------
# CSV
# --------------------------------------------------------------------------

def split_row(line):
    """Same '";"' token validate.py uses: bare semicolons occur inside fields."""
    parts = line.split('";"')
    if len(parts) != 2 or not line.startswith('"') or not line.endswith('"'):
        return None
    return parts[0][1:], parts[1][:-1]


def walk_ro(ro, handle):
    """Rewrite the Romanian field, calling handle(kind, text, existing) -> name
    or None per segment. Runs on the Romanian field ONLY -- </i> appears on the
    Ukrainian side too, so a whole-line replace would tag the Ukrainian sentence.

    `existing` is the clip already attached to this segment (ours, or None). It
    matters because returning None renders as no tag, which ERASES whatever was
    there. For a CSV that is the wanted repair; for a live Anki note it would be
    silent data loss, so backfill.py's handler returns `existing` unchanged
    whenever it declines to spend."""
    def repl(m):
        kind, inner, existing = m.group(1), m.group(2), m.group(3)
        old = SOUND_RE.match(existing).group(1) if existing else None
        if old and not NAME_RE.match(old):
            return m.group(0)               # hand-placed clip: leave it alone
        name = handle(kind, inner, old)
        tag = "[sound:%s]" % name if name else ""
        return "<%s>%s</%s>%s" % (kind, inner, kind, tag)
    return SEG_RE.sub(repl, ro)


def read_rows(path):
    if not os.path.exists(path):
        die("no such file: %s" % path)
    return io.open(path, encoding="utf-8").read().split("\n")


def write_rows(path, lines):
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_voices():
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not key:
        die("ELEVENLABS_API_KEY not set (.env in project root)")
    data = api_get("/voices", key)
    current = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        mark = " <-- ELEVENLABS_VOICE_ID" if v.get("voice_id") == current else ""
        print("%-24s %-22s %s%s" % (
            v.get("voice_id", "?"), v.get("name", "?"),
            ", ".join("%s=%s" % kv for kv in sorted(labels.items())), mark))
    return 0


def cmd_add(path, dry, limit=None):
    return run_add(path, dry, limit)[0]


def run_add(path, dry, limit=None, quiet=False):
    """-> (exit code, stats). quiet drops the per-segment dry-run lines."""
    cfg = load_config()
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    anki = os.environ.get("ANKI_MEDIA_DIR", "").strip()
    if not voice:
        die("ELEVENLABS_VOICE_ID not set (.env in project root)")
    if not dry and not key:
        die("ELEVENLABS_API_KEY not set (.env in project root)")
    if not dry and anki and not os.path.isdir(anki):
        die("ANKI_MEDIA_DIR does not exist: %s" % anki)
    prof = profile_string(cfg, voice)

    stat = {"new": 0, "cached": 0, "failed": 0, "refused": 0, "installed": 0,
            "chars": 0, "skipped": 0, "attempted": 0}
    lines, out, fatal = read_rows(path), [], None

    for n, line in enumerate(lines, 1):
        if fatal:
            out.append(line)                    # abort: pass the rest through
            continue
        if not line.strip():
            out.append(line)
            continue
        row = split_row(line)
        if row is None:
            print("line %d: bad field structure -- left untouched" % n)
            stat["failed"] += 1
            out.append(line)
            continue
        ua, ro = row

        def handle(kind, inner, _existing=None, _n=n):
            text = tts_text(inner)
            why = refuse(text)
            if why:
                print("line %d %s: REFUSED (%s) %r" % (_n, kind, why, text))
                stat["refused"] += 1
                return None
            name = clip_name(text, prof)
            have = os.path.exists(os.path.join(MEDIA, name))
            if dry:
                if not quiet:
                    print("line %-4d %s  %-46s -> %s  %s" % (
                        _n, kind, text[:46], name, "CACHED" if have else "NEW"))
                if not have:
                    stat["new"] += 1; stat["chars"] += len(text)
                else:
                    stat["cached"] += 1
                return name
            if have:
                stat["cached"] += 1
            else:
                # Count ATTEMPTS, not successes: --limit is a spend guard, and a
                # failing endpoint must not be allowed to burn through the batch.
                if limit is not None and stat["attempted"] >= limit:
                    stat["skipped"] += 1
                    return None                 # untagged; a later run fills it
                stat["attempted"] += 1
                try:
                    store(name, synthesize(text, cfg, voice, key))
                except FatalError:
                    raise                       # aborts the run, see below
                except Exception as e:
                    print("line %d %s: FAILED %r -- %s" % (_n, kind, text[:40], e))
                    stat["failed"] += 1
                    return None                 # no tag: a re-run fills the gap
                stat["new"] += 1; stat["chars"] += len(text)
            if install(name, anki):
                stat["installed"] += 1
            return name

        try:
            out.append('"%s";"%s"' % (ua, walk_ro(ro, handle)))
        except FatalError as e:
            fatal = e
            out.append(line)

    if fatal:
        print("\nABORTED after the first rejection -- it applies to every segment,\n"
              "so nothing further was sent and nothing was billed.\n  %s\n\n"
              "  401/403  the key is wrong or under-scoped: check ELEVENLABS_API_KEY\n"
              "           in .env and enable 'text_to_speech' (plus 'voices_read'\n"
              "           if you want the `voices` subcommand).\n"
              "  402      the plan does not cover this voice. Shared/library voices\n"
              "           need a paid plan; run `voices` to see what this account\n"
              "           may actually use." % fatal)

    if dry:
        print("\nDRY RUN -- nothing written, nothing sent.")
    else:
        write_rows(path, out)

    print("%s: %d new, %d cached, %d refused, %d failed, %d copied to collection.media, "
          "%d characters billed" % (rel(path), stat["new"], stat["cached"], stat["refused"],
                                    stat["failed"], stat["installed"], stat["chars"]))
    if stat["skipped"]:
        print("%d segment(s) left untagged by --limit %d -- re-run to complete."
              % (stat["skipped"], limit))
    if stat["failed"]:
        print("%d segment(s) failed -- re-run to complete." % stat["failed"])
    stat["model_id"] = cfg["model_id"]
    return (1 if (stat["failed"] or fatal) else 0), stat


def cmd_check(path):
    """Catch text edited after its audio was generated. No API calls."""
    cfg = load_config()
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    if not voice:
        die("ELEVENLABS_VOICE_ID not set (.env in project root)")
    prof = profile_string(cfg, voice)
    bad = 0
    for n, line in enumerate(read_rows(path), 1):
        if not line.strip():
            continue
        row = split_row(line)
        if row is None:
            print("line %d: bad field structure" % n); bad += 1; continue
        for m in SEG_RE.finditer(row[1]):
            kind, inner, existing = m.group(1), m.group(2), m.group(3)
            if not existing:
                print("line %d %s: no audio" % (n, kind)); bad += 1; continue
            old = SOUND_RE.match(existing).group(1)
            if not NAME_RE.match(old):
                continue                        # hand-placed, not ours to judge
            text = tts_text(inner)
            want = clip_name(text, prof)
            if old != want:
                print("line %d %s: DRIFTED %r\n    has  %s\n    want %s"
                      % (n, kind, text[:60], old, want)); bad += 1
            elif not os.path.exists(os.path.join(MEDIA, old)):
                print("line %d %s: missing from the media cache: %s" % (n, kind, old)); bad += 1
    print("%s: %s" % (path, "IN SYNC" if not bad else "%d PROBLEM(S) -- re-run `add`" % bad))
    return 1 if bad else 0


def cmd_orphans():
    """List cached clips nothing references. Read-only, by design: Anki's
    Tools -> Check Media is the right tool for deleting from collection.media.

    "Referenced" = a [sound:] tag on any live note (the source of truth), or in
    the project's own records: batch CSVs (a batch written but not imported yet)
    and the import/backfill journals. With Anki closed only the records count,
    and they miss the cards made before v3 -- the report says which it used."""
    import glob
    used, live = set(), None
    try:
        from ankilib import ac, notes_info
        for n in notes_info(ac("findNotes", query="deck:*")):
            for f in n["fields"].values():
                used |= set(SOUND_RE.findall(f["value"]))
        live = len(used)
    except Exception:                       # Anki closed, or no AnkiConnect
        pass
    records = set()
    for pat in (os.path.join(DATA_DIR, "*_import.csv"), os.path.join(LOGS, "import-*.jsonl"),
                os.path.join(ROOT, "v2", "logs", "backfill-*.jsonl")):
        for p in sorted(glob.glob(pat)):
            records |= set(SOUND_RE.findall(io.open(p, encoding="utf-8").read()))
    used |= records
    have = set(os.listdir(MEDIA)) if os.path.isdir(MEDIA) else set()
    have = {f for f in have if f.endswith(".mp3")}
    orphans = sorted(have - used)
    missing = sorted(f for f in records - have if NAME_RE.match(f))
    for f in orphans:
        print("ORPHAN   %s" % f)
    for f in missing:
        print("MISSING  %s  (referenced but absent from %s)" % (f, rel(MEDIA)))
    print("%s: %d clips, %d referenced, %d orphaned, %d missing"
          % (rel(MEDIA), len(have), len(used & have), len(orphans), len(missing)))
    print("references from: %s" % ("the live collection + project records" if live is not None
                                    else "project records only -- Anki is closed, so clips of "
                                         "cards made before v3 may show as orphans"))
    return 1 if missing else 0


def estimate(fields):
    """Billable characters for Romanian fields. Cached segments are free, so
    they are counted separately -- the gate is on what gets billed.
    -> (stats, report lines)"""
    cfg = load_config()
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip()
    prof = profile_string(cfg, voice) if voice else None
    stat = {"segments": 0, "billable": 0, "cached": 0, "refused": 0, "chars": 0}
    out = []
    for n, line in enumerate(fields, 1):
        line = line.strip()
        if not line:
            continue
        for m in SEG_RE.finditer(line):
            text = tts_text(m.group(2))
            if refuse(text):
                out.append("line %d %s: would be REFUSED (%s) %r"
                           % (n, m.group(1), refuse(text), text))
                stat["refused"] += 1
                continue
            stat["segments"] += 1
            if prof and os.path.exists(os.path.join(MEDIA, clip_name(text, prof))):
                stat["cached"] += 1
                continue
            stat["billable"] += 1
            stat["chars"] += len(text)
    out.append("%d segments: %d to synthesize, %d cached, %d refused -- %d characters billable"
               % (stat["segments"], stat["billable"], stat["cached"], stat["refused"],
                  stat["chars"]))
    if not prof:
        out.append("(ELEVENLABS_VOICE_ID not set: cache not consulted, every segment counted)")
    limit = tts_confirm_chars()
    stat["needs_approval"] = stat["chars"] >= limit
    out.append("GATE %s (%d billable, approval needed at %d -- about %d ElevenLabs credits)"
               % ("needs approval" if stat["needs_approval"] else "pass", stat["chars"],
                  limit, round(stat["chars"] * 0.375)))
    return stat, out


def cmd_estimate(src=None):
    if src:
        text = io.open(src, encoding="utf-8-sig").read()
    else:
        sys.stdin.reconfigure(encoding="utf-8")
        text = sys.stdin.read()
    print("\n".join(estimate(text.splitlines())[1]))
    return 0


def main(argv):
    load_env()
    if not argv:
        print(__doc__); return 2
    cmd = argv[0]
    if cmd == "voices":
        return cmd_voices()
    if cmd == "orphans":
        return cmd_orphans()
    if cmd == "estimate":
        src = argv[argv.index("--from") + 1] if "--from" in argv[:-1] else None
        return cmd_estimate(src)
    if cmd in ("add", "check"):
        limit = None
        rest = argv[1:]
        if "--limit" in rest:
            i = rest.index("--limit")
            limit = int(rest[i + 1])
            rest = rest[:i] + rest[i + 2:]
        args = [a for a in rest if not a.startswith("--")]
        if len(args) != 1:
            print(__doc__); return 2
        if args[0].endswith("_grammar.csv"):
            print("%s: grammar cards are text-only -- no audio by the learner's choice"
                  % args[0]); return 2
        if cmd == "check":
            return cmd_check(args[0])
        return cmd_add(args[0], "--dry-run" in rest, limit)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
