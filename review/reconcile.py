"""Making the record agree with what a reviewer said about it.

A review decision changes one column at the moment it is made, and
nothing afterwards checks that the rest of the record agrees. Measured
after 810 discovery and 3,602 extraction decisions: 100 articles exist
for URLs a reviewer called "not a story", 58 of them enriched and 16
published; all 16 "re-extract" articles are still paused; 17 rejected
articles sit at `labeled`, queued to be enriched.

None of that is a failed write. Each decision did what it was written to
do. This is the part nobody wrote.

See `MizzouNewsCrawler/docs/QUEUE_RECONCILIATION.md` for the rules and
what they were measured against.

WHY HERE. The reconciler needs to read dispositions and write crawler
statuses, and this console is the only thing that already holds both:
`review_reviewdecision` in its own database, `crawler_rw` for the other.
`Article.status` and `CandidateLink.status` are both inside the write
boundary (`services.WRITABLE`), so every change goes through the audited
path rather than around it. Putting it in the crawler would mean giving
the crawler credentials for this database to read a table it otherwise
has no business in.
"""

from dataclasses import dataclass, field

#: Statuses a record is finished at. Nothing here moves one of these.
TERMINAL = ("enriched", "enrichment_skipped", "not_article")

#: What the crawler's three BigQuery syncs select on. An article outside
#: this set is not published, which is how retraction works: no deletes,
#: and `article_geoids` now carries the same filter so a retracted
#: story's counties leave with it.
PUBLISHED = ("enriched", "enrichment_skipped")

#: Where a record goes when a reviewer says it should never have been
#: fetched. Terminal, and outside `PUBLISHED`.
REJECTED = "not_article"

#: What extraction selects, and it is a LINK status. An article sent
#: back for a fetch has to reach its link: extraction reads
#: `candidate_links.status`, so setting an article status schedules
#: nothing. See docs/PIPELINE_STATES.md.
FETCHABLE_LINK = "article"

#: What classification selects (`analyze` defaults to
#: `['cleaned','local']`). There is no article status meaning "clean me
#: again" -- cleaning happens inside extraction and `cleaned` is its
#: output -- so this is where a record with a usable body goes back to.
CLASSIFIABLE = "cleaned"


@dataclass
class Change:
    """One record, and the reason it is being moved."""

    model: str
    pk: str
    field: str
    before: str
    after: str
    rule: str


@dataclass
class Plan:
    """Everything a run would do, before it does any of it."""

    changes: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def add(self, model, pk, fieldname, before, after, rule):
        if (before or "") == (after or ""):
            return
        self.changes.append(Change(model, pk, fieldname, before or "", after, rule))

    def refuse(self, pk, why):
        self.skipped.append((pk, why))

    def by_rule(self):
        counts = {}
        for change in self.changes:
            counts[change.rule] = counts.get(change.rule, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    def retracted(self):
        """Changes that take an article out of BigQuery.

        Counted separately because it is the only thing here that is not
        ordinary processing: a story stops being published as local news.
        """
        return [
            c
            for c in self.changes
            if c.model == "article"
            and c.before in PUBLISHED
            and c.after not in PUBLISHED
        ]


def _decisions(queue, verbs=None):
    from review.models import ReviewDecision

    rows = ReviewDecision.objects.filter(queue=queue)
    if verbs:
        rows = rows.filter(verb__in=verbs)
    return rows


def plan_not_a_story(plan):
    """A URL that should never have been fetched, and everything
    downstream of it.

    The article is moved out of `PUBLISHED`, which removes it from every
    BigQuery sync. Its enrichment, CIN labels and entities are kept: they
    cannot reach a published figure once the status excludes them, and
    deleting them destroys the record of what the pipeline concluded --
    which is the set a reviewer disagreed with, and so the training data.
    """
    from explorer.models import Article

    decided = list(
        _decisions("discovery", ["not_story"]).values_list("subject_id", flat=True)
    )
    if not decided:
        return
    for article in Article.objects.filter(candidate_link_id__in=decided).only(
        "id", "status"
    ):
        if article.status == REJECTED:
            continue
        plan.add(
            "article",
            article.id,
            "status",
            article.status,
            REJECTED,
            "discovery: not a story",
        )


def plan_rejected_in_extraction(plan):
    """Same treatment, from the other queue.

    The urgent ones are those at `labeled`: they are queued to be
    enriched, so every day this does not run is a day the pipeline may
    spend money on something a person already rejected.
    """
    from explorer.models import Article

    decided = list(
        _decisions("extraction", ["reject"]).values_list("subject_id", flat=True)
    )
    if not decided:
        return
    for article in Article.objects.filter(id__in=decided).only("id", "status"):
        if article.status == REJECTED:
            continue
        plan.add(
            "article",
            article.id,
            "status",
            article.status,
            REJECTED,
            "extraction: rejected",
        )


def plan_kind_mismatch(plan):
    """A story whose kind forbids the state it is in.

    `lnic_contracts.discovery_verdict` maps a reviewer's kind to a link
    status, and it is the contract both sides read. This enforces that
    mapping rather than restating it: a reviewer who said "opinion"
    should not find the article enriched, which is what happened to 4
    opinion pieces and 20 columns before the mapping was applied at
    decision time.
    """
    from lnic_contracts import discovery_verdict

    from explorer.models import Article, CandidateLink

    decided = {
        row.subject_id: (row.value or "").strip()
        for row in _decisions("discovery", ["story"])
        if (row.value or "").strip()
    }
    if not decided:
        return

    links = {
        link.id: link
        for link in CandidateLink.objects.filter(id__in=list(decided)).only(
            "id", "status", "meta"
        )
    }
    for link_id, kind in decided.items():
        link = links.get(link_id)
        if link is None:
            plan.refuse(link_id, "link no longer exists")
            continue
        verdict = discovery_verdict.build(
            verdict=discovery_verdict.IS_A_STORY, kind=kind, decided_by=""
        )
        wanted = discovery_verdict.link_status_for(verdict)
        plan.add(
            "candidate_link",
            link.id,
            "status",
            link.status,
            wanted,
            f"discovery: kind is {kind}",
        )

    # And the article, where one exists in a state the kind forbids. A
    # withheld kind is a kind no enrichment stage selects, so an article
    # sitting at a published status contradicts the reviewer.
    for article in Article.objects.filter(candidate_link_id__in=list(decided)).only(
        "id", "status", "candidate_link_id"
    ):
        kind = decided.get(article.candidate_link_id, "")
        verdict = discovery_verdict.build(
            verdict=discovery_verdict.IS_A_STORY, kind=kind, decided_by=""
        )
        wanted_link = discovery_verdict.link_status_for(verdict)
        if wanted_link == discovery_verdict.RESTORED_STATUS:
            # An ordinary story: nothing about the kind constrains the
            # article, and the pipeline decides its status.
            continue
        if article.status in PUBLISHED:
            plan.add(
                "article",
                article.id,
                "status",
                article.status,
                kind,
                f"discovery: {kind} should not be published",
            )


def report_stalled_extractions(plan):
    """Articles extraction could not turn into text -- REPORTED, never
    re-queued.

    A re-fetch without knowing why the last one failed runs the same
    method against the same page and fails the same way. The telemetry
    says so plainly, and in two different ways:

      77 articles, `text IS NULL`, `pause_reason='null_text'`:
          telemetry `is_success = true`, HTTP 200, newspaper4k. The fetch
          worked. The parser produced nothing from a page that came back
          fine -- a paywall interstitial, or markup newspaper4k cannot
          read, or a body rendered in JavaScript. Re-queuing repeats it.
          These carry no review disposition either, so they are not this
          module's business except to be counted.

      16 articles, `text = ''`, a reviewer asked to re-extract:
          telemetry `is_success = false`. 15 only ever tried
          `http_fetch`; the 16th tried Selenium and failed. "Re-extract"
          as the reviewer meant it needs a DIFFERENT method, which no
          status change expresses.

    The empty-string group is also a gap in the pipeline rather than in
    this rule: housekeeping pauses on `text IS NULL`, so `text = ''`
    slips past it and is never parked with a reason.
    """
    from explorer.models import Article

    for article in Article.objects.filter(status="paused").only(
        "id", "status", "text", "metadata"
    ):
        reason = (article.metadata or {}).get("pause_reason")
        if reason == "null_text":
            plan.refuse(
                article.id,
                "extraction returned 200 and no text; needs a method, not a retry",
            )
        elif not (article.text or "").strip():
            plan.refuse(
                article.id,
                "extraction failed and left empty text; needs a method, not a retry",
            )


def plan_parked_by_review(plan):
    """Articles the review queue parked, which the queue's own verdict
    should release.

    101 paused articles carry no `pause_reason`, and 101 is exactly the
    number with an extraction-queue decision (49 reject, 36 accept, 16
    reextract). They were parked by review, not by a pipeline failure.

    `reject` has its own rule and reaches `not_article`. `reextract` is
    reported rather than applied, above. The rest hold a usable body and
    go back to `cleaned`, which is what classification selects (`analyze`
    defaults to `['cleaned','local']`), so they are labelled and enriched
    in the ordinary way.
    """
    from explorer.models import Article

    decided = {
        row.subject_id: row.verb
        for row in _decisions("extraction", ["restore", "accept"])
    }
    if not decided:
        return

    for article in Article.objects.filter(id__in=list(decided), status="paused").only(
        "id", "status", "text", "metadata"
    ):
        if (article.metadata or {}).get("pause_reason") == "null_text":
            continue
        if not (article.text or "").strip():
            # Nothing to classify. Reported by the rule above.
            continue
        verb = decided[article.id]
        plan.add(
            "article",
            article.id,
            "status",
            article.status,
            CLASSIFIABLE,
            f"review: {verb}, back to classification",
        )


#: Every rule, in the order a run applies them. Retraction first, so a
#: record that should not be published stops being published before
#: anything spends effort moving it through the pipeline.
RULES = (
    plan_not_a_story,
    plan_rejected_in_extraction,
    plan_kind_mismatch,
    plan_parked_by_review,
    report_stalled_extractions,
)


def build_plan():
    """What a run would do, without doing any of it."""
    plan = Plan()
    for rule in RULES:
        rule(plan)
    return plan


def apply_plan(plan, user):
    """Carry out a plan through the audited write path.

    One audit entry per rule rather than per row: a reconciliation is one
    action with a reason, and 159 entries saying "not a story" is a log
    nobody reads. Grouped by model too, because `audited_update` takes
    rows of one model.
    """
    from explorer.models import Article, CandidateLink
    from review.services import audited_update

    models = {"article": Article, "candidate_link": CandidateLink}
    written = []
    grouped = {}
    for change in plan.changes:
        grouped.setdefault((change.rule, change.model, change.after), []).append(change)

    for (rule, model_name, after), changes in grouped.items():
        model = models[model_name]
        rows = list(model.objects.filter(pk__in=[c.pk for c in changes]))
        if not rows:
            continue
        entry = audited_update(
            user,
            rows,
            {"status": after},
            action=f"reconcile:{model_name}",
            reason=f"{rule} ({len(rows)} rows)",
        )
        written.append((rule, model_name, after, len(rows), entry.pk))
    return written
