# Grammar cards (rules, not words)

Read this only when the learner gives a grammar rule or paradigm table instead of words.

Make **drill cards**, not rule summaries. The learner's choices (2026-10-18, first batch: subjunctive formation):

- **Concrete forms, one per card.** Front: the translation cue plus the Romanian input form (`<b>щоб він бачив</b><br><br>vede → să …`). Back: the answer, the infinitive with its conjugation, and the ending change (`<b>să v<u>a</u>dă</b><br><br>a vede<u>a</u> · conj. II · -e → -ă (e → a)`). Also make cards for the high-frequency irregulars, and short cards that map an infinitive ending to its conjugation. **No abstract rule-summary cards** (the learner removed them).
- **Text only, no audio.** **Stress is still marked** on the Romanian side, and every polysyllabic form is checked with `dex.py --forms`. There is no stress on the front: `validate.py` forbids `<u>` on the translation side.
- **Pipeline.** `batch.py prep` with the answer forms (preflight + dedup), `dex.py --forms` for every form, then one batch file with `"kind": "grammar"` → `batch.py commit`. Every card is `"pos": "grammar"` with the `gram` tag of the construction being drilled; `scheduled` and `stretch` are not used. Skip the naturalness review (there are no sentences); there is no spend gate (nothing is billed) and no counter update. The batch log is still written (the `log` field).
- The cards go to `my-cards/{date}_grammar.csv` with their own manifest. The scripts refuse `[sound:]` tags in a grammar file and `pos::grammar` in an `_import.csv`, and `tts.py` refuses grammar files.
