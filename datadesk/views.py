"""Project-level views: the landing page and the health check."""

from django.conf import settings
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import render

from accounts.roles import role_for_user
from explorer.crawler import dataset_row_counts


def landing(request):
    """Landing page.

    Authenticated users see their email and role (SCOPE.md §2.1); with a
    role assigned they also see live row counts per dataset — the Phase 0
    exit test (SCOPE.md §4). Unauthenticated visitors get the Google
    sign-in.
    """
    context = {"google_configured": settings.GOOGLE_SIGN_IN_CONFIGURED}
    if request.user.is_authenticated:
        role = role_for_user(request.user)
        context["role"] = role
        if role is not None:
            counts = dataset_row_counts()
            context["crawler_connected"] = counts is not None
            context["dataset_counts"] = counts
    return render(request, "landing.html", context)


def health(request):
    """Deploy-time health check (deploy.yml probes the candidate revision).

    Touches the database on purpose: a revision that starts but cannot
    reach Cloud SQL must fail here, before traffic shifts to it.
    """
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return HttpResponse("ok", content_type="text/plain")
