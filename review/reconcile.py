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


@dataclass(frozen=True)
class Rework:
    """A record a rewind handed to a crawler stage.

    The status change alone schedules nothing housekeeping can find: every
    stage selects by status, and a rewound record shares its status with
    the whole backlog. This is the row that says which records were asked
    for. The crawler closes it and queues the stage after it.
    """

    record_type: str
    record_id: str
    stage: str
    reason: str


#: The crawler stages housekeeping runs, in order. `discovered` is not
#: here: discovery and URL verification are the pipeline's own crons, and
#: housekeeping starts at records ready for extraction. A rule that wants
#: a stage outside this set is asking for something no workflow runs.
HOUSEKEEPING_STAGES = ("extract", "classify", "enrich")

#: The status a record must already carry for a stage to select it. A row
#: written against any other status sits open forever, because the stage
#: reads `pipeline_rework` AND its own status filter.
STAGE_SELECTS = {
    "extract": ("article",),
    "classify": ("cleaned", "local"),
    "enrich": ("labeled",),
}


@dataclass
class Plan:
    """Everything a run would do, before it does any of it.

    Rules propose; the plan resolves. A record is the unit: two rules
    that agree about one move it once, and two that disagree move it not
    at all. Before this, the last rule to run won -- silently, and only
    because the two rules that overlapped on the first night happened to
    agree.
    """

    proposals: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    rework: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    def add(self, model, pk, fieldname, before, after, rule, stage=None):
        """Propose a move, and the stage it owes afterwards, if any."""
        if (before or "") == (after or ""):
            return
        if stage is not None:
            assert stage in HOUSEKEEPING_STAGES, stage
            assert after in STAGE_SELECTS[stage], (stage, after)
        self.proposals.append(
            (Change(model, pk, fieldname, before or "", after, rule), stage)
        )

    def refuse(self, pk, why):
        self.skipped.append((pk, why))

    def resolve(self):
        """Turn proposals into changes, one per record.

        A record two rules disagree about is refused with both answers in
        the reason, and owes nothing: the rework row is the instruction,
        and a refusal is the absence of one.
        """
        by_record = {}
        for change, stage in self.proposals:
            by_record.setdefault((change.model, change.pk), []).append((change, stage))

        for (model, pk), entries in by_record.items():
            wanted = {c.after for c, _ in entries}
            if len(wanted) > 1:
                rules = ", ".join(sorted(f"{c.rule} -> {c.after}" for c, _ in entries))
                self.refuse(pk, f"rules disagree about this {model}: {rules}")
                continue
            change, stage = entries[0]
            self.changes.append(change)
            if stage is None:
                continue
            record_type = "article" if model == "article" else "candidate_link"
            row = Rework(record_type, pk, stage, change.rule)
            if row not in self.rework:
                self.rework.append(row)
        return self

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
    """The current decision about each subject in a queue.

    Decisions are a history, not a state: a person who said "not a story"
    and then "story" left both rows, and reading every row makes two rules
    disagree about a record the reviewer was clear on. The latest row for
    a subject is the verdict. Filtered by verb AFTER that, so a superseded
    verb disappears rather than still matching its own rule.
    """
    from review.models import ReviewDecision

    latest = {}
    for row in ReviewDecision.objects.filter(queue=queue).order_by(
        "-decided_at", "-id"
    ):
        latest.setdefault((row.subject_type, row.subject_id), row)
    rows = list(latest.values())
    if verbs:
        rows = [row for row in rows if row.verb in verbs]
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

    decided = [row.subject_id for row in _decisions("discovery", ["not_story"])]
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

    decided = [row.subject_id for row in _decisions("extraction", ["reject"])]
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
        "id", "status", "text", "candidate_link_id"
    ):
        kind = decided.get(article.candidate_link_id, "")
        verdict = discovery_verdict.build(
            verdict=discovery_verdict.IS_A_STORY, kind=kind, decided_by=""
        )
        wanted_link = discovery_verdict.link_status_for(verdict)
        if wanted_link == discovery_verdict.RESTORED_STATUS:
            # An ordinary story. Nothing about the kind constrains the
            # article -- except that a superseded "not a story" decision
            # already retracted it, and nothing put it back. Latest-wins
            # makes the retraction rule stop firing; it does not undo
            # what an earlier night wrote. So an article at `not_article`
            # whose reviewer has since called it a story goes back to
            # classification, where a story with a body re-enters.
            if article.status == REJECTED:
                if (article.text or "").strip():
                    plan.add(
                        "article",
                        article.id,
                        "status",
                        article.status,
                        CLASSIFIABLE,
                        "discovery: restored, back to classification",
                        stage="classify",
                    )
                else:
                    plan.refuse(
                        article.candidate_link_id,
                        "restored, but the article has no body: needs a "
                        "method, not a status",
                    )
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
            stage="classify",
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
    return plan.resolve()


def apply_plan(plan, user):
    """Carry out a plan through the audited write path.

    One audit entry per rule rather than per row: a reconciliation is one
    action with a reason, and 159 entries saying "not a story" is a log
    nobody reads. Grouped by model too, because `audited_update` takes
    rows of one model.

    The rework rows come last, and only for changes that were written: a
    row is an instruction to a crawler stage, and instructing a stage to
    work on a record whose status was not moved is how a run spends a
    night on nothing.
    """
    from explorer.models import Article, CandidateLink
    from review.services import audited_update

    models = {"article": Article, "candidate_link": CandidateLink}
    written = []
    grouped = {}
    for change in plan.changes:
        grouped.setdefault((change.rule, change.model, change.after), []).append(change)

    moved = set()
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
        moved.update((model_name, str(row.pk)) for row in rows)
        written.append((rule, model_name, after, len(rows), entry.pk))

    _request_rework(plan, user, moved)
    return written


def _request_rework(plan, user, moved):
    """Write the outstanding-work rows for the records that moved.

    `ON CONFLICT DO NOTHING` is not available through the ORM's audited
    create, and the crawler's partial unique index would raise on a row
    that is already outstanding -- which happens whenever a run finds a
    record the crawler has not taken yet. So the outstanding rows are
    read first and those requests are skipped: asking twice is the same
    ask.
    """
    from explorer.models import PipelineRework
    from review.services import audited_create

    wanted = [row for row in plan.rework if (row.record_type, row.record_id) in moved]
    if not wanted:
        return None

    outstanding = {
        (r.record_type, r.record_id, r.stage)
        for r in PipelineRework.objects.filter(
            done_at__isnull=True,
            record_id__in=[row.record_id for row in wanted],
        )
    }
    fresh = [
        PipelineRework(
            record_type=row.record_type,
            record_id=row.record_id,
            stage=row.stage,
            reason=row.reason,
            requested_by=user.get_username(),
        )
        for row in wanted
        if (row.record_type, row.record_id, row.stage) not in outstanding
    ]
    if not fresh:
        return None
    return audited_create(
        user,
        fresh,
        action="reconcile:rework",
        reason=f"{len(fresh)} record(s) owe a crawler stage",
    )
