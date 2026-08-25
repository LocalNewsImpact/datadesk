"""User and role administration (SCOPE.md §2.1). Admin only.

Role assignment is a mutating action, so it goes through the same
append-only audit log as every other write: actor, target, before and
after (SCOPE.md §2.1).

**This screen grants a role across the whole application**, which is what
the three role groups did before it. ROADMAP item 1 also allows a role on
a single dataset, and that wants a richer screen than a column of radio
buttons — so a person's dataset-level grants are *shown* here and are not
editable here. The count is displayed rather than hidden, because a
screen that said "no role" about somebody who edits one dataset would be
lying.
"""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import APP, requires_admin
from accounts.models import WHOLE_APPLICATION, Grant
from accounts.privileges import ADMIN, ROLES
from audit.models import AuditLogEntry


def _application_role(user):
    """The role this person holds over the whole application, if any."""
    grant = next(
        (g for g in user.grants.all() if g.app == APP and g.scope == WHOLE_APPLICATION),
        None,
    )
    return grant.role if grant else None


def _dataset_grant_count(user):
    """How many single-dataset grants this person holds here."""
    return sum(
        1 for g in user.grants.all() if g.app == APP and g.scope != WHOLE_APPLICATION
    )


def _people():
    """Every account, with what it holds here, newest sign-in first."""
    User = get_user_model()
    return [
        {
            "user": user,
            "role": ADMIN if user.is_superuser else _application_role(user),
            "dataset_grants": _dataset_grant_count(user),
            # A superuser holds everything from the account flag, not a
            # grant, and cannot be changed here.
            "locked": user.is_superuser,
        }
        for user in User.objects.prefetch_related("grants").order_by(
            "-last_login", "email", "username"
        )
    ]


@requires_admin
def users(request):
    """Who can sign in, their role, and when they last did.

    And who may sign in but has not yet: an invitation is a decision
    already taken, so it belongs on the list of who can get in rather
    than somewhere else.
    """
    from accounts.models import Invitation
    from accounts.privileges import DESIGNER, ROLE_CHOICES

    return render(
        request,
        "accounts/users.html",
        {
            "people": _people(),
            "invitations": Invitation.objects.all(),
            "datasets": _invitable_datasets(),
            "roles": ROLE_CHOICES,
            "default_role": DESIGNER,
            "domains": settings.ALLOWED_AUTH_DOMAINS,
        },
    )


def _invitable_datasets():
    """Datasets an invitation can name, or [] where the corpus is
    unreachable -- an invite screen that cannot list them should say so
    rather than offer a free-text box that admits a typo."""
    from django.db import DatabaseError

    try:
        from explorer.models import Dataset

        return list(Dataset.objects.order_by("slug").values("slug", "label"))
    except DatabaseError:
        return []


@requires_admin
@require_POST
def invite(request):
    """Admit one address from outside the organisation.

    A dataset is required, because an invitation admits somebody to work
    on something: one naming no dataset would admit them to a console
    with nothing in it, which reads as a broken sign-in rather than as a
    grant nobody made.
    """
    from accounts.models import Invitation
    from accounts.privileges import DESIGNER, ROLES

    email = (request.POST.get("email") or "").strip().lower()
    scope = (request.POST.get("scope") or "").strip()
    role = (request.POST.get("role") or DESIGNER).strip()

    if "@" not in email:
        messages.error(request, "That is not an address.")
        return redirect("accounts:users")
    if not scope:
        messages.error(request, "Choose the dataset they are being invited to.")
        return redirect("accounts:users")
    if role not in ROLES:
        messages.error(request, f"{role} is not a role.")
        return redirect("accounts:users")
    if any(email.endswith(f"@{d.lower()}") for d in settings.ALLOWED_AUTH_DOMAINS):
        # They can already sign in. An invitation would be a second answer
        # to a question the domain has already answered.
        messages.error(
            request, f"{email} can already sign in; give them a role on Roles."
        )
        return redirect("accounts:users")

    invitation, made = Invitation.objects.get_or_create(
        email=email,
        defaults={"scope": scope, "role": role, "invited_by": request.user},
    )
    if not made:
        messages.error(request, f"{email} is already invited.")
        return redirect("accounts:users")

    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:invited",
        target_table="accounts_invitation",
        target_ids=[email],
        after={"scope": scope, "role": role},
        reason=f"invited {email} as {role} on {scope}",
    )
    messages.success(
        request,
        f"{email} may now sign in with Google. They will hold {role} on {scope}.",
    )
    return redirect("accounts:users")


@requires_admin
@require_POST
def uninvite(request):
    """Withdraw an invitation.

    The grant it made is not withdrawn with it -- a role somebody holds
    is its own decision, changed on Roles -- but without the invitation
    they cannot sign in again, which is the door this screen controls.
    """
    from accounts.models import Invitation

    email = (request.POST.get("email") or "").strip().lower()
    invitation = Invitation.for_email(email)
    if invitation is None:
        messages.error(request, f"No invitation for {email}.")
        return redirect("accounts:users")
    invitation.delete()
    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:uninvited",
        target_table="accounts_invitation",
        target_ids=[email],
        reason=f"withdrew the invitation for {email}",
    )
    messages.success(
        request,
        f"{email} can no longer sign in. Any role they hold is unchanged; "
        "change that on Roles.",
    )
    return redirect("accounts:users")


@requires_admin
def roles(request):
    """Role assignment. Self-demotion is refused, which is also what
    keeps at least one admin in place: an admin may demote another admin
    only while remaining one themselves."""
    return render(
        request,
        "accounts/roles.html",
        {"people": _people(), "roles": ROLES},
    )


@requires_admin
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

    previous = ADMIN if target.is_superuser else _application_role(target)

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
            "A superuser holds every role from the account flag, "
            "not a grant, and is changed in the Django admin.",
        )
        return redirect("accounts:roles")

    if previous == new_role:
        return redirect("accounts:roles")

    # One row per person per scope, so this replaces rather than adds --
    # the model refuses a second application-wide grant anyway.
    Grant.objects.filter(user=target, app=APP, scope=WHOLE_APPLICATION).delete()
    if new_role:
        Grant.objects.create(
            user=target,
            app=APP,
            scope=WHOLE_APPLICATION,
            role=new_role,
            granted_by=request.user,
        )

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
