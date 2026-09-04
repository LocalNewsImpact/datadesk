"""Acting on a classification the pipeline made (SCOPE.md 2.3).

Automated triage removes an article from processing by writing a status.
Each status is a claim, and a person can accept it, reject it, or say the
fields themselves are missing.

WHY REJECT IS A STATUS REWIND AND NOT A RE-EXTRACTION
-----------------------------------------------------
The pipeline advances by status. Labeling promotes an article only
`if article.status in ("cleaned", "local")` (models/database.py); enrichment
selects `status = 'labeled'` and nothing else (enrichment/repository.py).

So an article removed at some stage is returned by putting its status back
to the stage before the one that erred. It has already been extracted --
every field the page offered is on the row -- and fetching the URL again
would produce the same result from the same page. Re-extraction was never
the remedy for a wrong verdict.

The exception is a body that is not there to rewind: extraction drops
`text` when it judges a body to be furniture, and on 788 rows it dropped
`content` too. Those need the archived HTML re-parsed, which is a
different thing from a decision about a verdict, and is offered only while
the 30-day archive still holds the page.
"""

from django.utils import timezone

#: Which stage made the claim, from what it left behind. Measured over the
#: corpus this separates 1,051 extraction rows from 207 enrichment rows
#: with no overlap on any column.
EXTRACTION = "extraction"
LABELING = "labeling"
ENRICHMENT = "enrichment"

#: Where REJECT puts the status back to. Labeling promotes from `cleaned`,
#: so anything decided at or before labeling rewinds there; enrichment
#: reads `labeled`, so its own mistakes rewind one step later.
REWIND_TO = {
    EXTRACTION: "cleaned",
    LABELING: "cleaned",
    ENRICHMENT: "labeled",
}

#: Where re-extraction puts the article.
#:
#: NOT `extracted`. That status asserts extraction succeeded -- the
#: extractor writes it only on success (utils/extraction_telemetry.py) --
#: and `pipeline_status` counts it as eligible for classification, so a
#: body-less row set to `extracted` would be queued for labelling with
#: nothing to label. Worse, housekeeping hunts exactly that shape:
#:
#:     UPDATE articles SET status = 'paused',
#:            metadata = jsonb_set(..., '{pause_reason}', '"null_text"')
#:      WHERE status = 'extracted' AND text IS NULL
#:
#: so the decision would be reverted by the next housekeeping run.
#:
#: `paused` is where such a row belongs and where housekeeping already
#: puts it. The article is out of the pipeline, `raw_gcs_path` says the
#: capture is there to rebuild it from, and the reason distinguishes a
#: body somebody asked to have rebuilt from one that merely arrived
#: empty.
REEXTRACT_TO = "paused"

#: The note's shape is not defined here. The crawler writes it and this
#: reads it, and each repository having its own copy of the key names is
#: what let a rename strand every held article -- two tests, neither able
#: to see the other. lnic_contracts is the one definition both import.
from lnic_contracts import review_note as _contract  # noqa: E402

from review import kernel  # noqa: E402

#: Where a record waits while somebody is deciding about it.
#:
#: A flagged record keeps a status the pipeline reads. Everything the queue
#: surfaces today is already held -- not_article, obituary and the rest are
#: selected by no stage -- but every field-level defect sits on `labeled`
#: articles, 85,246 of them, which enrichment picks up. Flag one of those
#: and it is enriched and exported before anybody looks.
#:
#: No stage selects `in_review`, so a record parked here is out of the
#: pipeline. `status_before` on the decision is how it gets back: ACCEPT
#: means the classification stands, which is not the same as leaving the
#: article wherever review put it.
IN_REVIEW = _contract.IN_REVIEW


def hold_for_review(article, stage, user=None):
    """Park a flagged article out of the pipeline until somebody decides.

    A flagged record otherwise keeps a status the pipeline reads. Every
    field-level defect sits on a `labeled` article -- 85,246 of them --
    which enrichment picks up, so a flag raised there is enriched and
    exported before anybody looks at it.

    Nothing releases a held record but a decision. There is no timeout,
    deliberately: what is a risk to the export is not knowable in advance,
    so the safe default is to stop and ask. Reviews piling up is not a
    failure of the queue, it is the pipeline telling you how often it is
    wrong.

    Returns the status the article was holding, which is what ACCEPT puts
    back.
    """
    from review.models import ReviewDecision

    was = getattr(article, "status", "")
    if was == IN_REVIEW:
        # Already held. Return what it was holding, from the note rather
        # than from the status, which now says in_review.
        return status_before_review(article) or was

    # Already answered? Then it is not a question any more, and holding it
    # would park a record on a decision somebody already made.
    if ReviewDecision.objects.filter(
        subject_type="article",
        subject_id=str(article.pk),
        question=question_for(was, stage),
    ).exists():
        return was

    meta = article.metadata or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    # Built by the contract, which refuses a status_before of `in_review`
    # -- what a caller gets by reading `status` after applying the hold,
    # and the defect that once made this a one-way door.
    article.metadata = _contract.into_metadata(
        meta,
        _contract.build(claim=was, status_before=was, stage=stage),
    )
    article.status = IN_REVIEW
    article.save(update_fields=["status", "metadata"])
    return was


def answered_questions(article_ids):
    """Which (article, question) pairs already have a decision.

    The queue asks this so a settled question is not asked twice, and so a
    NEW question about the same article still is.
    """
    from review.models import ReviewDecision

    return set(
        ReviewDecision.objects.filter(
            subject_type="article", subject_id__in=list(article_ids)
        ).values_list("subject_id", "question")
    )


def decisions_for(article_ids):
    """The decision on each of these articles, by article id.

    One query for a page of rows. Used to render a decided row as decided
    rather than offering verbs that would be refused -- the proposals
    queue's `state=all` does the same, and showing an answered question
    with live buttons is how somebody comes to believe they changed
    something they did not.
    """
    from review.models import ReviewDecision

    latest = {}
    for decision in ReviewDecision.objects.filter(
        queue=EXTRACTION_QUEUE_KEY,
        subject_type="article",
        subject_id__in=[str(i) for i in article_ids],
    ).order_by("decided_at"):
        latest[decision.subject_id] = decision
    return latest


#: The verbs, as names. They used to be class attributes on
#: ExtractionDecision, which made a vocabulary look like a property of a
#: table -- and the table is no longer where a decision is written. They
#: belong to the queue, and the queue declares them at the end of this
#: module.
ACCEPT = "accept"
REJECT = "reject"
REEXTRACT = "reextract"

#: The statuses the export selects (MizzouNewsCrawler
#: src/enrichment/repository.py EXPORTABLE_STATUSES). Stated here because
#: a reviewer has to be told what a decision does to the export, and
#: `enrichment_skipped` is exported -- unenriched, but exported.
#:
#: A cross-service fact, and a candidate for lnic-contracts: it is
#: written by one service and read by another, and restating it is
#: exactly the drift that package exists to stop. Left here until the
#: next contract release rather than bundled into an unrelated one.
EXPORTED_STATUSES = ("enriched", "enrichment_skipped")


def _what_accept_leaves(article):
    """What accepting does to THIS row.

    "Leave it out" was on every row, and on a scope-recorded article it
    leaves it IN: `enrichment_skipped` is exported. A reviewer reading it
    on a 14,149-character bylined feature was being told the opposite of
    what the button does.
    """
    if getattr(article, "status", "") in EXPORTED_STATUSES:
        return "Stays in the export, unenriched"
    return "Stays out of the export"


#: What a person can say an article actually is.
#:
#: Every one of these is a status the pipeline already writes (production,
#: 2026-09-04: obituary 4,475, weather 2,369, paywall 2,161, opinion
#: 2,061, not_article 1,258, out_of_scope 3,769). Nothing here invents a
#: vocabulary; it offers the one the crawler uses.
#:
#: Reject answers "the classification is wrong" when what the article
#: really is happens to be a news story. It does not answer "this is not
#: an obituary, it is a weather report", and a reviewer looking at a
#: 53,926-character bylined sports feature filed as an obituary needs to
#: be able to say which of the two they mean.
CONTENT_TYPES = (
    {"value": "not_article", "label": "Not an article"},
    {"value": "obituary", "label": "Obituary"},
    {"value": "weather", "label": "Weather"},
    {"value": "opinion", "label": "Opinion"},
    {"value": "wire", "label": "Wire"},
    {"value": "paywall", "label": "Paywalled stub"},
    {"value": "out_of_scope", "label": "Out of scope"},
)


#: The queue this module declares. Named before the declaration so the
#: reader above can use it; the queue itself is built at the end of the
#: module, once its verbs and their apply function exist.
EXTRACTION_QUEUE_KEY = "extraction"


#: Where the hold records what it held, so the article can be put back.
#:
#: `status` is overwritten by IN_REVIEW, so the claim being reviewed and
#: the status to restore have to live somewhere else. metadata is where the
#: crawler already keeps this kind of note -- housekeeping writes
#: pause_reason there -- and it survives the round trip through a page load,
#: which an attribute on the instance does not.
REVIEW_META_KEY = _contract.METADATA_KEY


#: The keys the hold must record. The crawler writes them
#: (src/pipeline/review_hold.py) and this reads them; a key renamed on
#: either side strands every article held after the rename, because the
#: status to restore and the claim being reviewed both live here.
REQUIRED_NOTE_KEYS = _contract.REQUIRED_KEYS


def review_note(article):
    """What the hold recorded about this article, or {}."""
    return _contract.from_metadata(getattr(article, "metadata", None))


def note_is_readable(article):
    """Can this article be put back where it came from?

    An article on IN_REVIEW whose note cannot be read is stranded: the
    status to restore is gone, so ACCEPT has nothing to restore it to and
    it stays out of the pipeline for ever.

    The failure this guards is a rename across two repositories. The
    crawler writes the note and this reads it, and nothing enforces the
    shape jointly, so a key renamed on one side is invisible until an
    article is held and cannot be released.

    Reported rather than assumed away. Silently treating such an article
    as "never held" is how the data would go missing without anybody
    seeing it -- the queue shows it instead, saying what is wrong.
    """
    if getattr(article, "status", "") != IN_REVIEW:
        return True
    return _contract.is_readable(review_note(article))


def unreadable_note_reason(article):
    """What is wrong with the note, for a reviewer to read."""
    if note_is_readable(article):
        return ""
    note = review_note(article)
    if not note:
        return (
            "Held for review with no note recorded. The status it was held "
            "from cannot be recovered from the article, so it cannot be "
            "released automatically."
        )
    missing = _contract.missing_keys(note)
    return (
        "Held for review with an incomplete note — missing "
        f"{', '.join(missing)}. Written by the crawler and read here; a key "
        "renamed on either side strands the article."
    )


def status_before_review(article):
    """The status this article carried when review took it.

    Read from metadata rather than from an attribute. Held on the instance
    it survived exactly one function call: the status was overwritten by
    IN_REVIEW, the original was lost on the next page load, and ACCEPT then
    had nothing to put the article back to -- a one-way door.
    """
    return review_note(article).get("status_before", "")


def claim_under_review(article):
    """The claim being reviewed, which IN_REVIEW would otherwise hide.

    Once held, `status` says `in_review` and no longer says what was
    claimed -- so the question cannot be formed from it.
    """
    return review_note(article).get("claim", "")


def question_for(status, stage):
    """What is being asked, as a stable key.

    A decision answers a question, not an article. Keyed on the article
    alone, the first decision would silence every later question about it
    -- and an article whose byline is later found to be garbage is a new
    question, askable even though its classification was settled months
    ago.

    The claim and the stage that made it. Re-flagging the same claim
    produces the same key and stays answered; a different claim is a
    different question and surfaces.
    """
    return f"{status or 'unknown'}:{stage or 'unknown'}"


#: Written beside the status so the request is legible to whatever acts
#: on it, and distinguishable from housekeeping's own "null_text".
REEXTRACT_PAUSE_REASON = "reextract_requested"


def stage_of(article, enrichment=None, labels_updated_at=None):
    """Which stage decided this article's status.

    The enrichment row is the discriminator, and it is absolute: an
    article the enrichment gate ruled on always has one, and an article
    extraction ruled on never does.
    """
    if enrichment is not None:
        return ENRICHMENT
    if labels_updated_at is not None:
        return LABELING
    return EXTRACTION


def has_body_to_rewind_to(article) -> bool:
    """Is there anything for the pipeline to reprocess?

    Extraction drops `text` when it calls a body furniture, and on 788
    rows dropped `content` as well. Rewinding one of those sends an empty
    body to the labeler, so Reject on it is not a decision anybody can
    act on.
    """
    return bool((getattr(article, "content", "") or "").strip()) or bool(
        (getattr(article, "text", "") or "").strip()
    )


def can_reextract(article) -> bool:
    """Is the page as captured still in the archive?

    The bucket keeps 30 days. Without a path there is nothing to re-parse
    and the row is Accept-only.
    """
    return bool((getattr(article, "raw_gcs_path", "") or "").strip())


def verbs_for(article, stage):
    """Which decisions this row can actually carry out.

    A verb that cannot be performed is not offered. Reject needs a body to
    return to the pipeline; re-extract needs the archived page.
    """
    verbs = [ACCEPT]
    if has_body_to_rewind_to(article):
        # Both are available together: "this is a real story" and "this is
        # a weather report" are different answers, and a reviewer looking
        # at a bylined sports feature filed as an obituary has to be able
        # to say which one they mean.
        verbs.append(REJECT)
    elif can_reextract(article):
        verbs.append(REEXTRACT)
    return verbs


def rewind_target(stage):
    """The status REJECT writes, or None where the stage is unknown."""
    return REWIND_TO.get(stage)


def _metadata_of(article):
    """The article's metadata as a mapping, whatever the driver returned.

    `json` columns come back parsed from psycopg3 and as text from
    anything that stores them as text, and a decision written over either
    shape has to merge with what is already there.
    """
    import json

    meta = getattr(article, "metadata", None)
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = None
    return meta if isinstance(meta, dict) else {}


def record(
    article,
    *,
    decision,
    stage,
    user,
    reason="",
    label="",
    article_status="",
    content_type="",
):
    """Write the decision, and the status where the decision requires it.

    ACCEPT writes nothing to the crawler: the article's status already
    excludes it, so the only thing needed is that the queue stops asking.
    REJECT and REEXTRACT each write one field -- the status -- and differ
    only in how far back it goes.
    """
    from review.models import ReviewDecision

    # Read before the rewind. Taken afterwards this recorded the status
    # the decision PRODUCED rather than the claim it answered, so every
    # rejected row said it had been classified as "labeled".
    claimed = article_status or getattr(article, "status", "")

    # Where the article was before review held it. Only meaningful once a
    # record is parked in `in_review`; until then the claim IS the prior
    # status and the two agree.
    # The claim, and the status to restore, from the note the hold left.
    # Falls back to the current status for a record that was never held --
    # nothing the queue surfaces today is, and the two then agree.
    held_before = status_before_review(article)
    before = held_before or claimed
    if not article_status:
        claimed = claim_under_review(article) or claimed

    rewound = ""
    target = None
    if decision == ACCEPT:
        # The classification stands. If review parked the article, put it
        # back on the status it was flagged with -- leaving it in
        # `in_review` would hold it out of the pipeline for ever on the
        # strength of a decision that said nothing was wrong.
        if getattr(article, "status", "") == IN_REVIEW and before != IN_REVIEW:
            target = before
    elif decision == REJECT:
        target = rewind_target(stage)
    elif decision == REEXTRACT:
        # Out of the pipeline rather than back into it: there is no status
        # meaning "re-parse me", and the one that looks like it --
        # `extracted` -- asserts extraction succeeded and would be undone
        # by housekeeping.
        target = REEXTRACT_TO
    # The answer, written onto the article where the pipeline can see it.
    #
    # The console's own record is not enough. The crawler raises a hold
    # from the article's own fields, so a claim answered here is raised
    # again by the next run that reads those fields -- held, released,
    # held again, with the decision undone by a stage that never knew it
    # was made. The two databases do not join, so the fact has to travel
    # on the row.
    #
    # Merged into whatever metadata the article carries, never written
    # over it: the crawler keeps the hold note in the same column.
    answered = _contract.record_decision(
        _metadata_of(article),
        _contract.build_decision(claim=claimed, stage=stage, decision=decision),
    )
    article.metadata = answered
    written = ["metadata"]

    # What the reviewer said it actually is, if they said. It decides the
    # status whatever the verb was: a corrected type is not a note about
    # the article, it is the article's status, and every later stage reads
    # that. Accept plus a type means the exclusion was right and the
    # reason was wrong.
    #
    # Refused rather than guessed at: the value comes from a form, and a
    # status this does not recognise would be written straight through to
    # the pipeline.
    said = (content_type or "").strip()
    if said:
        if said not in {t["value"] for t in CONTENT_TYPES}:
            raise ValueError(f"{said!r} is not a content type this queue offers")
        target = said

    if target:
        article.status = target
        written.append("status")
        rewound = target
    article.save(update_fields=written)

    # One record for every queue in the console (review/models.py
    # ReviewDecision). ExtractionDecision is left with the rows migration
    # 0015 carried across and is written to no longer: two records for one
    # decision meant the read paths could disagree about what had been
    # decided, which they did -- a decided row rendered blank because the
    # writer and the reader had come apart.
    entry, _ = ReviewDecision.objects.update_or_create(
        subject_type="article",
        subject_id=str(article.pk),
        field="",
        question=question_for(claimed, stage),
        defaults={
            "queue": EXTRACTION_QUEUE_KEY,
            "subject_label": label or (getattr(article, "title", "") or "")[:300],
            "claim": claimed,
            "stage": stage,
            "verb": decision,
            "before": before,
            "after": target or before,
            "wrote": {"rewound_to": rewound} if rewound else {},
            "reason": reason,
            "decided_by": user,
            "decided_at": timezone.now(),
        },
    )
    return entry


# --- the extraction queue, declared ------------------------------------------
#
# The verbs were written into the template, three times over: the button
# text, the tooltip that says what will happen, and the tally in the dock.
# Here they are said once, and review/kernel.py renders and submits them.


def _apply_extraction(article, verb, value, user):
    """Carry out one extraction decision and say what it wrote.

    The rules are unchanged -- `record` still holds them -- so this is
    the shape the kernel needs, not a second implementation of the
    disposition.
    """
    stage = stage_of(article)
    entry = record(
        article,
        decision=verb.name,
        stage=stage,
        user=user,
        # The queue's qualifier: what the reviewer said it actually is,
        # given alongside the verb rather than instead of it.
        content_type=value,
        label=(getattr(article, "title", "") or "")[:300],
    )
    return {
        "label": entry.subject_label,
        "before": entry.before,
        "after": entry.after,
        "wrote": entry.wrote,
        "reason": entry.reason,
    }


EXTRACTION_QUEUE = kernel.register(
    kernel.Queue(
        key=EXTRACTION_QUEUE_KEY,
        subject_type="article",
        verbs=(
            kernel.Verb(
                name=ACCEPT,
                label="Accept",
                # Per row: what the current status does differs by row,
                # and one phrase for all of them was false on some.
                sublabel="The current status stands",
                sublabel_for=_what_accept_leaves,
                past="accepted",
                tone="accept",
            ),
            kernel.Verb(
                name=REJECT,
                label="Reject",
                # What rejecting asserts, not where it sends the row. A
                # reviewer reading "Back to the pipeline" on a sports
                # feature filed as an obituary cannot tell whether it says
                # "this is a real story" or "put it back and re-decide".
                sublabel="It is a real story, put it back",
                past="rejected",
                tone="reject",
            ),
            kernel.Verb(
                name=REEXTRACT,
                label="Re-extract",
                sublabel="Re-parse the capture",
                past="sent for re-extraction",
                tone="fix",
            ),
        ),
        apply=_apply_extraction,
        # Said alongside the verb, not instead of it. An article can be
        # wrongly called an obituary and still belong out of the pipeline:
        # the exclusion was right and the reason was wrong, and as
        # separate verbs a reviewer had to choose which half to record.
        qualifier=kernel.Qualifier(
            name="content_type",
            label="Actually it is",
            sublabel=(
                "Leave alone unless the type is wrong. Out of scope, "
                "not an article, weather, opinion and wire are all out "
                "of the export; a real story goes back to the pipeline."
            ),
            values=CONTENT_TYPES,
        ),
        # Which of the three a PARTICULAR row can carry out: reject needs a
        # body to hand back, re-extract needs the archived capture.
        verbs_for=lambda article: verbs_for(article, stage_of(article)),
    )
)
