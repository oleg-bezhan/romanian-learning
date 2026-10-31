#!/usr/bin/env python3
"""Helpers for the setup skill: checks and settings in plain language.

Usage:
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py status
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py learner --language NAME --code xx [--chat-language NAME] [--level B1] [--mode quality]
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py page [--step level|grammar|mode|anki|key|voice]
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py permissions
  fallbacks, when the page cannot be used:
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py env-init
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py open-env
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py env-set KEY VALUE
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py anki
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py voices
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py test-voice [VOICE_ID]
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py grammar --level B1 [--answers FILE] [--force]
    runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py mode quality|balanced|lean

status       every setup step as OK / TODO, in words a non-technical learner understands
learner      creates learner.md, or changes only the settings given (the rest of
             the file is the learner's own prose). The language step of setup
             writes language, code and chat language; the page writes the rest.
page         opens the settings page in the browser (settings_page.py): level,
             grammar, mode, Anki deck, ElevenLabs key, voice. Each step is
             saved as the learner goes; nothing passes through the chat.
permissions  lets Claude Code run this skill's scripts, read dexonline and save
             card files without asking each time (.claude/settings.local.json).
             Other assistants keep their own settings: it prints what to tell
             the learner instead, and changes nothing outside this folder.
env-init     creates .env from .env.example (no secrets in it yet)
open-env     opens .env in the text editor, so the learner pastes the key themselves
env-set      sets one non-secret setting in .env (the API key is refused on purpose)
anki         lists decks, note types (with fields) and Anki's media folder -- read-only
voices       the ElevenLabs voices this key may use
test-voice   speaks one short Romanian sentence (~35 characters billed) and plays it
grammar      writes grammar-profile.md from a level's preset plus changes
             ({"<tag>": "allowed|learning|blocked"}); never overwrites a profile
             with history unless --force (the old one is kept as .bak)
mode         sets the token mode in learner.md

Presets and the page's grammar examples: .agents/skills/setup/page/grammar.json.
Writes to Anki only through anki_add.py; never prints the ElevenLabs key.
"""
import io, os, re, sys, json, shutil, subprocess, tempfile
import urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from config import ROOT, TEMPLATES, LEARNER, PROFILE, MODES, SETUP_PAGE, RUNTIME, rel
from ankilib import ac, Unreachable

ENV = config.ENV_FILE
ENV_EXAMPLE = os.path.join(ROOT, ".env.example")
SECRET = "ELEVENLABS_API_KEY"
SETTINGS = os.path.join(ROOT, ".claude", "settings.local.json")
PYTHON = "runtime/python/python"
RUNTIME_EXE = os.path.join(ROOT, "runtime", "python", "python.exe")
_RUN = "%s %s/*" % (PYTHON, rel(config.SCRIPTS))
RULES = ["Bash(%s)" % _RUN, "PowerShell(%s)" % _RUN,
         "WebFetch(domain:dexonline.ro)", "Write(**)", "Edit(**)"]
SAMPLE = "Bună ziua! Mă bucur să te cunosc."
IN_CLAUDE = os.environ.get("CLAUDECODE") == "1"
ELEVEN = os.environ.get("ELEVENLABS_API_URL", "").strip() or "https://api.elevenlabs.io/v1"
GRAMMAR = os.path.join(SETUP_PAGE, "grammar.json")
DEFAULT_TOPICS = ("everyday life, work, shopping and services, travel, health, "
                  "home and family")


# --------------------------------------------------------------------------
# grammar presets (grammar.json)
# --------------------------------------------------------------------------

def grammar_data():
    return json.load(io.open(GRAMMAR, encoding="utf-8"))


def levels():
    return grammar_data()["levels"]


def suggested(level, tags=None):
    """-> {tag: status} for a level: its preset, everything else blocked."""
    g = grammar_data()
    level = level.upper()[:2]
    if level not in g["levels"]:
        raise ValueError("level must be one of %s" % ", ".join(g["levels"]))
    p = g["presets"][level]
    tags = tags or [c["tag"] for c in g["constructions"]]
    out = {t: "allowed" if t in p["allowed"] else "learning" if t in p["learning"] else "blocked"
           for t in tags}
    out.update(g["locked"])
    return out


# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------

def env_lines():
    return io.open(ENV, encoding="utf-8-sig").read().split("\n") if os.path.exists(ENV) else []


def env_value(key):
    for line in env_lines():
        k, sep, v = line.strip().partition("=")
        if sep and k.strip() == key:
            return v.strip().strip('"').strip("'")
    return ""


def cmd_env_init():
    if os.path.exists(ENV):
        print("OK   the settings file .env already exists -- nothing changed")
        return 0
    shutil.copyfile(ENV_EXAMPLE, ENV)
    print("OK   created the settings file .env")
    return 0


def cmd_open_env():
    if not os.path.exists(ENV):
        cmd_env_init()
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["notepad.exe", ENV])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-e", ENV])
        else:
            subprocess.Popen(["xdg-open", ENV])
    except OSError as e:
        print("TODO could not open the file automatically (%s). Open it by hand: %s" % (e, ENV))
        return 1
    print("OK   opened %s in the text editor. Paste the key after ELEVENLABS_API_KEY= ,\n"
          "     save the file (Ctrl+S, or Cmd+S on a Mac) and close the editor." % ENV)
    return 0


def write_env(key, value):
    """Set one line of .env. The settings page also writes the key through this,
    after ElevenLabs accepted it; the chat never can (cmd_env_set refuses it)."""
    if not os.path.exists(ENV):
        shutil.copyfile(ENV_EXAMPLE, ENV)
    lines, done = env_lines(), False
    for i, line in enumerate(lines):
        k, sep, _ = line.strip().partition("=")
        if sep and k.strip() == key:
            lines[i] = "%s=%s" % (key, value)
            done = True
    if not done:
        if lines and lines[-1] == "":
            lines.insert(len(lines) - 1, "%s=%s" % (key, value))
        else:
            lines.append("%s=%s" % (key, value))
    io.open(ENV, "w", encoding="utf-8", newline="\n").write("\n".join(lines))


def cmd_env_set(key, value):
    if key == SECRET:
        print("REFUSED  the ElevenLabs key is never set through the chat -- the settings page\n"
              "         (setup.py page --step key) or open-env lets the learner enter it.")
        return 2
    if not re.match(r"^[A-Z][A-Z0-9_]*$", key) or "\n" in value:
        print("REFUSED  not a valid setting: %r" % key)
        return 2
    write_env(key, value)
    print("OK   %s = %s" % (key, value))
    return 0


# --------------------------------------------------------------------------
# ElevenLabs
# --------------------------------------------------------------------------

def eleven(path, key):
    req = urllib.request.Request(ELEVEN + path,
                                 headers={"xi-api-key": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError) as e:
        return 0, str(e)


def cmd_voices():
    key = env_value(SECRET)
    if not key:
        print("TODO the ElevenLabs key is not in .env yet (setup.py page --step key)")
        return 1
    code, data = eleven("/voices", key)
    if code != 200:
        print("TODO ElevenLabs refused the key (HTTP %s). Check it was copied completely, and\n"
              "     that it has the 'Voices: read' and 'Text to speech' permissions." % code)
        return 1
    current = env_value("ELEVENLABS_VOICE_ID")
    for v in data.get("voices", []):
        labels = v.get("labels") or {}
        desc = ", ".join(str(labels[k]) for k in ("gender", "accent", "age", "description",
                                                  "use_case") if labels.get(k))
        print("%-24s %-24s %s%s" % (v.get("voice_id"), v.get("name"), desc,
                                    "   <- chosen now" if v.get("voice_id") == current else ""))
    return 0


def cmd_test_voice(voice=None):
    import tts
    key = env_value(SECRET)
    voice = voice or env_value("ELEVENLABS_VOICE_ID")
    if not key or not voice:
        print("TODO the key or the voice is not set yet")
        return 1
    try:
        audio = tts.synthesize(SAMPLE, tts.load_config(), voice, key)
    except Exception as e:
        print("TODO the voice did not work: %s" % str(e)[:200])
        return 1
    path = os.path.join(tempfile.gettempdir(), "romanian-voice-sample-%s.mp3" % voice[:8])
    io.open(path, "wb").write(audio)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        played = "playing it now"
    except OSError:
        played = "open this file to listen: %s" % path
    print("OK   spoke %r (%d characters) -- %s" % (SAMPLE, len(SAMPLE), played))
    return 0


# --------------------------------------------------------------------------
# Anki
# --------------------------------------------------------------------------

def cmd_anki():
    try:
        decks = ac("deckNames")
        models = ac("modelNames")
        media = ac("getMediaDirPath")
    except Unreachable:
        print("TODO Anki is not open, or the AnkiConnect add-on is not installed yet")
        return 1
    print("DECKS")
    for d in decks:
        print("  %-30s %d cards" % (d, len(ac("findCards", query='deck:"%s"' % d))))
    print("NOTE TYPES")
    for m in models:
        print("  %-30s fields: %s" % (m, ", ".join(ac("modelFieldNames", modelName=m))))
    print("MEDIA FOLDER\n  %s" % media)
    return 0


# --------------------------------------------------------------------------
# learner, grammar, mode
# --------------------------------------------------------------------------

def opt(args, flag, default=None):
    return args[args.index(flag) + 1] if flag in args[:-1] else default


def update_learner(fields):
    """Create learner.md from the template (topics: the general default list), or
    change only the given `key: value` settings -- every other line is the
    learner's own."""
    if not os.path.exists(LEARNER):
        d = dict(config.LEARNER_DEFAULTS, level="", topics=DEFAULT_TOPICS)
        d.update(fields)
        d.setdefault("chat_language", d["language"])
        text = io.open(os.path.join(TEMPLATES, "learner.example.md"), encoding="utf-8").read()
        for k in ("language_code", "chat_language", "language", "level", "mode", "topics"):
            text = text.replace("{{%s}}" % k.upper(), d[k])
        os.makedirs(os.path.dirname(LEARNER), exist_ok=True)
        io.open(LEARNER, "w", encoding="utf-8", newline="\n").write(text)
        return
    lines = io.open(LEARNER, encoding="utf-8-sig").read().split("\n")
    head = next((i for i, l in enumerate(lines) if l.startswith("## ")), len(lines))
    for k, v in fields.items():
        hit = next((i for i in range(head) if re.match(r"^%s:" % re.escape(k), lines[i])), None)
        if hit is not None:
            lines[hit] = "%s: %s" % (k, v)
            continue
        last = max([i for i in range(head) if config.SETTING.match(lines[i].strip())
                    and ":" in lines[i]] or [head - 1])
        lines.insert(last + 1, "%s: %s" % (k, v))
        head += 1
    io.open(LEARNER, "w", encoding="utf-8", newline="\n").write("\n".join(lines))


def cmd_learner(args):
    fields = {}
    for flag, key in (("--language", "language"), ("--code", "language_code"),
                      ("--chat-language", "chat_language"), ("--level", "level"),
                      ("--mode", "mode")):
        v = opt(args, flag)
        if v is not None:
            fields[key] = v.strip()
    if "language_code" in fields:
        fields["language_code"] = fields["language_code"].lower()
        if not re.match(r"^[a-z]{2}$", fields["language_code"]):
            print("--code is the two-letter language code (uk, ru, en, de, ...)")
            return 2
    if "level" in fields:
        fields["level"] = fields["level"].upper()
        if fields["level"] not in levels():
            print("--level is one of %s" % ", ".join(levels()))
            return 2
    if fields.get("mode", "quality") not in MODES:
        print("--mode is one of %s" % ", ".join(MODES))
        return 2
    new = not os.path.exists(LEARNER)
    if new and not ("language" in fields and "language_code" in fields):
        print("a new learner profile needs --language and --code")
        return 2
    if not fields:
        print("nothing to change")
        return 2
    update_learner(fields)
    l = config.learner()
    print("OK   %s %s: cards in %s (%s), talking in %s%s, mode %s"
          % ("wrote" if new else "updated", rel(LEARNER), l["language"], l["language_code"],
             l["chat_language"], ", level %s" % l["level"] if l["level"] in levels() else "",
             l["mode"]))
    if l["language_code"] not in config.CYRILLIC:
        print("NOTE %s is not written in Cyrillic: card checks for it are the generic ones "
              "only, and the card writer should take extra care with translations." % l["language"])
    return 0


def cmd_grammar(args):
    import profile
    level = opt(args, "--level")
    if not level:
        print("need --level %s" % "|".join(levels()))
        return 2
    seed = profile.load_rows(profile.SEED)
    try:
        answers = suggested(level, [r["tag"] for r in seed])
    except ValueError as e:
        print(e)
        return 2
    src = opt(args, "--answers")
    if src:
        try:
            changes = json.load(io.open(src, encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            print("cannot read %s: %s" % (src, e))
            return 2
        if changes.get("prezent", "allowed") != "allowed":
            print("the present tense stays allowed: every sentence needs a tense")
            changes.pop("prezent")
        answers.update(changes)
    tmp = os.path.join(tempfile.gettempdir(), "romanian-grammar-answers.json")
    json.dump(answers, io.open(tmp, "w", encoding="utf-8"))
    try:
        return profile.cmd_init(PROFILE, tmp, "--force" in args)
    finally:
        os.remove(tmp)


def cmd_mode(mode):
    if mode not in MODES:
        print("mode is one of: %s" % ", ".join(MODES))
        return 2
    if not os.path.exists(LEARNER):
        print("TODO learner.md does not exist yet (setup.py learner ...)")
        return 1
    update_learner({"mode": mode})
    print("OK   mode set to %s" % mode)
    print(config.mode_line(mode))
    return 0


def cmd_permissions():
    if not IN_CLAUDE:
        print("NOTE this assistant is not Claude Code, so nothing is changed from here (its\n"
              "     settings live outside this folder). Tell the learner: the first time I ask\n"
              "     to run a card command, choose the option that ALWAYS allows it, not just\n"
              "     once. The card commands all start with %s." % PYTHON)
        return 0
    data = {}
    if os.path.exists(SETTINGS):
        try:
            data = json.load(io.open(SETTINGS, encoding="utf-8-sig"))
        except ValueError as e:
            print("TODO %s is not valid JSON (%s) -- fix or remove it first" % (rel(SETTINGS), e))
            return 1
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    added = [r for r in RULES if r not in allow]
    allow.extend(added)
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    io.open(SETTINGS, "w", encoding="utf-8", newline="\n").write(json.dumps(data, indent=2) + "\n")
    print("OK   %s" % ("added %d permission rule(s) to %s" % (len(added), rel(SETTINGS))
                       if added else "permissions already in place"))
    return 0


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def cmd_status():
    todo = []

    def ok(msg):
        print("OK   " + msg)

    def need(msg):
        print("TODO " + msg)
        todo.append(msg)

    if os.path.exists(RUNTIME_EXE):
        ok("this folder has its own Python (%s)" % rel(os.path.dirname(RUNTIME_EXE)))
    else:
        need("install this folder's own Python (.agents/skills/setup/bootstrap.ps1)")
    if os.path.exists(ENV):
        ok("settings file .env exists")
    else:
        need("settings file .env is missing (setup.py env-init, or open the settings page)")
    l = config.learner()
    if l["_exists"]:
        ok("cards translate into %s; we talk in %s" % (l["language"], l["chat_language"]))
        if l["level"] in levels():
            ok("level %s, mode %s" % (l["level"], l["mode"]))
        else:
            need("choose the level (settings page: setup.py page --step level)")
    else:
        need("choose the languages (setup.py learner --language ... --code ...)")
    if os.path.exists(PROFILE):
        ok("grammar profile exists")
    else:
        need("choose the grammar (settings page: setup.py page --step grammar)")
    try:
        ver = ac("version")
        ok("Anki is open and the AnkiConnect add-on answers (version %s)" % ver)
        deck, model = env_value("ANKI_IMPORT_DECK"), env_value("ANKI_MODEL")
        if deck and deck in ac("deckNames"):
            ok("deck %r exists" % deck)
        else:
            need("choose the deck for new cards (settings page: setup.py page --step anki)")
        front, back = config.fields()
        if model and model in ac("modelNames"):
            have = ac("modelFieldNames", modelName=model)
            if front in have and back in have:
                ok("note type %r has the %s and %s fields" % (model, front, back))
            else:
                need("note type %r has fields %s, not %s and %s" % (model, have, front, back))
        else:
            need("choose the note type, usually Basic (setup.py page --step anki)")
        media = env_value("ANKI_MEDIA_DIR")
        if media and os.path.isdir(media):
            ok("Anki's media folder is set")
        else:
            need("set Anki's media folder (setup.py page --step anki sets it)")
    except Unreachable:
        need("Anki is not open, or the AnkiConnect add-on (code 2055492159) is not installed")
    key = env_value(SECRET)
    if not key:
        need("enter the ElevenLabs key (settings page: setup.py page --step key)")
    else:
        code, _ = eleven("/voices", key)
        if code == 200:
            ok("ElevenLabs accepts the key")
        elif code == 0:
            need("could not reach ElevenLabs -- check the internet connection")
        else:
            need("ElevenLabs refused the key (HTTP %s): enter it again (setup.py page --step key)"
                 % code)
    if env_value("ELEVENLABS_VOICE_ID"):
        ok("a voice is chosen")
    else:
        need("choose a voice (settings page: setup.py page --step voice)")
    if not IN_CLAUDE:
        ok("permissions: your assistant asks in its own prompts")
    else:
        allowed = []
        if os.path.exists(SETTINGS):
            try:
                allowed = json.load(io.open(SETTINGS, encoding="utf-8-sig")).get(
                    "permissions", {}).get("allow", [])
            except ValueError:
                pass
        if RULES[0] in allowed and RULES[1] in allowed:
            ok("the assistant may run the card scripts without asking each time")
        else:
            need("let the assistant run the card scripts (setup.py permissions)")
    print("\nALL SET" if not todo else "\n%d thing(s) left" % len(todo))
    return 0 if not todo else 1


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "status":
        return cmd_status()
    if cmd == "learner":
        return cmd_learner(args)
    if cmd == "page":
        import settings_page
        return settings_page.launch(opt(args, "--step"), "--no-browser" not in args)
    if cmd == "serve":                    # started by `page`, detached
        import settings_page
        return settings_page.serve(int(opt(args, "--port")), opt(args, "--token"))
    if cmd == "permissions":
        return cmd_permissions()
    if cmd == "env-init":
        return cmd_env_init()
    if cmd == "open-env":
        return cmd_open_env()
    if cmd == "env-set" and len(args) == 2:
        return cmd_env_set(args[0], args[1])
    if cmd == "anki":
        return cmd_anki()
    if cmd == "voices":
        return cmd_voices()
    if cmd == "test-voice":
        return cmd_test_voice(args[0] if args else None)
    if cmd == "grammar":
        return cmd_grammar(args)
    if cmd == "mode" and len(args) == 1:
        return cmd_mode(args[0])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
