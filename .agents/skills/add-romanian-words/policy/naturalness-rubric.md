# Naturalness review — role brief

An independent quality gate. **Must run in a fresh subagent** (Claude Code: the Agent tool) so the reviewer does not inherit the generator's context or justifications.

## Input the reviewer receives

- Target lemma and its intended sense (one line).
- The Romanian candidate sentence(s).
- The translation(s), in the learner's language.
- The learner: native language and Romanian level (from `learner.md`).
- Optionally: which supporting words are new for the learner.

## Input the reviewer must NOT receive

- Grammar-coverage deficits or which construction was "scheduled".
- Any pressure to preserve a particular construction.
- The generator's reasoning or explanation of why the sentence works.

## Rubric (score each candidate 1–5)

1. Incorrect Romanian or semantically strange.
2. Exercise-like, translated-sounding, or clearly awkward.
3. Grammatically possible but marked, stiff, or needs an unusual context.
4. Natural in an obvious ordinary context.
5. Default, contemporary everyday phrasing — what a Romanian speaker would actually say.

Only 4 and 5 are acceptable for a card.

## Questions the reviewer answers

- Would a Romanian speaker say this without knowing it's an exercise?
- What ordinary situation does the sentence imply? (If it takes a paragraph to explain, the sentence fails.)
- Is the target's intended sense immediately clear from the sentence?
- Is there a more usual way to express the same meaning? (**Simpler-alternative test:** if a plainer formulation is what a speaker would actually choose in the same situation, score ≤3.)
- Does the possessive/clitic/tense choice require special contrast or context to sound right?
- Is the register contemporary and conversational (not literary, dated, or officialese)?
- Does the translation match the exact Romanian sentence (sense, aspect, tone)?

## Output format (per candidate)

**Score 4–5 (passing):** one line only — `TAG: <score> — <one-line verdict> — translation ok | issue: <what>`

**Score 1–3 (failing):** the full block —

```
score: <1-3>
verdict: <one line>
implied_situation: <a short phrase>
translation: ok | issue: <what>
fix: <the smallest change that makes it natural, or "discard">
```

**Per card (after its candidates):** `PICK <card>: <tag>` — the 4–5 candidate a native speaker would most naturally choose to teach the word — or `PICK <card>: none`.
