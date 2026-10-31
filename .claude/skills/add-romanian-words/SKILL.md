---
name: add-romanian-words
description: DEFAULT workflow for turning Romanian vocabulary into Anki cards for the learner described in learner.md (translation language, level, token mode), with grammar scheduling, controlled supporting vocabulary, independent naturalness review, TTS audio, and direct tagged import into Anki via AnkiConnect. Invoke whenever the user provides Romanian words to add to their Anki deck. For first-time setup use the setup skill.
allowed-tools:
  - Bash(runtime/python/python .agents/skills/add-romanian-words/scripts/*)
  - PowerShell(runtime/python/python .agents/skills/add-romanian-words/scripts/*)
  - WebFetch(domain:dexonline.ro)
---
<!-- Generated from .agents/skills/add-romanian-words/SKILL.md by adapters.py sync -- edit that file, not this one. -->

# Add Romanian words to Anki

Words in, finished cards in Anki out. Anki is the source of truth for dedup and vocabulary. The import is automatic, every note is tagged (`gram::`, `pos::`, `batch::`), and a batch under the spend limit runs start to finish without a single question.

**Where things live.** Scripts: `.agents/skills/add-romanian-words/scripts/`, always called by that full path from the project root. The learner's own files: `my-cards/learner.md` (who the cards are for) and `my-cards/grammar-profile.md` (constructions and their statuses). Shipped with this skill: `.agents/skills/add-romanian-words/policy/policies.md` (generation rules) and `.agents/skills/add-romanian-words/policy/naturalness-rubric.md` (the reviewer's brief). `batch.py prep` prints learner.md, the policies and the grammar queue, so don't read them separately. Outputs: `my-cards/` (CSV, manifest) and `my-cards/logs/` (batch log, import journal, usage). Grammar-rule cards: `.agents/skills/add-romanian-words/reference/grammar-cards.md`. Anything broken or to be corrected: `.agents/skills/add-romanian-words/reference/corrections.md`.

**Save requests on mechanics, never on cards.** Every request re-sends the whole conversation, so batch every stage into one call, never interleave per-word rounds, and never re-read files the scripts already printed. But the card work the MODE asks for is never cut to save a request: every candidate, every stretch example that earns its place, every repair round, and every stress lookup a winning sentence needs. One extra `dex.py` call for a good stretch example is always worth it.

## Unattended by design

Do not stop to ask questions mid-pipeline. Decide, record the decision in the flags summary, and let the learner fix the note in Anki if they disagree:

- **Polysemy:** one card per headword per batch. The dominant contemporary sense is the core example. A second sense that maps to a different translation becomes the stretch example, with both answers on the front (`бути вартим, варто; заслуговувати`). Further senses: flag them, don't card them.
- **Doubtful input** (a typo, a register clash, a formula natives don't use): fix it the way the evidence (DEX, the reviewer) says, keep the learner's intent, and flag the change.
- **Same lemma, clearly different meaning** from an existing card: include it with `"new_sense": true`.

There are exactly three reasons to stop and wait for the learner:
1. `prep` prints **NOT READY** (Anki closed, AnkiConnect missing, a setting absent). Tell them exactly what to fix.
2. **The spend gate:** `commit` exits 3.
3. **The import is refused for a reason only the learner can clear:** the Anki reviewer is open, or Anki stopped answering.

## Card format

**Rows.** Column 1 (front) = the translation, in the learner's language from learner.md. Column 2 (back) = Romanian. One row per card, never reverse rows. Headword in `<b>…</b>`, then `<br><br>`, then examples `<i>…</i>` joined by `<br>`. The stressed vowel is `<u>…</u>`, Romanian side only. Never type a `[sound:]` tag.

```
front: <b>розчісувати</b><br><br><i>Розчісую волосся щоранку.</i>
back:  <b>a pieptăn<u>a</u>, pi<u>e</u>ptăn</b><br><br><i>Îmi pi<u>e</u>ptăn părul în fiecare dimineață.</i>
```

**Normalization** (the headword exactly as the table says; the audio and dedup scripts depend on it):

| Type | Format | Example |
|---|---|---|
| Verb | `a <inf>, <1sg>` | `a merge, merg` |
| Reflexive verb (has 1sg) | `a (se) <inf>, (mă) <1sg>` | `a (se) cântări, (mă) cântăresc` |
| Reflexive impersonal (no 1sg) | `a (se) <inf>, (se) <3sg>` | `a (se) caria, (se) cariază` |
| Long-reflexive (`-și`) | `a-și <inf>, îmi <1sg>` | `a-și aminti, îmi amintesc` |
| Noun | `<indef-art> <sg>, <num> <pl>` | `un vârf, două vârfuri` |
| Uncountable noun | singular only, flag | `o salivă` |
| Adjective | bare masculine | `diferit` |
| Phrase / idiom | keep as-is | `gumă de mestecat` |
| Formula with an open slot | keep as-is, `...` for the slot, no space before it | `Te felicităm cu ocazia...` |

Fix source-data errors (Cyrillic look-alikes, `ş`/`ţ` → `ș`/`ț`, typos) and flag them. Derive the translation independently; a Russian source translation is a cross-check only.

**Stress** is not predictable from spelling, and it moves within a paradigm (`pieptăn<u>a</u>` vs `pi<u>e</u>ptăn`). So it is looked up per form, never inferred:
- `HEADWORD`/`1SG`/`PL` lines from prep, and `FORM` lines from `dex.py --forms`, are the only sources. `no stress found` → leave the word unmarked and flag it. Never guess.
- Mark **every part of the headword** (infinitive and 1sg; singular and plural) and, in each example, **the target word only**. Never mark the rest of the sentence. Never mark a monosyllable (DEX gives none; that is correct).
- Use `<u>` on one vowel. **Never use accented characters** (`pieptănă`): they change the letters, and dedup silently breaks. `validate.py` refuses a `<u>` that wraps more than one letter, and an example with more marked words than the headword has.

**Tags** are built by the scripts from the fields you give per card. A card gets a `gram::` tag for every construction it practises, so `tag:gram::conjunctiv` in Anki finds every card with the subjunctive:
- `gram`: the construction **realized in the core example** (first example), using the Tag column of the grammar profile. If the scheduled one was dropped, name the most salient listed construction the core sentence does use; every sentence has a tense (`prezent`, `perfect-compus`, …). Blocked tags are refused.
- `pos`: verb rows → `verb`; noun and uncountable rows → `noun`; adjective → `adjective`; phrase/idiom → `phrase`; a single-word adverb → `adverb`; a single function word → `other`.
- `stretch`: each stretch example's main construction, which also becomes a `gram::` tag.
- `also` (only when the MODE line says `grammar tags: every listed construction`): every OTHER construction from the profile that any example realizes, e.g. `Nu-l mai împinge pe fratele tău!` → gram `imperativ-negativ`, also `pe-acuzativ`, `clitic-acuzativ`. A construction that contains another counts as both (`trebuie-sa` → also `conjunctiv`; `daca-ipotetic` → also `conditional`). List only what the sentence really does; free variation (negation, questions, possessive adjectives) has no tag. A blocked tag is refused, so an example that slips in blocked grammar is caught.
- `batch::<date>` is added automatically.

## Pipeline

1. **Prep — the first action, before any research:**
   ```
   runtime/python/python .agents/skills/add-romanian-words/scripts/batch.py prep "a accelera, accelerez" "o draperie" ...
   ```
   Pass every candidate headword (normalized as far as the input allows). On NOT READY, stop (reason 1). Otherwise you get the following, and never need to re-derive any of it:
   - **DEDUP**: `DUPLICATE` = already a card, drop it unless it is a clearly different sense (`new_sense`). `KNOWN`/`UNKNOWN` = proceed. `[FILE-ONLY]`: see `.agents/skills/add-romanian-words/reference/corrections.md`. A word normalized after prep (e.g. `Stă de vorbă` → `a sta de vorbă`) goes into the step-4 `known.py` call.
   - **DEX**: senses and headword stress per survivor.
   - **QUEUE**: the grammar schedule.
   - **LEARNER** and **POLICIES**: follow both.
   - **MODE**: how many candidates, how many repair rounds, when a stretch example is allowed, and how much the log keeps. Follow it exactly.
   - A **NOTE** about an earlier batch in this conversation: pass it on in the report.
2. **Senses and schedule.** Per card, pick ONE productive contemporary sense (polysemy rule above).
   - Assign exactly **one** scheduled construction. Walk the allowed queue in order: each card takes the first construction that fits its sense naturally (compatibility rules in POLICIES), and a construction once picked moves to the back for the rest of the batch.
   - Learning slots: at most the cap printed in QUEUE, least-used first, only where natural. A card that takes a learning slot has the learning construction *instead of* an allowed one.
   - Grammar the target always carries (`cu ocazia` + genitive, `a-și aminti` + dative) is inherent: it needs no scheduling and does not use the learning cap. Record it in the card's `inherent` list (step 7): it is tagged but never counted.
3. **Draft** the candidates the MODE allows, per POLICIES: core-example constraints (no unknown supporting words, allowed grammar only, 4–11 words, an ordinary implied situation), varied contexts across the batch, and an optional stretch example where it adds a distinct sense, collocation or context.
4. **One shell command** for all drafted candidates together:
   ```
   runtime/python/python .agents/skills/add-romanian-words/scripts/known.py check <every supporting content word, stems for inflected forms>; runtime/python/python .agents/skills/add-romanian-words/scripts/dex.py <target> --forms <every inflected target form in any candidate> -- <target2> --forms ...
   ```
   Classify supporting words as known / assumed core / unknown per POLICIES (prefix-only hits: judge from the context, `apă` inside `apăra` is not evidence). Rewrite core candidates with unknown words, within the MODE's repair rounds, then fall back simpler.
5. **Independent naturalness review.** Use ONE fresh `naturalness-reviewer` subagent for the whole batch (split only above ~15 cards); it reads the rubric itself.
   - Start the prompt with `Learner: native <language> speaker, Romanian <level>.` (from LEARNER). Then give per candidate only: tag (`1a`, `1b`, …), target lemma, intended sense, Romanian sentence, translation.
   - NEVER include the scheduled construction, coverage reasoning or why a sentence was written.
   - **For idioms and fixed phrases, add the register question as an EXTRA in this first pass** ("is this current spoken Romanian? what do natives say instead?"). Asked later, it costs two extra rounds.
   - Each repair round is one more fresh reviewer call covering all repaired candidates together.
6. **Select.** The best candidate scoring ≥4 wins. On equal scores the reviewer's `PICK` for that card wins; only when it says `none`, prefer the one realizing the scheduled construction. A scheduled construction never beats a more natural sentence.
   - All core candidates <4: regenerate once, simpler. Still <4: a plain simple sentence (present or perfect compus, zero novelty).
   - A stretch example is optional: if none scores ≥4, drop it; never regenerate or fall back for a stretch.
   - Check the winner isn't a near-duplicate of an existing example for a related lemma (person/number/tense variants count).
   - Stress for every target form in the winners comes from step 4's FORM lines. Only a form that wasn't there needs one more `dex.py` call, all such words chained in one command.
7. **Commit.** Write the batch file `my-cards/batch.json` with your file-writing tool (never a shell heredoc), then run `runtime/python/python .agents/skills/add-romanian-words/scripts/batch.py commit my-cards/batch.json`:
   ```json
   {"cards": [{"front": "<b>розчісувати</b><br><br><i>Розчісую волосся щоранку.</i>",
               "back": "<b>a pieptăn<u>a</u>, pi<u>e</u>ptăn</b><br><br><i>Îmi pi<u>e</u>ptăn părul în fiecare dimineață.</i>",
               "gram": "reflexiv-dativ", "pos": "verb", "scheduled": "reflexiv-dativ", "stretch": [], "also": ["prezent"]}],
    "log": "## a pieptăna, pieptăn\n- Input …; sense …; scheduled X, realized Y\n- Candidates: 1a \"…\" — 5 ✔; 1b \"…\" — 3 ✗ (reason)\n- Stress: …\n\n## Totals\n- fallbacks, rejected candidates, incidental lemmas, learning slot used/unused"}
   ```
   - `scheduled` = the one construction assigned in step 2, whether it survived or not.
   - `stretch` = the primary construction of each stretch example, one tag per example after the first. `also` = the other constructions, per the MODE line. `inherent` = grammar the target itself carries (step 2). Only `gram` and `stretch` count in the grammar counters, and never an inherent one.
   - `log` = per card: input, chosen sense, scheduled vs realized, the candidates as MODE says, rejection reasons, novelty introduced; then the batch totals only you know. The script adds the audio, import, counter and cost lines itself.

   Exit codes:
   - **0**: committed.
   - **2**: refused, nothing written. Fix every listed problem in the file, then re-run.
   - **3**: the spend gate. Show the learner the preview table it printed, plus each card's naturalness score, scheduled construction and flags, and wait for one approval (it covers the card text, the audio cost and the import). Then re-run with `--approved`.
   - **1**: stopped midway. Do what it says, then re-run the same command; it resumes.
8. **Report** (always). This is usually the first time the learner sees the cards:
   - the card table (front, back, `gram`, `pos`);
   - one line each for audio, import and cost, from commit's output;
   - the **flags summary**: translation disagreements, source-data fixes, uncountables, `new_sense` overlaps, uncertain gender/plural/conjugation, fallbacks, reviewer rejections with scores, incidental vocabulary, words left unmarked for lack of DEX stress data, polysemy and input decisions taken without asking, `[FILE-ONLY]` hits, skipped rows, and sync warnings.
   - If prep printed the earlier-batch NOTE, end with one line suggesting a fresh conversation for the next batch.

## Do NOT

- Emit reverse rows, headers, or Russian translations (unless learner.md says the translation language is Russian).
- Insert accented characters for stress, guess a stress position, mark a monosyllable, mark the translation side, or mark a whole sentence.
- Prefix-match paradigm cells, or infer an inflected form's stress from the headword.
- Read the scripts' source, import their functions, or call AnkiConnect yourself (not even to read). The commands in this file are the whole interface; a script run with no arguments prints its usage.
- List or open earlier batches in `my-cards/` (CSVs, manifests, logs) to copy their format, or explore the project folder. The card format is in this file, and `prep` prints today's file and everything else a batch needs.
- Write ad-hoc extraction, normalization or validation code when a script exists. Extend the script instead, so fixes accumulate (every dedup and stress bug so far came from re-deriving logic inline).
- Let the reviewer see the scheduled construction or the generator's reasoning.
- Run per-word rounds, re-read files `prep` printed, or split a stage into several calls.
- Use a blocked construction in any example, even if the sentence is natural. Copy literary or dated DEX examples onto cards.
- Type, hand-edit or "fix" a `gram::`, `pos::`, `batch::` or `[sound:]` tag. The batch file and the scripts own them.
- Write to Anki except through the scripts (`anki_add.py` is the only one that writes). Work around a refusal or a read-back mismatch. Pass `--force` (only for the reviewer-open guard, and only when the learner asks).
- Delete, edit or "fix" existing notes on your own initiative (see `.agents/skills/add-romanian-words/reference/corrections.md`).
- Alter a sentence to make it read better aloud. TTS adapts to the text, never the reverse.
- Stop to ask anything outside the three stop conditions.
- Delete anything from `collection.media`.
- Print, echo, or paste the ElevenLabs API key. The scripts read `.env` themselves.
