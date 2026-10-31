#!/usr/bin/env python3
"""AnkiConnect plumbing for the card scripts (known.py, anki_add.py).

The request helper, deck check, preflight, backup and media upload are copied
from v2/scripts/backfill.py, where they carried a 1,619-note write without a
single scheduling change. backfill.py keeps its own copy on purpose: it is
finished, proven code and is not re-plumbed. Keep the two in step if
AnkiConnect ever changes.

One deliberate difference: ac() RAISES Unreachable instead of exiting, so a
script can tell "Anki is not running" (stop before any work) from "Anki died
mid-import" (abort, journal says where, re-run resumes). Each script's entry
point turns an uncaught Unreachable into a clean exit 2.

Env (.env in the project root; real environment variables win):
    ANKI_CONNECT_URL   default http://127.0.0.1:8765
    ANKI_CONNECT_KEY   optional -- only if AnkiConnect is configured with a key
Paths and the .env parser live in config.py.
"""
import io, os, re, sys, json, time, socket
import urllib.request, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate import cyr
from config import ROOT, MEDIA, BACKUPS, load_env, fields, cyrillic_translation


class Unreachable(RuntimeError):
    """Anki is not answering. Subclasses RuntimeError so a write loop's
    `except RuntimeError` aborts on it like on any other failed call."""


def die(msg):
    print("ERROR: %s" % msg)
    sys.exit(2)


def ac(action, **params):
    url = os.environ.get("ANKI_CONNECT_URL", "http://127.0.0.1:8765")
    body = {"action": action, "version": 6, "params": params}
    key = os.environ.get("ANKI_CONNECT_KEY", "").strip()
    if key:
        body["key"] = key
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=300).read().decode("utf-8"))
    except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
        raise Unreachable(
            "AnkiConnect unreachable at %s (%s).\n"
            "Anki must be running with the AnkiConnect add-on (code 2055492159)." % (url, e))
    if r.get("error"):
        raise RuntimeError("%s: %s" % (action, r["error"]))
    return r["result"]


def quote_term(s):
    return '"%s"' % re.sub(r'([\\"*_])', r"\\1", s)


def notes_info(nids, chunk=200):
    out = []
    for i in range(0, len(nids), chunk):
        out.extend(ac("notesInfo", notes=nids[i:i + chunk]))
    return out


def preflight(actions_used, writing):
    if ac("version") < 6:
        die("AnkiConnect version 6+ required")
    actions = set(ac("apiReflect", scopes=["actions"], actions=None)["actions"])
    missing = [a for a in actions_used if a not in actions]
    if missing:
        die("AnkiConnect is missing required actions: %s" % ", ".join(missing))
    if writing and ac("guiReviewActive"):
        die("the Anki reviewer is open. Every write pulls the next card, which makes\n"
            "reviewing unusable mid-run. Close the reviewer, or pass --force.")


def backup(ds):
    """One restore point per deck per day, independent of Anki's own rotation.
    Taken before the day's FIRST write; later writes that day reuse it, so it
    is the state at the start of the day, not just before each import.
    -> one line describing what happened, for the caller to print."""
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d")
    out = []
    for d in ds:
        path = "%s/%s_%s_sched.apkg" % (BACKUPS, stamp, re.sub(r"\W+", "-", d))
        name = os.path.basename(path)
        if os.path.exists(path):
            out.append("reusing today's %s (taken %s)"
                       % (name, time.strftime("%H:%M", time.localtime(os.path.getmtime(path)))))
            continue
        ac("exportPackage", deck=d, path=path, includeSched=True)
        out.append("%s (%.1f MB) in %s" % (name, os.path.getsize(path) / 1e6, BACKUPS))
    return ", ".join(out)


def ensure_media(name, have, media_dir=MEDIA):
    """storeMediaFile registers the clip in Anki's media DB (so AnkiWeb syncs it
    deterministically); `have` is the caller's cache of names already there."""
    if name in have:
        return
    got = ac("storeMediaFile", filename=name,
             path=os.path.abspath(os.path.join(media_dir, name)), deleteExisting=False)
    if got and got != name:
        # Anki renames on a same-name/different-content clash. Our names are
        # content-addressed, so this means the collection holds a foreign file.
        raise RuntimeError("storeMediaFile stored %s as %s -- name clash in collection.media"
                           % (name, got))
    have.add(name)


def romanian_field(note):
    """The field with no Cyrillic. Exactly one candidate, or None: 14 notes in
    the old export have Cyrillic on both sides or neither. A learner whose
    translation language is not Cyrillic cannot be told apart by script, so for
    them it is the configured Romanian field (ANKI_BACK_FIELD) when the note has
    one."""
    names = list(note["fields"])
    if not cyrillic_translation():
        back = fields()[1]
        return (back, None) if back in names else (None, "no %s field" % back)
    if len(names) != 2:
        return None, "notetype has %d fields" % len(names)
    cand = [n for n in names if not cyr(note["fields"][n]["value"])]
    if len(cand) != 1:
        return None, "cannot tell which field is Romanian"
    return cand[0], None
