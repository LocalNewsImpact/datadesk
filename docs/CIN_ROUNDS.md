# Rounds: what a batch of coding is for

A cohort is a batch of work — so many articles, so many coders each. It
carries no reason. Two very different pieces of work are indistinguishable
in the interface today:

- test whether a revised Civic Information / Civic Life wording improves
  agreement, and
- collect enough labelled data to retrain the classifier.

The only place that intent can be written is `ClassificationCohort.note`,
a free-text field nothing reads. So the size of a round is guessed, its
finish line is a matter of opinion, and nothing tells anybody beforehand
what it will cost.

## The proposal

A **Round** owns cohorts. It carries the question, the rule that draws
the articles, and the rule that says when it is finished. Cohorts stay
what they are: the batches the work is handed out in.

Two kinds, because their stopping rules are genuinely different.

### A comparison round

*Did the revised wording move agreement?*

The author states the effect worth detecting and the confidence wanted.
**The size is derived, never typed.**

| detect a move | articles needed |
| --- | ---: |
| 12% → 30% | 80 |
| 12% → 25% | 139 |
| 12% → 20% | 329 |
| 65% → 75% | 305 |
| 65% → 70% | 1,184 |
| 65% → 68% | 3,034 |

Two-proportion comparison at 95% confidence and 80% power, over
double-coded articles.

The table is the argument for deriving rather than typing. Testing the
Civic Information revision is 139 articles — a weekend. Chasing three
points on the overall rate is 3,034, which is a different project
requiring a different decision. A number somebody types cannot tell them
that; a number the interface derives says it before any coder is asked
for anything.

Tightening costs what it costs, and should be visible at the moment of
choosing:

| 12% → 25% at | articles |
| --- | ---: |
| 95% confidence, 80% power | 139 |
| 95% confidence, 90% power | 186 |
| 99% confidence, 80% power | 208 |
| 99% confidence, 90% power | 264 |

**Done when** the count is reached *and* the measured interval excludes
no-change. Reaching the count while the interval still spans zero is not
a null result, it is an unfinished round, and the two must not read the
same.

### A collection round

*Enough labelled data to retrain.*

The author states articles per category. The binding constraint is the
rarest category, and naming it is most of the value: in the existing
ground truth Education has 39 articles against Emergencies' 280, so
Education sets the cost of the whole round. A target of 500 per category
is a small ask of Emergencies and a seven-fold increase for Education.

**Done when** every category clears its floor — not when the total is
reached. A total hides exactly the shortfall that matters, because the
categories that fill fastest are the ones already best represented.

## The draw belongs to the round

`ClassificationSample.inclusion_probability` already records the chance a
row had of being drawn, because a rate measured over rows that were not
equally likely to be drawn is not a rate. What is missing is the rule
that produced it.

A comparison round draws from where the labels in question collide; a
collection round draws balanced across categories, oversampling the rare
ones. Those are different populations, and an analysis that cannot tell
which it is looking at cannot weight it. The rule is a property of the
round, not of the batch, and storing it there is what makes a result
reproducible.

## What the screens become

One page currently does five jobs — Coders, Open a cohort, Cohorts,
Invited, Who has done what — with five forms nested inside table cells.

- **CIN → Rounds.** Define a round; see progress against its target.
  Cohorts become an implementation detail underneath the round that
  needed them.
- **CIN → Coders.** People and grants. Nothing else.

Progress reads as a target rather than a count: *139 needed · 84
double-coded · 61%*. For a collection round, per-category bars with the
binding category named, because that is the number that decides when the
round ends.

## What not to build

**Do not let the target be typed.** A field accepting "500 articles"
returns the interface to a note field with better formatting: the number
carries no reasoning, cannot be checked, and cannot warn anybody that
what they are asking for costs twenty times what they expect. State the
effect and the confidence; derive the count. Where the derived count is
unaffordable, that is the finding.

**Do not report a total for a collection round.** See above: the total is
reached long before the categories that matter are.

## Schema sketch

```
Round
  question        what is being asked, in a sentence
  kind            comparison | collection
  draw            the rule that selects eligible articles
  # comparison
  baseline        the rate being moved, measured
  detect          the rate worth detecting
  confidence      0.95 / 0.99
  power           0.80 / 0.90
  # collection
  per_category    the floor every category must clear
  opened_at, closed_at
```

`ClassificationCohort` gains `round`, and keeps everything else. Nothing
about how work is handed out or how agreement is measured changes.

## Arithmetic

Two-proportion sample size, per group:

```
n = ( z(a/2)·sqrt(2·p̄·q̄) + z(b)·sqrt(p1·q1 + p2·q2) )² / (p1 − p2)²
```

with `p̄` the mean of the two rates. The tables above come from it. It is
worth putting in code rather than a spreadsheet precisely because the
answer is often surprising, and a surprise nobody sees is a round that
runs out of coders before it runs out of articles.
