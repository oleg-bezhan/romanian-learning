#!/usr/bin/env python3
"""The setup page: every choice a learner makes during setup, in the browser.

Started by `setup.py page [--step STEP]`, which runs this server in the
background (detached, so the assistant's command returns at once), opens the
browser on it and prints the address. The page itself is
.agents/skills/setup/page/index.html; it walks the learner through

    level -> grammar -> mode -> Anki deck -> ElevenLabs key -> voice

and saves each step the moment they press Next. No AI is involved: the choices
cost no tokens and look the same in every assistant.

Safety:
  - listens on 127.0.0.1 only; every /api call needs the random token from the
    page address, and the Host header must name this server (no DNS rebinding);
  - the ElevenLabs key goes from the page straight into .env, and only after
    ElevenLabs accepts it; it is never sent back to the page, logged or printed;
  - the only Anki write is creating a new, empty deck, through anki_add.py;
  - stops after "done", or after 2 hours without a request.
"""
import io, os, re, sys, json, time, socket, secrets, tempfile, threading, subprocess
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from config import ROOT, LEARNER, PROFILE, MODES, SETUP_PAGE, RUNTIME, rel
from ankilib import ac, Unreachable
import setup
import profile

STATE = os.path.join(RUNTIME, "settings-page.json")
LOG = os.path.join(RUNTIME, "settings-page.log")
SAMPLES = os.path.join(RUNTIME, "voice-samples")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/text.json": ("text.json", "application/json; charset=utf-8"),
          "/grammar.json": ("grammar.json", "application/json; charset=utf-8")}
STEPS = ("level", "grammar", "mode", "anki", "key", "voice", "done")
IDLE_SECONDS = 2 * 3600
# The voice the project's own cards use. A Voice Library voice: the page asks the
# learner to add it to their ElevenLabs voices, then offers it first.
RECOMMENDED_VOICE = {"id": "8nBBDfYxYXmDNaqTCxPH", "name": "Serban Popescu - Professional Narrator"}
SETUP_PY = os.path.join(config.SCRIPTS, "setup.py")
ANKI_ADD = os.path.join(config.SCRIPTS, "anki_add.py")


# --------------------------------------------------------------------------
# what the page reads
# --------------------------------------------------------------------------

def state():
    l = config.learner()
    chat = l["chat_language"].strip().lower()
    statuses = None
    if os.path.exists(PROFILE):
        statuses = {r["tag"]: r["status"] for r in profile.load_rows(PROFILE)}
    return {"ui": "uk" if chat in ("ukrainian", "українська", "uk") else "en",
            "learner": {"language": l["language"], "chat_language": l["chat_language"],
                        "level": l["level"] if l["_exists"] else "", "mode": l["mode"]},
            "statuses": statuses, "modes": list(MODES)}


def anki_state():
    try:
        decks = ac("deckNames")
        models = ac("modelNames")
    except Unreachable:
        return {"ok": False}
    front, back = config.fields()
    out = {"ok": True, "deck": setup.env_value("ANKI_IMPORT_DECK"),
           "model": setup.env_value("ANKI_MODEL"), "decks": [], "models": []}
    for d in decks:
        out["decks"].append({"name": d, "cards": len(ac("findCards", query='deck:"%s"'
                                                         % d.replace('"', '\\"')))})
    for m in models:
        f = ac("modelFieldNames", modelName=m)
        out["models"].append({"name": m, "fields": f, "ok": front in f and back in f})
    return out


def voices_state():
    key = setup.env_value(setup.SECRET)
    out = {"key_set": bool(key), "key_ok": False, "voice_id": setup.env_value("ELEVENLABS_VOICE_ID"),
           "voices": [], "recommended": dict(RECOMMENDED_VOICE, in_account=False)}
    if not key:
        return out
    code, data = setup.eleven("/voices", key)
    if code != 200:
        out["error"] = "offline" if code == 0 else "refused"
        return out
    out["key_ok"] = True
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        out["voices"].append({"id": v.get("voice_id"), "name": v.get("name"),
                              "labels": [str(labels[k]) for k in ("gender", "accent", "age",
                                                                  "description", "use_case")
                                         if labels.get(k)],
                              "preview_url": v.get("preview_url")})
    rec = RECOMMENDED_VOICE["id"]
    out["recommended"]["in_account"] = any(v["id"] == rec for v in out["voices"])
    out["voices"].sort(key=lambda v: v["id"] != rec)          # the recommended one first
    return out


# --------------------------------------------------------------------------
# what the page saves
# --------------------------------------------------------------------------

def save(step, data):
    """-> (ok, message, extra dict)"""
    if step == "level":
        level = str(data.get("level", "")).upper()
        if level not in setup.levels():
            return False, "unknown level", {}
        setup.update_learner({"level": level})
        return True, "level %s" % level, {}
    if step == "grammar":
        statuses = data.get("statuses")
        if not isinstance(statuses, dict):
            return False, "statuses missing", {}
        if os.path.exists(PROFILE):
            statuses = dict(statuses, **setup.grammar_data()["locked"])
            rc, lines = profile.set_statuses(PROFILE, statuses)
            return rc == 0, "; ".join(lines), {}
        # A first profile: anything the page didn't send comes from the learner's
        # level, never from the seed (which is the B1 preset).
        level = config.learner()["level"]
        base = setup.suggested(level) if level in setup.levels() else {}
        statuses = dict(base, **statuses)
        statuses.update(setup.grammar_data()["locked"])
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            json.dump(statuses, io.open(tmp, "w", encoding="utf-8"))
            out = io.StringIO()
            from contextlib import redirect_stdout
            with redirect_stdout(out):
                rc = profile.cmd_init(PROFILE, tmp, False)
        finally:
            os.remove(tmp)
        return rc == 0, out.getvalue().strip(), {}
    if step == "mode":
        mode = data.get("mode")
        if mode not in MODES:
            return False, "unknown mode", {}
        setup.update_learner({"mode": mode})
        return True, "mode %s" % mode, {}
    if step == "anki":
        deck, model = str(data.get("deck", "")).strip(), str(data.get("model", "")).strip()
        if not deck or not model:
            return False, "deck and note type are needed", {}
        try:
            if data.get("new_deck"):
                r = subprocess.run([sys.executable, ANKI_ADD, "create-deck", deck],
                                   capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
                if r.returncode:
                    return False, "could not create the deck", {}
            have = ac("modelFieldNames", modelName=model)
            front, back = config.fields()
            if front not in have or back not in have:
                return False, "this note type has no %s and %s fields" % (front, back), {}
            media = ac("getMediaDirPath")
        except Unreachable:
            return False, "anki-closed", {}
        setup.write_env("ANKI_IMPORT_DECK", deck)
        setup.write_env("ANKI_MODEL", model)
        setup.write_env("ANKI_MEDIA_DIR", media)
        return True, "deck %s, note type %s" % (deck, model), {}
    if step == "key":
        key = str(data.get("key", "")).strip()
        if not re.match(r"^[A-Za-z0-9_\-]{20,200}$", key):
            return False, "not-a-key", {}
        code, _ = setup.eleven("/voices", key)
        if code != 200:
            return False, "offline" if code == 0 else "refused", {}
        setup.write_env(setup.SECRET, key)
        return True, "key saved", {}
    if step == "voice":
        voice = str(data.get("voice_id", "")).strip()
        if not re.match(r"^[A-Za-z0-9]{6,64}$", voice):
            return False, "unknown voice", {}
        setup.write_env("ELEVENLABS_VOICE_ID", voice)
        return True, "voice %s" % voice, {}
    return False, "unknown step", {}


def voice_sample(voice):
    """One short Romanian sentence in that voice -- billed once, then cached."""
    if not re.match(r"^[A-Za-z0-9]{6,64}$", voice or ""):
        raise ValueError("unknown voice")
    path = os.path.join(SAMPLES, "%s.mp3" % voice)
    if not os.path.exists(path):
        import tts
        key = setup.env_value(setup.SECRET)
        if not key:
            raise ValueError("no key")
        audio = tts.synthesize(setup.SAMPLE, tts.load_config(), voice, key)
        os.makedirs(SAMPLES, exist_ok=True)
        io.open(path, "wb").write(audio)
    return io.open(path, "rb").read()


# --------------------------------------------------------------------------
# the server
# --------------------------------------------------------------------------

def serve(port, token):
    last = [time.time()]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):         # never log bodies: the key passes here
            pass

        def reply(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def json(self, code, obj):
            self.reply(code, "application/json; charset=utf-8",
                       json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def allowed(self, api):
            if self.headers.get("Host") not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
                self.json(403, {"error": "wrong host"})
                return False
            if api and not secrets.compare_digest(self.headers.get("X-Token", ""), token):
                self.json(403, {"error": "wrong token"})
                return False
            last[0] = time.time()
            return True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                if not self.allowed(False):
                    return
                name, ctype = STATIC[path]
                return self.reply(200, ctype, io.open(os.path.join(SETUP_PAGE, name), "rb").read())
            if not self.allowed(True):
                return
            if path == "/api/ping":
                return self.json(200, {"ok": True})
            if path == "/api/state":
                return self.json(200, state())
            if path == "/api/anki":
                return self.json(200, anki_state())
            if path == "/api/voices":
                return self.json(200, voices_state())
            self.json(404, {"error": "not found"})

        def do_POST(self):
            if not self.allowed(True):
                return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                data = json.loads(self.rfile.read(n).decode("utf-8") or "{}") if n else {}
            except ValueError:
                return self.json(400, {"error": "bad json"})
            path = self.path.split("?", 1)[0]
            m = re.match(r"^/api/save/([a-z]+)$", path)
            if m:
                try:
                    ok, msg, extra = save(m.group(1), data)
                except Exception as e:                     # shown on the page, not a crash
                    ok, msg, extra = False, "error: %s" % str(e)[:200], {}
                return self.json(200 if ok else 400, dict(extra, ok=ok, message=msg))
            if path == "/api/sample":
                try:
                    return self.reply(200, "audio/mpeg", voice_sample(data.get("voice_id")))
                except Exception as e:
                    return self.json(400, {"ok": False, "message": str(e)[:200]})
            if path == "/api/done":
                self.json(200, {"ok": True})
                threading.Thread(target=httpd.shutdown, daemon=True).start()
                return
            self.json(404, {"error": "not found"})

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False      # see evals/fake_services.py

    httpd = Server(("127.0.0.1", port), Handler)
    os.makedirs(RUNTIME, exist_ok=True)
    json.dump({"port": port, "token": token, "pid": os.getpid()},
              io.open(STATE, "w", encoding="utf-8"))

    def watch():
        while True:
            time.sleep(30)
            if time.time() - last[0] > IDLE_SECONDS:
                httpd.shutdown()
                return
    threading.Thread(target=watch, daemon=True).start()
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
        try:
            if json.load(io.open(STATE, encoding="utf-8")).get("pid") == os.getpid():
                os.remove(STATE)
        except (OSError, ValueError):
            pass
    return 0


# --------------------------------------------------------------------------
# launching
# --------------------------------------------------------------------------

def call(st, path, body=None, timeout=5):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (st["port"], path),
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-Token": st["token"],
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def running():
    """-> the running server's {port, token, pid}, or None"""
    try:
        st = json.load(io.open(STATE, encoding="utf-8"))
        if call(st, "/api/ping", timeout=2).get("ok"):
            return st
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        pass
    return None


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start():
    """Start the server detached from this process -> {port, token, pid}"""
    port, token = free_port(), secrets.token_urlsafe(18)
    os.makedirs(RUNTIME, exist_ok=True)
    log = io.open(LOG, "ab")
    cmd = [sys.executable, SETUP_PY, "serve", "--port", str(port), "--token", token]
    kw = dict(stdin=subprocess.DEVNULL, stdout=log, stderr=log, cwd=ROOT, close_fds=True)
    if os.name == "nt":
        detached = 0x00000008 | 0x00000200       # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:   # also leave the assistant's job object, so ending its command doesn't end us
            subprocess.Popen(cmd, creationflags=detached | 0x01000000, **kw)
        except OSError:
            subprocess.Popen(cmd, creationflags=detached, **kw)
    else:
        subprocess.Popen(cmd, start_new_session=True, **kw)
    log.close()
    st = {"port": port, "token": token}
    for _ in range(60):
        time.sleep(0.25)
        try:
            if call(st, "/api/ping", timeout=1).get("ok"):
                return st
        except (OSError, urllib.error.URLError):
            pass
    return None


def launch(step=None, open_browser=True):
    if step and step not in STEPS:
        print("--step is one of: %s" % ", ".join(STEPS))
        return 2
    st = running() or start()
    if not st:
        print("TODO the settings page did not start -- see %s" % rel(LOG))
        return 1
    url = "http://127.0.0.1:%d/?t=%s%s" % (st["port"], st["token"], "#" + step if step else "")
    opened = False
    if open_browser:
        import webbrowser
        opened = webbrowser.open(url)
    print("OK   the settings page is %s:\n     %s\n     It closes itself when the learner "
          "presses Done (or after 2 hours unused)."
          % ("open in the browser" if opened else "ready -- open this address in the browser", url))
    return 0
