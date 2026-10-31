# Generation policies

Provider-neutral rules for producing card examples. The priority order is absolute:

1. Correct Romanian and correct intended meaning.
2. Natural contemporary phrasing.
3. A clear, useful everyday situation.
4. Appropriate difficulty (A2–B1 learner).
5. Useful grammar revision.
6. Context and vocabulary diversity.

A lower priority never overrides a higher one. If a scheduled construction can't be realized naturally, use another compatible one; the final fallback is a simple natural sentence.

## Core and stretch examples

- **Core example** (the primary example on every card): no unfamiliar supporting content words; only allowed grammar; short, concrete, unambiguous.
- **Stretch example** (optional second example): at most ONE unfamiliar supporting content lemma, and only if it is high-frequency, personally relevant, or a strong natural collocation with the target. Add a stretch example only when it teaches a genuinely different sense, collocation, or context — never a person/number/tense variant of the core.

## Novelty budget (per batch)

- Core examples: 0 new supporting content lemmas.
- Across all stretch examples in a batch (~20 cards): at most 2 distinct new incidental lemmas.
- An incidental lemma introduced earlier should be recycled in later batches before another new one appears in the same domain.

## Sentence length

4–11 words for normal sentences. Phrases/idioms may need slightly more context; fragments only when the target itself is a formula (e.g. `din păcate`).

## Supporting-vocabulary check (generate → check → repair)

1. Generate the candidate freely (do NOT restrict generation to a whitelist).
2. For each supporting content word (noun, verb, adjective, adverb — not the target), classify:
   - **known** — `known.py check` reports it DUPLICATE or KNOWN. That is the live Anki collection. Query the longest expression first, then single lemmas (stems for inflected forms);
   - **assumed core** — elementary A1 vocabulary and function words any A2 learner has (a fi, a avea, a face, a merge, azi, mâine, casă, bun, mare, eu/tu/el, numbers, prepositions, conjunctions…). That includes the textbook A1 core of home, food, family, body, time and weather: rooms (baie, bucătărie, cameră), ușă, fereastră, masă, pat, apă, pâine, cafea, mamă/tată, ochi, mână, zi, săptămână, ploaie, soare. An A1 word counts as core even when the deck lacks it. Beyond that, judge honestly; when unsure, treat as unknown;
   - **unknown** — everything else.
3. Core example with unknown words → rewrite preserving target + construction but replacing the unknown words. Max 2 repair rounds; then fall back to a simpler construction or a plain natural sentence.
4. Stretch example: one unknown lemma allowed if it passes the novelty test (common OR personally relevant OR strong collocation). Rare/domain-specific words: reject.

Personally relevant domains (may override frequency): the topics listed in `learner.md`.

## Grammar compatibility

Use a construction only when the target sense participates in it naturally:
- Accusative clitic — only if the sense takes a natural direct object.
- Pe-marked object + doubling — only for a human (or personified, definite) direct object: `O aștept pe colega mea`, never `*Îl caut pe pixul`.
- Dative clitic (recipient) — only if an ordinary recipient/beneficiary exists. Dative experiencer — only for psych/state predicates (`a plăcea`, `a-i fi frig/dor/foame`, `a i se părea`).
- Temporal `când` + present — only when the clause refers to the future (`Te sun când ajung`); Romanian never uses the future after `când` there, which is exactly the Ukrainian-speaker trap.
- Impersonal/passive `se` — generic or agentless statements (`Aici se vorbește engleza`, `Se poate plăti cu cardul?`); don't use it where a normal personal subject is more natural.
- Supine — only after its natural licensers (`am de…`, `e greu/ușor de…`, `mașină de spălat`).
- Dacă-hypothetical — a genuinely counterfactual or unlikely condition, conditional in both clauses; a real future condition is `dacă` + present, which is free variation.
- Don't add reflexive features to an already-reflexive target just to "cover" reflexivity.
- Imperfect — for background, repetition, duration; not for one-off completed events.
- Subjunctive — only after verbs/expressions that naturally license it.
- Explicit subject pronouns — only for contrast or emphasis.
- Body parts resist possessive adjectives/pronouns: Romanian uses the definite article with `a avea` or a posture verb (`Are sprâncene groase`, not `Sprâncenele mele sunt…`; `Stătea cu coatele pe masă`, not `Coatele lui erau…`). Three candidates have been rejected on this.
- Subjunctive (`conjunctiv`, `trebuie-sa`) — prefer a form that differs from the indicative where natural: 3rd person (`să vină`, `să facă`) or `să fie` / `să aibă`. Natural triggers include `ca să`, `hai să`, `Să te ajut?`.
- Possessive dative vs reflexive dative — the owner is NOT the subject → `dativ-posesiv` (`Mi s-a stricat telefonul`, `Îi tremură mâinile`); `mi-am / și-a` + the subject's own thing → `reflexiv-dativ` (`Mi-am uitat cheile`).
- Literary future (`voi/vei/va`) — written or work register only: emails, notices, plans, forecasts (`Vă vom anunța rezultatul`). Never casual speech, ads or captions.
- Imperative + clitic — only with a real pronoun object or recipient, or an inherently reflexive verb (`uită-te`, `du-te`). A negated command is `imperativ-negativ`, where the pronoun moves in front (`nu-mi spune`).
- Spoken counterfactual (`imperfect-ipotetic`) — imperfect in both clauses, casual frame, and the condition clearly unreal; otherwise the sentence reads as a past habit.
- Numbers with `de` — only where an amount of 20 or more is natural (prices, minutes, kilometres). Write numbers in words: the voice reads them, and digits read badly.
- `nu mai` — only with a real "no longer / not again / not since" meaning.
- Combined clitics — only when both a recipient and an already-known object are in play (`Mi l-a dat ieri`, `Ți-o trimit mâine`).
- Pluperfect — only for an event that happened before another past event in the same sentence or obvious context (`Plecase deja când am ajuns`).
- Conditional perfect — a missed past possibility or regret (`Aș fi venit, dar am fost bolnav`).
- Relative `care / pe care` — only when the clause identifies which person or thing; `pe care` + clitic for objects (`Filmul pe care l-am văzut`).
- Genitive — possessor phrases and genitive prepositions (`din cauza traficului`, `în fața blocului`, `în timpul ședinței`).
- If the combination needs a contrived scenario, pick another construction.
- Nouns can host grammar through an ordinary supporting predicate (`O să-i trimit factura mâine.` — target: factură; realized: popular future + dative).

## Complexity budget

One primary scheduled construction + at most one secondary per example; everything else simple. A vocabulary card must never become a grammar puzzle.

## Reflexive verbs

Two examples (non-reflexive + reflexive) ONLY when both forms are common and the contrast is meaningful. Obligatorily/predominantly reflexive verbs get one natural reflexive example. (This replaces v1's mandatory transitive+reflexive pair.)

## Example count

Default: one strong core example. Add a stretch example only when it adds a distinct sense, collocation, or context worth learning.

## DEXonline usage

Get evidence with the skill's `dex.py` (senses + stress in one call; WebFetch of dexonline only as a fallback when the script reports a fetch failure). Use it for: sense selection, governing prepositions, valency, collocations, register warnings. Do NOT copy literary or dated dictionary examples onto cards — write contemporary learner-level sentences informed by the evidence.

## Near-duplicate control

Before accepting an example, compare against existing examples for the same lemma anywhere in the known-set: differing only in person/number/tense = duplicate, reject. Two examples on one card must contrast in sense, complement, collocation, register, or situation.

## Context rotation

Prefer an ordinary context that fits the word naturally: home, work, shopping, transport, restaurant, doctor/pharmacy, administration, friends, travel, car, planning. Avoid reusing the same scenario repeatedly within a batch, and never force a context that makes the sentence stranger.

## Naturalness gate

Every candidate goes through the independent review in `naturalness-rubric.md`. Only scores 4–5 are acceptable. If all candidates for a card score <4: regenerate once with simpler grammar; if still <4, use a plain simple sentence (present or perfect compus, zero novelty) — a simple natural card beats a sophisticated awkward one.

## Per-card call budget

At most ~6 LLM steps per card (analysis+generation, review, up to 2 repair rounds with re-review). A card that exhausts its budget gets the simple fallback or is parked in the flags summary for manual attention.
