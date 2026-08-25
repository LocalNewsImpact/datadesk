"""Project-level views: the landing page and the health check."""

from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import render

from accounts.access import has_any_grant
from accounts.decorators import APP
from explorer.costs import recorded_costs
from explorer.crawler import dataset_row_counts
from explorer.dashboard import corpus_summary, datasets_table


def privacy(request):
    """What this application does with a Google account, in public.

    Google requires the consent screen to link a privacy policy that
    discloses how the app accesses, uses, stores and shares Google user
    data, and requires the page to be reachable without signing in --
    which every other page of this console is not.

    Served by the application rather than written on a marketing site so
    that it deploys with the code and cannot quietly stop describing what
    the code does.
    """
    return render(
        request,
        "legal/privacy.html",
        {"domains": settings.ALLOWED_AUTH_DOMAINS, "role": settings.SERVICE_ROLE},
    )


def terms(request):
    """The terms the consent screen links beside the privacy policy."""
    return render(
        request,
        "legal/terms.html",
        {"domains": settings.ALLOWED_AUTH_DOMAINS, "role": settings.SERVICE_ROLE},
    )


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
        # A bool now, not a role name, so this tests truthiness -- the
        # `is not None` this replaced would have been true for False.
        has_access = has_any_grant(request.user, APP)
        context["has_access"] = has_access
        if has_access:
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
