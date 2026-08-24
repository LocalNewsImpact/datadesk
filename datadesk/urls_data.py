"""URLs for the public data front end.

One image serves several front ends; `SERVICE_ROLE=data` selects this one
(ROADMAP item 24). It carries what a reader or another newsroom fetches --
feeds, visual payloads, embeds -- and nothing of the console.

Nothing of the console *exists* here rather than existing and returning
403. A hostname that anybody may link to should not have an admin behind
it at all: not a login form, not a redirect to one, not a URL that reverses
to one. That is the whole reason this is a separate service rather than a
host check on the console.

Everything served here is public by definition. There is no session, no
sign-in and no CSRF-protected write, because there is nothing to protect --
a published visual is published, and an unpublished one is not here.

Addresses here are uuids rather than slugs. A URL served from this host
has been pasted into somebody else's article and cannot be moved
afterwards, so the thing naming it must be incapable of changing -- and a
slug is unique, editable and derived from the title, so renaming a visual
in the admin would break every embed in the wild and tell nobody. The slug
routes stay, as permanent redirects, for the addresses already handed out.
"""

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import include, path, reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.clickjacking import xframe_options_exempt

from visuals import views as visuals
from visuals.models import Visual


def healthz(request):
    """Liveness only. This front end has no database of its own to prove."""
    return JsonResponse({"status": "ok", "role": "data"})


# How long a payload may be believed is decided per response now, by
# `visuals._cache_for`, because it depends on the request rather than on
# the route. A URL naming a version is immutable and may be held for a
# year; the same route without one means "current" and may not. Holding
# that one for a year is why republishing a visual never reached a reader
# who already had it.
#
# The redirects below are the exception. They are true for exactly as long
# as the uuid is, which is forever.
FOREVER = cache_control(public=True, max_age=31536000, immutable=True)


def _to_uuid(route):
    """A permanent redirect from a slug address to the uuid one.

    Permanent because it is. The slug may later be edited, or come to name
    a different visual, but the uuid this resolves to today never moves.

    The query string is carried across. Dropping it would turn a reader's
    `?v=3` into "whatever is current" without saying so, which is the one
    thing `?v=` exists to prevent.
    """

    def view(request, slug):
        # Published only, and the filter is here rather than left to the
        # target. Redirecting a draft would 404 at the far end but confirm
        # on the way that the visual exists and hand out its uuid -- and
        # this host has no sign-in, so there is nobody here a draft could
        # be previewed for.
        visual = get_object_or_404(Visual, slug=slug, status=Visual.PUBLISHED)
        target = reverse(route, args=[visual.uuid])
        query = request.META.get("QUERY_STRING")
        return redirect(f"{target}?{query}" if query else target, permanent=True)

    return view


# Under the `visuals` namespace, because the renderer templates reverse
# `visuals:data` to find their own feed. Declared bare, the embed raised
# NoReverseMatch and returned 500 -- and `_health` passed, because it
# reverses nothing. A route is not exercised by the route beside it.
#
# The uuid routes are declared first, and the order matters. `<slug:slug>`
# matches a uuid -- letters, digits and hyphens are the whole of what a
# slug is -- so a slug route above these would swallow every canonical URL
# and redirect it to itself.
_visuals = [
    # The embed and its data. Framing is decided per visual by the
    # allowlist the console records, which is why the embed view stays the
    # one the console already uses rather than a copy.
    path(
        "embed/<uuid:uuid>/",
        xframe_options_exempt(visuals.embed),
        name="embed",
    ),
    path(
        "visuals/<uuid:uuid>/data.json",
        visuals.data_json,
        name="data",
    ),
    # Where the fallback link inside every snippet lands, for the reader
    # whose browser never ran the script.
    #
    # `visuals.page` cannot serve it: that one keeps the sign-in wall and
    # renders in the console's chrome, and this host has neither.
    path(
        "visuals/<uuid:uuid>/",
        visuals.public_page,
        name="page",
    ),
    # The addresses already pasted into other people's articles.
    path("embed/<slug:slug>/", FOREVER(_to_uuid("visuals:embed"))),
    path("visuals/<slug:slug>/data.json", FOREVER(_to_uuid("visuals:data"))),
    path("visuals/<slug:slug>/", FOREVER(_to_uuid("visuals:page"))),
]

urlpatterns = [
    path("_health", healthz, name="healthz"),
    path("", include((_visuals, "visuals"), namespace="visuals")),
]
