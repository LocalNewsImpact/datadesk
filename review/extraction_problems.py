"""Publishers whose extraction is producing garbage.

A reviewer saying "this is an article and the body is unusable" is
answering about one story and reporting about a site. ROT47 that never
decoded, JavaScript captured instead of prose, a list of counties where
the story should be -- these come from a parser meeting a page shape it
does not handle, and that page shape is the site's, not the story's.

Acting on the row is not enough. The row goes to `paused`, out of the
export, and without this the only trace is a status that says nothing
about what was wrong or where it came from.

The decisions are in the application database and the articles in the
crawler's, which do not join, so the publisher is recorded on the
decision when the finding is made (review/dispositions.py) rather than
looked up here.
"""

from collections import Counter


def reported(limit=50):
    """Publishers with garbage bodies reported against them, worst first.

    Returns [{host, reports, latest, examples}], where `examples` are the
    most recent article ids -- enough to open one and see what the parser
    did.
    """
    from review.models import ReviewDecision

    counts = Counter()
    latest = {}
    examples = {}
    for decision in ReviewDecision.objects.filter(wrote__body="garbage").order_by(
        "-decided_at"
    ):
        host = (decision.wrote or {}).get("host") or "unknown publisher"
        counts[host] += 1
        # Ordered newest first, so the first one seen is the latest.
        latest.setdefault(host, decision.decided_at)
        examples.setdefault(host, []).append(
            {"id": decision.subject_id, "label": decision.subject_label}
        )

    return [
        {
            "host": host,
            "reports": count,
            "latest": latest[host],
            "examples": examples[host][:5],
        }
        for host, count in counts.most_common(limit)
    ]


def total():
    """How many garbage bodies have been reported at all."""
    from review.models import ReviewDecision

    return ReviewDecision.objects.filter(wrote__body="garbage").count()
