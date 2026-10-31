#!/usr/bin/env python3
"""Offline tests for the skill's scripts: seconds, free, no real Anki or ElevenLabs.

    runtime/python/python .agents/skills/add-romanian-words/evals/run.py unit

Run after any script edit. The prep/commit tests start the fakes from
fake_services.py on a tiny collection and run the real scripts as
subprocesses, with every folder redirected to a temp dir.
"""
import io, os, re, sys, json, glob, shutil, tempfile, unittest, subprocess

EVALS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(EVALS), "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, EVALS)
import config                                          # noqa: E402
from config import ROOT                                # noqa: E402
from fake_services import FakeServices                 # noqa: E402

TINY = {"decks": ["Default"], "models": {"Basic": ["Front", "Back"]}, "notes": [
    {"nid": 1001, "deck": "Default", "model": "Basic", "tags": [],
     "fields": {"Front": "<b>прискорювати</b>", "Back": "<b>a acceler<u>a</u>, accelerez</b>"}},
    {"nid": 1002, "deck": "Default", "model": "Basic", "tags": [],
     "fields": {"Front": "<b>машина</b>", "Back": "<b>o mașin<u>ă</u>, două mașini</b><br><br>"
                "<i>Mașina e în fața casei.</i>"}}]}
CARDS = [
    {"front": "<b>вагання</b><br><br><i>Після короткого вагання він погодився.</i>",
     "back": "<b>o ezit<u>a</u>re, două ezit<u>ă</u>ri</b><br><br><i>După o scurtă ezit<u>a</u>re, a acceptat.</i>",
     "gram": "perfect-compus", "pos": "noun", "scheduled": "perfect-compus", "stretch": [],
     "inherent": ["genitiv"]},                    # tagged, never counted
    {"front": "<b>гальмувати</b><br><br><i>Гальмуй, попереду світлофор!</i><br><i>Бюрократія гальмує проєкт.</i>",
     "back": "<b>a fr<u>â</u>na, fr<u>â</u>nez</b><br><br><i>Fr<u>â</u>nează, e semafor în față!</i><br><i>Birocrația fr<u>â</u>nează proiectul.</i>",
     "gram": "imperativ", "pos": "verb", "scheduled": "imperativ", "stretch": ["prezent"],
     "also": ["prezent", "imperativ"]}]          # duplicates of stretch/gram: one tag each
BATCH = {"cards": CARDS, "log": "## o ezitare\n- 1a — 5 ✔\n\n## a frâna\n- 2a — 4 ✔"}


def sandbox_learner(folder):
    """A test learner from the templates, never the project's own: Ukrainian
    cards, English chat, B1, the seed's grammar with empty counters. (The shared
    policies come from the skill's policy/ folder.)"""
    import profile, setup
    from contextlib import redirect_stdout
    text = io.open(os.path.join(config.TEMPLATES, "learner.example.md"), encoding="utf-8").read()
    for k, v in (("LANGUAGE_CODE", "uk"), ("CHAT_LANGUAGE", "English"), ("LANGUAGE", "Ukrainian"),
                 ("LEVEL", "B1"), ("MODE", "quality"), ("TOPICS", setup.DEFAULT_TOPICS)):
        text = text.replace("{{%s}}" % k, v)
    io.open(os.path.join(folder, "learner.md"), "w", encoding="utf-8", newline="\n").write(text)
    answers = os.path.join(folder, "answers.json")
    io.open(answers, "w", encoding="utf-8").write("{}")
    with redirect_stdout(io.StringIO()):
        profile.cmd_init(os.path.join(folder, "grammar-profile.md"), answers, False)
    os.remove(answers)


def py(script, *args, env=None):
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, script)] + list(args),
                         capture_output=True, text=True, encoding="utf-8", env=env)
    return out.returncode, out.stdout + out.stderr


class Sandbox:
    """Temp policy/data/media folders plus the fakes; env for the subprocesses."""

    def __init__(self, gate=3000):
        self.dir = tempfile.mkdtemp(prefix="rcu-")
        pol = os.path.join(self.dir, "policy")
        os.makedirs(pol)
        sandbox_learner(pol)
        for d in ("data", "media", "anki-media", "backups"):
            os.makedirs(os.path.join(self.dir, d))
        col = os.path.join(self.dir, "collection.json")
        json.dump(TINY, io.open(col, "w", encoding="utf-8"), ensure_ascii=False)
        self.fake = FakeServices(col, os.path.join(self.dir, "anki-media"))
        url = self.fake.start()
        self.env = {k: v for k, v in os.environ.items()
                    if not re.match(r"^(ELEVENLABS_|ANKI_|RO_|TTS_)", k)}
        self.env.update({
            "RO_POLICY_DIR": pol, "RO_DATA_DIR": os.path.join(self.dir, "data"),
            "RO_MEDIA_DIR": os.path.join(self.dir, "media"),
            "ANKI_MEDIA_DIR": os.path.join(self.dir, "anki-media"),
            "ANKI_BACKUP_DIR": os.path.join(self.dir, "backups"), "ANKI_CONNECT_URL": url,
            "ELEVENLABS_API_URL": url + "/v1", "ELEVENLABS_API_KEY": "fake",
            "ELEVENLABS_VOICE_ID": "evalvoice", "ANKI_IMPORT_DECK": "Default",
            "ANKI_MODEL": "Basic", "TTS_CONFIRM_CHARS": str(gate),
            "CLAUDE_CODE_SESSION_ID": "unit-test-no-transcript"})

    def batch(self, b):
        p = os.path.join(self.dir, "batch.json")
        json.dump(b, io.open(p, "w", encoding="utf-8"), ensure_ascii=False)
        return p

    def files(self):
        return sorted(os.path.relpath(p, self.dir).replace("\\", "/")
                      for p in glob.glob(os.path.join(self.dir, "data", "**", "*"), recursive=True)
                      if os.path.isfile(p))

    def close(self):
        self.fake.stop()
        shutil.rmtree(self.dir, ignore_errors=True)


class Scripts(unittest.TestCase):
    def test_adapters_in_step(self):
        rc, out = py("adapters.py", "check")
        self.assertEqual(rc, 0, out)

    def test_every_committed_batch_still_validates(self):
        import validate
        files = glob.glob(os.path.join(config.DATA_DIR, "*_import.csv")) + \
            glob.glob(os.path.join(config.DATA_DIR, "*_grammar.csv"))
        for f in files:
            rc, out = py("validate.py", f)
            self.assertEqual(rc, 0, out)

    def test_validate_catches_bad_stress(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "2026-01-01_import.csv")
            io.open(p, "w", encoding="utf-8").write(
                '"<b>розчісувати</b><br><br><i>Розчісую волосся.</i>";"<b>a pieptăn<u>a</u>, '
                'pi<u>e</u>ptăn</b><br><br><i>Îmi pi<u>e</u>ptăn p<u>ă</u>rul în fi<u>e</u>care '
                'zi.</i>"\n"<b>штора</b>";"<b>o draper<u>ie</u></b>"\n'
                '"<b>штора</b>";"<b>o draperie</b><br><br><i>Trage draperiile.</i>"\n')
            rc, out = py("validate.py", p, "--text")
            self.assertEqual(rc, 1)
            self.assertIn("3 stressed words", out)
            self.assertIn("exactly one vowel", out)
            self.assertIn("3 rows, 2 PROBLEM", out)  # third row is clean: Cyrillic + no stress
        finally:
            shutil.rmtree(d)

    def test_normalize(self):
        from known import normalize
        for raw, key in [("a (se) tunde, (mă) tund", "tunde"), ("un vârf, două vârfuri", "vârf"),
                         ("a-și aminti, îmi amintesc", "aminti"), ("afum<u>a</u>t", "afumat"),
                         ("Te felicităm cu ocazia...", "te felicităm cu ocazia"),
                         ("<b>o draper<u>i</u>e, două draper<u>i</u>i</b>[sound:x.mp3]<br><br>"
                          "<i>Trage draperiile.</i>", "draperie"),
                         ("a se baza", "baza"), ("pârjoală/e", "pârjoală")]:
            self.assertEqual(normalize(raw), key, raw)

    def test_dex_groups(self):
        from dex import groups
        self.assertEqual(groups(["a", "b"]), [("a", []), ("b", [])])
        self.assertEqual(groups(["a", "--forms", "x", "y", "--", "b"]),
                         [("a", ["x", "y"]), ("b", [])])
        self.assertEqual(groups(["a", "--forms", "să picure"]), [("a", ["să picure"])])

    def test_profile_init_and_counters(self):
        import profile
        d = tempfile.mkdtemp()
        try:
            path, ans = os.path.join(d, "gp.md"), os.path.join(d, "a.json")
            json.dump({"genitiv": "allowed"}, io.open(ans, "w"))
            self.assertEqual(profile.cmd_init(path, ans, False), 0)
            self.assertEqual(profile.cmd_init(path, ans, False), 1)        # never overwrite
            rows = {r["tag"]: r for r in profile.load_rows(path)}
            self.assertEqual(rows["genitiv"]["status"], "allowed")
            self.assertEqual(rows["prezent"]["uses"], "0")
            rc, _ = profile.update(path, "bump", "2026-01-02", ["prezent:2", "genitiv:1"])
            self.assertEqual(rc, 0)
            rows = {r["tag"]: r for r in profile.load_rows(path)}
            self.assertEqual((rows["prezent"]["uses"], rows["prezent"]["last_used"]),
                             ("2", "2026-01-02"))
            before = io.open(path, encoding="utf-8").read()
            rc, _ = profile.update(path, "sched", "2026-01-03", ["prezent", "no-such-tag"])
            self.assertEqual(rc, 1)
            self.assertEqual(io.open(path, encoding="utf-8").read(), before)
        finally:
            shutil.rmtree(d)

    def test_learning_queue_tries_longest_ago_first(self):
        import profile
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "gp.md")
            io.open(path, "w", encoding="utf-8").write(
                "| C | c | x | learning | – | 2026-10-25 | 0 |\n"     # 0 uses, tried recently
                "| B | b | x | learning | 2026-09-01 | – | 1 |\n"     # tried long ago
                "| A | a | x | learning | – | – | 0 |\n")              # never tried
            lines = [l.split()[0] for l in profile.queue_lines(path, 5)
                     if l.startswith("      ")]
            self.assertEqual(lines, ["a", "b", "c"])
        finally:
            shutil.rmtree(d)

    def test_setup_grammar_data(self):
        """The setup page's grammar.json covers every seed row (a new row without
        page examples fails here), and its B1 preset is the seed itself."""
        import profile, setup
        seed = profile.load_rows(profile.SEED)
        tags = [r["tag"] for r in seed]
        g = setup.grammar_data()
        self.assertEqual(sorted(c["tag"] for c in g["constructions"]), sorted(tags))
        groups = {x["id"] for x in g["groups"]}
        for c in g["constructions"]:
            self.assertIn(c["group"], groups, c["tag"])
            self.assertTrue(c["label"].strip(), c["tag"])
            self.assertGreaterEqual(len(c["examples"]), 2, c["tag"])
            for e in c["examples"]:
                self.assertRegex(e, r"\*\*[^*]+\*\*", "%s: no marked span in %r" % (c["tag"], e))
                self.assertEqual(e.count("**") % 2, 0, e)
        for level in g["levels"]:
            p = g["presets"][level]
            self.assertFalse(set(p["allowed"]) & set(p["learning"]), level)
            self.assertLessEqual(set(p["allowed"]) | set(p["learning"]), set(tags), level)
            s = setup.suggested(level, tags)
            self.assertEqual(s["prezent"], "allowed", level)
            self.assertEqual(set(s), set(tags))
        self.assertEqual(setup.suggested("B1", tags), {r["tag"]: r["status"] for r in seed})
        rank = {"blocked": 0, "learning": 1, "allowed": 2}
        for lo, hi in zip(g["levels"], g["levels"][1:]):       # a level never allows less
            a, b = setup.suggested(lo, tags), setup.suggested(hi, tags)
            worse = [t for t in tags if rank[b[t]] < rank[a[t]]]
            self.assertFalse(worse, "%s allows more than %s: %s" % (lo, hi, worse))

    def test_set_statuses_keeps_counters(self):
        import profile
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "gp.md")
            io.open(path, "w", encoding="utf-8").write(
                "intro\n| A | a | x | allowed | 2026-10-01 | 2026-10-02 | 7 |\n"
                "| B | b | x | blocked | – | – | – |\n")
            before = io.open(path, encoding="utf-8").read()
            self.assertEqual(profile.set_statuses(path, {"zz": "allowed"})[0], 2)
            self.assertEqual(profile.set_statuses(path, {"a": "sometimes"})[0], 2)
            self.assertEqual(io.open(path, encoding="utf-8").read(), before)   # untouched
            rc, lines = profile.set_statuses(path, {"a": "learning", "b": "allowed"})
            self.assertEqual(rc, 0, lines)
            rows = {r["tag"]: r for r in profile.load_rows(path)}
            self.assertEqual((rows["a"]["status"], rows["a"]["last_used"], rows["a"]["uses"]),
                             ("learning", "2026-10-01", "7"))
            self.assertEqual(rows["b"]["status"], "allowed")
            self.assertTrue(io.open(path, encoding="utf-8").read().startswith("intro\n"))
        finally:
            shutil.rmtree(d)

    def test_learner_update_keeps_prose(self):
        sb = Sandbox()
        try:
            p = os.path.join(sb.env["RO_POLICY_DIR"], "learner.md")
            os.remove(p)
            rc, out = py("setup.py", "learner", "--language", "Ukrainian", "--code", "uk",
                         "--chat-language", "English", env=sb.env)
            self.assertEqual(rc, 0, out)
            l = config.learner(p)
            self.assertEqual((l["language"], l["chat_language"], l["mode"]),
                             ("Ukrainian", "English", "quality"))
            io.open(p, "a", encoding="utf-8").write("- My own note stays.\n")
            rc, out = py("setup.py", "learner", "--level", "a2", env=sb.env)
            self.assertEqual(rc, 0, out)
            text = io.open(p, encoding="utf-8").read()
            self.assertEqual(config.learner(p)["level"], "A2")
            self.assertIn("- My own note stays.", text)
            self.assertEqual(py("setup.py", "learner", "--level", "Z9", env=sb.env)[0], 2)
        finally:
            sb.close()

    def test_settings_page(self):
        """The page's server: token and host checks, saves land in the right
        files, the key is stored but never sent back, done stops it."""
        import socket, urllib.request, urllib.error
        sb = Sandbox()
        env = dict(sb.env, RO_ENV_FILE=os.path.join(sb.dir, ".env"),
                   RO_RUNTIME_DIR=os.path.join(sb.dir, "runtime"))
        del env["ELEVENLABS_API_KEY"], env["ELEVENLABS_VOICE_ID"]
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        proc = subprocess.Popen([sys.executable, os.path.join(SCRIPTS, "setup.py"), "serve",
                                 "--port", str(port), "--token", "t0k"], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        def call(path, body=None, token="t0k"):
            req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={"X-Token": token})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode("utf-8")
        try:
            for _ in range(40):
                try:
                    if call("/api/ping")[0] == 200:
                        break
                except OSError:
                    import time
                    time.sleep(0.25)
            self.assertEqual(call("/api/state", token="wrong")[0], 403)
            self.assertEqual(call("/")[0], 200)
            code, body = call("/api/state")
            self.assertEqual(code, 200, body)
            self.assertEqual(json.loads(body)["ui"], "en")      # sandbox learner: chat English
            key = "sk_unitTestKey0123456789abcdef"
            self.assertEqual(call("/api/save/key", {"key": "short"})[0], 400)
            self.assertEqual(call("/api/save/key", {"key": key})[0], 200)
            self.assertIn("ELEVENLABS_API_KEY=" + key,
                          io.open(env["RO_ENV_FILE"], encoding="utf-8").read())
            for p in ("/api/state", "/api/voices", "/api/anki"):
                code, body = call(p)
                self.assertEqual(code, 200, p)
                self.assertNotIn(key, body, p)
            voices = json.loads(call("/api/voices")[1])
            self.assertTrue(voices["key_ok"])
            self.assertFalse(voices["recommended"]["in_account"])     # the fake has one voice
            self.assertEqual(call("/api/save/voice", {"voice_id": "evalvoice"})[0], 200)
            code, body = call("/api/save/anki", {"deck": "Romanian", "new_deck": True,
                                                 "model": "Basic"})
            self.assertEqual(code, 200, body)
            self.assertIn("Romanian", sb.fake.decks)
            self.assertEqual(call("/api/save/level", {"level": "b2"})[0], 200)
            lp = os.path.join(sb.env["RO_POLICY_DIR"], "learner.md")
            self.assertEqual(config.learner(lp)["level"], "B2")
            self.assertEqual(call("/api/save/mode", {"mode": "lean"})[0], 200)
            self.assertEqual(config.learner(lp)["mode"], "lean")
            gp = os.path.join(sb.env["RO_POLICY_DIR"], "grammar-profile.md")
            import profile
            uses = {r["tag"]: r["uses"] for r in profile.load_rows(gp)}
            code, body = call("/api/save/grammar", {"statuses": {"gerunziu": "learning",
                                                                 "prezent": "blocked"}})
            self.assertEqual(code, 200, body)
            rows = {r["tag"]: r for r in profile.load_rows(gp)}
            self.assertEqual(rows["gerunziu"]["status"], "learning")
            self.assertEqual(rows["prezent"]["status"], "allowed")          # locked
            self.assertEqual({t: r["uses"] for t, r in rows.items()}, uses)  # counters kept
            os.remove(gp)                  # a first profile: gaps come from the level (B2)
            self.assertEqual(call("/api/save/grammar", {"statuses": {"pasiv": "blocked"}})[0], 200)
            rows = {r["tag"]: r["status"] for r in profile.load_rows(gp)}
            self.assertEqual((rows["gerunziu"], rows["prezumtiv"], rows["pasiv"]),
                             ("allowed", "learning", "blocked"))
            env_text = io.open(env["RO_ENV_FILE"], encoding="utf-8").read()
            for k, v in (("ANKI_IMPORT_DECK", "Romanian"), ("ELEVENLABS_VOICE_ID", "evalvoice")):
                self.assertIn("%s=%s" % (k, v), env_text)
            self.assertEqual(call("/api/done", {})[0], 200)
            self.assertEqual(proc.wait(timeout=15), 0)
            self.assertNotIn(key, proc.stdout.read().decode("utf-8", "replace"))
        finally:
            if proc.poll() is None:
                proc.kill()
            sb.close()

    def test_bootstrap_refuses_a_bad_download(self):
        """A Python package whose checksum doesn't match is never unpacked."""
        d = tempfile.mkdtemp()
        try:
            skill = os.path.join(d, ".agents", "skills", "setup")
            os.makedirs(skill)
            shutil.copy(os.path.join(ROOT, ".agents", "skills", "setup", "bootstrap.ps1"), skill)
            fake = os.path.join(d, "fake.zip")
            io.open(fake, "wb").write(b"PK not really python")
            out = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                                  os.path.join(skill, "bootstrap.ps1"), "-Zip", fake],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
            self.assertIn("checksum", out.stdout)
            self.assertFalse(os.path.exists(os.path.join(d, "runtime", "python")))
        finally:
            shutil.rmtree(d)

    def test_setup_never_takes_the_key(self):
        self.assertEqual(py("setup.py", "env-set", "ELEVENLABS_API_KEY", "x")[0], 2)

    def test_learner_settings(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "learner.md")
            io.open(p, "w", encoding="utf-8").write(
                "# L\n\nlanguage: English\nlanguage_code: EN\nmode: turbo\n\n## About\nmode: lean\n")
            l = config.learner(p)
            self.assertEqual((l["language"], l["language_code"], l["mode"]),
                             ("English", "en", "quality"))   # bad mode falls back; ## stops parsing
            self.assertFalse(config.cyrillic_translation(l))
        finally:
            shutil.rmtree(d)


class Pipeline(unittest.TestCase):
    def test_prep_dedups_without_network(self):
        sb = Sandbox()
        try:
            rc, out = py("batch.py", "prep", "a accelera, accelerez", "o mașină", env=sb.env)
            self.assertEqual(rc, 0, out)
            self.assertIn("READY", out)
            self.assertEqual(len(re.findall(r"DUPLICATE  nid:\d+", out)), 2, out)
            self.assertIn("QUEUE (0 cards)", out)
            self.assertIn("MODE quality", out)
            self.assertIn("== POLICIES", out)
        finally:
            sb.close()

    def test_commit_then_resume(self):
        sb = Sandbox()
        try:
            b = sb.batch(BATCH)
            before = io.open(os.path.join(sb.dir, "policy", "grammar-profile.md"),
                             encoding="utf-8").read()
            rc, out = py("batch.py", "commit", b, "--dry-run", env=sb.env)
            self.assertEqual((rc, sb.files()), (0, []), out)
            rc, out = py("batch.py", "commit", b, env=sb.env)
            self.assertEqual(rc, 0, out)
            added = sb.fake.state["added"]
            self.assertEqual(len(added), 2)
            self.assertIn("gram::genitiv", added[0]["tags"])           # built-in grammar tagged
            self.assertEqual(len(added[0]["tags"]), 4)
            self.assertEqual(sorted(added[1]["tags"]),        # stretch construction tagged too
                             sorted(["gram::imperativ", "gram::prezent", "pos::verb",
                                     "batch::" + added[1]["tags"][-1].split("::")[1]]))
            self.assertEqual(added[0]["fields"]["Back"].count("[sound:"), 2)
            self.assertEqual(sb.fake.state["tts_calls"], 5)
            self.assertEqual(sb.fake.state["backups"], 1)
            log = io.open(glob.glob(os.path.join(sb.dir, "data", "logs", "*-batch.md"))[0],
                          encoding="utf-8").read()
            self.assertIn("1a — 5", log)
            self.assertIn("realized imperativ +1, perfect-compus +1, prezent +1; "
                          "built-in, not counted: genitiv", log)
            gp = io.open(os.path.join(sb.dir, "policy", "grammar-profile.md"),
                         encoding="utf-8").read()
            self.assertNotEqual(gp, before)
            genitiv = [l for t in (gp, before) for l in t.splitlines() if "| genitiv |" in l]
            self.assertEqual(genitiv[0], genitiv[1], "built-in grammar must not be counted")
            files = sb.files()
            rc, out = py("batch.py", "commit", b, env=sb.env)          # re-run = resume
            self.assertEqual(rc, 0, out)
            self.assertEqual(len(sb.fake.state["added"]), 2)
            self.assertEqual(sb.fake.state["tts_calls"], 5)
            self.assertIn("already updated", out)
            self.assertEqual(io.open(os.path.join(sb.dir, "policy", "grammar-profile.md"),
                                     encoding="utf-8").read(), gp)
            self.assertEqual(sb.files(), files)
        finally:
            sb.close()

    def test_retag_only_adds(self):
        sb = Sandbox()
        try:
            p = os.path.join(sb.dir, "retag.json")
            json.dump({"tags": {"1001": ["prezent", "comparativ"]}}, io.open(p, "w"))
            rc, out = py("anki_add.py", "retag", p, env=sb.env)            # dry run
            self.assertEqual(rc, 0, out)
            self.assertIn("+ gram::prezent gram::comparativ", out)
            self.assertEqual(sb.fake.notes[1001]["tags"], [])
            json.dump({"tags": {"1001": ["pasiv"]}}, io.open(p, "w"))       # blocked
            self.assertEqual(py("anki_add.py", "retag", p, "--commit", env=sb.env)[0], 2)
            self.assertEqual(sb.fake.state["backups"], 0)
            json.dump({"tags": {"1001": ["prezent", "comparativ"]}}, io.open(p, "w"))
            rc, out = py("anki_add.py", "retag", p, "--commit", env=sb.env)
            self.assertEqual(rc, 0, out)
            self.assertIn("every tag read back", out)
            self.assertEqual(sb.fake.notes[1001]["tags"], ["gram::prezent", "gram::comparativ"])
            rc, out = py("anki_add.py", "retag", p, "--commit", env=sb.env)  # re-run
            self.assertIn("nothing to add", out)
            journal = io.open(glob.glob(os.path.join(sb.dir, "data", "logs", "import-*.jsonl"))[0],
                              encoding="utf-8").read()
            self.assertIn('"tags_before": []', journal)
        finally:
            sb.close()

    def test_spend_gate_writes_nothing(self):
        sb = Sandbox(gate=50)
        try:
            rc, out = py("batch.py", "commit", sb.batch(BATCH), env=sb.env)
            self.assertEqual(rc, 3, out)
            self.assertIn("SPEND GATE", out)
            self.assertEqual((sb.files(), sb.fake.state["tts_calls"]), ([], 0))
            rc, out = py("batch.py", "commit", sb.batch(BATCH), "--approved", env=sb.env)
            self.assertEqual(rc, 0, out)
        finally:
            sb.close()

    def test_refusals_write_nothing(self):
        sb = Sandbox()
        try:
            bad = json.loads(json.dumps(BATCH))
            bad["cards"][0]["stretch"] = ["prezent"]                     # no stretch example
            bad["cards"][1]["gram"] = "gerunziu"                          # blocked
            bad["cards"][1]["score"] = 5                                  # unknown field
            bad["cards"][0]["back"] += "[sound:x.mp3]"                    # typed sound tag
            bad["cards"][0]["also"] = ["pasiv"]                           # blocked, as an also
            bad["cards"].append({"front": "<b>прискорювати</b><br><br><i>Машина прискорюється.</i>",
                                 "back": "<b>a acceler<u>a</u>, acceler<u>e</u>z</b><br><br>"
                                         "<i>Mașina accelere<u>a</u>ză.</i>",
                                 "gram": "prezent", "pos": "verb", "scheduled": "prezent",
                                 "stretch": []})                          # already in Anki
            rc, out = py("batch.py", "commit", sb.batch(bad), env=sb.env)
            self.assertEqual(rc, 2, out)
            for what in ("[sound:]", "stretch tag", "unknown field", "BLOCKED", "already in Anki",
                         "also 'pasiv' is a BLOCKED"):
                self.assertIn(what, out)
            self.assertEqual((sb.files(), sb.fake.state["tts_calls"]), ([], 0))
        finally:
            sb.close()


if __name__ == "__main__":
    unittest.main(verbosity=1)
