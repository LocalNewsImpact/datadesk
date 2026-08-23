"""View guards, asked as privileges over datasets (ROADMAP item 1).

These replaced three guards named after roles — `role_required`,
`editor_required`, `admin_required` — which could only ask whether
somebody was an editor, and gave the same answer for every dataset.

**A guard asks two questions.** First, does this person hold the
privilege on *anything* in this application; a page with nothing to show
them is a page they should not reach. Second, if the request names a
dataset explicitly, may they see *that* one. Datadesk's views are
corpus-wide with an optional `?dataset=` filter rather than one page per
dataset, so the scope is a filter the reader chooses, not part of the
URL — and a filter naming a dataset they do not hold is refused here
rather than silently returning no rows.

**Narrowing the rows is not this file's job.** A guard opens the door;
the view still has to filter its queryset to `permitted_scopes(...)`.
The two fail differently and that is deliberate: a wrong guard denies
somebody and they say so within the hour, a missing filter shows them
another dataset's rows and nobody notices. Guards are the cheap half.

**Each guard marks the view it wraps** with the privilege it demanded, so
a test can walk the URL configuration and prove that every page behind a
navigation section actually carries a check. Hiding a link is not access
control; adding a page and forgetting the decorator fails the suite
rather than shipping.
"""

from functools import wraps

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.access import (
    has_privilege,
    has_privilege_anywhere,
    is_application_admin,
)
from accounts.models import DATADESK, SOURCES
from accounts.privileges import CREATE

#: Which application this process serves. `SERVICE_ROLE` already selects
#: the front end; a grant names the same thing, so the two agree without
#: a second setting.
APP = SOURCES if getattr(settings, "SERVICE_ROLE", "") == "sources" else DATADESK

#: The query parameter a reader chooses a dataset with. One name, here,
#: rather than repeated at each view that reads it.
DATASET_PARAM = "dataset"


def _selected_dataset(request):
    """The dataset the request explicitly asked for, if any."""
    return (request.GET.get(DATASET_PARAM) or "").strip()


def requires(privilege):
    """Hold `privilege` on some dataset — and on the selected one, if any.

    The everyday guard. `requires(READ)` is "you have something to look
    at here"; `requires(WRITE)` is "you have something to act on".
    """

    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not has_privilege_anywhere(request.user, APP, privilege):
                raise PermissionDenied(f"No dataset grants {privilege}")
            chosen = _selected_dataset(request)
            if chosen and not has_privilege(request.user, APP, privilege, chosen):
                # Refused rather than quietly emptied: a reader who picks a
                # dataset they cannot see should be told, not shown a page
                # that looks like the dataset is empty.
                raise PermissionDenied(f"No {privilege} on {chosen}")
            return view(request, *args, **kwargs)

        wrapped.required_privilege = privilege
        return wrapped

    return decorate


def requires_import(view):
    """Bring new rows into a dataset.

    A thin name over `requires(CREATE)`, kept because "import" is what the
    pages are called and the redirect message should say so. An import
    does not correct a record that exists, it adds records that did not --
    which is the whole of the reviewer/editor line.
    """
    return requires(CREATE)(view)


def requires_admin(view):
    """Application-wide administration.

    Unchanged in meaning: user administration and the audit log are not
    per dataset. An admin grant cannot name a dataset — the model refuses
    it — so this is application-wide by construction.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_application_admin(request.user, APP):
            raise PermissionDenied("Admin role required")
        return view(request, *args, **kwargs)

    wrapped.requires_admin = True
    return wrapped
