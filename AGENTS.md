# Romanian flashcards

This folder turns Romanian words into Anki flashcards. Two skills do the work; both are in `.agents/skills/`:

- **setup**: first-time setup and later changes (language, level, grammar, deck, mode, voice). Use it when someone is new, types "setup", or says something isn't working. The person is usually not technical: follow the skill's "How to talk" rules.
- **add-romanian-words**: whenever someone gives Romanian words to turn into cards.

- **Talk to the learner in their `chat_language`** from `my-cards/learner.md`, which `setup.py status` and `batch.py prep` print. Before setup has written it, use the language they write in. Card translations always go into `language`.
- **Run every script with the project's own Python, `runtime/python/python`,** never a plain `python`: the learner's computer may have no other Python. If `runtime/python` is missing, the setup skill installs it.
- **Never print, echo or paste the contents of `.env`:** it holds the learner's ElevenLabs key. The scripts read it themselves.
