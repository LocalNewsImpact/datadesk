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
"""

from django.http import JsonResponse
from django.urls import include, path
from django.views.decorators.cache import cache_control
from django.views.decorators.clickjacking import xframe_options_exempt

from visuals import views as visuals


def healthz(request):
    """Liveness only. This front end has no database of its own to prove."""
    return JsonResponse({"status": "ok", "role": "data"})


# A published payload is immutable at its version, so it may be cached hard.
# The list that names versions is not, and is cached briefly instead -- the
# same split the directory's feed already uses.
FOREVER = cache_control(public=True, max_age=31536000, immutable=True)
BRIEFLY = cache_control(public=True, max_age=300)


# Under the `visuals` namespace, because the renderer templates reverse
# `visuals:data` to find their own feed. Declared bare, the embed raised
# NoReverseMatch and returned 500 -- and `_health` passed, because it
# reverses nothing. A route is not exercised by the route beside it.
_visuals = [
    # The embed and its data. Framing is decided per visual by the
    # allowlist the console records, which is why the embed view stays the
    # one the console already uses rather than a copy.
    path(
        "embed/<slug:slug>/",
        xframe_options_exempt(FOREVER(visuals.embed)),
        name="embed",
    ),
    path(
        "visuals/<slug:slug>/data.json",
        BRIEFLY(visuals.data_json),
        name="data",
    ),
    # Where the fallback link inside every snippet lands. It named this URL
    # before the URL existed, so a reader whose browser never ran the
    # script got a 404 on the host the snippet points at.
    #
    # `visuals.page` cannot serve it: that one keeps the sign-in wall and
    # renders in the console's chrome, and this host has neither.
    path(
        "visuals/<slug:slug>/",
        BRIEFLY(visuals.public_page),
        name="page",
    ),
]

urlpatterns = [
    path("_health", healthz, name="healthz"),
    path("", include((_visuals, "visuals"), namespace="visuals")),
]
