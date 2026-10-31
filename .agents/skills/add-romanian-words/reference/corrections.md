# Corrections, recovery and the single-step commands

Read this only when something needs fixing. A normal batch never needs it.

Scripts are in `.agents/skills/add-romanian-words/scripts/`; run them from the project root as `runtime/python/python .agents/skills/add-romanian-words/scripts/<script>.py`.

## An interrupted commit

Re-run the same `batch.py commit my-cards/batch.json`. Every step is idempotent: rows already in today's file are not appended twice, cached audio is free, notes already in Anki are skipped, and the counters, usage record and batch log are written once per batch (a batch id in the import journal and the log guards them).

## `[FILE-ONLY file:line]` in a dedup line

A project CSV has the headword but Anki does not.
- From a **`my-cards/`** file: a previous import did not finish. Resume it first (`anki_add.py add <that csv> --commit`), then re-run `batch.py prep`.
- From an **older** file: the note was deleted or re-worded in Anki, or that batch was never imported. Anki wins: treat the word by its live verdict, and flag it.

## Replacing a card after import (the learner corrects a sense)

1. Replace the row in today's `my-cards/{date}_import.csv` (text only; drop its `[sound:]` tags).
2. Fix the manifest. Write `{"<old headword>": null, "<new headword>": {"gram": …, "pos": …}}` to a file, then run `anki_add.py meta my-cards/{date}_import.csv --from <file>`. `null` removes the stale entry, and `add` refuses a manifest entry with no row.
3. `tts.py add my-cards/{date}_import.csv`, then `anki_add.py add my-cards/{date}_import.csv --commit`. Only the new row is added.
4. Correct the counters with a negative bump: `profile.py bump --date {date} "perfect-compus:-1" "imperativ-negativ:1"`.
5. Append what changed to the day's batch log.

The old note stays in Anki. Deleting it is the learner's job; give them its note id from `my-cards/logs/import-{date}.jsonl`.

## Deleting notes

Only when the learner explicitly allows deletion. `anki_add.py delete <nid> ...` is a dry run that prints each note. Ask the learner to confirm the exact notes, then add `--commit`. It refuses notes outside the import deck, takes the day's backup, journals every note in full, deletes, verifies and syncs. Media files are kept (`tts.py orphans` lists unreferenced clips; Anki's Tools → Check Media removes them).

## The single-step commands

`batch.py` only chains these, and each still works on its own:

| Command | Does |
|---|---|
| `anki_add.py preflight` | readiness probe |
| `known.py check <words>` | live dedup / vocabulary verdicts (`--files`: the old file-based ones, for comparison) |
| `dex.py <w> [--forms …] [-- <w2> …]` | DEX senses and stress |
| `profile.py queue --cards N` / `sched` / `bump` | grammar scheduler state |
| `validate.py <csv> [--text]` | card text and audio checks |
| `tts.py estimate --from FILE` / `add <csv>` / `check <csv>` / `orphans` / `voices` | audio |
| `anki_add.py meta <csv> --from FILE` / `add <csv> [--commit]` / `delete` / `create-deck` | Anki writes (the only script that writes to Anki) |
| `usage.py` / `usage.py history` | token usage of this session / of every recorded batch |
