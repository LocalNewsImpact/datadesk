"""Project-level views: the landing page and the health check."""

from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import render

from accounts.roles import role_for_user
from explorer.costs import recorded_costs
from explorer.crawler import dataset_row_counts
from explorer.dashboard import corpus_summary, datasets_table


def landing(request):
    """The dashboard.

    Authenticated users see their email and role (SCOPE.md §2.1); with a
    role assigned they also see the corpus: articles by status, how far
    enrichment reached, the review backlog, and recorded cost per dataset
    beside its live row count — the Phase 0 exit test (SCOPE.md §4).
    Unauthenticated visitors get the Google sign-in.

    Every crawler read here returns None rather than raising when the
    database is not configured, so the page degrades to one banner
    instead of five empty panels.
    """
    context = {"google_configured": settings.GOOGLE_SIGN_IN_CONFIGURED}
    if request.user.is_authenticated:
        role = role_for_user(request.user)
        context["role"] = role
        if role is not None:
            counts = dataset_row_counts()
            recorded = recorded_costs()
            context["crawler_connected"] = counts is not None
            context["dataset_counts"] = counts
            context["summary"] = corpus_summary()
            context["datasets"] = datasets_table(counts, recorded)
            context["recorded_total"] = (recorded or {}).get("totals", {}).get("total")
    return render(request, "landing.html", context)


def health(request):
    """Deploy-time health check (deploy.yml probes the candidate revision).

    Touches the database on purpose: a revision that starts but cannot
    reach Cloud SQL must fail here, before traffic shifts to it.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")
