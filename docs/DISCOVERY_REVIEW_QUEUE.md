# The discovery review queue

What is reviewed before extraction, why it cannot be reviewed today, and
what has to be built. Measured against production on 2026-09-06.

The subject is the pre-extraction judgement: **is this URL a story?**
That call is made on every discovered link, it decides whether the page
is ever fetched, and no part of it is recorded.

The policy it is measured against (§2) is that the filter should be
**generous**: reject only where a URL cannot be an article whatever it
contains, and let everything else through to extraction, where there is
text to judge on. Against that policy the filter is currently wrong in
the expensive direction — two-thirds of its rejections are judgement
calls, and four of its rules reject on subject matter the content stage
already decides.

This describes what a decision has to record, the queue that samples it,
what the labels can improve, and the order it is built in.

---

## 1. The judgement being reviewed

Discovery collects URLs; a verification stage then decides which of them
are stories. The stage runs in production — `verify-urls` in the
crawler's `k8s/argo/base-pipeline-workflow.yaml`, executed after
`wait-for-candidates` — and decides by four mechanisms in strict order
(`src/services/url_verification.py:752-775`):

| Order | Mechanism | Verdict | What it knows |
| --- | --- | --- | --- |
| 1 | wire filter | `wire` | the URL belongs to a wire service |
| 2 | pattern rule | `pattern_status`, usually `not_article` | which of 46 rules in `verification_patterns` matched |
| 3 | storysniffer | `article` | a scikit-learn model's answer |
| 4 | nothing matched | `not_article` | that no rule and no model said yes |

The stage writes exactly one thing: `candidate_links.status`. A rejected
URL is never fetched again, so a wrong rejection removes an article from
the corpus permanently and silently.

### The two errors, named

- **Type I — a false positive.** A section index, video page or tag
  listing accepted as a story. It costs a fetch, an extraction, and a
  junk row that later stages have to reject. Visible downstream: it
  becomes an article with no prose.
- **Type II — a false negative.** A real story rejected. It costs the
  article. **Nothing downstream can see it**, because there is no row —
  which is why it has to be caught here or not at all.

The queue exists to hold both, and the second is the one that has no
other route.

---

## 2. The filter's policy: generous

**The URL stage filters only on structural certainty. Everything else
waits for extraction, where there is text to judge.**

The two errors do not cost the same (§1), and the asymmetry is not close:

- A **type I** error — a non-story accepted — costs one fetch and one
  extraction, and is then caught by content analysis, which is better at
  it because it can read the page. The cost is compute.
- A **type II** error — a story rejected — costs the article. There is no
  row, no status, no telemetry, and no later stage that can notice. The
  cost is the corpus.

So the filter's job is not accuracy. It is **recall on stories, at a
rejection threshold set high enough that a rejection is never a
judgement call**.

### Two questions, and only one of them is storysniffer's

**Is this a story?** and **do we want this story?** are different
questions, and the URL stage has been answering the second by lying about
the first. A national story is a story. So is an obituary, a weather
write-up, a column. Recording any of them as `not_article` is false about
the URL, and it is terminal — the row cannot be revisited, because the
statement it carries is not the one that was meant.

| Axis | Question | Who answers it | What a wrong answer costs |
| --- | --- | --- | --- |
| **Article-ness** | is this a story at all? | storysniffer, and the structural rules | a story lost with no trace |
| **Topic** | is it wire, an obituary, weather, opinion? | the content stage, after extraction — and the URL, *where it is clear* | a fetch, or a misfiled article |

The pipeline already has the topic vocabulary and applies it after
extraction with the text in hand: `wire` 78,225, `obituary` 3,181,
`opinion` 3,695, `weather` 2,035. Nothing at the URL stage needs to
duplicate that. What the URL stage may do is carry a **hint** — recorded
as its own field, never as a rejection — and a hint may skip a fetch only
where it has been measured to be nearly always right.

### What that permits and forbids

| Reject at the URL stage | Do not |
| --- | --- |
| The URL cannot be an article whatever it contains: `/api/`, `/feed/`, `/rss`, `.xml`, `.json`, `sitemap`, `wp-admin`, `wp-content`, `wp-includes`, image placeholders | Reject anything on **subject matter** |
| Endpoints that are not content: `login`, `cart`, `checkout`, `search`, `print`, `subscribe`, `newsletter`, `advertise`, `classifieds`, `coupons`, `deals`, `marketplace` | Record a topic decision as `not_article` |
| Index pages, **once each rule's precision is measured**: `category`, `tag`, `topic`, `section`, `archive`, `staff`, `about`, `contact` | Reject where the answer depends on reading the page |

### When a topic *is* clear from the URL, measured

For URLs that reached extraction, how often the URL's topic hint matched
what the content stage then decided:

| URL hint | fetched | content stage said the same | filed as ordinary `labeled` |
| --- | ---: | ---: | ---: |
| `/nation-world/`, `/us-world-news/` | 3,853 | 99.7% `wire` | 0.2% |
| `/obituaries/` | 2,022 | 90.3% `obituary` | 133 |
| `/opinion/` | 643 | 88.5% `opinion` | 57 |
| `/weather/` | 747 | 37.3% `weather` | 384 |

**The residual is not the URL being wrong.** Read the rows: the 133
obituary-hinted articles the content stage left as `labeled` are
obituaries; the 384 weather-hinted ones are forecasts, radar pages and
storm tracking; the 57 opinion-hinted ones are columns, letters and
"It's Your Call". The hint was right and the content classifier missed
the category.

So this column measures the **content stage's recall**, and agreement is
only a *lower bound* on the URL hint's precision. `/weather/` looks like
the worst hint of the four and is not: weather is simply what the content
stage misses most.

Read as samples rather than as a census — a dozen rows per hint, and
nearly all of them the topic their URL claimed. The exceptions were
adjacent rather than wrong: a rising-heating-bills consumer story and a
Caribbean tropical-storm report, both filed by their publisher under
weather. Establishing the real precision is what human labels are for
(§10), and that is a reason to collect them rather than a reason to act
on the hint now.

Two consequences, and they point in opposite directions from the same
fact:

- **A topic hint is good enough to be recorded, and good enough to be a
  prior for the content stage.** It is not good enough, on its own, to
  skip a fetch: that decision needs the precision measured against human
  labels rather than against a classifier that is demonstrably missing
  half of one category. The queue produces those labels.
- **The content classifier is missing topic content a URL rule finds
  trivially** — 51% of weather, 8.9% of opinion, 6.6% of obituaries. That
  is roughly 570 rows sitting in the corpus as ordinary articles: forecast
  pages and death notices among the local journalism. It is an extraction
  defect, not a discovery one, and it is listed here because the same
  measurement found it.

Neither changes the filter policy. A forecast, an obituary and a national
story are all stories, they all get fetched, and topic is decided where
there is text to decide it on.

### The four subject-matter rules have never worked

`entertainment`, `obituary`, `us_world_news` and `weather` are exactly
the four rules whose regexes do not compile (§7). They have never fired —
`_load_dynamic_patterns` logs the compile failure and skips the rule, so
they are dormant rather than dangerous.

That is why the table above can be measured at all — the URLs flowed
through, so the sample is unbiased — and it means removing them changes
nothing in production.

### Two topic filters do fire, and they are the pattern to follow

Not through the rules table. `verify_url` carries a hardcoded `/opinion/`
check as its first stage (`url_verification.py:528`), and a wire-service
check a few stages later. Both skip the fetch, and both are already doing
what this policy asks: they record the **topic** — `opinion`, `wire` —
never `not_article`.

| Candidate status | links | of which reached extraction |
| --- | ---: | ---: |
| `wire` | 78,225 | 48% |
| `opinion` | 3,695 | 49% |
| `obituary` | 3,181 | 92% |
| `weather` | 2,035 | 97% |

The first two are half-fetched because the URL stage skipped the rest:
roughly 40,650 wire fetches and 1,870 opinion fetches never happened. The
last two are the other way round — nothing filters them at the URL stage,
so their candidate status is extraction's verdict written back.

So topic-based skipping at the URL stage is not a proposal; it is running,
on the two topics with the clearest signals, recorded honestly. What is
missing is the record of each decision (§3), without which the precision
of the `/opinion/` filter cannot be checked at all: the 1,870 URLs it
skipped were never fetched, so nothing downstream knows what they were.

### Two changes to how a verdict is reached

1. **The default becomes accept.** Today the last branch of
   `src/services/url_verification.py` rejects when no rule fired and the
   model said no. Under this policy that is exactly the uncertain case
   that should go to extraction. Nothing matching is not evidence of
   anything.
2. **storysniffer stops rejecting and starts ranking.** Its verdict is
   not confident enough to exclude on — §5 shows why — but it is a
   perfectly good ordering. `candidate_links.priority` already exists: a
   negative verdict lowers priority, it does not exclude. Fetch order is
   reversible; a rejection is not.

### What it costs, measured

| | |
| --- | ---: |
| candidate links | 262,137 |
| rejected at the URL stage today | 26,024 (9.9%) |
| of those, matched by a structurally unambiguous rule | 34.7% |
| **rejected on a judgement call, and would now pass** | **65.3% ≈ 17,000** |
| extra fetches, against the whole corpus | **+6.5%** |

`sampled_out` is 16,453 links — a deliberate budget decision of the same
size. Volume belongs there: sampling cuts at random, so it costs a known
fraction of everything, while a filter that cuts on judgement costs
whichever stories its rules happen to be wrong about.

### What this does to the training objective

Not accuracy, and not F1. The model is chosen and its threshold set so
that **rejection happens only above a calibrated P(not a story) that is
very high** — the operating point is picked from the precision-recall
curve on the held-out random sample (§10), with the false-negative rate
as the constraint rather than something to trade away.

Which also means the queue's own measure of success changes: the number
to watch is not how many bad URLs were caught, it is **how few stories
were rejected**, and only the random-sample stratum can say.

---

## 3. What production records today

| Table | Rows | What it holds |
| --- | ---: | --- |
| `url_verifications` | **0** | one row per URL decision — `storysniffer_result`, `verification_confidence`, `article_headline`, `article_excerpt`, and `human_label` / `human_notes` / `reviewed_by` / `reviewed_at` |
| `verification_telemetry` | 20,511 | one row per **batch**: counts, timings, and `sources_processed` as a list of publisher names |
| `verification_jobs` | 0 | never written |
| `verification_patterns` | 46 | the rules, with `total_matches` / `article_rate` / `confidence_score` columns that are all null |

`url_verifications` is empty for a reason worth writing down: the
implementation that writes it — `src/services/url_verification_service.py`,
which has `save_verification_result` — is not the one that runs. The CLI
and the Argo step import `src/services/url_verification.py`, which
updates the candidate's status and records a batch summary. The table has
been correct and unused since it was created.

Telemetry cannot stand in for it. A batch row says *175 URLs were
rejected across these publishers*; it carries no URL, no verdict, no
score, and its publisher list is names rather than a join key.

**Volumes** (`candidate_links.status`): `extracted` 84,998 · `wire`
78,225 · `paused` 40,587 · `not_article` 26,024 · `sampled_out` 16,453 ·
`article` 4,709 · `opinion` 3,695 · `obituary` 3,181 · `weather` 2,035 ·
`404` 1,551 · `paywall` 431 · `discovered` 159 · `skipped` 45 ·
`proxy_blocked` 37 · `filtered` 7. The rejected pile is 26,024 URLs
across 828 publishers.

`sampled_out` is not a classification: it is discovery's own budget
decision. It stays out of this queue.

---

## 4. URL shape is not a confidence metric

Measured over every verified link — 219,449 that reached a fetch against
26,024 rejected:

| URL signal | Accepted | Rejected |
| --- | ---: | ---: |
| dated path `/YYYY/MM/` | 28.7% | **35.9%** |
| long hyphenated slug | 81.1% | 45.8% |
| ends `.html` | 19.3% | 25.2% |

Only the slug test discriminates, and it is far too coarse to rank on:
it would put 11,900 rejected URLs in the queue on its own.

The dated-path row is the finding. A `/YYYY/MM/` path is among the
strongest indicators that a URL is a story, and it is **more** common
among the URLs the verifier discarded than among the ones it kept:
**9,350 rejected URLs carry one**.

That is not the model's doing. Of a random 1,065 rejected URLs with a
full `/YYYY/MM/` path, storysniffer would have accepted **926 (87%)**,
and 976 (92%) matched an active rule. The dated pile is being discarded
by the rules, over the model's objection — §5 and §7.

It is not, however, a substitute for a confidence metric. It ranks URLs
by one hand-picked feature, which finds the errors that feature happens
to correlate with and hides the rest.

---

## 5. The confidence metric

`storysniffer.guess()` runs a scikit-learn classifier and returns a bare
boolean (`storysniffer/__init__.py:120-161`): `model.predict(data)[0] == 1`,
with whitelist and blacklist overrides applied afterwards.
`predict_proba` is available on the same model.

**It is not usable as a confidence metric.** The classifier is a
`GaussianNB` over a 180-feature character n-gram count vector, and naive
Bayes saturates. Scored over 6,000 production URLs — 3,000 rejected and
3,000 accepted, drawn at random:

| P(story) | URLs |
| --- | ---: |
| exactly 0.0 or 1.0 | 5,959 |
| anywhere in between | **41** (0.7%) |

There is no uncertain band in that distribution to sample from. A
threshold on this number is a second copy of the verdict, not a
confidence in it.

Two more properties of the same model, both worth knowing before anyone
proposes tuning it:

- **Its vocabulary is year-bound, and it does not matter.** Six of the
  180 features are the literals `/2022`, `/2022/`, `/2022/0`, `2022`,
  `2022/`, `2022/0` — an artifact of fitting character n-grams on a
  2022-era corpus under a 180-feature cap, not a rule anybody wrote.
  Tested by rewriting the year on 2,051 dated production URLs, the
  verdict changes in **24 cases (1.2%)**, all of them archive indexes
  like `/2026/10` with no slug. The shorter n-grams `/2`, `/20`, `/202`
  match any year and the slug features dominate. Worth knowing before
  anyone proposes retraining for it; not worth acting on.
- **The accept path is the model's; the reject path mostly is not.** Run
  over the same 6,000 URLs, `guess()` agrees with 99.6% of the
  acceptances and with only 33.6% of the rejections: **66.4% of rejected
  URLs are ones storysniffer itself would have accepted**, and a rule
  killed them first. Scaled to the corpus that is roughly 17,300 of the
  26,024 rejections.

So the confidence metric has to be built rather than read (§10), and the
first thing to review is not the model's judgement but the rules that
overrule it.

What a decision has to record, per URL:

| Field | Source | Why the queue needs it |
| --- | --- | --- |
| `storysniffer_result` | the verdict | which class the row is in |
| `verification_confidence` | `predict_proba` positive class | the band it is sampled from |
| decided-by (`wire` / `pattern:<id>` / `sniffer` / `default`) | the branch that fired | a wrong rule is fixed once, for every URL it will ever match |
| `article_headline`, `article_excerpt` | the fetch, where one happened | a reviewer judging a bare URL is guessing |

The first two are columns on `url_verifications` already. The third fits
its `meta` JSON. The fourth is already a column and is only sometimes
knowable, which is fine: a queue row without it is a URL to judge on its
own.

---

## 6. What gets built, in order

### Phase 1 — the crawler records the decision

One `url_verifications` row per verified URL, written by the
implementation that actually runs. No verdict changes; nothing about the
pipeline's behaviour changes. This is the whole prerequisite, and it is
small because the table and its columns exist.

### Phase 1b — backfill the existing decisions

storysniffer is a local model: no network, no proxy, no egress. The
245,473 already-verified links can be scored in one offline batch and
their rows written, which:

- gives the queue the whole backlog to review immediately rather than
  waiting for crawling to resume — discovery has been idle since
  **2026-08-12**, the date of the last `discovered_at` and the last
  verification batch;
- measures where a re-run disagrees with the recorded verdict, which is
  a first estimate of the error rate before any human looks at a row.

A re-run will not always reproduce the original call: the pattern filter
ran first, and rules and model versions change. The disagreement is the
point, not a defect.

### Phase 2 — the queue

A new nav group, **Discovery**, between Sources and Extraction
(`accounts/sections.py`), requiring `write` like the other two queues.

---

## 7. Triggers: what is in the queue

Four row sets, chosen so that the queue finds errors *and* can measure
them. The first three are ranked; the fourth is not, and that is what
makes it useful.

| Set | Rows | Answers |
| --- | --- | --- |
| **Overruled** | rejected, where storysniffer said story and something rejected it anyway | type II — the largest set, and the one no other surface can see |
| **By rule** | the overruled set grouped by the rule that fired | a bad rule, fixed once for every URL it will ever match |
| **Wrong acceptances** | accepted, where storysniffer said not-a-story | type I — 0.4% of acceptances, small and worth reading |
| **Sample** | a random sample of each class | the true error rate |

The first three replace the confidence bands an earlier draft of this
document assumed. They cannot be built on `predict_proba` (§5): the model
is saturated, so *nearly said yes* has no meaning in it. **Disagreement
between the mechanisms is the uncertainty signal that actually exists**,
it is computable today, and it is where the errors are — 66.4% of
rejections are cases where the model and a rule disagreed and the rule
won silently.

Once §10's calibrated model exists, a genuine low-confidence band is
added beside these, not instead of them: a calibrated score ranks within
the overruled set, which is 17,300 rows and needs ranking.

The fourth set is not optional. A doubt-ranked sample is biased by
construction: it is drawn from the rows a signal already suspects, so it
can only ever find errors and can never say how many there are. Two
hundred randomly drawn labels per class give an error rate with a
confidence interval; two hundred doubt-ranked ones give a list of
mistakes. Both are wanted, for different questions.

**Where the overruled rows are, measured.** Of 1,992 disagreements in the
6,000-URL sample, 1,579 match an active rule and 413 match none at all —
so those were rejected by the wire filter or by nothing matching:

| Rule | URLs |
| --- | ---: |
| `image_placeholder` | 488 |
| `video` | 474 |
| `feed` | 468 |
| `shopping` | 53 |
| `gallery` | 44 |
| no active rule matched | 413 |

Three rules account for 1,430 of the 1,992. Whether they are wrong is a
question for a reviewer; that they are the question is not.

**Four of the 46 active rules have regexes that do not compile**:
`/(entertainment`, `obituar(y`, `/(us-world-news`, `/(weather`. Whatever
`pattern_filtered` does with those, it is not what was intended. That is
a crawler defect, independent of this queue, and it is listed here
because it was found by the same measurement.

---

## 8. Verbs and dispositions

The subject is a candidate link, not an article: there is no body to
read, no byline, no capture — only a URL, its publisher, and whatever the
verification recorded. Two verbs, and a qualifier that carries the part
worth counting.

| Verb | Tone | What it writes |
| --- | --- | --- |
| **It is a story** | fix | `candidate_links.status = 'discovered'`, so the pipeline fetches it on the next run |
| **Not a story** | reject | nothing on the crawler — the status already excludes it. The decision is recorded so the queue stops asking |

**Qualifier — what it actually is**, answered alongside either verb:
`section index`, `tag or author page`, `video`, `photo gallery`,
`event listing`, `obituary index`, `subscribe or account page`,
`homepage`, `story`, `other`.

The qualifier is the half that improves the classifier. A verb says the
call was wrong; the qualifier says what the right answer was, and a
count of "section index" against a rule or a publisher is what a fix is
built from. This mirrors the extraction queue, where the verb and the
content type are two independent answers rather than alternatives
(`review/kernel.py` `Qualifier`).

**Where decisions go.** `ReviewDecision` in Datadesk's own database, with
`subject_type="candidate_link"` — the same record every other queue
writes, so the audit entry, the receipt, the "already answered" filter
and the revert path are the ones that already exist. The labelled set is
then exportable for training without the crawler having to own it.

`url_verifications.human_label` is deliberately **not** written. Datadesk
does not create rows in the crawler's tables (SCOPE.md §2.5), and a label
in two places is a label that can disagree with itself. If the crawler
later wants the labels, it reads them.

---

## 9. What has to change to write a verb

"It is a story" is the console's first write to `candidate_links`, and
the boundary is deliberately narrow, so it is three changes and no more:

1. `review/services.py` — `CandidateLink: ("status",)` in `WRITABLE`.
2. `infra/sql/create_crawler_write_role.sql` — `GRANT UPDATE (status) ON
   candidate_links TO datadesk_rw`, applied to production.
3. `explorer/models.py` — `CandidateLink` gains the columns the queue
   reads (`status`, `discovered_at`, `discovered_by`, `meta`), which it
   does not have today.

Nothing else in the console may write that table, and no verb here
deletes or creates one.

---

## 10. The labelled set, and what it can improve

The queue's real output is not the rows it fixes; it is a labelled corpus
of URL decisions from 828 publishers, which is a larger and more current
domain sample than the upstream model was trained on. This says what to
record so that corpus is usable, and what it can and cannot improve.

### What a label row carries

One row per reviewed URL, from `ReviewDecision` plus the recorded
verification:

| Field | Why it is needed |
| --- | --- |
| `url`, and the **path** separately | the path is the only feature a pre-fetch model may use |
| `host`, `source_id` | grouping for the split, and per-publisher error rates |
| **label**: story / not a story | the target |
| **qualifier**: section index, tag or author page, video, photo gallery, event listing, obituary index, subscribe page, homepage, other | the negative class is not one class, and a model that cannot tell a video page from a section index cannot be improved against either |
| **topic**, where the reviewer can tell: local story, wire or national, obituary, weather, opinion, sports, other | the second axis (§2). A national story labelled "not a story" teaches the model the wrong thing about both questions; labelled "story, wire" it trains article-ness and topic at once |
| **mechanism**: `wire` / `pattern:<id>` / `sniffer` / `default` | attributes the error to the thing that made it |
| `storysniffer_result`, `verification_confidence` | the baseline being beaten |
| `discovered_by` (rss, homepage, section crawl, newspaper4k) | discovery method correlates with URL shape; a model trained on one method's URLs does not transfer |
| `discovered_at`, `reviewed_at`, reviewer | drift, and inter-reviewer disagreement |
| **stratum**: which facet the row came from | the single most important field, below |

### The stratum field decides what the set can be used for

A row drawn from a doubt-ranked facet and a row drawn at random are not
interchangeable, and mixing them silently makes both useless:

- **Doubt-ranked rows** (overruled, by-rule) are enriched for errors by
  construction. They train well — hard cases are what a classifier needs
  — and they **cannot** measure anything. An error rate computed on them
  is a statement about the ranking, not about the corpus.
- **Random rows** are the only ones that measure. Two hundred per class
  give a rate with an interval that holds for the whole pile.

So: random rows are held out as the evaluation set and are never trained
on. Doubt-ranked rows train only. The stratum column is what keeps that
honest six months later, when nobody remembers which query produced which
row.

### Splitting by publisher, not at random

URLs from one publisher share a CMS, a slug convention and a section
vocabulary. A random train/test split puts near-duplicates of the same
pattern on both sides, and the model scores well by memorising the
publisher rather than by learning what a story URL is — which is
precisely the failure that matters here, since the crawler adds
publishers it has never seen. The split is **grouped by host**, and a
second evaluation on **held-out publishers only** is what says whether a
change generalises.

### Three improvements, in order of payoff

1. **Fix the rules.** Three rules — `image_placeholder`, `video`, `feed`
   — account for 1,430 of the 1,992 measured disagreements, and four
   rules do not compile at all (§7). This needs no model and no labels;
   it needs somebody to look at fifty URLs per rule and decide whether
   the rule means what it says.
2. **Record the mechanism, then measure each rule.**
   `verification_patterns` already has `total_matches`, `article_matches`,
   `article_rate` and `confidence_score` columns, all null. Human labels
   fill them, and a rule with a poor rate is then a fact rather than an
   argument. This is phase 1 plus arithmetic.
3. **Train a calibrated path classifier on the labels.** Logistic
   regression or gradient boosting over character n-grams of the path,
   wrapped in `CalibratedClassifierCV`, produces the probability the
   queue's bands need and the current model cannot give (§5). Keep
   storysniffer's answer as an input feature and as the baseline: the
   thing being beaten is not the model on its own, it is the **whole
   verifier** — rules, model and default together — because that is what
   decides.
4. **A separate topic-from-URL model, if it earns its place.** The same
   labels train it, on the topic field rather than the label field, and
   it needs no human review at all to be evaluated: every fetched URL
   already carries the content stage's answer, so its precision can be
   measured against 100,000 rows of existing corpus (§2). It is a
   different model with a different job, and it must never be allowed to
   answer the first question.

### What the labels cannot improve

- **Anything post-fetch.** The path-and-text model exists, and the
  headline and excerpt are far stronger signals than a URL — but they are
  only knowable after a fetch, and this decision happens before one.
  Labels collected here train the path model; the text model belongs to a
  different stage.
- **Upstream storysniffer.** It is a third-party package. Our labels can
  train a local model, and can be offered upstream, but the package's own
  model is not ours to version.

---

## 11. What this does not cover

- **Publisher-level discovery health** — "this publisher yields nothing"
  is a report across a publisher, not a row somebody dispositions. That
  is ROADMAP item 25.
- **`sampled_out`** — a budget decision, not a classification.
- **Fetch failures** (`404`, `proxy_blocked`) — the URL was judged a
  story and the fetch failed. A different queue, if any.
- **Changing a verdict in bulk** — a rule that is wrong is fixed in the
  rule, not by re-dispositioning its matches.

---
