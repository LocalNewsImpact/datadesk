"""User and role administration (SCOPE.md §2.1). Admin role only.

Role assignment is a mutating action, so it goes through the same
append-only audit log as every other write: actor, target, before and
after (SCOPE.md §2.1).
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from accounts.roles import ADMIN, ROLES, role_for_user
from audit.models import AuditLogEntry


def _people():
    """Every account, with its role, newest sign-in first."""
    User = get_user_model()
    return [
        {
            "user": user,
            "role": role_for_user(user),
            # A superuser's admin role comes from the flag, not a group,
            # and cannot be changed here.
            "locked": user.is_superuser,
        }
        for user in User.objects.prefetch_related("groups").order_by(
            "-last_login", "email", "username"
        )
    ]


@admin_required
def users(request):
    """Who can sign in, their role, and when they last did."""
    return render(request, "accounts/users.html", {"people": _people()})


@admin_required
def roles(request):
    """Role assignment. Self-demotion is refused, which is also what
    keeps at least one admin in place: an admin may demote another admin
    only while remaining one themselves."""
    return render(
        request,
        "accounts/roles.html",
        {"people": _people(), "roles": ROLES},
    )


@admin_required
@require_POST
def set_role(request):
    """Move one account between the role groups, audited."""
    User = get_user_model()
    user_id = request.POST.get("user_id")
    new_role = request.POST.get("role") or ""

    if new_role and new_role not in ROLES:
        messages.error(request, f"{new_role} is not a role.")
        return redirect("accounts:roles")

    target = User.objects.filter(pk=user_id).first()
    if target is None:
        messages.error(request, "No such account.")
        return redirect("accounts:roles")

    previous = role_for_user(target)

    # The classic failure is an admin locking themselves out, and with it
    # the last admin locking out the console. Refusing self-demotion
    # prevents both: whoever makes a change keeps their own admin role, so
    # an admin always remains.
    if target.pk == request.user.pk and previous == ADMIN and new_role != ADMIN:
        messages.error(
            request,
            "An admin cannot remove their own admin role. "
            "Ask another admin to make the change.",
        )
        return redirect("accounts:roles")

    if target.is_superuser:
        messages.error(
            request,
            "A superuser's admin role comes from the account flag, "
            "not a group, and is changed in the Django admin.",
        )
        return redirect("accounts:roles")

    if previous == new_role:
        return redirect("accounts:roles")

    target.groups.remove(*Group.objects.filter(name__in=ROLES))
    if new_role:
        target.groups.add(Group.objects.get(name=new_role))

    AuditLogEntry.objects.create(
        actor=request.user,
        action="role_change",
        target_table="auth_user",
        target_ids=[str(target.pk)],
        before={"role": previous},
        after={"role": new_role or None},
        reason=request.POST.get("reason", ""),
    )
    messages.success(
        request,
        f"{target.email or target.username}: "
        f"{previous or 'no role'} → {new_role or 'no role'}.",
    )
    return redirect("accounts:roles")
