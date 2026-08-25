"""URLs for the Source Directory front end.

One image serves several front ends; `SERVICE_ROLE=sources` selects this
one (ROADMAP.md item 14). It routes the directory's admin and nothing of
Datadesk's — a console the directory's users have no business reaching
does not exist here at all, rather than existing and returning 403.

The views come from the `directory` package. The urlconf does not,
because composing URLs is a project's job and this project serves them
on a different host with a different sign-in.
"""

from directory.views import admin_login_gateway, healthz
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from datadesk import views

admin.site.site_header = "News Source Directory"
admin.site.site_title = "News Source Directory"
admin.site.index_title = "Registry"

urlpatterns = [
    path("_health", healthz, name="healthz"),
    path("accounts/", include("allauth.urls")),
]

# Django's admin login form carries no Google button, so left alone
# /admin/login/ is a password box with no valid password behind it. The
# gateway stands in front of it and sends people somewhere that can
# actually sign them in.
#
# Guarded so the escape hatch survives: blank the OAuth client and
# redeploy, and the ordinary form returns. A broken OAuth client cannot
# lock everybody out.
if settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET:
    urlpatterns += [path("admin/login/", admin_login_gateway)]

urlpatterns += [
    path("admin/", admin.site.urls),
    # The admin is the whole of this front end, so the root goes there
    # rather than mounting admin.site twice, which breaks URL reversing.
    # Public, and deliberately so: Google requires the page its consent
    # screen links to be reachable without signing in, which every other
    # page of this console is not.
    path("privacy/", views.privacy, name="privacy"),
    path("terms/", views.terms, name="terms"),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
]
