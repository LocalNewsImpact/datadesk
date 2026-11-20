# CIN V2: what datadesk needs to run the relabeling

Development proposal. What datadesk must do to run the CIN V2 procedure
end to end: manage the stories and answers, onboard and accept coders,
record the result of every gate, and compute every agreement figure the
procedure uses.

The procedure itself is the **V2 Procedure** tab of the *Mizzou ML News
Labeling CODEBOOK* Google Doc. The codebook coders work from is the
**V2** tab of the same doc. Nothing here changes either; this document
is the build that lets them run.

Builds on, rather than replaces:

- `docs/CIN_ROUNDS.md`: the Round model (comparison and collection
  rounds, derived sizes, finish rules).
- `docs/CLASSIFICATION_REVIEW_QUEUE.md` §7a (agreement metrics, admin
  only), §7c (embedded check stories, removing a coder), §7e (the
  thesis consensus rule).
- `review/classification.py`: cohorts, samples, assignments, decisions,
  `agreement_report`, `coder_report`, `draw_cohort`, `assign_cohort`.

---

## 1. The procedure in one table

| Round | Stories | Coders per story | Coders | Finished when |
| --- | --- | --- | --- | --- |
| 1. Practice | 20, same for every coder | all | 4, then each new coder | all coded; review meeting held |
| 2. Pilot | ~770 V1 stories: the 367 civic stories + ~400 of the other 567 | 3 | 4 (each codes ~575) | every story has 3 answers; 4 pass conditions met or failed |
| 3. Training | new stories to bring every category to 250 labels, V1 records counted first (~1,500 new labels) | 2, +1 on a split | 6–9 | every category has 250 labels: 200 to train, 50 held back |

The pilot recodes V1 stories so V2 answers are compared with V1 answers
**on the same stories**. The 66 stories V1 coders agreed to reject are
left out of the pilot; the 157 they never agreed on stay in. "Civic
stories" are the 367 remaining V1 stories any V1
coder put in Civic information or Civic Life, primary or secondary.

Round 2 pass conditions, all four required. Each is first-pair
agreement (M2): the share of stories on which the first two coders chose
the same primary. Each is a fixed threshold on the observed rate; the
95% margin at these sizes is about 4 points and is reported beside it.
V1 figures are scored by the same rule, cohorts A–D:

1. The 367 civic stories: at least 83% (V1: 33%).
2. Civic Information and Community Life, each: at least 71% (V1: 11%,
   33%). 71% is the per-category rate implied by condition 4: a matched
   story counts once for its category and a mismatched story counts
   against two, so at 83% overall the average category sits at
   0.83 / (0.83 + 2 × 0.17) = 71%.
3. The other pilot stories: at least 83% (V1: 72%). "Not below V1"
   contradicted condition 4: at 72% here, no result on the civic
   stories brings the weighted total to 83%.
4. All stories, weighted to the V1 mix (367 civic in 934): at least 83% (V1: 57%).

The target is a reliability (Krippendorff's alpha) of 0.80. Chance
agreement across the ten categories is about 0.15, from the V1 data
(64.5% raw agreement, kappa 0.583), so alpha 0.80 needs
0.80 + 0.20 × 0.15 = 83% raw agreement. At 83%, with a third coder
settling about 63% of splits as in V1, about 94% of stories end with a
label two coders agreed on. The earlier 7-in-10 bar gave about 89%
settled labels but an alpha near 0.65, below the 0.667 floor for even
tentative conclusions.

Final label rule (thesis, unchanged): two coders' primaries match →
label at 0.9; one coder's primary is another's secondary → label at
0.7; otherwise the story is left out of training and counted against its
categories. **No story is decided by one person.**

## 2. What exists, and what the procedure needs

| Procedure needs | Datadesk today | Gap |
| --- | --- | --- |
| Coders see headline and text only, as V1 coders did: no URL, publisher, date, byline, photo, caption | Queue printed the URL and fell back to the raw page capture when cleaned text was missing | Fixed with WP1: no URL; `text` only, never `raw`. Captions or datelines inside the stored text are an extraction matter the queue cannot see |
| V1 and V2 answers never pooled | One `CodebookSettings` row; per-category guidance hard-coded in `CODING_GUIDE` | No codebook version on cohorts or decisions |
| "Community Life" on screen, "Civic Life" stored | Labels shown as stored | No display map |
| Rejections: Obituary, Advertorial, Service or lifestyle copy | Six rejections | Three missing |
| Practice, pilot and training rounds with finish rules | Cohorts only; `note` is free text nothing reads | Round model (`CIN_ROUNDS.md`) not built |
| Pilot on ~770 V1 stories, civic and other shuffled, mix hidden from coders | Fixed 25% × 4 strata from the crawler | No fixed-list draw; no per-round draw rule |
| V1 stories with their 2023 text | Queue reads crawler `articles` only; crawler starts 2025-09-21; 0/378 match by URL or ID | No non-crawler story source |
| V1 answers as the baseline | V1 answers live in five JotForm CSVs | Not loaded |
| 2 coders, third only on a split | Fixed `target_coders` | No conditional third |
| Civic and other pilot stories interleaved | Queue serves oldest assignment first | Order reveals the block |
| Coders admitted only after practice | Any granted coder is assigned | No onboarding state |
| Hidden check stories per coder; pause or repeat practice | `coder_report` counts done/outstanding | No check stories; no per-coder accuracy; no pause |
| Agreement with intervals, first-pair agreement, alpha, kappa, label confidence | `agreement_report`: exact primary %, by category and cohort, no intervals | Most metrics missing |
| Gate decisions on record | Nothing | No gate record |
| Training file | None (`CLASSIFICATION_REVIEW_QUEUE.md` Phase 5, undesigned) | No export |

## 3. Work packages

Each package lists what it adds and the test that says it is done.

### WP1. Codebook versions

- **`CodebookVersion`**: `name` (V1, V2.0, V2.1…), `instructions`,
  `guidance` (JSON, replaces `CODING_GUIDE`), `display_names` (JSON,
  stored label → shown label), `rejections` (list), `frozen_at`.
  `CodebookSettings` becomes the editable draft; **Freeze** copies it
  into a new immutable version.
- **`ClassificationCohort.codebook_version`**: required. Copied onto
  each `ClassificationDecision` as `stratum` already is.
- **Display map**: under V2, `Civic Life` shows as `Community Life` in the
  menu, guidance and admin pages. The stored value stays `Civic Life`
  until the retrain changes the label contract (`lnic-contracts`), for
  the reason `CODEBOOK.md` gives: the label string is the model's class
  name.
- **Rejections**: add `obituary`, `advertorial`, `service_copy` to
  `REJECTIONS`. A version lists which it offers, so V1 cohorts keep
  their six.

Done when: a V1 cohort and a V2 cohort run side by side; the V2 menu
shows Community Life and the three new rejections; a V2 decision stores
`Civic Life`; no report mixes the two versions unless asked to compare
them.

### WP2. Rounds

As designed in `CIN_ROUNDS.md`, with the kinds this procedure needs:

- **`Round`**: `name`, `kind` (practice | comparison | collection),
  `codebook_version`, `question`, `draw_rule` (JSON), `coders_rule`
  (base count, third-on-split on/off), `finish_rule` (JSON),
  `opened_at`, `closed_at`. `ClassificationCohort.round` FK; cohorts
  become the batches inside a round.
- **Practice rounds** are excluded from every metric and export by kind,
  not by a filter someone has to remember.
- **Progress** is shown against the finish rule, never as a bare total:
  pilot as "civic stories coded / needed"; training as per-category
  bars with the binding category named.

Done when: Rounds 1–3 can be created from the admin with their rules,
cohorts open inside them, and each round page shows progress against
its own finish rule.

### WP3. Stories from outside the crawler: the V1 import

The pilot's stories are 2023 stories coded in V1. The crawler has
none of them. Re-crawling would change the text, hit paywalls
(stltoday, kansascity) and push 2023 stories into the crawler's
enrichment and BigQuery export. They load into datadesk instead.

- **`ClassificationStory`**: `id` (namespaced `v1:<JotForm ID>`),
  `headline`, `body`, `url`, `host`, `origin` (`v1-2023`), `v1_hash`
  (the JotForm `article_id`). Read by the queue when an assignment's
  `article_id` carries a namespace; crawler articles are unchanged.
- **`V1Decision`**: `story`, `group` (A–E), `primary`, `secondary`,
  `also_present`. The V1 answers, for the baseline (WP7). Not a
  `ClassificationDecision`: V1 coders are not datadesk users, and
  `one_classification_per_coder_per_article` would not apply.
- **`import_v1_stories`** management command: reads the five
  `Missouri_News_Labeling (Group A–E)` CSVs, loads all 1,000 stories'
  V1 answers, and the text for every story. Records each story's V1
  outcome by the thesis rule: label at 0.9 (649), label at 0.7 (128),
  rejected by two coders (66), or no agreed label (157). Flags the 367
  civic stories among the 934 not rejected (any coder chose Civic
  information or Civic Life, primary or secondary). Idempotent.

Source files: Groups A, C, E in `~/Downloads/Archive Excel/`; B, D in
iCloud `Documents/Mizzou/LNIC/`. 1,000 stories, 2,246 answers; text
present for every civic story (median 359 words).

The V1 files' `bart_label` is an older topic model's label set (Roads
and Car Crashes, Weather, Crime…), not CIN. It cannot stand in for the
current model's label.

Done when: 1,000 stories and 2,246 V1 answers load; the 367 are
selectable as a fixed list; a coder assigned `v1:500` sees its 2023
headline and text in the queue.

### WP4. Draw rules

`draw_cohort` gains a rule per round, recorded on the round, and keeps
writing `inclusion_probability` per sample as it does now.

- **Practice**: a fixed list of 20, reused for every new coder.
- **Pilot**: a fixed list. The 367 civic V1 stories plus about 400 drawn
  at random from the other 567 (V1-rejected stories excluded), with the draw's inclusion probability
  recorded.
- **Training**: every category is brought to 250 labels, V1 records
  counted first. A V1 story counts when it has a label: its V2 label
  from the pilot, or its V1 label (0.9 or 0.7) if it was not in the
  pilot. V1 has 272 Emergencies but 39 Education, so new draws go where
  V1 is thin: about 1,500 new labels in all, none for Emergencies.
- **Sources**: the Missouri, Washington and Pennsylvania datasets.
  Vermont is excluded (student newspapers, not fully collected or
  labeled). Per category by model label: up to 60 from Washington, up
  to 60 from Pennsylvania, the rest from Missouri; where a state has
  fewer than 60, all of them. Missouri is 96% of labeled stories, and an
  unstratified draw would train a Missouri-only model for use in three
  states. Thin spots: Pennsylvania has 22 Economic Development and 22
  Sports stories; Washington 53 and 68.
- **Pool statuses**: `CLASSIFIABLE_STATUSES` is `("cleaned", "local")`,
  which holds 321 stories. The labeled corpus is in `labeled` and
  `enriched` (about 100,500 across the three datasets). The draw pool
  becomes those four statuses.
- **Hit rate**: the model's label is not always the coders', so more
  than 1,500 stories are handed out. The first batch of 300 measures,
  per category, the share of draws coders place in the category drawn
  for; the remaining draws per category are then
  (250 − labels so far) ÷ hit rate.
- **Order**: a coder's queue is shuffled within a batch, so civic
  stories never arrive as a run. The mix of a round is shown to admins
  only.

Done when: the pilot list holds 367 civic and ~400 other stories,
interleaved in every coder's queue; inclusion probabilities sum
correctly per stratum; the training round page shows hit rate per
category after its first batch.

### WP5. Two coders, a third on a split

- `coders_rule` = base 2 + third on split (training); base 3 (pilot,
  spread round-robin over its 4 coders).
- When a story's second decision lands and the two primaries differ (or
  the two rejection reasons differ), `assign_cohort` adds one assignment
  to an admitted coder not already on the story, round-robin, in the
  same transaction.
- `coder_report` "needed" counts the conditional third as it arises
  rather than assuming three.

Done when: a story with matching first answers gets no third; a split
gets exactly one third, to a coder who has not seen it; workload stays
within one story across coders.

### WP6. Onboarding and coder acceptance

Per coder, per round, a status with who and when:

`invited → signed in → practice assigned → practice coded → practice
reviewed → admitted` and, later, `paused` or `removed`.

- **Only admitted coders** are assigned pilot or training stories.
  `assign_cohort` filters on it.
- **Practice review page** (admin): each practice story with every
  coder's answer side by side and the V2 definition of each category
  chosen. Opens once every coder has finished. The lead marks the
  meeting held and logs any definition change, which lands in the next
  `CodebookVersion` draft.
- **New coders** joining Round 3 get the same practice list and the same
  review before admission.
- **Check stories** (§7c, decided): 40 stories with known answers
  are seeded into every Round 3 coder's queue, indistinguishable from
  the rest. They are pilot stories on which all three V2 coders chose
  the same primary, and which that coder has not coded. Per-coder
  accuracy against them is M11 (WP7). **Pause rule**: a coder is paused
  when their misses reach the smallest k with
  P(X ≥ k | n, p₀) ≤ 0.01, X binomial, n their check stories coded, p₀
  the other coders' pooled miss rate on check stories. At p₀ = 0.10 and
  n = 40, k = 10: a normal coder is paused 0.5% of the time, a coder
  missing 30% is caught 80% of the time, one missing 40%, 98%. Twenty
  check stories would catch the 30% coder only 39% of the time, which is
  why the count is 40. Evaluated once, at n = 40. Paused → no new assignments, outstanding ones released to other
  coders, practice repeated. The release step is new:
  `ClassificationAssignment.reassigned_from` records a reassignment but
  nothing performs one today. The same step serves the 14-day
  `stale_after_days` rule. Check-story decisions are excluded from
  agreement metrics and from the export.
- **Removal** (decided): a removed coder's decisions are withdrawn from
  metrics and export, and their stories return to the queue.
- **Never shown to coders**: their own or anyone's agreement, or other
  answers on a story (§7a).

Done when: an un-admitted coder receives nothing outside practice; the
review page lists every practice disagreement; a coder below threshold
is paused automatically and their open work is reassigned.

### WP7. Agreement and reliability: every calculation

Computed from decisions on demand (`CLASSIFICATION_REVIEW_QUEUE.md`
§7a: a query, not a stored figure), and frozen into a gate record when a
gate is evaluated (WP8). All exclude practice rounds and removed coders.
All are computed for V1 (from `V1Decision`) by the same code, so every
V1/V2 comparison is like for like.

| # | Metric | Definition | Cut by | Interval | Used for |
| --- | --- | --- | --- | --- | --- |
| M1 | Pairwise agreement | Share of scored coder pairs with the same primary among the ten categories. A category against a rejection is a mismatch. Two rejections are not scored. **Changes `_agree`**, which counts two identical rejections as agreement today. In a third-on-split round, first-pair decisions only: third-coder pairs exist only where two coders already disagreed | overall, category (pairs where either chose it), round, batch, stratum, dataset | 95%, bootstrap over stories (pairs on one story are not independent) | gates 1–3; monitoring |
| M2 | First-pair agreement | Share of stories whose first two decisions (by `decided_at`) match on primary | round, batch, category | 95% bootstrap | gate 4 |
| M3 | V2 − V1 change | M1 under V2 minus M1 under V1 on the same stories, for the 367 civic stories and for the other pilot stories | story group, category | 95% paired bootstrap over stories | gates 1 and 3 |
| M4 | Krippendorff's alpha (nominal) | Overall and per category (one category vs the rest); random stratum only; handles a varying number of coders | round, dataset | 95% bootstrap | gate target 0.80 |
| M5 | Cohen's kappa | Model vs the human label on the holdout; coder vs coder where exactly two coded | category | 95% bootstrap | model evaluation |
| M6 | Label confidence | Thesis rule per story: 0.9, 0.7, or none | category, batch | n/a | share at 0.9 (about 94% expected at the gate); training export |
| M7 | Left-out stories | Stories with no 0.9 or 0.7 label, counted against every category any coder chose | category, batch | n/a | definition signal |
| M8 | Per-coder agreement | M1 restricted to first pairs that include the coder (never tiebreak assignments, which are all on split stories) | coder, category | 95% bootstrap | coder review; removal |
| M9 | Per-coder pace | Median `seconds_spent`, stories per day, rejection rate | coder | n/a | onboarding, workload |
| M11 | Check-story accuracy | Share of a coder's check stories where their primary equals the known answer | coder | 95% Wilson | pause; removal |
| M10 | Round size | Two-proportion sample size (`CIN_ROUNDS.md` formula) from baseline, target, confidence, power | round | n/a | shown before a round opens |

Bootstrap: 2,000 resamples of stories, seeded, so a figure recomputed
from the same decisions is identical.

Done when: each metric has a unit test against a hand-computed fixture;
M2 over the 934 V1 stories kept, cohorts A–D, gives: all stories 56.9%
(923 scored); the 367 civic stories 33.2%; the other 567 stories 72.4%;
Civic information 11.3%; Civic Life 32.6%. M1 over all 1,000 gives 952
scored pairs at 55.4%. With every rejection pair dropped
instead (817 pairs) it gives the published 11.9% and 38.7%, which
confirms the import against the earlier analysis. M6 over all five
groups gives 71.5% of stories at 0.9.

### WP8. Gates and acceptance records

- **`GateResult`**: `round`, `evaluated_at`, `evaluated_by`, per
  condition the metric, population, value, interval, threshold and
  outcome, and an overall outcome: **pass**, **fail**, or **not enough
  data**. Immutable; re-evaluating adds a row. "Not enough data" is
  never shown as a fail.
- **Gate definitions** live on the round's `finish_rule`:
  - Round 1: every coder coded all 20; review held; definition changes
    logged.
  - Round 2: the four conditions in §1.
  - Round 3: every category has ≥ 250 stories at 0.9 or 0.7, V1 records
    and new stories together; per-batch M2 below 83% pauses the round
    and offers a practice round.
- **Coder acceptance record**: every WP6 status change, with who, when
  and the M8 value behind it.
- **Round page**: the gate panel, its history, M1/M2/M6/M7 tables, the
  coder table.

Done when: evaluating the pilot gate writes a `GateResult` with all four
conditions and their intervals; a fail names the condition; a pass
freezes the codebook draft as V2.0.

### WP9. Training export

`CLASSIFICATION_REVIEW_QUEUE.md` Phase 5, now specified.

One row per story with a label:

`story_id` (crawler UUID or `v1:` ID), `source` (crawler | v1), `label`,
`secondary`, `confidence` (0.9 | 0.7), `weight` (primary 1.0, secondary
0.5), `n_coders`, `codebook_version`, `round`, `stratum`,
`inclusion_probability`, `split` (train | holdout).

- Excludes practice rounds, removed coders, rejections and left-out
  stories.
- **V1 stories not in the pilot** (about 220) are exported with their V1
  labels by the same rule, `codebook_version = V1`. Pilot stories carry
  their V2 labels.
- **Holdout** (decided): 50 stories per category, 500 in all, flagged at
  draw time and never exported as `train`.
- Label names: as stored by default; with `--label-contract <version>`
  mapped to that contract's names (Community Life), for the retrain.

Done when: the export of a finished round reproduces M6 counts exactly,
and no holdout story appears with `split = train`.

### WP10. Admin screens

- **CIN → Rounds**: rounds with progress against their finish rule.
- **Round page**: gate panel, metrics, coders, batches; "Add batch".
- **CIN → Coders**: onboarding status column; pause, resume, remove.
- **Practice review page** (WP6).
- **CIN → Codebook**: the draft, the frozen versions, and Freeze.

All admin only.

## 4. Build order

| Order | Packages | Unblocks |
| --- | --- | --- |
| 1 | WP1, WP2, WP6 (practice and admission), WP10 (Rounds, Codebook) | Round 1 |
| 2 | WP3, WP7, WP4 (pilot), WP8 | Round 2: the pilot's stories and its V1 baseline both come from the V1 import |
| 3 | WP4 (training), WP5, WP6 (check stories), WP9 | Round 3 |

## 5. Decided

1. **Scoring.** Only answers within the ten categories are scored. A
   category against a rejection is a mismatch. Two rejections are not
   scored.
2. **Pilot.** About 770 V1 stories, 4 coders, 3 per story. The 66
   stories V1 coders agreed to reject are out; the 157 they never
   agreed on are in.
3. **A failed pilot** is rerun on the same stories by four new coders.
   `one_classification_per_coder_per_article` stays: no coder codes a
   story twice.
4. **Coder quality** is checked with hidden check stories, not a
   first-50 agreement check.
5. **Removed coders'** decisions are withdrawn.
6. **Holdout** is 50 per category.
7. **The 50 MNF stories** coded earlier at WSU are out of scope.
8. **The gate targets reliability 0.80**: 83% first-pair agreement and
   71% per category for Civic Information and Community Life. Reaching
   it is the point of the retraining.
9. **Pause rule** for check stories: binomial, 1 in 100, 40 stories
   (WP6).
10. **Stories come from** the Missouri, Washington and Pennsylvania
    datasets plus the V1 records. Vermont is excluded.
11. **V1 records count toward each category's 250**, and only those with
    a label. Rejected and unlabeled V1 stories are never training data.
12. **State split**: up to 60 per category from each of Washington and
    Pennsylvania, the rest from Missouri.
13. **Coders see the headline and the story text only**: no URL,
    publisher, date, byline, photo or caption, as in V1.

## 6. Open

1. **How much text a coder sees.** The queue shows the first 250 words;
   V1 coders saw the whole story (median 359 words). For the pilot's
   same-story comparison the two must match.
