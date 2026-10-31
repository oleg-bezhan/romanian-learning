# Evals

Run these before and after changing the skill, so a change can't silently make cards worse.

```
runtime/python/python .agents/skills/add-romanian-words/evals/run.py unit                 # scripts: ~10 s, free
runtime/python/python .agents/skills/add-romanian-words/evals/run.py skill                # the whole skill: ~10 min, one small batch
runtime/python/python .agents/skills/add-romanian-words/evals/run.py skill --ref <commit>  # the same, for an older version
runtime/python/python .agents/skills/add-romanian-words/evals/run.py history              # every run so far
```

The scripts and the skill run on the project's own Python, `runtime/python/`. Install it once with `powershell -NoProfile -ExecutionPolicy Bypass -File .agents/skills/setup/bootstrap.ps1`; `skill` runs copy it into each throwaway project.

- **unit** (`unit.py`) checks the scripts offline:
  - every batch already in `my-cards/` still validates;
  - stress and field rules catch bad rows;
  - normalization, the grammar profile, status changes that keep the counters, and the setup page's `grammar.json` (every seed row has examples; level presets never shrink as the level rises; B1 is the seed);
  - the setup page's server: token and host checks, every save, the key stored but never sent back;
  - the bootstrap refusing a download with the wrong checksum;
  - a full prep and commit against fake Anki and ElevenLabs: happy path, resume, spend gate, and refusals.
- **skill** (`run.py`) runs the real skill headless (`claude -p`) on the words in `cases.json`. It works in a throwaway copy of the project, against the fakes in `fake_services.py`, with the deck snapshot in `fixtures/collection.json`. Nothing reaches the real Anki and no audio is billed.
  - It grades the cards against the answer key: headword, stress, part of speech, the duplicate skipped.
  - It grades the batch: validation, tags, log, counters, and a reviewer that was used and kept blind.
  - A separate blind judge (Sonnet, the rubric only) scores every example sentence.
  - Tokens and requests come from the run's own transcript.
  - Each run is appended to `results.jsonl` and compared with the previous one. A check that passed before and fails now is reported as a **REGRESSION**. Cards and logs are kept in `runs/`.
- **`--mode balanced|lean`** runs the same words in another token mode, to see what a cheaper mode costs in quality.
- **`--learner A1 --cases cases-low.json`** runs a NEW learner at that level (learner.md from the template, the grammar profile from the level's preset, as the setup page writes them) on A1–A2 words. The judge then also scores **level fit**: are the grammar and supporting words within that learner's reach?
- **`--tool agy`** runs the same eval in Antigravity (`agy -p`, default model `gemini-3.8-flash-high`; `--reviewer-model` sets the reviewer's). The judge stays Claude Sonnet, so scores compare across tools.
  - **Close Anki first.** The run waits until nothing answers on port 8765, then the fake takes that port, so a stray AnkiConnect call can never reach your collection.
  - It auto-approves commands (`--dangerously-skip-permissions`), as a learner clicking "allow" would. Allowlists didn't work: headless agy ends the run at the first refused command, and Flash kept finding new command shapes.
  - Deny rules block deleting, moving, writing and network tools. They hold under auto-approve (verified). A command inside a nested shell (`pwsh -Command "..."`) is not inspected.
  - On Windows, agy permission rules only match in regex form, e.g. `command(regex:python .*)`.
- **Claude runs** may run only the skill's own scripts, so Anki can stay open for them.
- **snapshot** refreshes `fixtures/collection.json` from the live Anki (read-only). After a refresh, check that the `cases.json` words are still absent from the deck.

Adding a case: append it to `cases.json`. Take the expected headword stress from `dex.py <word> --forms <second form>`, never from memory (`morcov` is m<u>o</u>rcov).
