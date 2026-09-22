"""Bylines: what a byline string is, decided one string at a time.

`articles.author` is what a parser made of a page, and across a dataset the same
reporter appears under several spellings, a desk name appears as a person, and a
job title rides along after a name. Counting those strings counts spellings.

WHAT THIS PAGE IS NOT. It is not a list of every byline: the crawler computes
which strings show a defect and writes them to `byline_review_candidates`, and
this renders that. On Mizzou it is about 150 rows against 1,824 people.

WHAT A DECISION IS. One row per (dataset, raw string) in `byline_normalizations`
saying which people the string names -- or nobody. The crawler applies it to
`articles.author`, so the permanent record carries the corrected name, and the
row survives a re-extraction that writes the raw form again.

CO-AUTHORS ARE NOT A DEFECT and never appear here. A byline naming three
reporters is three records, which the crawler's `author_records` produces.
"""

from django.db import transaction
from django.utils import timezone

#: The decisions a reviewer can take on a string.
ACCEPT = "accept"  # the string is already right
FIX = "fix"  # these are the people it names
DROP = "drop"  # it names nobody: a desk, a bot, an address

DECISION_LABELS = {
    ACCEPT: "accepted as it stands",
    FIX: "fixed",
    DROP: "dropped: names nobody",
}


def candidates(dataset_id, signal="", limit=200):
    """The queue for one dataset, worst signal first, then biggest.

    Ordered in the database rather than in Python: the page shows a slice and
    the count underneath it is of the whole queue.
    """
    from explorer.models import BylineReviewCandidate

    rows = BylineReviewCandidate.objects.using("crawler").filter(dataset_id=dataset_id)
    if signal:
        rows = rows.filter(signal=signal)
    return rows.order_by("signal", "-articles", "raw_byline")[:limit]


def signal_counts(dataset_id):
    """`[(signal, label, count)]` for the filter chips, biggest first."""
    from django.db.models import Count, Max

    from explorer.models import BylineReviewCandidate

    rows = (
        BylineReviewCandidate.objects.using("crawler")
        .filter(dataset_id=dataset_id)
        .values("signal")
        .annotate(n=Count("id"), label=Max("signal_label"))
        .order_by("-n")
    )
    return [(row["signal"], row["label"], row["n"]) for row in rows]


def parse_names(value):
    """The names a reviewer typed, one per line or comma-separated.

    Both, because a co-authored byline is natural to type on one line and a
    correction is natural to type on its own.
    """
    parts = []
    for line in (value or "").replace(",", "\n").splitlines():
        name = " ".join(line.split())
        if name:
            parts.append(name)
    return parts


@transaction.atomic(using="crawler")
def decide(dataset_id, raw_byline, decision, names, user, reason=""):
    """Record what a string is, and take it off the queue.

    The candidate row goes now rather than at the next refresh, so a worked
    queue empties as it is worked. The crawler rewrites it on the next refresh
    only if the string still shows a defect -- which a decided string does not,
    because `refresh_candidates` reads the decisions.

    `articles.author` is NOT written here. The crawler owns that write
    (`byline-report apply`), which keeps one writer for the column and keeps a
    review request from updating thousands of rows.
    """
    from explorer.models import BylineNormalization, BylineReviewCandidate

    if decision not in DECISION_LABELS:
        raise ValueError(f"not a decision: {decision!r}")
    canonical = [] if decision == DROP else list(names)

    existing = (
        BylineNormalization.objects.using("crawler")
        .filter(dataset_id=dataset_id, raw_byline=raw_byline)
        .first()
    )
    if existing:
        existing.canonical_names = canonical
        existing.decision = decision
        existing.reason = reason or None
        existing.decided_by = getattr(user, "username", "") or None
        existing.decided_at = timezone.now()
        # Re-decided, so the write to `articles.author` is owed again.
        existing.applied_at = None
        existing.save(using="crawler")
        row = existing
    else:
        import uuid

        row = BylineNormalization.objects.using("crawler").create(
            id=str(uuid.uuid4()),
            dataset_id=dataset_id,
            raw_byline=raw_byline,
            canonical_names=canonical,
            decision=decision,
            reason=reason or None,
            decided_by=getattr(user, "username", "") or None,
            decided_at=timezone.now(),
        )
    BylineReviewCandidate.objects.using("crawler").filter(
        dataset_id=dataset_id, raw_byline=raw_byline
    ).delete()
    return row


def decided(dataset_id, limit=50):
    """What has been decided, newest first -- the page's receipt."""
    from explorer.models import BylineNormalization

    return (
        BylineNormalization.objects.using("crawler")
        .filter(dataset_id=dataset_id)
        .order_by("-decided_at")[:limit]
    )


# ---------------------------------------------------------------------------
# The two reports the review exists to produce.
#
# LOCAL_STATUSES is what "local news" means here: an article that reached
# enrichment. A byline on a wire story, a paywall stub or a page that was never
# an article is not a local reporter's byline, and counting it inflates every
# number on both reports.
# ---------------------------------------------------------------------------

LOCAL_STATUSES = ("enriched", "enrichment_skipped")


def _author_rows(dataset_id, statuses=LOCAL_STATUSES):
    """`(author, host, owner)` for the dataset's local articles that have one.

    `.values()` rather than model instances: the reports read three columns off
    a few thousand rows and building an Article for each is the whole cost.
    """
    from explorer.models import Article

    return (
        Article.objects.using("crawler")
        .filter(dataset_id=dataset_id, status__in=statuses)
        .exclude(author__isnull=True)
        .exclude(author="")
        .values_list(
            "author",
            "candidate_link__source__host",
            "candidate_link__source__owner",
        )
    )


def _resolve(raw, decisions):
    """The people a raw byline names, after what a reviewer decided about it.

    A dropped string names nobody, so it contributes to neither report -- which
    is what dropping it was for. An undecided string is split on the separators
    the crawler writes, so co-authors count as the people they are.
    """
    if raw in decisions:
        return list(decisions[raw])
    parts = []
    for piece in str(raw).replace(" and ", ", ").split(","):
        name = " ".join(piece.split())
        if name:
            parts.append(name)
    return parts


def decisions_map(dataset_id):
    """`{raw string: [canonical names]}` for one dataset. Dropped -> `[]`."""
    from explorer.models import BylineNormalization

    rows = BylineNormalization.objects.using("crawler").filter(dataset_id=dataset_id)
    return {r.raw_byline: (r.canonical_names or []) for r in rows}


def bylines_with_hosts(dataset_id, statuses=LOCAL_STATUSES):
    """Unique local bylines, and the hosts each one appears on.

    More than one host is not a defect: a stringer files to several papers, and
    papers under one owner share copy. The report says which hosts, and the
    reader judges it.
    """
    decisions = decisions_map(dataset_id)
    found = {}
    for raw, host, owner in _author_rows(dataset_id, statuses):
        for name in _resolve(raw, decisions):
            row = found.setdefault(
                name, {"byline": name, "articles": 0, "hosts": set(), "owners": set()}
            )
            row["articles"] += 1
            if host:
                row["hosts"].add(host)
            if owner:
                row["owners"].add(owner)
    rows = []
    for row in found.values():
        row["hosts"] = sorted(row["hosts"])
        row["owners"] = sorted(row["owners"])
        rows.append(row)
    rows.sort(key=lambda r: (-r["articles"], r["byline"].lower()))
    return rows


def hosts_with_bylines(dataset_id, statuses=LOCAL_STATUSES):
    """Unique hosts, and how many distinct local bylines each one carries.

    The count is of PEOPLE, not of byline strings: two spellings of one
    reporter are one byline here only because the review collapsed them, which
    is the whole reason this report waits on the queue being worked.
    """
    decisions = decisions_map(dataset_id)
    found = {}
    for raw, host, owner in _author_rows(dataset_id, statuses):
        if not host:
            continue
        row = found.setdefault(
            host, {"host": host, "owner": owner or "", "articles": 0, "names": set()}
        )
        row["articles"] += 1
        row["names"].update(_resolve(raw, decisions))
    rows = []
    for row in found.values():
        row["bylines"] = len(row["names"])
        row["examples"] = sorted(row["names"])[:3]
        row.pop("names")
        rows.append(row)
    rows.sort(key=lambda r: (-r["bylines"], r["host"]))
    return rows
