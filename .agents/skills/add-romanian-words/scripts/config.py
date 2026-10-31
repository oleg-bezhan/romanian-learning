#!/usr/bin/env python3
"""One place for paths, .env, learner settings and token modes.

Every script imports from here instead of deriving its own paths, so a
learner with a different layout (or a test) changes one thing, not seven.

What ships with the skill sits in its own policy/ folder: policies.md (the
generation rules), naturalness-rubric.md (the reviewer's brief) and
tts-config.json (the voice model settings). Everything the learner owns sits
in one folder of the project, my-cards/.

Paths (real environment variables win, then .env, then these defaults):
    RO_DATA_DIR     my-cards/      the learner's folder: batch CSVs and manifests
    RO_POLICY_DIR   <data>/        learner.md and grammar-profile.md
    RO_MEDIA_DIR    <data>/media/  content-addressed audio cache
    RO_LOGS_DIR     <data>/logs/   batch logs, import journals, usage log
    ANKI_BACKUP_DIR backups/       one restore point per deck per day
    RO_RUNTIME_DIR  runtime/       the setup page's state and voice samples (the
                                   Python itself is always runtime/python)
    RO_ENV_FILE     .env           the settings file itself (tests point it elsewhere;
                                   only a real environment variable can move it)

Other settings:
    TTS_CONFIRM_CHARS   3000       a batch billing this many characters or more
                                   waits for the learner's approval
    ANKI_FRONT_FIELD    Front      note-type field for the translation side
    ANKI_BACK_FIELD     Back       note-type field for the Romanian side

learner.md holds who the cards are for. Its `key: value`
lines before the first `##` heading are machine-read:
    language        the translation language, e.g. Ukrainian
    language_code   ISO 639-1, e.g. uk -- selects the language checks in validate.py
    chat_language   the language the assistant talks to the learner in (default:
                    the translation language), e.g. English
    level           e.g. B1
    mode            quality | balanced | lean  (see MODES)
Everything else in the file is prose for the card writer.
"""
import io, os, re

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "..", "..", "..", ".."))
SKILL_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(SKILL_DIR, "templates")
SHARED = os.path.join(SKILL_DIR, "policy")
# The setup page lives in the setup skill's own folder. runtime/ (never
# committed) holds the project's own Python, from the setup skill's bootstrap.ps1.
SETUP_PAGE = os.path.join(ROOT, ".agents", "skills", "setup", "page")
ENV_FILE = os.environ.get("RO_ENV_FILE", "").strip() or os.path.join(ROOT, ".env")


def load_env():
    """Parse .env ourselves: the Anki path contains a space and is unquoted,
    so shell sourcing mangles it. Real environment variables take precedence."""
    path = ENV_FILE
    if not os.path.exists(path):
        return
    for line in io.open(path, encoding="utf-8-sig"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:]
        k, sep, v = line.partition("=")
        k = k.strip()
        if k and sep and k not in os.environ:
            os.environ[k] = v.strip().strip('"').strip("'")


load_env()


def _dir(var, default):
    v = os.environ.get(var, "").strip()
    return os.path.normpath(os.path.join(ROOT, v) if v else default)


DATA_DIR = _dir("RO_DATA_DIR", os.path.join(ROOT, "my-cards"))
POLICY_DIR = _dir("RO_POLICY_DIR", DATA_DIR)
MEDIA = _dir("RO_MEDIA_DIR", os.path.join(DATA_DIR, "media"))
LOGS = _dir("RO_LOGS_DIR", os.path.join(DATA_DIR, "logs"))
BACKUPS = _dir("ANKI_BACKUP_DIR", os.path.join(ROOT, "backups"))
RUNTIME = _dir("RO_RUNTIME_DIR", os.path.join(ROOT, "runtime"))
PROFILE = os.path.join(POLICY_DIR, "grammar-profile.md")
LEARNER = os.path.join(POLICY_DIR, "learner.md")
POLICIES = os.path.join(SHARED, "policies.md")
RUBRIC = os.path.join(SHARED, "naturalness-rubric.md")
TTS_CONFIG = os.path.join(SHARED, "tts-config.json")


def tts_confirm_chars():
    v = os.environ.get("TTS_CONFIRM_CHARS", "").strip()
    return int(v) if v.isdigit() else 3000


def fields():
    """(translation field, Romanian field) of the note type."""
    return (os.environ.get("ANKI_FRONT_FIELD", "").strip() or "Front",
            os.environ.get("ANKI_BACK_FIELD", "").strip() or "Back")


def rel(p):
    r = os.path.relpath(os.path.abspath(p), ROOT)
    return (os.path.abspath(p) if r.startswith("..") else r).replace("\\", "/")


# --------------------------------------------------------------------------
# learner settings
# --------------------------------------------------------------------------

LEARNER_DEFAULTS = {"language": "Ukrainian", "language_code": "uk",
                    "level": "A2-B1", "mode": "quality"}
SETTING = re.compile(r"^([a-z_]+):\s*(.*?)\s*$")
# Translation languages written in Cyrillic: validate.py and known.py tell the
# Romanian side from the translation side by script for these.
CYRILLIC = {"uk", "ru", "bg", "sr", "mk", "be"}


def learner(path=None):
    """-> settings dict (defaults filled in) plus "_exists"."""
    path = path or LEARNER
    out = dict(LEARNER_DEFAULTS)
    out["_exists"] = os.path.exists(path)
    if out["_exists"]:
        for line in io.open(path, encoding="utf-8-sig"):
            if line.startswith("## "):
                break
            m = SETTING.match(line.strip())
            if m and m.group(2):
                out[m.group(1)] = m.group(2)
    out["language_code"] = out["language_code"].lower()
    out.setdefault("chat_language", out["language"])
    if out["mode"] not in MODES:
        out["mode"] = "quality"
    return out


def cyrillic_translation(settings=None):
    return (settings or learner())["language_code"] in CYRILLIC


# --------------------------------------------------------------------------
# token modes
# --------------------------------------------------------------------------
# A mode decides how hard the pipeline searches for the best sentence -- never
# whether a sentence is checked. Blind review, the >=4 gate, stress lookup,
# dedup, the vocabulary check and every script validation run in all modes.

MODES = {
    "quality": {
        "candidates": "2-3 per card",
        "repair_rounds": 2,
        "stretch": "when it adds a distinct sense, collocation or context",
        "log": "every candidate with its score and rejection reason",
        "grammar_tags": "every listed construction in every example (main + `also`)",
    },
    "balanced": {
        "candidates": "2 per card",
        "repair_rounds": 1,
        "stretch": "when it adds a distinct sense, collocation or context",
        "log": "every candidate with its score and rejection reason",
        "grammar_tags": "the main construction of each example (no `also`)",
    },
    "lean": {
        "candidates": "1 per card; draft a 2nd only for a card whose 1st fails review",
        "repair_rounds": 1,
        "stretch": "only for a second sense that needs a different translation",
        "log": "winners plus rejected candidates with their scores",
        "grammar_tags": "the main construction of each example (no `also`)",
    },
}


def mode_line(mode):
    m = MODES[mode]
    return ("MODE %s -- candidates: %s; repair rounds: max %d; stretch example: %s; "
            "batch log: %s; grammar tags: %s" % (mode, m["candidates"], m["repair_rounds"],
                                                 m["stretch"], m["log"], m["grammar_tags"]))
