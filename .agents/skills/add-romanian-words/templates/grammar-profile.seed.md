# Grammar learner profile

Which Romanian grammar the example sentences may use. Created by the setup skill on {{DATE}}; edit the Status column freely, or run the setup skill again to redo the interview.

Statuses:
- **allowed** — used freely in examples.
- **learning** — practised a little: at most 1 example per 20 cards (rounded up), across ALL learning rows together.
- **blocked** — never appears in an example.

Columns:
- **Tag** — the card's `gram::<tag>` in Anki. Never rename a tag: cards already carry it. To change a construction, add a new row with a new tag.
- **Last used / Uses** — updated when the construction is *realized* in a committed example (`profile.py bump`, run by `batch.py commit`).
- **Last scheduled** — updated whenever the scheduler *picks* it, realized or not (`profile.py sched`).

The counters are the scripts' job; the statuses are yours.

| Construction | Tag | Example | Status | Last used | Last scheduled | Uses |
|---|---|---|---|---|---|---|
| Present indicative | prezent | Lucrez de acasă azi. | allowed | – | – | 0 |
| Perfect compus | perfect-compus | Am terminat raportul ieri. | allowed | – | – | 0 |
| Imperfect | imperfect | Când eram mic, citeam mult. | allowed | – | – | 0 |
| Popular future (o să) | viitor-popular | O să vin mâine dimineață. | allowed | – | – | 0 |
| Present subjunctive (să…) | conjunctiv | Vreau să vină și Ana cu noi. | allowed | – | – | 0 |
| Trebuie să + subjunctive | trebuie-sa | Trebuie să fie gata până vineri. | allowed | – | – | 0 |
| Modal a putea (pot să / pot + infinitive) | modal-putea | Nu pot veni mâine la birou. | allowed | – | – | 0 |
| Conditional (polite requests/softeners) | conditional | Aș vrea o cafea. | allowed | – | – | 0 |
| Imperative (affirmative) | imperativ | Închide fereastra, te rog! | allowed | – | – | 0 |
| Negative imperative (nu + infinitive) | imperativ-negativ | Nu pleca încă, mai stai puțin! | allowed | – | – | 0 |
| Accusative clitic (anaphoric) | clitic-acuzativ | L-am sunat ieri, dar nu a răspuns. | allowed | – | – | 0 |
| Pe-marked object + clitic doubling | pe-acuzativ | O aștept pe colega mea la gară. | allowed | – | – | 0 |
| Dative clitic (recipient) | clitic-dativ | Îi trimit factura azi. | allowed | – | – | 0 |
| Dative experiencer (îmi place / mi-e / mi se pare) | dativ-experimentator | Îmi place cafeaua de dimineață. | allowed | – | – | 0 |
| Possessive dative | dativ-posesiv | Mi s-a stricat telefonul. | allowed | – | – | 0 |
| Reflexive accusative | reflexiv-acuzativ | Mă pregătesc de plecare. | allowed | – | – | 0 |
| Reflexive dative (a-și) | reflexiv-dativ | Îmi amintesc adresa. | allowed | – | – | 0 |
| Comparative & superlative (mai … decât / cel mai) | comparativ | Cafeaua de aici e mai bună decât la birou. | allowed | – | – | 0 |
| Temporal clause, future reference (când + present) | cand-prezent | Te sun când ajung acasă. | allowed | – | – | 0 |
| Imperative + clitic after the verb (dă-mi, spune-i, uită-te) | imperativ-clitic | Dă-mi numărul tău, te rog. | allowed | – | – | 0 |
| nu mai inside the verb group (no longer / not again) | nu-mai | N-am mai fost la mare de doi ani. | allowed | – | – | 0 |
| Numbers from 20 up take de | numeral-de | Parcarea costă douăzeci de lei pe zi. | allowed | – | – | 0 |
| Literary future (voi/vei/va) | viitor-literar | Voi reveni cu detalii. | learning | – | – | 0 |
| Combined clitics (dative + accusative only) | clitice-combinate | Mi l-a dat ieri. | learning | – | – | 0 |
| Pluperfect | mai-mult-ca-perfect | Plecase deja când am ajuns. | learning | – | – | 0 |
| Conditional perfect | conditional-perfect | Aș fi venit, dar am fost bolnav. | learning | – | – | 0 |
| Impersonal/passive se | se-impersonal | Se poate plăti cu cardul? | allowed | – | – | 0 |
| Supine (de + participle form) | supin | Am multe de făcut până diseară. | learning | – | – | 0 |
| Genitive (noun phrase, or din cauza / în fața / în timpul + genitive) | genitiv | Cheile mașinii sunt pe masă. | learning | – | – | 0 |
| Relative clause (care / pe care + clitic) | pe-care | Filmul pe care l-am văzut ieri era slab. | learning | – | – | 0 |
| Dacă-hypothetical (conditional in both clauses) | daca-ipotetic | Dacă aș avea timp, aș găti mai des. | learning | – | – | 0 |
| Spoken counterfactual (imperfect in both clauses) | imperfect-ipotetic | Dacă știam, veneam mai devreme. | learning | – | – | 0 |
| Gerund | gerunziu | Am învățat engleza uitându-mă la filme. | blocked | – | – | – |
| Periphrastic passive (a fi + participle) | pasiv | Cererea a fost aprobată ieri. | blocked | – | – | – |
| Presumptive | prezumtiv | Unde o fi telefonul meu? | blocked | – | – | – |

## Scheduling rule

`batch.py prep` prints the queue; never sort dates by hand.

- **Allowed:** walk the queue in order. Each card takes the first construction that is *naturally compatible* with its target sense (compatibility rules in `policies.md`); a construction once picked moves to the back for the rest of the batch.
- **Learning:** at most **1 example per 20 cards, rounded up**, across all learning constructions combined. A slot goes to the learning construction tried longest ago (the later of Last used and Last scheduled; fewer uses breaks a tie) that fits a card naturally. Never force one — an unused slot is fine, and a learning slot never justifies choosing a lower-scored candidate.
- **Blocked:** never. A candidate containing one is rejected regardless of quality.
- **Built-in grammar** — grammar the target word itself always carries (`cu ocazia` + genitive, `a-și aminti` + dative) is tagged on the card but never scheduled or counted: it is no evidence of practice.

Anything not listed is free variation — negation, questions, person, number, possessive adjectives (`mașina mea`), că-clauses, `după ce` + perfect, and so on.
