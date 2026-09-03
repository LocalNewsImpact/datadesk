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

#: Where re-extraction rewinds to. Further back than a rejection, because
#: the body has to be rebuilt before anything can read it: `extracted` is
#: the status an article carries before cleaning has run.
#:
#: There is no separate request and no worker waiting on one. The article
#: goes back in the queue with a status saying what it needs, and
#: `raw_gcs_path` already says the capture is there to rebuild it from --
#: non-null exactly when the page is in the archive. A decision recorded
#: somewhere nothing reads is a decision that does not happen.
REEXTRACT_TO = "extracted"


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
    verbs = ["accept"]
    if has_body_to_rewind_to(article):
        verbs.append("reject")
    elif can_reextract(article):
        verbs.append("reextract")
    return verbs


def rewind_target(stage):
    """The status REJECT writes, or None where the stage is unknown."""
    return REWIND_TO.get(stage)


def record(article, *, decision, stage, user, reason="", label="", article_status=""):
    """Write the decision, and the status where the decision requires it.

    ACCEPT writes nothing to the crawler: the article's status already
    excludes it, so the only thing needed is that the queue stops asking.
    REJECT and REEXTRACT each write one field -- the status -- and differ
    only in how far back it goes.
    """
    from review.models import ExtractionDecision

    # Read before the rewind. Taken afterwards this recorded the status
    # the decision PRODUCED rather than the claim it answered, so every
    # rejected row said it had been classified as "labeled".
    claimed = article_status or getattr(article, "status", "")

    rewound = ""
    target = None
    if decision == ExtractionDecision.REJECT:
        target = rewind_target(stage)
    elif decision == ExtractionDecision.REEXTRACT:
        # One step further back than a rejection: the body has to be
        # rebuilt from the archived capture before anything can read it.
        target = REEXTRACT_TO
    if target:
        article.status = target
        article.save(update_fields=["status"])
        rewound = target

    entry, _ = ExtractionDecision.objects.update_or_create(
        article_id=str(article.pk),
        defaults={
            "article_label": label or (getattr(article, "title", "") or "")[:300],
            "classified_as": claimed,
            "stage": stage,
            "decision": decision,
            "rewound_to": rewound,
            "reason": reason,
            "decided_by": user,
            "decided_at": timezone.now(),
        },
    )
    return entry
