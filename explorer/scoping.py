"""Narrowing corpus queries to the datasets somebody may see.

ROADMAP item 1. A guard decides whether a page opens; this decides which
rows are on it. The two are separate on purpose, and this is the half
that fails quietly: a wrong guard denies somebody and they say so within
the hour, a missing filter here shows them another dataset's rows and
nobody notices.

**One membership path.** A story belongs to a dataset through its source:
`article → candidate_link.source_id → dataset_sources`. That is the
crawler's own enrichment query and the path `?dataset=` already filtered
on, so scoping uses it rather than inventing a second answer to the same
question.

**Application-wide access skips the filter rather than enumerating.**
`permitted_scopes` says `ALL_SCOPES` when somebody's access is not
limited to particular datasets, and a query for that person must not turn
into `WHERE slug IN (every slug in the corpus)` — that would grow a
subquery for nothing and, worse, would silently exclude a dataset created
after the grant.

**No grants means no rows, not all rows.** The empty case returns
`none()`. Getting that backwards is the failure this module exists to
prevent, so it is written once here rather than at each view.
"""

from accounts.access import ALL_SCOPES, permitted_scopes
from accounts.decorators import APP
from explorer.models import Dataset, DatasetSource

#: How each queryset reaches the source a dataset is defined over. An
#: article holds it through its candidate link; anything hanging off an
#: article prefixes that path.
ARTICLE_SOURCE = "candidate_link__source_id"


def scopes_for(user, privilege):
    """The datasets this person may exercise `privilege` on."""
    return permitted_scopes(user, APP, privilege)


def narrow(qs, user, privilege, source_path=ARTICLE_SOURCE):
    """`qs`, restricted to the datasets this person may see.

    `source_path` is how the queryset reaches `source_id` — the default
    suits anything rooted at an article.
    """
    scopes = scopes_for(user, privilege)
    if scopes is ALL_SCOPES:
        return qs
    if not scopes:
        return qs.none()
    members = DatasetSource.objects.filter(dataset__slug__in=scopes).values("source_id")
    return qs.filter(**{f"{source_path}__in": members})


def datasets_for(user, privilege):
    """The dataset rows to offer in a picker.

    A selector listing datasets somebody cannot choose is an invitation to
    a 403, and the guard would then refuse them for picking what they were
    shown.
    """
    qs = Dataset.objects.order_by("label")
    scopes = scopes_for(user, privilege)
    if scopes is ALL_SCOPES:
        return qs
    if not scopes:
        return qs.none()
    return qs.filter(slug__in=scopes)
