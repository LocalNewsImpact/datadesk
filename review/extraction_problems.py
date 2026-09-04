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


def repeated_bodies(limit=50):
    """Publishers whose bodies repeat exactly, worst first.

    The half nobody has reported. `reported()` counts what reviewers
    found one story at a time; this is what the corpus says on its own,
    computed by `manage.py find_repeated_bodies`.

    Each row carries what the pipeline decided these were, because the
    finding is not that a body repeats -- it is that a failed capture was
    confidently labelled something. 472 of newspressnow.com's 486
    identical comment-policy bodies were classified `wire`.
    """
    from review.models import RepeatedBody

    rows = list(RepeatedBody.objects.all()[:limit])
    for row in rows:
        counts = row.statuses or {}
        row.total_reaching_the_pipeline = sum(
            n for status, n in counts.items() if status not in EXCLUDED_BY_STATUS
        )
        # Ordered so the worst-labelled shows first, which is the part
        # worth reading.
        row.status_counts = sorted(
            counts.items(), key=lambda pair: pair[1], reverse=True
        )
    return rows


#: Statuses that already keep an article out of the pipeline. A repeated
#: body under one of these is a capture that failed and was excluded; a
#: repeated body under any other status is one that failed and was not.
EXCLUDED_BY_STATUS = frozenset(
    {"not_article", "obituary", "weather", "opinion", "wire", "paywall", "out_of_scope"}
)
