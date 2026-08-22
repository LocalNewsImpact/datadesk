"""View guards for the three-role model (SCOPE.md §2.1).

Each guard marks the view it wraps (`requires_role`, `requires_editor`,
`requires_admin`). Hiding a navigation link is not access control, so the
marks let a test walk the URL configuration and prove that every view
behind an admin section actually carries the check — adding a page and
forgetting the decorator fails the suite rather than shipping.
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from accounts.roles import ADMIN, EDITOR, role_for_user


def role_required(view):
    """Any assigned role may enter; none assigned may not.

    Anonymous users go to sign-in; an authenticated user with no role gets
    403 — new sign-ins have no role until one is assigned in the admin,
    and the landing page already tells them so.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if role_for_user(request.user) is None:
            raise PermissionDenied("No role assigned")
        return view(request, *args, **kwargs)

    wrapped.requires_role = True
    return wrapped


def editor_required(view):
    """Review/cleanup actions are for editors and admins (SCOPE.md §2.1);
    viewers browse."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if role_for_user(request.user) not in (EDITOR, ADMIN):
            raise PermissionDenied("Editor role required")
        return view(request, *args, **kwargs)

    wrapped.requires_editor = True
    return wrapped


def admin_required(view):
    """Dataset management and destructive actions are admin-only
    (SCOPE.md §2.1)."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if role_for_user(request.user) != ADMIN:
            raise PermissionDenied("Admin role required")
        return view(request, *args, **kwargs)

    wrapped.requires_admin = True
    return wrapped
