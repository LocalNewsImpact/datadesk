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
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
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


def _back_to(request, fallback):
    """Where a form said to return to, or the screen that usually owns it.

    The role and dataset endpoints are shared between the list and one
    person's page. Without this, changing a role from somebody's page
    landed on the list -- the right change, and then somewhere else.

    Only a path on this host: a redirect target taken from a form is one
    an attacker can write, and `url_has_allowed_host_and_scheme` is what
    stops it pointing somewhere off it.
    """
    from django.utils.http import url_has_allowed_host_and_scheme

    wanted = request.POST.get("next") or ""
    if wanted and url_has_allowed_host_and_scheme(
        wanted, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(wanted)
    return redirect(fallback)


def _dataset_grants(user):
    """The datasets this person holds, and the role on each.

    Counted before this and never listed, which meant a role given by an
    invitation could be created and then not changed, moved or taken
    away by anything in the interface -- the only remedies were
    withdrawing the invitation, which does not touch the grant, or a
    database shell.
    """
    return sorted(
        (
            {"scope": g.scope, "role": g.role, "label": g.get_role_display()}
            for g in user.grants.all()
            if g.app == APP and g.scope != WHOLE_APPLICATION
        ),
        key=lambda g: g["scope"],
    )


def _people():
    """Every account, with what it holds here, newest sign-in first."""
    User = get_user_model()
    return [
        {
            "user": user,
            "role": ADMIN if user.is_superuser else _application_role(user),
            "dataset_grants": _dataset_grant_count(user),
            "datasets": _dataset_grants(user),
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
    from accounts.mail import configured as mail_configured
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
            # Whether a set-password link can actually be sent. A screen
            # that promises one where nothing sends mail makes an account
            # nobody can reach.
            "mail_configured": mail_configured(),
            # Admin is missing on purpose: it is application-wide by
            # definition and the model refuses it with a scope, so
            # offering it here would offer a save that cannot happen.
            "dataset_roles": [
                (value, label) for value, label in ROLE_CHOICES if value != ADMIN
            ],
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
    from accounts.mail import configured

    link = request.build_absolute_uri(reverse("account_login"))
    if configured():
        _send_invitation(email, link, role, scope)
        messages.success(
            request,
            f"{email} was told, and may now sign in with Google. "
            f"They will hold {role} on {scope}.",
        )
    else:
        # Shown rather than swallowed, the way add_account already does it:
        # an invitation nobody was told about is an invitation nobody uses.
        messages.success(
            request,
            f"{email} may now sign in with Google and will hold {role} on "
            f"{scope} — but mail is not configured here, so tell them "
            f"yourself: {link}",
        )
    return redirect("accounts:users")


@requires_admin
def person(request, user_id):
    """One account, and everything an admin can do to it.

    The list answers "who can sign in"; this answers "what about this
    one". Spreading a person's role, their datasets, their address and
    their password across three screens meant an admin correcting a
    mistake had to know which screen each half lived on.
    """
    from accounts.mail import configured as mail_configured
    from accounts.models import Invitation
    from accounts.privileges import DESIGNER, ROLE_CHOICES

    User = get_user_model()
    target = User.objects.filter(pk=user_id).prefetch_related("grants").first()
    if target is None:
        raise Http404("No such account")

    return render(
        request,
        "accounts/person.html",
        {
            "person": {
                "user": target,
                "role": ADMIN if target.is_superuser else _application_role(target),
                "datasets": _dataset_grants(target),
                "locked": target.is_superuser,
                # How they get in, which decides what can be done about a
                # lost password: a Google account has none to reset.
                "password": target.has_usable_password(),
                "google": (
                    target.socialaccount_set.exists()
                    if hasattr(target, "socialaccount_set")
                    else False
                ),
                "invitation": Invitation.for_email(target.email or ""),
            },
            "roles": ROLE_CHOICES,
            "dataset_roles": [
                (value, label) for value, label in ROLE_CHOICES if value != ADMIN
            ],
            "default_role": DESIGNER,
            "datasets": _invitable_datasets(),
            "mail_configured": mail_configured(),
            "is_self": target.pk == request.user.pk,
        },
    )


@requires_admin
@require_POST
def set_name(request, user_id):
    """What to call somebody.

    Google fills these in on first sign-in, but a password account made in
    the admin has nothing to fill them from, and allauth's generated
    username -- an email local part with a number stuck on the end to make
    it unique -- is not a name anybody chose. "damon6" is what the console
    called the person who owns it.

    Both halves stored, first shown. A surname is what tells two Damons
    apart on the Roles page, and a first name is what a person is called
    everywhere else.
    """
    User = get_user_model()
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        raise Http404("No such account")

    first = (request.POST.get("first_name") or "").strip()[:150]
    last = (request.POST.get("last_name") or "").strip()[:150]
    if not first:
        messages.error(request, "A first name at least.")
        return redirect("accounts:person", user_id=user_id)

    was = f"{target.first_name} {target.last_name}".strip()
    target.first_name, target.last_name = first, last
    target.save(update_fields=["first_name", "last_name"])
    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:name_set",
        target_table="auth_user",
        target_ids=[str(target.pk)],
        before={"name": was},
        after={"name": f"{first} {last}".strip()},
        reason=f"named {target.email or target.username} {first} {last}".strip(),
    )
    messages.success(request, f"Called {first} {last}".strip() + " from now on.")
    return redirect("accounts:person", user_id=user_id)


@requires_admin
@require_POST
def set_email(request, user_id):
    """Change the address an account signs in with.

    Not cosmetic: Google sign-in matches an account by its verified
    address, and an invitation is held by address too. Changing it moves
    both, so it is audited with what it was.
    """
    User = get_user_model()
    target = User.objects.filter(pk=user_id).first()
    email = (request.POST.get("email") or "").strip().lower()

    if target is None:
        raise Http404("No such account")
    if "@" not in email:
        messages.error(request, "That is not an address.")
        return redirect("accounts:person", user_id=user_id)
    if User.objects.filter(email__iexact=email).exclude(pk=target.pk).exists():
        messages.error(request, f"{email} is already another account's address.")
        return redirect("accounts:person", user_id=user_id)

    was = target.email
    if was and was.lower() == email:
        messages.error(request, "That is already the address.")
        return redirect("accounts:person", user_id=user_id)

    target.email = email
    target.save(update_fields=["email"])
    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:email_changed",
        target_table="auth_user",
        target_ids=[str(target.pk)],
        before={"email": was},
        after={"email": email},
        reason=f"changed {was or 'a blank address'} to {email}",
    )
    messages.success(
        request,
        f"Address changed to {email}. They sign in with that from now on.",
    )
    return redirect("accounts:person", user_id=user_id)


@requires_admin
@require_POST
def send_password_link(request, user_id):
    """A fresh set-password link, for somebody who has lost theirs.

    The password is never seen here, the same as when the account was
    made: the link sets it and the only person who knows it is the person
    using it. Where mail is unconfigured the link is shown once instead,
    because an admin who cannot see it cannot help.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from accounts.mail import configured

    User = get_user_model()
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        raise Http404("No such account")
    if not target.email:
        messages.error(request, "That account has no address to send to.")
        return redirect("accounts:person", user_id=user_id)

    link = request.build_absolute_uri(
        reverse(
            "set_password",
            args=[
                urlsafe_base64_encode(force_bytes(target.pk)),
                default_token_generator.make_token(target),
            ],
        )
    )
    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:password_link_sent",
        target_table="auth_user",
        target_ids=[str(target.pk)],
        reason=f"sent {target.email} a link to set a new password",
    )
    if configured():
        _send_set_password(target, link)
        messages.success(request, f"A link was sent to {target.email}.")
    else:
        messages.success(
            request,
            f"Mail is not configured here, so send them this yourself: {link}",
        )
    return redirect("accounts:person", user_id=user_id)


@requires_admin
@require_POST
def set_active(request, user_id):
    """Turn an account off without deleting it.

    Deleting takes the audit trail's subject with it. Disabling keeps the
    record of what they did and stops them signing in, which is what
    "they have left" actually means.
    """
    User = get_user_model()
    target = User.objects.filter(pk=user_id).first()
    if target is None:
        raise Http404("No such account")
    wanted = request.POST.get("active") == "1"

    if target.pk == request.user.pk and not wanted:
        # The same failure the role screen refuses: an admin locking
        # themselves out, and with them possibly the last admin.
        messages.error(request, "You cannot disable your own account.")
        return redirect("accounts:person", user_id=user_id)
    if target.is_active == wanted:
        messages.error(request, "It is already like that.")
        return redirect("accounts:person", user_id=user_id)

    target.is_active = wanted
    target.save(update_fields=["is_active"])
    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:enabled" if wanted else "accounts:disabled",
        target_table="auth_user",
        target_ids=[str(target.pk)],
        after={"is_active": wanted},
        reason=("enabled " if wanted else "disabled ")
        + (target.email or target.username),
    )
    messages.success(
        request,
        f"{target.email or target.username} "
        + ("can sign in again." if wanted else "can no longer sign in."),
    )
    return redirect("accounts:person", user_id=user_id)


@requires_admin
@require_POST
def set_dataset_grant(request):
    """Give, change or take away one person's role on one dataset.

    One endpoint for all three, because they are one decision with
    different answers: an empty role means "none", which is the only
    way to take a dataset away without taking the account with it.

    Application-wide roles are not touched here. Those are Roles, and an
    admin grant cannot name a dataset -- the model refuses it -- so the
    two cannot be confused for each other.
    """
    from accounts.privileges import ADMIN as ADMIN_ROLE
    from accounts.privileges import ROLES

    User = get_user_model()
    target = User.objects.filter(pk=request.POST.get("user_id")).first()
    scope = (request.POST.get("scope") or "").strip()
    role = (request.POST.get("role") or "").strip()

    if target is None:
        messages.error(request, "No such account.")
        return _back_to(request, "accounts:users")
    if target.is_superuser:
        # A superuser holds everything from the account flag rather than
        # from a grant, so a dataset row would be a decoration that
        # changes nothing.
        messages.error(request, f"{target.email} is a superuser; grants do not apply.")
        return _back_to(request, "accounts:users")
    if not scope:
        messages.error(request, "Choose a dataset.")
        return _back_to(request, "accounts:users")
    if role and role not in ROLES:
        messages.error(request, f"{role} is not a role.")
        return _back_to(request, "accounts:users")
    if role == ADMIN_ROLE:
        # The model refuses it, and saying why is better than an
        # IntegrityError: admin means the whole application by
        # definition, so an admin grant naming one dataset reads as
        # "everything, but only here".
        messages.error(
            request,
            "Admin is application-wide by definition; give it on Roles instead.",
        )
        return _back_to(request, "accounts:users")

    held = Grant.objects.filter(user=target, app=APP, scope=scope).first()
    before = held.role if held else None

    if not role:
        if held is None:
            messages.error(request, f"{target.email} holds nothing on {scope}.")
            return _back_to(request, "accounts:users")
        held.delete()
        said = f"took {scope} away from {target.email}"
    elif held is None:
        Grant.objects.create(
            user=target, app=APP, scope=scope, role=role, granted_by=request.user
        )
        said = f"gave {target.email} {role} on {scope}"
    elif held.role == role:
        messages.error(request, f"{target.email} already holds {role} on {scope}.")
        return _back_to(request, "accounts:users")
    else:
        held.role = role
        held.granted_by = request.user
        held.save(update_fields=["role", "granted_by"])
        said = f"changed {target.email} from {before} to {role} on {scope}"

    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:dataset_grant",
        target_table="accounts_grant",
        target_ids=[f"{target.email or target.username}:{scope}"],
        before={"role": before} if before else None,
        after={"role": role} if role else None,
        reason=said,
    )
    messages.success(request, said.capitalize() + ".")
    return _back_to(request, "accounts:users")


@requires_admin
@require_POST
def add_account(request):
    """Create an account for somebody who does not sign in with Google.

    The same person, the same `User` row and the same grants as everybody
    else; only the door differs. An institution that does not use Google,
    or a contractor, could otherwise not have an account at all.

    The password is never set here. The account is made with an unusable
    one and a set-password link is sent, so the only person who ever knows
    it is the person using it -- an admin typing a password into a form is
    an admin who knows a password they should not.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    from accounts.mail import configured
    from accounts.privileges import DESIGNER, ROLES

    User = get_user_model()
    email = (request.POST.get("email") or "").strip().lower()
    scope = (request.POST.get("scope") or "").strip()
    role = (request.POST.get("role") or DESIGNER).strip()

    if "@" not in email:
        messages.error(request, "That is not an address.")
        return redirect("accounts:users")
    if not scope:
        messages.error(request, "Choose the dataset they are being given.")
        return redirect("accounts:users")
    if role not in ROLES:
        messages.error(request, f"{role} is not a role.")
        return redirect("accounts:users")
    if User.objects.filter(email__iexact=email).exists():
        messages.error(request, f"{email} already has an account.")
        return redirect("accounts:users")

    person = User.objects.create(username=email[:150], email=email)
    # Unusable rather than blank: a blank password is a password, and
    # `set_unusable_password` is what makes the login form refuse until
    # they have set one of their own.
    person.set_unusable_password()
    person.save(update_fields=["password"])

    Grant.objects.create(
        user=person, app=APP, scope=scope, role=role, granted_by=request.user
    )
    AuditLogEntry.objects.create(
        actor=request.user,
        action="accounts:account_created",
        target_table="auth_user",
        target_ids=[email],
        after={"scope": scope, "role": role},
        reason=f"created an account for {email} as {role} on {scope}",
    )

    link = request.build_absolute_uri(
        reverse(
            "set_password",
            args=[
                urlsafe_base64_encode(force_bytes(person.pk)),
                default_token_generator.make_token(person),
            ],
        )
    )
    if configured():
        _send_set_password(person, link)
        messages.success(
            request,
            f"{email} can now set a password from the link sent to them. "
            f"They hold {role} on {scope}.",
        )
    else:
        # Shown rather than swallowed. Mail unconfigured is a state a
        # local console is always in, and an admin who cannot see the
        # link has made an account nobody can reach.
        request.session["proposal_receipt"] = None
        messages.success(
            request,
            f"{email} holds {role} on {scope}. Mail is not configured here, "
            f"so send them this link yourself: {link}",
        )
    return redirect("accounts:users")


def set_password(request, uidb64, token):
    """Where the link lands: choose a password, then sign in.

    Django's own token generator, so the link expires on its own and is
    spent once the password changes. No sign-in required to reach it --
    the whole point is that they cannot sign in yet.
    """
    from django.contrib.auth.forms import SetPasswordForm
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    User = get_user_model()
    try:
        person = User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        person = None

    if person is None or not default_token_generator.check_token(person, token):
        return render(request, "accounts/set_password.html", {"spent": True})

    if request.method == "POST":
        form = SetPasswordForm(person, request.POST)
        if form.is_valid():
            form.save()
            AuditLogEntry.objects.create(
                actor=person,
                action="accounts:password_set",
                target_table="auth_user",
                target_ids=[person.email or person.username],
                reason=f"{person.email or person.username} set their password",
            )
            messages.success(request, "Password set. Sign in with it.")
            return redirect("account_login")
    else:
        form = SetPasswordForm(person)
    return render(
        request, "accounts/set_password.html", {"form": form, "person": person}
    )


def _send_invitation(email, link, role, scope):
    """Tell the person who was invited.

    The invite screen wrote the row and told the admin "they may now sign
    in with Google", which is true and reaches nobody who needs to know
    it. Two people were invited on 2026-08-27 and neither was told; the
    invitations were live the whole time.

    No password link here: an invitation admits a Google account, and
    there is nothing for them to set.
    """
    from django.core.mail import send_mail

    send_mail(
        subject="You have been added to Datadesk",
        message=(
            "You have been given access to Datadesk, the Local News Impact "
            "Consortium's research console.\n\n"
            f"Sign in with Google, using this address:\n{link}\n\n"
            f"You will hold {role} on {scope}.\n\n"
            "If the address you are reading this at has no Google account, "
            "say so to whoever invited you — there is another way in that "
            "does not need one.\n"
        ),
        from_email=None,
        recipient_list=[email],
        fail_silently=False,
    )


def _send_set_password(person, link):
    """The other message this application sends."""
    from django.core.mail import send_mail

    send_mail(
        subject="Your Datadesk account",
        message=(
            "An account has been made for you on Datadesk, the Local News "
            "Impact Consortium's research console.\n\n"
            f"Set a password and sign in:\n{link}\n\n"
            "The link can be used once. If it has expired, ask whoever "
            "invited you to send another.\n"
        ),
        from_email=None,
        recipient_list=[person.email],
        fail_silently=False,
    )


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
        return _back_to(request, "accounts:roles")

    target = User.objects.filter(pk=user_id).first()
    if target is None:
        messages.error(request, "No such account.")
        return _back_to(request, "accounts:roles")

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
        return _back_to(request, "accounts:roles")

    if target.is_superuser:
        messages.error(
            request,
            "A superuser holds every role from the account flag, "
            "not a grant, and is changed in the Django admin.",
        )
        return _back_to(request, "accounts:roles")

    if previous == new_role:
        return _back_to(request, "accounts:roles")

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
    return _back_to(request, "accounts:roles")
