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

**One label per article, not several.** The requirement says "a checkbox
menu ... and evaluate by ONE checkbox", and that is also the only shape
this model can learn from. A multi-select would produce training rows
its objective cannot consume, and a reviewer allowed to tick three boxes
is being asked a different question from the one being scored.

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

**Proposed:** a `CLASSIFY` privilege and a `classifier` rung between
viewer and designer:

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

A reviewer who cannot classify a row says why, from a fixed list:
paywall stub, not an article, garbage text, opinion, obituary, other
error.

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
fine-tuning. So at scale the draw should move **toward** the natural
distribution and away from equal strata — a model trained on a flattened
distribution learns a prior the world does not have — while still
boosting the rarest one or two classes.

That is a different draw from the first cohort's, and the sampling code
has to take the shape as a parameter rather than hard-coding either.

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
