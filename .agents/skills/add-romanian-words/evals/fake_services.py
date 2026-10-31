#!/usr/bin/env python3
"""Fake AnkiConnect + fake ElevenLabs on one local port, for the evals.

    server = FakeServices(collection_json_path, anki_media_dir)
    server.start()  ->  server.url  (AnkiConnect: POST /, ElevenLabs: /v1/...)
    ...
    server.state    added notes, stored media, synthesized characters
    server.stop()

The collection starts as a snapshot of a real deck (fixtures/collection.json,
from `run.py snapshot`), so dedup and the vocabulary check behave as they do
for real. Writes only change memory -- nothing reaches a real collection, and
no audio is ever billed.
"""
import io, os, re, json, hashlib, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACTIONS = ["version", "apiReflect", "deckNames", "modelNames", "modelFieldNames", "findNotes",
           "notesInfo", "addNote", "storeMediaFile", "getMediaFilesNames", "guiReviewActive",
           "exportPackage", "sync", "deleteNotes", "createDeck", "getMediaDirPath", "findCards",
           "addTags"]


class FakeServices:
    def __init__(self, collection_path, anki_media_dir, port=0):
        self.port = port            # 0 = any free port; 8765 = stand in for a closed Anki
        data = json.load(io.open(collection_path, encoding="utf-8"))
        self.notes = {int(n["nid"]): n for n in data["notes"]}
        self.decks = set(data.get("decks") or ["Default"]) | {"Default"}
        self.models = data.get("models") or {"Basic": ["Front", "Back"]}
        self.media_dir = anki_media_dir
        self.lock = threading.Lock()
        self.state = {"added": [], "media": set(), "tts_chars": 0, "tts_calls": 0,
                      "actions": {}, "synced": 0, "backups": 0}
        self.next_nid = 1900000000000
        self.httpd = None

    # ---- AnkiConnect -----------------------------------------------------
    def anki(self, action, p):
        st = self.state
        st["actions"][action] = st["actions"].get(action, 0) + 1
        if action == "version":
            return 6
        if action == "apiReflect":
            return {"scopes": ["actions"], "actions": ACTIONS}
        if action == "deckNames":
            return sorted(self.decks)
        if action == "modelNames":
            return sorted(self.models)
        if action == "modelFieldNames":
            return self.models.get(p.get("modelName"), [])
        if action == "guiReviewActive":
            return False
        if action == "getMediaDirPath":
            return self.media_dir
        if action == "findCards":
            return []
        if action == "findNotes":
            q = p.get("query", "")
            nid = re.search(r"nid:(\d+)", q)
            deck = re.search(r'deck:"((?:[^"\\]|\\.)*)"', q) or re.search(r"deck:(\S+)", q)
            hits = [int(nid.group(1))] if nid else list(self.notes)
            hits = [n for n in hits if n in self.notes]
            if deck and deck.group(1) != "*":
                name = deck.group(1).replace('\\"', '"')
                hits = [n for n in hits if self.notes[n].get("deck") == name]
            return sorted(hits)
        if action == "notesInfo":
            out = []
            for n in p.get("notes", []):
                note = self.notes.get(int(n))
                if note:
                    out.append({"noteId": int(n), "modelName": note.get("model", "Basic"),
                                "tags": note.get("tags", []), "cards": [int(n) + 1],
                                "fields": {k: {"value": v, "order": i}
                                           for i, (k, v) in enumerate(note["fields"].items())}})
            return out
        if action == "addNote":
            n = p["note"]
            self.next_nid += 1
            self.notes[self.next_nid] = {"nid": self.next_nid, "deck": n["deckName"],
                                         "model": n["modelName"], "tags": n.get("tags", []),
                                         "fields": dict(n["fields"])}
            st["added"].append(self.notes[self.next_nid])
            return self.next_nid
        if action == "storeMediaFile":
            st["media"].add(p["filename"])
            return p["filename"]
        if action == "getMediaFilesNames":
            rx = re.compile(re.escape(p.get("pattern", "*")).replace("\\*", ".*") + r"\Z")
            return sorted(f for f in st["media"] if rx.match(f))
        if action == "exportPackage":
            os.makedirs(os.path.dirname(p["path"]), exist_ok=True)
            io.open(p["path"], "wb").write(b"fake apkg")
            st["backups"] += 1
            return True
        if action == "sync":
            st["synced"] += 1
            return None
        if action == "createDeck":
            self.decks.add(p["deck"])
            return 1
        if action == "addTags":
            for n in p.get("notes", []):
                note = self.notes[int(n)]
                for t in p.get("tags", "").split():
                    if t.lower() not in {x.lower() for x in note.setdefault("tags", [])}:
                        note["tags"].append(t)
            return None
        if action == "deleteNotes":
            for n in p.get("notes", []):
                self.notes.pop(int(n), None)
            return None
        raise ValueError("unsupported action %s" % action)

    # ---- ElevenLabs ------------------------------------------------------
    def eleven(self, method, path, body):
        if method == "GET" and path.startswith("/v1/voices"):
            return 200, "application/json", json.dumps(
                {"voices": [{"voice_id": "evalvoice", "name": "Eval", "labels": {}}]}).encode()
        if method == "POST" and path.startswith("/v1/text-to-speech/"):
            text = json.loads(body or b"{}").get("text", "")
            self.state["tts_chars"] += len(text)
            self.state["tts_calls"] += 1
            return 200, "audio/mpeg", b"ID3" + hashlib.sha1(text.encode("utf-8")).digest()
        return 404, "application/json", b'{"detail": "not found"}'

    # ---- HTTP ------------------------------------------------------------
    def start(self):
        svc = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, ctype, data):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                with svc.lock:
                    self.reply(*svc.eleven("GET", self.path, None))

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with svc.lock:
                    if self.path.startswith("/v1/"):
                        return self.reply(*svc.eleven("POST", self.path, body))
                    req = json.loads(body or b"{}")
                    try:
                        res = {"result": svc.anki(req.get("action"), req.get("params") or {}),
                               "error": None}
                    except Exception as e:
                        res = {"result": None, "error": str(e)}
                    self.reply(200, "application/json", json.dumps(res).encode("utf-8"))

        class Server(ThreadingHTTPServer):
            # HTTPServer sets SO_REUSEADDR, which on Windows lets a second socket
            # bind a port that is still in use -- e.g. a running Anki's 8765.
            allow_reuse_address = False

        self.httpd = Server(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        return self.url

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
