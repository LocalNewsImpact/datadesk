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
| 1. Practice | 20, same for every coder | all | 3, then each new coder | all coded; review meeting held |
| 2. Pilot | ~200, batches of 30 added | 3 | 3 | enough civic stories to tell; 4 pass conditions met or failed |
| 3. Training | 378 V1 stories + ≥ 2,000 new | V1 stories 3; new 2, +1 on a split | 6–9 | all 378 have 3 answers; every category has 200 labels |

Round 2 pass conditions, all four required:

1. Civic Information agreement rises from V1 (12%), 95% interval excluding no change.
2. Community Life agreement at least 39% (V1 Civic Life).
3. Every other category within its V1 interval.
4. First two coders choose the same primary on at least 7 stories in 10 (V1: 59.8%).

Final label rule (thesis, unchanged): two coders' primaries match →
label at 0.9; one coder's primary is another's secondary → label at
0.7; otherwise the story is left out of training and counted against its
categories. **No story is decided by one person.**

## 2. What exists, and what the procedure needs

| Procedure needs | Datadesk today | Gap |
| --- | --- | --- |
| V1 and V2 answers never pooled | One `CodebookSettings` row; per-category guidance hard-coded in `CODING_GUIDE` | No codebook version on cohorts or decisions |
| "Community Life" on screen, "Civic Life" stored | Labels shown as stored | No display map |
| Rejections: Obituary, Advertorial, Service or lifestyle copy | Six rejections | Three missing |
| Practice, pilot and training rounds with finish rules | Cohorts only; `note` is free text nothing reads | Round model (`CIN_ROUNDS.md`) not built |
| Pilot mix: ⅓ civic, ⅔ spread over other 8, hidden from coders | Fixed 25% × 4 strata | No per-round draw rule |
| 378 V1 stories with their 2023 text | Queue reads crawler `articles` only; crawler starts 2025-09-21; 0/378 match by URL or ID | No non-crawler story source |
| V1 answers as the baseline | V1 answers live in five JotForm CSVs | Not loaded |
| 2 coders, third only on a split | Fixed `target_coders` | No conditional third |
| V1 stories mixed into new batches | Queue serves oldest assignment first | Order reveals the block |
| Coders admitted only after practice | Any granted coder is assigned | No onboarding state |
| First-50 coder check; pause or repeat practice | `coder_report` counts done/outstanding | No per-coder agreement; no pause |
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

The 378 Round 3 stories are 2023 stories coded in V1. The crawler has
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
  V1 answers, and the text for every story. Selects the 378 where any
  coder chose Civic information or Civic Life, primary or secondary.
  Idempotent.

Source files: Groups A, C, E in `~/Downloads/Archive Excel/`; B, D in
iCloud `Documents/Mizzou/LNIC/`. 1,000 stories, 2,246 answers; text
present for all 378 (median 359 words).

The same path loads the 50 MNF 2025 stories and the collaborator's
labels if they are not in the crawler.

Done when: 1,000 stories and 2,246 V1 answers load; the 378 are
selectable as a fixed list; a coder assigned `v1:500` sees its 2023
headline and text in the queue.

### WP4. Draw rules

`draw_cohort` gains a rule per round, recorded on the round, and keeps
writing `inclusion_probability` per sample as it does now.

- **Practice**: a fixed list of 20, reused for every new coder.
- **Pilot**: ⅓ stories whose model label is `Civic information` or
  `Civic Life`; ⅔ split evenly across the other eight model labels.
  Batches of 30 until the finish rule is met.
- **Training**: per-category floor 200 by model label, oversampling the
  rare ones, plus the 378 V1 stories as a fixed list.
- **Order**: a coder's queue is shuffled within a batch, so V1 stories
  and civic stories never arrive as a run. The mix of a round is shown
  to admins only.

Done when: a pilot batch of 30 comes out ≈10 civic / 20 other; a
training batch interleaves V1 and new stories in every coder's queue;
inclusion probabilities sum correctly per stratum.

### WP5. Two coders, a third on a split

- `coders_rule` = base 2 + third on split (training, new stories);
  base 3 (pilot; V1 stories).
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
- **First-50 check**: after a coder's first 50 decisions on shared
  stories, their agreement with the others on those stories is computed
  (WP7). Below the threshold → `paused`: no new assignments, outstanding
  ones released to other coders, practice repeated. The release step is
  new: `ClassificationAssignment.reassigned_from` records a reassignment
  but nothing performs one today. The same step serves the 14-day
  `stale_after_days` rule.
- **Removal**: a removed coder's decisions are withdrawn from metrics
  and export, and their stories return to the queue
  (`CLASSIFICATION_REVIEW_QUEUE.md` §7c's safer answer).
- **Never shown to coders**: their own or anyone's agreement, or other
  answers on a story (§7a).
- **Embedded check stories** (§7c) are a later step; practice stories
  with settled answers can seed them.

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
| M1 | Pairwise agreement | Share of coder pairs on a story with the same primary; two identical rejections agree (`_agree`, unchanged) | overall, category (pairs where either chose it), round, batch, stratum, dataset | 95%, bootstrap over stories (pairs on one story are not independent) | gates 1–3; monitoring |
| M2 | First-pair agreement | Share of stories whose first two decisions (by `decided_at`) match on primary | round, batch, category | 95% bootstrap | gate 4 |
| M3 | V2 − V1 change | M1 per category, V2 minus V1, on the same kind of story (civic third vs the rest) | category | 95% bootstrap of the difference | gate 1 "clear of chance" |
| M4 | Krippendorff's alpha (nominal) | Overall and per category (one category vs the rest); random stratum only; handles a varying number of coders | round, dataset | 95% bootstrap | reported reliability; 0.667 tentative, 0.80 reportable |
| M5 | Cohen's kappa | Model vs coder on the MNF 50; coder vs coder where exactly two coded | category | 95% bootstrap | model check |
| M6 | Label confidence | Thesis rule per story: 0.9, 0.7, or none | category, batch | n/a | share at 0.9 (target 9 in 10); training export |
| M7 | Left-out stories | Stories with no 0.9 or 0.7 label, counted against every category any coder chose | category, batch | n/a | definition signal |
| M8 | Per-coder agreement | M1 restricted to pairs that include the coder, over shared stories | coder, category | 95% bootstrap | first-50 check; removal |
| M9 | Per-coder pace | Median `seconds_spent`, stories per day, rejection rate | coder | n/a | onboarding, workload |
| M10 | Round size | Two-proportion sample size (`CIN_ROUNDS.md` formula) from baseline, target, confidence, power | round | n/a | shown before a round opens |

Bootstrap: 2,000 resamples of stories, seeded, so a figure recomputed
from the same decisions is identical.

Done when: each metric has a unit test against a hand-computed fixture;
the V1 import reproduces the published V1 figures: M1 over cohorts A–D
(813 comparable pairs: Civic information 12%, Civic Life 39%, Sports
94%); M2 and M6 over all five groups (first pair 59.8%; 71.5% of
stories at 0.9).

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
  - Round 3: all 378 V1 stories have 3 answers; every category has ≥ 200
    stories at 0.9 or 0.7; per-batch M1 not below the pilot result
    (a drop pauses the round and offers a practice round).
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
- **Holdout**: a random-stratum set flagged at draw time and never
  exported as `train`. Proposed size: 200 stories.
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
| 2 | WP3 (V1 answers), WP7, WP4 (pilot), WP8 | Round 2: the gate needs the V1 baseline, so V1 answers load before the pilot opens |
| 3 | WP3 (V1 text), WP4 (training), WP5, WP6 (first-50 check), WP9 | Round 3 |
| 4 | Embedded check stories | Large-group quality (§7c) |

## 5. Open questions

1. **First-50 threshold.** Proposed: paused when a coder's M8 is more
   than 15 points below the group median over at least 50 shared
   stories.
2. **Removal.** Withdraw a removed coder's decisions (proposed, §7c) or
   keep them.
3. **Holdout size.** Proposed 200 random-stratum stories.
4. **MNF 50.** Whether the stories are in the crawler or load through
   WP3, and where the collaborator's labels are.
5. **One decision per coder per story, across all rounds**
   (`one_classification_per_coder_per_article`). Kept as is: it means a
   coder can never recode a story they saw in an earlier round, so any
   recode (MNF 50) needs a coder who has not seen it.
