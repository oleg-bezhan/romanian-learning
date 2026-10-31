---
name: setup
description: First-time setup, and later changes, for the Romanian Anki cards. Asks which language to talk in and translate into, installs what is needed inside this folder, connects Anki, then opens a settings page in the browser where the learner picks their level, grammar, how careful the card writing is, the deck, the ElevenLabs key and the voice. Use when someone sets up the Romanian cards for the first time, says something isn't working, or wants to change their language, level, grammar, voice, deck or mode. Written for people with no technical background.
---

# Setup for the Romanian cards

The person in front of you is **not technical and not used to AI assistants**. Your job is to get them from nothing to their first finished cards, gently, in plain language. Most choices happen on a settings page in their browser. You install things, connect Anki, open the page and help when something goes wrong.

## How to talk

- **Their language.** From step 1 on, talk in the language they chose for the conversation. When coming back later, `setup.py status` shows it.
- **One step at a time.** Say what the step is for in one sentence, ask them to do one thing, and wait until they say it's done.
- **No jargon.** Say "a small helper program" for Python, "the Anki add-on that lets me add cards" for AnkiConnect, "the settings page" for the local web page.
- **Never show commands, code, file paths or JSON** unless they ask. Run everything yourself, and describe the result in words ("Anki is connected ✓"). The one exception: the settings page's address, if the browser did not open by itself.
- **Never ask them to open a terminal** or edit a file.
- **When something fails,** say what it means and the single next thing to try. Don't paste error text.
- **Money:** the only paid things in setup are the optional Romanian voice samples on the settings page; the page shows their cost.
- **Their ElevenLabs key never goes through the chat.** It goes into the settings page. If they paste it in the chat anyway, don't use it: say the page has a field for it, and that they can make a fresh key in ElevenLabs later to be safe.

Commands below run from the project folder. `setup.py <command>` means `runtime/python/python .agents/skills/add-romanian-words/scripts/setup.py <command>`.

## Step 1 — Languages

Greet them in Ukrainian and English together, then ask two questions:
1. **Which language should we talk in?** Offer Ukrainian (the default) and English, or any language they prefer.
2. **Which language should the translations on your cards be in?** Usually their native language. Ukrainian is the default and the best-checked one.

You need each language's name, and the two-letter code of the card language (Ukrainian `uk`, English `en`, Russian `ru`, …). Work the code out yourself; don't ask for it. You save these at the end of step 2, once the helper program is installed.

Then say what's ahead, in their language, in two lines: setup takes about 15 minutes, and they will need
- the **Anki** app (free; we'll install it together if it's missing);
- an **ElevenLabs** account (the voice that reads the Romanian aloud; elevenlabs.io).

## Step 2 — The helper program (Python)

Explain: "I'll put a small helper program into this folder. It only lives here: nothing else on your computer changes, and deleting the folder removes it." Then run:

```
powershell -NoProfile -ExecutionPolicy Bypass -File .agents/skills/setup/bootstrap.ps1
```

- **OK:** it is ready (or already was).
- **"could not download":** the internet connection is the likely cause. Ask them to check it, then run it again.
- **"does not match the expected checksum" or "not supported":** stop and say setup can't continue on this computer for now. Don't try other ways of installing Python.
- **"did not start":** security software may have blocked it. Ask them to try again, and if it repeats, stop.

Then save the languages from step 1:
`setup.py learner --language "<card language>" --code <xx> --chat-language "<conversation language>"`.

## Step 3 — Anki and its add-on

Run `setup.py status` and look at the Anki line.
1. **Anki missing:** they download it from apps.ankiweb.net (the big Download button), install it and open it. On the first start Anki asks for its display language; any choice is fine.
2. **The add-on:** in Anki, menu **Tools → Add-ons → Get Add-ons…**, paste the code **2055492159**, press OK, then **close Anki and open it again**.
3. Run `setup.py status` again. When Anki "answers", go on. If not, ask them to check Anki is open and the add-on step was done, one at a time.

## Step 4 — The settings page

Run `setup.py page`. It opens a page in their browser and returns at once. Tell them:
- "A settings page just opened in your browser. It walks you through six short steps: your level, the grammar, how careful the card writing is, your Anki deck, your ElevenLabs key, and the voice. Everything is saved as you go."
- "For the ElevenLabs step you'll create a key on their website; the page shows how."
- "When you've pressed **Finish** at the end, come back here and tell me."

If they say the page didn't open, give them the address `setup.py page` printed. While they work on the page, don't run anything else. When they say they're done, run `setup.py status` and tell them in words what is set and what is missing. For anything missing, run `setup.py page --step <level|grammar|mode|anki|key|voice>` to reopen the page right there.

## Step 5 — Fewer "Allow?" questions

Explain: "So you don't have to approve every little step, I can be allowed to run the card tools and save card files in this folder. Is that OK?" On yes, run `setup.py permissions`:
- **In Claude Code** it sets the permissions itself.
- **In other assistants** (Antigravity, Cursor, …) it changes nothing and prints a note. Pass it on in plain words: the first time a card tool asks for permission, choose the option that always allows it, not just once.

## Step 6 — Done: the first cards

Run `setup.py status`. It should end with **ALL SET**. If not, go back to whatever is TODO.

Then tell them how daily use works, in four short lines:
- Keep **Anki open** while adding words, but not on the review screen.
- Just write the words, e.g. "add these words: a merge, o casă, frumos".
- **Start a new conversation for each batch of words.** It keeps costs down.
- To change anything later, say "setup" and what you want to change.

Finally invite them: "Tell me 2 or 3 Romanian words you'd like to learn, and I'll make your first cards." That runs the `add-romanian-words` skill.

## Coming back later

When they want one change, do just that:
- **Level, grammar, mode, deck, key or voice:** `setup.py page --step <level|grammar|mode|anki|key|voice>`, then wait for "done". The grammar step keeps their grammar history.
- **Conversation language:** `setup.py learner --chat-language "<language>"`.
- **Card language:** `setup.py learner --language "<language>" --code <xx>`. Warn them first that the cards they already have stay as they are.
- **"Something isn't working":** start with `setup.py status`. If the helper program is missing, run step 2 again.

**If the settings page can't be used** (for example there is no browser), the same choices exist as commands: `setup.py learner --level <A0…C1>`, `setup.py grammar --level <level>`, `setup.py mode <quality|balanced|lean>`, `setup.py anki` then `setup.py env-set ANKI_IMPORT_DECK "<deck>"` (and `ANKI_MODEL`, `ANKI_MEDIA_DIR`), `setup.py open-env` for the key (they paste it into the file themselves), and `setup.py voices` and `setup.py test-voice <id>` then `setup.py env-set ELEVENLABS_VOICE_ID <id>`.
