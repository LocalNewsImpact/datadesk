"""Project-level views: the landing page."""

from django.conf import settings
from django.shortcuts import render

from accounts.roles import role_for_user


def landing(request):
    """Landing page.

    Authenticated users see their email and role (SCOPE.md §2.1);
    unauthenticated visitors get the Google sign-in.
    """
    context = {"google_configured": settings.GOOGLE_SIGN_IN_CONFIGURED}
    if request.user.is_authenticated:
        context["role"] = role_for_user(request.user)
    return render(request, "landing.html", context)
