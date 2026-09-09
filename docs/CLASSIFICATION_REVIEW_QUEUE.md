# The classification review queue

A queue that collects human CIN labels good enough to measure the
production model against, and to retrain it.

It is not a review queue in the sense the other three are. Those ask
"was this call right", show the reviewer what was decided, and write a
correction. This one asks a person to read a story and say what it is
about, without showing them what anything decided, because a label
produced by a reader who has seen the model's answer is not independent
of it and cannot be used to score it.

---

## 1. What the model is, and what it can be given

`src/ml/article_classifier.py` in the crawler: `bert-base-uncased`,
fine-tuned, `AutoModelForSequenceClassification` behind a HuggingFace
pipeline. It is **single-label** — a softmax over ten classes — and
returns them ranked, of which the top two are stored.

Two consequences the UI cannot ignore:

**A primary label and an optional secondary — not one, and not ten.**

The original coding form asked for exactly that, and it is the right
shape: many stories do not sit in one category, and the production model
was trained on weighted values derived from the primary and secondary
votes. `article_labels` stores the model's own answer the same way —
`primary_label` and `alternate_label`, each with a confidence.

Measured in the historical cohorts, a secondary was given on **175 of
500** rows, so it is a control people use rather than a vestige.

The form also carried a third tier, "Also Present", a checkbox per
category. It was used on **15 of 500 rows — 3%**. Ten more checkboxes
for that is cognitive load buying almost nothing, and it should not be
rebuilt.

**The model reads title + body, truncated at 512 BERT tokens** — roughly
350–400 words. `ArticleClassificationService._prepare_text` joins the
headline and the cleaned body (`text`, falling back to `content`) with a
blank line.

So the reading window shown to a person should be about what the model
gets. Judged from the whole of a 2,000-word story, a human label
describes an input the model never saw, and disagreement then measures
the truncation rather than the model.

### The ten labels, and how uneven they are

Measured in production, `article_labels.primary_label`:

| label | rows |
| --- | ---: |
| Civic Life | 31,497 |
| Sports | 22,610 |
| Civic information | 21,176 |
| Emergencies and Public Safety | 17,566 |
| Political life | 11,794 |
| Environment and Planning | 6,090 |
| Health | 5,289 |
| Education | 4,517 |
| Economic Development | 3,231 |
| Transportation Systems | 2,412 |

13:1 between the commonest and the rarest. A sample drawn at random from
the corpus is mostly Civic Life and Sports, and would say almost nothing
about Transportation Systems — which is exactly where a model trained on
the same distribution is likely to be worst.

---

## 2. What the sample can and cannot prove

The requirement is "95%+ confidence in precision and recall". Those are
two different measurements with very different costs, and the second one
is the expensive half.

### Precision is affordable

Precision for label C: of the articles the model called C, how many are
C. Sample from the model's own output, per label.

For a proportion at 95% confidence, worst case (p=0.5):

| interval | rows per label | ten labels |
| --- | ---: | ---: |
| ±5% | 385 | 3,850 |
| ±7% | 196 | 1,960 |
| ±10% | 97 | 970 |

### Recall is not, and this needs saying plainly

Recall for label C: of the articles that ARE C, how many the model found.
That requires knowing the true label of articles the model did **not**
call C — so it is estimated by labelling a random sample of everything
and finding the ones it missed.

For a rare class this is brutal. Transportation Systems is 1.9% of the
corpus. A random sample of 1,000 articles contains about 19 of them,
which supports no useful interval at all. Estimating recall for the rare
labels to ±10% needs a random sample in the tens of thousands, which is
not a review queue, it is a year of somebody's life.

**What is achievable:**

- **Precision, per label, to a stated interval.** Straightforwardly.
- **Recall for the common labels**, from the same random stratum.
- **A lower bound on recall for the rare ones**, and an honest interval
  that will be wide.

**What is not achievable at this cost:** a tight per-label recall figure
for Transportation Systems, Economic Development or Education without a
targeted search for likely positives — which is a biased sample and
cannot itself produce an unbiased recall estimate.

This is worth deciding before the queue is built, because it determines
how many rows a person has to read, and the difference between ±5% and
±10% is 2,880 articles.

### Unlabelled articles: none today, a stream once the pipeline runs

Measured now: **104,019 extracted articles are labelled and 30 are not.**
That is a reading taken with the crons suspended, not a property of the
corpus. Once discovery and extraction resume, every newly extracted
article is unlabelled until the analysis stage reaches it, so this
stratum fills continuously and the queue has to be built for it rather
than around it.

It is also a different kind of row from the rest. An article the model
has not seen cannot be used to score the model — there is nothing to
agree or disagree with. A human label on one is **training data
outright**, and it is the only stratum where the reviewer is not, in
effect, marking the model's homework.

The other seam is the model's own uncertainty: **30,696 labels carry a
confidence below 0.5**, out of 126,182. Those are the rows where it is
guessing, and where a human label is worth most per minute spent.

So three of the four strata are drawn from labelled articles, and the
queue must still never show the label.

---

## 3. The strata

Every decision records which stratum drew it and with what probability —
a rate measured over rows that were not equally likely to be drawn is
not a rate.

| stratum | drawn from | answers |
| --- | --- | --- |
| **Confident** | confidence ≥ 0.7, balanced across predicted labels | does a human agree where the model is sure |
| **Uncertain** | confidence < 0.5, balanced across predicted labels | does it agree where the model is guessing |
| **Random** | the whole corpus, uniformly | recall for the common labels, and the only unbiased estimate of anything |
| **Unlabelled** | articles the analysis stage has not reached | training data outright: nothing to agree with, so nothing to bias it |

### Why confident and uncertain are drawn in balance

Agreement is not one number. A model that is right when it is sure and
wrong when it is guessing is a usable model with a threshold; a model
that is equally wrong in both is a different problem, and the two are
indistinguishable from an overall accuracy figure.

Drawing the two bands in equal size makes the comparison direct and
gives the confidence score a meaning that can be checked rather than
assumed. It also concentrates the training value: the uncertain half is
where a human label teaches the model most, and the confident half is
what stops a retrain on that half alone from teaching it that everything
is hard.

The middle band (0.5–0.7) is deliberately left out of the agreement
comparison. It is neither claim, and including it blurs the one thing
these two strata exist to separate. Rows there are still reachable
through the random stratum.

The random stratum is not optional and cannot be replaced by the others.
A doubt-ranked sample finds errors and can never say how many there are.

### Every dataset, and reported by dataset

The queue draws from all datasets, and each decision records which one
the article came from, so agreement can be reported per dataset rather
than as one number over a corpus that is 98% Mizzou.

That reporting is feasible for some datasets and not others, and the
plan should not pretend otherwise. Labelled articles today:

| dataset | high ≥0.7 | mid | low <0.5 | total |
| --- | ---: | ---: | ---: | ---: |
| Mizzou Missouri State | 63,760 | 29,899 | 30,188 | 123,847 |
| Lehigh Valley | 591 | 270 | 247 | 1,108 |
| VT Community News | 600 | 255 | 218 | 1,073 |
| (no dataset) | 72 | 36 | 42 | 150 |
| WSU Washington State | 1 | 2 | 1 | **4** |

- **Mizzou** supports per-label precision at any interval worth having.
- **Lehigh Valley and VT** support a per-dataset agreement rate — about
  97 rows each for ±10% — but not a per-label one: 1,100 articles across
  ten labels leaves the rare classes with single figures.
- **WSU has four labelled articles.** No sampling design produces a
  number from that. It is a dataset that has not been analysed, not a
  dataset the model does badly on, and a report that renders it as 0%
  agreement would be a lie of arithmetic.

A per-dataset comparison is worth having precisely because the model was
trained on Missouri copy and the other datasets are the test of whether
it travels. That question cannot be answered yet for WSU, and can be
answered coarsely for the other two.

The unlabelled stratum is empty while the crons are suspended and fills
as soon as they are not. Its size is therefore a function of how far
behind the analysis stage is running, which makes it a useful thing to
show on the page: a queue that is suddenly full of unlabelled rows is
also a pipeline reporting that labelling has fallen behind extraction.

---

## 4. The role

`accounts/privileges.py` is a strict ladder: viewer → designer →
reviewer → editor → admin, each carrying everything below it, with a
test asserting each is a superset of its predecessor.

A classifier does not fit that ladder as it stands. It must be able to
write its own classifications and must **not** be able to correct
records, which is what `write` means one rung up.

**Built:** a `CLASSIFY` privilege, and `classifier` as a role
**outside** the ladder holding `classify` and nothing else — not even
`read`.

It cannot be a rung, and the reason is the requirement. A rung carries
everything beneath it, so a classifier placed on the ladder would either

- hold `read` over the whole corpus, and a coder who can read the corpus
  can choose what to label — a sample somebody chose is not a sample; or
- hand `classify` to every viewer and designer above it, and then there
  is no saying who is doing the classification.

Controlling both — which people classify, and which records they are
given — is the point, and neither survives inheritance.

On the ladder, `classify` is granted at **editor**, who runs the
programme: draws cohorts, reads agreement, decides what is settled. A
reviewer corrects records and does not label them; that is a different
job and a different person.

    viewer      read
    designer    read, design
    reviewer    read, design, write
    editor      read, design, write, create, classify
    admin       the same, unscoped

    classifier  classify            (beside the ladder, not on it)

### A cohort is a scope

A classifier is not granted a dataset. They are granted a **cohort** — a
provisioned set of article ids — and the existing `Grant` model already
expresses that: `scope` is a slug, and `Grant(user, DATADESK,
scope="cohort-3", role="classifier")` says what is meant.

`scopes_for(user, CLASSIFY)` then returns the cohorts that person works,
with no new plumbing.

Cohort membership is **permanent**: an article drawn into cohort 3 stays
in cohort 3, so a rate computed over it does not move when a later
cohort is drawn.

**And a cohort does not leak into any other surface.** Cohorts are
Datadesk objects with slugs of their own; they are not rows in the
crawler's `datasets` table, so `datasets_for()` — which filters
`Dataset.objects` by scope slug — cannot return one. A cohort is an
overlay on the corpus, not a move: the articles in it still belong to
Mizzou or VT for every other purpose, and appear there normally.

The original proposal follows, kept because the reasoning against it is
the reason the built shape is what it is:

    viewer      reads and exports
    classifier  ...and classifies what it reads
    designer    ...and authors visuals
    reviewer    ...and corrects the records
    editor      ...and brings new records in
    admin       ...and is not limited to one dataset

Reviewer and above inherit `CLASSIFY`, which is right: anyone trusted to
correct a record is trusted to say what it is about. A classifier gets
`read` and `classify` and nothing else.

**The consequence to accept:** the ladder means a classifier cannot be
given classify without read. That is fine. What it also means is that
this is the first rung added since the ladder was written, and the
superset test will need to cover it.

---

## 5. Which queues a classifier may see

Requirement: "access to review queues we flag as classification task
support".

`review/kernel.py` already declares queues as objects. A `Queue` gains
one field — whether it is classification support — and the section
listing in `accounts/sections.py` shows a classifier only those.

This keeps the answer in one place. A queue is or is not classification
work, it says so itself, and nothing has to maintain a second list of
which ones a classifier may open.

---

## 6. What the page shows, and what it must not

**No filters.** Not by dataset, not by date, not by publisher. A
reviewer is presented with entries and answers them; choosing what to
look at is how a sample stops being one. The shared queue header the
other three queues carry is deliberately not used here — its whole
purpose is to let a reviewer narrow, and narrowing is the thing this
queue must not permit.

The draw decides what is shown, and the draw is recorded (section 3).

Shown:

- the headline
- the original URL, **as text, not a link** — a reviewer who opens the
  publisher's page is reading the live article, including whatever it
  has become since capture, and is no longer labelling the row
- the body, in a scrollable window of about 250 words
- ten checkboxes, single-select
- a reject control, and its reasons
- a link to the instructions

Not shown, deliberately:

- **any current CIN label**, or its confidence, or the alternate. This is
  the whole point: a label produced after seeing the model's answer
  cannot score the model.
- status, wire flags, enrichment, dataset, publisher notes — none of it
  bears on what a story is about, and each one is a cue that makes the
  judgement less independent.
- any other link off the page.

### Rejecting

A reviewer who cannot classify a row says why, from a fixed list. The
list should start from what coders actually reached for. In the
historical cohorts the rejections were folded into the primary dropdown,
which is why "primary category" contains things that are not categories,
and they ran:

| reason | of 500 |
| --- | ---: |
| NOT LOCAL | 24 |
| TECHNICAL ERROR | 19 |
| NO APPROPRIATE CATEGORY | 9 |
| TOO SHORT | 6 |
| NONE PRESENT | 4 |
| **unusable** | **62 (12.4%)** |

**"Not local" was the most-used reason and is missing from the proposed
list** (paywall stub, not an article, garbage text, opinion, obituary,
other error). It belongs there. So, arguably, does "no appropriate
category", which is a statement about the scheme rather than the
article and is worth counting separately from a technical failure.

Separating these from the category dropdown, as the requirement does, is
the better design: a rejection is not a label and storing it as one is
what made the historical primary column unusable without filtering.

These are not CIN labels and must not be stored as if they were. A
rejection says the row should not be in the training set at all, and
several of them are also facts the pipeline already records — an
agreement between a human and `paywall_stub_rule` is worth counting.

---

## 7. Where the decisions go

**Its own table, not `ReviewDecision`.** Three reviewers must dispose of
a record before it counts, and `ReviewDecision` has a unique constraint
on (subject_type, subject_id, field, question) — one decision per
article per question. Reusing it would permit exactly one rater, which
is the opposite of the requirement.

    ClassificationDecision
        article_id        which article
        decided_by        which reviewer      } unique together
        label             one of the ten, or empty if rejected
        reject_reason     paywall stub, not an article, garbage text,
                          opinion, obituary, other error
        stratum           which draw put it in front of somebody
        inclusion_probability
        dataset_id        recorded at decision time, so a per-dataset
                          rate does not depend on a later join
        decided_at

Unique on (article_id, decided_by): a reviewer answers a given article
once, and three of them answer it independently.

Nothing is written to the crawler. `article_labels` is the model's
record of its own output; a human label written there would corrupt the
thing being measured and flow into BigQuery as though the model had
produced it.

### Three reviewers, and what "agreement" then means

A record is eligible for training data once **three reviewers have
disposed of it**. That buys two things at once: a defensible label, and
a measurement of the labellers rather than only of the model.

It also triples the cost. Every figure in section 2 is articles, not
dispositions — 970 articles at ±10% per label is **2,910 dispositions**,
and at ±5% it is 11,550. Whatever interval is chosen, multiply by three
before deciding whether it is affordable.

**Decision needed: what counts as settled.** Three plausible rules, and
they produce different training sets:

- **Unanimous (3/3).** The cleanest training data and the smallest set.
  On ten classes with genuinely ambiguous stories, expect to discard a
  large fraction.
- **Majority (2/3).** Keeps far more, and the discarded third is itself
  a useful signal: articles three people cannot agree on are articles
  the model should not be scored against either.
- **Majority, with disagreement kept separately.** The same set, plus a
  recorded pile of contested articles worth reading before the next
  round.

I would take the third: it costs nothing extra to record, and "which
articles do humans disagree about" is the most interesting question this
queue can answer that nobody has asked yet.

**A rejection is a disposition too.** If two reviewers say "garbage
text" and one picks a label, the record is not training data, and the
agreement between the two is worth counting against the pipeline's own
`paywall_stub_rule` and boilerplate detector.

## 7b. Cohorts, assignment and coverage

Three dispositions per record is a coverage requirement, and coverage
does not happen by itself. Serving whatever has fewer than three
answers, first come first served, gives no guarantee: a keen reviewer
answers a thousand records once each and nothing reaches three.

So records are **assigned**, in **cohorts**.

### A cohort

A named batch of records drawn together and worked as a unit —
identified by number and carrying the window it covers.

    ClassificationCohort
        number        1, 2, 3 — what people will call it
        opened_at     when it was drawn
        closed_at     when it stopped taking work, or null
        target_coders how many must dispose of each record (3)
        note          why this batch exists

Cohorts are what makes "how are we doing" answerable. Without them there
is one undifferentiated pile, and the questions that actually get asked
— has last month's batch finished, did agreement improve after the
instructions were rewritten, is this week slower than last — have
nowhere to attach.

### An assignment

    ClassificationAssignment
        cohort        which batch
        article_id    which record          } unique together
        assigned_to   which coder
        assigned_at
        completed_at  set when a decision lands, null while outstanding

Unique on (cohort, article_id, assigned_to). Drawing a cohort creates
exactly `target_coders` assignments per record, spread evenly across the
available coders, so every record gets the same number of evaluators and
every coder gets roughly the same amount of work.

A coder's queue is then simply their outstanding assignments, oldest
first. They are never shown a record they were not assigned, and never
the same record twice.

### The failure this must survive

**Pre-assignment plus an absent coder leaves records stuck at two
dispositions forever.** Somebody is ill, or leaves, or was granted the
role and never signed in, and every record assigned to them is short one
evaluator with nothing in the system trying to fix it. That is the
predictable way this design fails, and it fails quietly.

So an assignment **expires**. An outstanding assignment older than the
cohort's staleness window is reassigned to another coder who has not
already answered that record. The expiry is what turns a stalled batch
into a slow one.

Expired-and-reassigned is worth recording rather than overwriting: a
coder whose assignments are routinely reassigned is a fact worth
knowing, and so is a cohort that needed a lot of it.

### When more coding is needed

The admin alert (phase 6) fires on the conditions that mean the work
cannot finish as assigned:

- records in an open cohort short of `target_coders` with no outstanding
  assignment — nobody is going to answer them
- outstanding assignments past the staleness window
- fewer active coders than `target_coders`, which makes full coverage
  arithmetically impossible
- a cohort whose outstanding work exceeds what the current coders have
  historically completed in its window — it will not land on time, and
  saying so early is the point

That last one needs throughput history, so it is worth recording
`completed_at` from the start even though nothing reads it yet.

## 7d. What the historical cohorts already tell us

Five files exist — Groups A to E, 2,246 dispositions over 1,000 distinct
articles. The design was **two coders per article** (A∩C = 250, A∩D =
250, A∩B = 0), with Group E a third coder brought in where the two did
not agree.

**E covers 246 of the 1,000 — a 24.6% disagreement rate.** That is a
direct prediction of how much third-coder work the new queue will need,
and it was measured rather than guessed.

### Reliability, measured

| measure | value |
| --- | ---: |
| exact primary match | 64.6% |
| Cohen's kappa (nominal, primary only) | 0.583 |
| either label set overlaps at all | 78.6% |
| mean Jaccard of the label sets | 0.605 |

**The metric matters more than usual here.** Judged on exact primary
match, kappa is 0.583 — below 0.667, the conventional floor. Judged as
label *sets*, which is what the model is trained on, agreement is 78.6%.
The first number understates coders who were asked to record that a
story spans two categories and did so.

Whatever is reported later should be the set-based measure, named, for
the same reason: a bare "0.58 agreement" would condemn a coding protocol
for doing what it was designed to do.

### One category is broken, and it is not a metric artifact

Agreement when a category appears anywhere in either coder's set:

| category | in set |
| --- | ---: |
| Sports | 93.9% |
| Emergencies and Public Safety | 70.1% |
| Health | 53.5% |
| Political life | 47.4% |
| Education | 44.6% |
| Environment and Planning | 41.2% |
| Economic Development | 34.3% |
| Civic Life | 33.8% |
| Transportation Systems | 33.6% |
| **Civic information** | **14.4%** |

When one coder uses **Civic information**, the other does not use it at
all — not as primary, not as secondary — in 85% of cases. Under the
generous set reading. It is in four of the eight commonest confusions:

| confusion | count |
| --- | ---: |
| Emergencies and Public Safety ↔ Environment and Planning | 41 |
| Civic information ↔ Emergencies and Public Safety | 36 |
| Civic Life ↔ Civic information | 23 |
| Civic information ↔ Economic Development | 22 |

That is a definitional failure, not a training one, and no volume of
coding fixes it. A model cannot learn a distinction its labellers do not
share, so 90,000 dispositions collected against this scheme would buy
the same confusion at scale.

Group C is also the weaker partner in both its pairings (55.0% with A,
62.3% with B, against A–D's 73.0%), which is the per-coder signal the
admin dashboard is for — and an argument for measuring it from the first
cohort rather than the tenth.

### So the order of work changes

**Before the 30,000, spend twenty hours instead of three thousand.**

1. Revise the guidance for the confusable pairs — a bright line between
   Civic Life and Civic information, or a merge; and a rule for whether
   a flood is Emergencies or Environment.
2. Draw a small cohort, 200–300 articles, and code it under the revised
   guidance.
3. Re-measure. If set agreement moves and Civic information comes up off
   the floor, scale. If it does not, the scheme needs more than guidance
   and that is worth knowing before the programme is staffed.

The historical cohorts also cannot be reused as an evaluation set: their
article ids are SHA-256 hashes from a pre-crawler system, and **zero of
the 500 in Group A match our corpus on id or on URL**. The publishers
are the same, the articles are not. They remain usable as training data,
because the headline and body are in the files, but nothing in them can
score the current model.

## 7e. What the thesis records

De Jesus, *ydejesus_ThesisDocFinalcopy2.pdf*, documents how the ground
truth and the model were built. It answers questions this plan had left
open and raises two the plan has to be careful about.

### The ground truth is smaller than anyone would guess

**1,003 articles.** Per class, before balancing:

| class | training rows |
| --- | ---: |
| Emergencies and Public Safety | 272 |
| Sports | 191 |
| Environment and Planning | 105 |
| Civic Life | 101 |
| Civic information | 86 |
| Economic Development | 60 |
| Health | 58 |
| Political life | 46 |
| Transportation Systems | 45 |
| **Education** | **39** |

Education was learned from 39 examples. That reframes the whole
programme: **1,000 new well-labelled records would double the ground
truth; 30,000 would be thirty times it.** The earlier estimate in
section 7c — 500–1,000 gold examples a class before the curve flattens
— is not a target plucked from the literature, it is roughly ten times
what exists.

### The consensus rule already exists, and it is documented

The thesis's weighted voting scheme is a direct answer to the open
question in section 7:

- primary counts 1.0, secondary counts 0.5
- concatenate all coders' primaries and secondaries, sum the weights,
  highest total wins
- **confidence follows from which combination won:** primary–primary
  **0.9**, primary–secondary **0.7**, secondary–secondary **0.4**
- rows at 0.4 were **discarded** — 14 of 1,017

That produced "Clean" (0.9 only, 860 rows) and "Fuzzy" (0.9 + 0.7,
1,003), and the model was evaluated against both as a sensitivity check.

The new queue should reuse this rather than invent one. It is already
the scheme the production labels were built on, three coders make it
stronger than two, and it answers "what counts as settled" with
precedent instead of preference.

### Reliability was known to be weak

**Krippendorff's alpha 0.399**, recorded in the thesis. That is
consistent with what this plan measured independently from the cohort
files — 64.6% exact primary agreement, 78.6% set overlap — and it means
the weak agreement is not a new finding, it is a known property of the
ground truth the production model learned from.

### The codebook is why the Civic pair fails

From the original codebook (*Mizzou ML News Labeling CODEBOOK*, adapted
from Friedland et al. 2016), verbatim:

> **7. Civic Information:** Communities need information about major
> civic institutions, **nonprofit organizations, and associations**,
> including their services, accessibility, and opportunities for
> participation in: libraries and community-based information services.

> **8. Civic Life:** Residents need information about things to do and
> places to go in town that are related to not just active
> participation, but also consumption of cultural arts and services;
> recreational opportunities; **nonprofit groups and associations**;
> community-based social services and programs; and religious
> institutions and programs...

**Both categories explicitly name nonprofits and associations.** Both
cover community-based services and programs. A story about a nonprofit
satisfies each by the literal text of its definition, so two coders
reading carefully and honestly will file it in different places. That is
not a training failure or a coder-quality failure; it is a codebook that
puts one subject in two categories, and it is why Civic information sits
at 14.4% agreement while Sports reaches 93.9%.

### The fix the codebook already contains

The same document carries a test the thesis appendix dropped, and it is
the sharpest instrument here:

> When classifying articles into categories, consider what specific
> understanding or **action** — on balance — might be undertaken as a
> result of the content.

With worked examples that do separate the pair:

> Pick a restaurant for lunch. → **Civic Life**
> Volunteer at the library. → **Civic Info**

And the short-form definitions carry the distinction the expanded ones
lose:

> 7. Civic information, including the availability of civic institutions
> and **opportunities to associate with others**
> 8. Civic life, including information and advice related to **daily
> activities and planning**

So the intended line is **participation versus consumption**: joining,
volunteering, accessing an institution's services is Civic Information;
attending, watching, eating, doing is Civic Life.

**Proposed:** restate both definitions around the action test, and
assign nonprofits and associations to one of them by that test rather
than naming them in both. A nonprofit's volunteer drive is Civic
Information; its fundraising gala is Civic Life.

This costs an afternoon of editing and a 200–300 article re-test. It is
the cheapest available improvement to the model, and nothing else in
this plan comes close per hour spent — a distinction the coders cannot
make is one the classifier will never learn, however many rows it is
given.

### Two things the thesis's headline numbers do not mean

Both matter because they will otherwise be quoted as the production
model's performance.

**The thesis model is not the production model.** The thesis selects
`MultinomialNB` — "NBE-1" — and reports Cohen's kappa 0.8855 and
accuracy 0.876 for it. The crawler runs a fine-tuned
`bert-base-uncased` (`productionmodel.pt`, 438 MB,
`AutoModelForSequenceClassification`). Whatever relationship those two
have, the thesis's figures describe a Naive Bayes classifier and cannot
be cited for the BERT in production.

**SMOTE was applied before the split.** Table 4.2 balances 1,003 rows to
1,102 by oversampling the minority classes, and the test set of 331 has
Transportation Systems at 34 rows where the real distribution would give
about 6. So 0.876 is accuracy on a rebalanced, partly synthetic sample,
not on the corpus as it occurs. It is not a wrong number, it is a number
about a different distribution.

**Which is the strongest argument for this queue.** There is currently
no measurement of the production model against real, unbalanced,
independently-labelled data. That is precisely what the evaluation
strata in section 3 would produce, and it does not exist yet.

### The instructions exist

Appendix B carries the coder instructions, and most of them are JotForm
and Google Sheets logistics this queue removes — entering article ids,
tracking position in a spreadsheet, duplicate submissions. What survives
is the substance: read the headline and text, assign a required primary,
add a secondary **only with significant confidence**, and the four
reasons a story cannot be categorised (NOT LOCAL, NO APPROPRIATE
CATEGORY, TOO SHORT, TECHNICAL ERROR).

Those four are the empirical basis for the reject list in section 6, and
"only with significant confidence" is the instruction that makes the
secondary label mean something — without it the 0.5 weight is noise.

The instructions to carry over, then, are the definitions, the action
test, the worked examples, the secondary-confidence rule and the four
reject reasons. Everything about article ids, assignment sheets, tab
management and duplicate submissions is logistics this queue removes,
and reproducing it would be reproducing the workflow rather than the
method.

## 7c. The size of the thing

**1,000 records to start; 30,000 as the goal.** At three coders each
that is **3,000 dispositions**, then **90,000**.

| | records | dispositions | coder-hours @2–3 min |
| --- | ---: | ---: | ---: |
| first cohorts | 1,000 | 3,000 | 100–150 |
| the goal | 30,000 | 90,000 | 3,000–4,500 |

Elapsed time is a function of how many coders and how many hours each,
and the range is wide enough that it should be planned rather than
discovered:

| coders | hours each per week | elapsed |
| ---: | ---: | ---: |
| 10 | 4 | ~94 weeks |
| 25 | 10 | ~15 weeks |
| 50 | 10 | ~8 weeks |
| 100 | 4 | ~9 weeks |

### What 30,000 changes about the draw

At 1,000 records the corpus distribution is useless — Transportation
Systems would get 19 — so the draw is stratified to about 100 a class.

At 30,000 the natural distribution is nearly sufficient on its own:

| label | share | records at natural rate |
| --- | ---: | ---: |
| Civic Life | 25.0% | 7,488 |
| Sports | 17.9% | 5,376 |
| Civic information | 16.8% | 5,035 |
| Emergencies and Public Safety | 13.9% | 4,176 |
| Political life | 9.3% | 2,804 |
| Environment and Planning | 4.8% | 1,448 |
| Health | 4.2% | 1,257 |
| Education | 3.6% | 1,074 |
| Economic Development | 2.6% | 768 |
| Transportation Systems | 1.9% | 573 |

Every class clears 500, which is the bottom of the useful range for
fine-tuning.

**But the original labelling deliberately oversampled the low-incidence
categories to add signal, and that was the right call.** It shows in the
cohorts: Emergencies is 25% of Group A's primaries against 13.9% of the
corpus, Transportation 4.2% against 1.9%. The thesis then applied
SMOTE-Tomek on top, taking Education from 39 rows to 100.

So there is no single correct shape, because the draw serves two
purposes that want opposite things:

- **Training** wants the rare classes over-represented. A classifier
  given 39 examples of Education learns Education badly, and no
  reweighting at scoring time fixes what was never in the data.
- **Evaluation** wants the real distribution, or it cannot say how the
  model performs on the corpus as it occurs. This is exactly the trap in
  the thesis's 87.6%: measured on a SMOTE-balanced test set, it
  describes a distribution that does not exist.

**Both are available from one draw, provided the inclusion probability
is recorded per stratum** — which section 3 already requires. Oversample
the rare classes for the training value, record how much each row was
over-represented, and weight back to corpus rates when reporting
precision and recall. Without that number the same rows can serve only
one of the two purposes.

So the sampling code takes the shape as a parameter, and the recorded
probability is what makes an oversampled draw honest rather than
misleading.

### What a large group of coders changes

Two things the plan does not currently have, and both become necessary
somewhere between ten coders and a hundred.

**Embedded checks.** In a group of that size some proportion of
dispositions will be produced without reading the article. The
established remedy is a small number of records with a known answer,
seeded invisibly into every coder's assignments at a low rate, and a
per-coder accuracy figure computed against them.

This is not the same measurement as inter-coder reliability. Alpha says
whether coders agree with each other; three coders who all click the
first checkbox agree perfectly. The check records are the only thing
that catches that, and they cost about 1% of the work.

**A path for a coder whose work is not usable.** Per-coder agreement and
check accuracy are already computable from
`ClassificationDecision`. What is missing is what follows: a coder can
be deactivated, their outstanding assignments released for reassignment,
and — the question worth settling early — a decision about whether their
completed dispositions are withdrawn from the training set or kept.

Withdrawing is the safer answer and it is not free: a record loses a
disposition and drops below three, so it returns to the queue. At 90,000
dispositions a single bad coder can send thousands of records back
around, which is a reason to catch one early rather than a reason not
to withdraw.

## 7a. Inter-coder reliability, and who sees it

Three reviewers per record exist to produce a defensible label. The same
rows also measure the reviewers, and that measurement is wanted three
ways: **overall, within each dataset, and per CIN category.**

No schema is needed for it. `ClassificationDecision` already carries
article, reviewer, label, dataset and stratum; reliability is a query
over those, not a stored figure.

**The metric has to suit the shape of the data.** Fleiss' kappa assumes
a fixed number of raters per item, and this queue will not have one —
articles sit at one or two dispositions while they wait for a third, and
a rejection is a disposition that is not a label. Krippendorff's alpha
handles a varying number of coders and missing values, which is the
actual shape here. Whatever is chosen should be named in the report
beside the number, because a bare "0.71 agreement" is not interpretable
without it.

**Per category is where it gets useful.** An overall alpha averages away
the thing worth knowing. If reviewers agree readily on Sports and
Obituaries but scatter across Civic Life and Civic information, that is
a finding about the *label scheme* rather than about the reviewers or
the model, and it is the single most likely reason a retrained model
would still perform badly. Two categories humans cannot separate will
not be separated by a classifier trained on their labels.

**Per dataset**, subject to section 3's arithmetic: Mizzou will support
a reliability figure comfortably; Lehigh Valley and VT coarsely; WSU not
at all.

### Not shown to classifiers

The dashboard is admin-only, and a classifier must not see their own
agreement rate or anyone else's.

This is not about secrecy. A reviewer who can see their agreement score
has been given a target that is not accuracy, and the cheapest way to
raise it is to guess what other reviewers would say rather than to read
the article. That converts three independent judgements into one
judgement copied three times, which is exactly the thing the third
reviewer was bought to prevent.

The same argument bars showing a classifier how many others have already
answered a record, or what they said.

### Deferred

The admin views themselves — layout, cuts, what is charted — are not
designed here. They should be settled once the queue is wired and there
are real dispositions to look at, because the useful cuts will be
obvious then and are guesses now.

## 8. Schema

**Datadesk (its own database):**

- `ClassificationSample` — which articles were drawn, into which
  stratum, with what inclusion probability and when. Required for two
  reasons: an estimate needs to know how a row was selected, and the
  queue has to be stable, so a reviewer coming back tomorrow sees the
  same set rather than a fresh random draw.
- `ClassificationDecision` — one row per reviewer per article, unique
  together. Section 7.
- `ReviewDecision` — untouched. This queue does not use it.

- `ClassificationCohort` and `ClassificationAssignment` — section 7b.
  A coder's queue is their outstanding assignments, oldest first; they
  are never shown an unassigned record, and never the same one twice.

**Crawler:** nothing. This queue reads `articles` and `article_labels`
and writes neither.

**Accounts:** `classifier` joins `ROLE_CHOICES`. No migration: `role` is
a CharField with choices, and existing rows are untouched.

---

## 9. Risks

**Three dispositions per article, and the arithmetic that follows.**
Every sample size in section 2 counts articles. Three reviewers each
means the reviewer-minutes are three times that, and the difference
between ±10% and ±5% becomes 2,910 dispositions against 11,550. The
interval is the single biggest lever on whether this queue is finished
in a month or a year.

**The sample is read but not used.** The largest risk is not technical.
Three thousand human labels are worth nothing until a retraining run
consumes them, and nothing in this plan retrains anything. The export
shape should be agreed with whoever runs training before the first
reviewer starts, or the queue produces a table nobody can ingest.

**Truncation makes disagreement meaningless.** If the window shows more
than the model reads, some disagreement measures the 512-token limit
rather than the model. The window is 250 words for this reason.

**The rare labels stay unmeasured.** Section 2. Worth stating in
whatever reports the result, or "95% confidence" will be read as
covering all ten.

**Order effects.** Ten checkboxes in a fixed order, thousands of times,
and the first plausible one gets clicked. Worth considering rotating the
order per reviewer, and worth measuring before assuming.

**A classifier is a new kind of account.** The first rung added to the
ladder, and the first role that is not a subset of "staff who can fix
things". Whoever holds it can read every article in scope.

---

## 10. What gets built, in order

**Phase 1 — the role.** `CLASSIFY`, the `classifier` rung, the superset
test, and the section listing that shows a classifier only classification
queues. Nothing user-visible yet.

**Phase 2 — the sample and the cohort.** `ClassificationSample`, the
four strata, and a management command that draws a cohort and assigns
it. Reviewable as a table before any UI exists, which is when the
sampling and the balance can still be argued with.

**Phase 3 — the queue.** The page, the single-select, the reject
reasons, the receipt. No filters, and not the shared header.

**Phase 4 — the instructions.** A modal, text supplied separately.

**Phase 5 — the export.** Whatever shape training consumes. Not
designed here, because it depends on an answer this document does not
have.

**Phase 6 — the admin dashboard.** Inter-coder reliability overall, by
dataset and by category; throughput; the contested pile. Admin only.
Designed after the queue is wired, when there are real dispositions to
look at and the useful cuts are obvious rather than guessed.
