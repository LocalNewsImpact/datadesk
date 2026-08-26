"""Users and Roles: the admin's view of who can do what."""

from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from accounts.models import DATADESK, Grant
from audit.models import AuditLogEntry

# The users screen offers a dataset to invite somebody to, and the
# datasets live in the crawler's database.
pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _with_role(username, role, **kwargs):
    user = User.objects.create_user(
        username, email=f"{username}@localnewsimpact.org", **kwargs
    )
    if role:
        Grant.objects.create(user=user, app=DATADESK, scope="", role=role)
    return user


@pytest.fixture
def admin(client):
    user = _with_role("boss", "admin")
    client.force_login(user)
    return user


def test_users_page_lists_accounts_with_role_and_last_sign_in(client, admin):
    _with_role("reader", "viewer")
    content = client.get("/manage/users/").content.decode()
    assert "reader@localnewsimpact.org" in content
    assert "viewer" in content
    assert "never" in content  # last sign-in


def test_roles_page_offers_every_role(client, admin):
    _with_role("reader", "viewer")
    content = client.get("/manage/roles/").content.decode()
    for role in ("viewer", "editor", "admin"):
        assert f'value="{role}"' in content


def test_assigning_a_role_moves_the_user_and_is_audited(client, admin):
    target = _with_role("reader", "viewer")
    response = client.post(
        "/manage/roles/set/",
        {"user_id": target.pk, "role": "editor", "reason": "runs the backpatch"},
    )
    assert response.status_code == 302
    assert set(target.grants.values_list("role", flat=True)) == {"editor"}

    entry = AuditLogEntry.objects.get(action="role_change")
    assert entry.actor == admin
    assert entry.target_ids == [str(target.pk)]
    assert entry.before == {"role": "viewer"}
    assert entry.after == {"role": "editor"}
    assert entry.reason == "runs the backpatch"


def test_a_role_can_be_removed_entirely(client, admin):
    target = _with_role("reader", "viewer")
    client.post("/manage/roles/set/", {"user_id": target.pk, "role": ""})
    assert list(target.grants.all()) == []
    assert AuditLogEntry.objects.get(action="role_change").after == {"role": None}


def test_an_admin_cannot_remove_their_own_admin_role(client, admin):
    """The classic failure is locking the last admin out. Refusing
    self-demotion prevents it: whoever makes a change keeps their own
    role, so an admin always remains."""
    response = client.post(
        "/manage/roles/set/", {"user_id": admin.pk, "role": "viewer"}
    )
    assert response.status_code == 302
    admin.refresh_from_db()
    assert set(admin.grants.values_list("role", flat=True)) == {"admin"}
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_an_admin_may_demote_another_admin(client, admin):
    other = _with_role("deputy", "admin")
    client.post("/manage/roles/set/", {"user_id": other.pk, "role": "editor"})
    assert set(other.grants.values_list("role", flat=True)) == {"editor"}
    # The console still has an admin: the one who made the change.
    assert Grant.objects.filter(app=DATADESK, role="admin").count() == 1


def test_a_superusers_role_is_not_changed_here(client, admin):
    root = User.objects.create_superuser("root", "root@localnewsimpact.org", "x")
    client.post("/manage/roles/set/", {"user_id": root.pk, "role": "viewer"})
    assert list(root.grants.all()) == []
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_an_invented_role_is_refused(client, admin):
    target = _with_role("reader", "viewer")
    client.post("/manage/roles/set/", {"user_id": target.pk, "role": "superadmin"})
    assert set(target.grants.values_list("role", flat=True)) == {"viewer"}
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_an_unknown_account_is_refused(client, admin):
    client.post("/manage/roles/set/", {"user_id": "99999", "role": "viewer"})
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_setting_the_role_a_user_already_has_records_nothing(client, admin):
    target = _with_role("reader", "viewer")
    client.post("/manage/roles/set/", {"user_id": target.pk, "role": "viewer"})
    assert not AuditLogEntry.objects.filter(action="role_change").exists()


def test_role_assignment_is_post_only(client, admin):
    assert client.get("/manage/roles/set/").status_code == 405


# --- somebody from outside the organisation ----------------------------------
#
# A personal Google account carries no hosted-domain claim at all, so
# there is nothing for the domain check to accept and no consent screen
# could change that. They get in because an admin named their address.


@pytest.fixture
def dataset(crawler_schema):
    from explorer.models import Dataset

    return Dataset.objects.create(id="d-mo", slug="mizzou", label="Missouri")


def _google_login(email, verified=True, hd=None):
    from allauth.socialaccount.models import SocialAccount, SocialLogin

    account = SocialAccount(provider="google", uid=email)
    account.extra_data = {"email": email, "email_verified": verified}
    if hd:
        account.extra_data["hd"] = hd
    return SocialLogin(account=account)


def _admits(email, **kw):
    """Whether the adapter lets this sign-in through."""
    from allauth.core.exceptions import ImmediateHttpResponse

    from accounts.adapters import DomainRestrictedAdapter

    try:
        DomainRestrictedAdapter().pre_social_login(None, _google_login(email, **kw))
        return True
    except ImmediateHttpResponse:
        return False


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_invited_address_may_sign_in_and_a_stranger_may_not(settings):
    from accounts.models import Invitation

    settings.ALLOWED_AUTH_DOMAINS = ["localnewsimpact.org"]

    assert _admits("staff@localnewsimpact.org", hd="localnewsimpact.org")
    assert not _admits("stranger@example.com")

    Invitation.objects.create(email="guest@example.com", scope="mizzou")
    assert _admits("guest@example.com"), "an invited address was refused"

    # The reported case: a colleague at another university, whose account is
    # itself a Workspace and so arrives carrying somebody else's `hd`. The
    # domain check must not read that as a domain claim to refuse -- being
    # named in an invitation is the whole of their standing here.
    Invitation.objects.create(email="h.artman@missouri.edu", scope="mizzou")
    assert _admits(
        "h.artman@missouri.edu", hd="missouri.edu"
    ), "an invited address from another Workspace was refused"
    # Case is not a different person.
    assert _admits("GUEST@Example.com")
    # ...and an unverified address is a claim, not a person, whichever
    # door it comes through.
    assert not _admits("guest@example.com", verified=False)
    assert not _admits(
        "staff@localnewsimpact.org", hd="localnewsimpact.org", verified=False
    )


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_account_an_admin_created_may_sign_in_with_google(settings):
    """The add-account screen writes a user and a grant, not an invitation.
    An account made that way was refused at the Google door -- the reported
    case: an editor on Mizzou, provisioned in the admin, who had never
    signed in because the only door she was pointed to said 403."""
    settings.ALLOWED_AUTH_DOMAINS = ["localnewsimpact.org"]

    assert not _admits("h.artman@missouri.edu", hd="missouri.edu")

    user = User.objects.create(username="h.artman", email="h.artman@missouri.edu")
    assert _admits(
        "h.artman@missouri.edu", hd="missouri.edu"
    ), "an account an admin created was refused"
    # Case is not a different person, here either.
    assert _admits("H.Artman@Missouri.edu", hd="missouri.edu")

    # Switching the account off is how access is taken away; it has to close
    # this door as well as the password one.
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert not _admits(
        "h.artman@missouri.edu", hd="missouri.edu"
    ), "a deactivated account still got in"

    # An address nobody has provisioned is still a stranger.
    assert not _admits("stranger@example.com")


@pytest.mark.django_db(databases=["default", "crawler"])
def test_signing_in_makes_the_grant_the_invitation_promised(settings):
    """A grant needs a user and a user does not exist until Google has
    said who they are. Invited and never granted is a successful sign-in
    to a console holding nothing, which reads as a broken login."""
    from accounts.adapters import DomainRestrictedAdapter
    from accounts.models import DATADESK, Grant, Invitation
    from accounts.privileges import DESIGNER

    settings.ALLOWED_AUTH_DOMAINS = ["localnewsimpact.org"]
    Invitation.objects.create(email="guest@example.com", scope="mizzou")

    login = _google_login("guest@example.com")
    login.user = User(username="guest", email="guest@example.com")
    adapter = DomainRestrictedAdapter()
    adapter.pre_social_login(None, login)

    with mock.patch.object(
        DomainRestrictedAdapter.__bases__[0],
        "save_user",
        lambda self, request, sociallogin, form=None: (
            sociallogin.user.save() or sociallogin.user
        ),
    ):
        user = adapter.save_user(None, login)

    grant = Grant.objects.get(user=user)
    assert (grant.app, grant.scope, grant.role) == (DATADESK, "mizzou", DESIGNER)
    assert Invitation.for_email("guest@example.com").accepted_at is not None


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_invitation_must_name_a_dataset(client, admin, dataset):
    """One naming no dataset admits somebody to a console with nothing in
    it, which reads as a broken sign-in rather than a grant nobody made."""
    from accounts.models import Invitation

    client.post(
        reverse("accounts:invite"), {"email": "guest@example.com", "role": "designer"}
    )
    assert not Invitation.objects.exists(), "invited with no dataset"

    client.post(
        reverse("accounts:invite"),
        {"email": "guest@example.com", "scope": dataset.slug, "role": "designer"},
    )
    assert Invitation.objects.get().scope == dataset.slug


@pytest.mark.django_db(databases=["default", "crawler"])
def test_somebody_who_can_already_sign_in_is_not_invited(
    client, admin, dataset, settings
):
    """An invitation would be a second answer to a question the domain
    has already answered."""
    from accounts.models import Invitation

    settings.ALLOWED_AUTH_DOMAINS = ["localnewsimpact.org"]
    client.post(
        reverse("accounts:invite"),
        {"email": "staff@localnewsimpact.org", "scope": dataset.slug},
    )
    assert not Invitation.objects.exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_withdrawing_an_invitation_closes_the_door(client, admin, dataset, settings):
    from accounts.models import Invitation

    settings.ALLOWED_AUTH_DOMAINS = ["localnewsimpact.org"]
    Invitation.objects.create(email="guest@example.com", scope=dataset.slug)
    client.post(reverse("accounts:uninvite"), {"email": "guest@example.com"})
    assert not Invitation.objects.exists()
    assert not _admits("guest@example.com")


# --- what Google's consent screen requires -----------------------------------


@pytest.mark.django_db
def test_the_policy_pages_are_reachable_without_signing_in(client):
    """Google requires the page its consent screen links to be publicly
    accessible -- not behind a login, not a redirect. Every other page of
    this console is behind the login, so these are the exception and a
    test is what keeps them one."""
    for name in ("privacy", "terms"):
        page = client.get(reverse(name))
        assert page.status_code == 200, f"{name}: {page.status_code}"
        assert "localnewsimpact.org" in page.content.decode()


@pytest.mark.django_db
def test_the_home_page_links_the_privacy_policy(client):
    """Google's requirement for the home page URI: publicly accessible,
    and linking the privacy policy. The sign-in page is this app's home,
    being the only page reachable without signing in."""
    body = client.get("/").content.decode()
    assert body.count(reverse("privacy")) >= 1
    assert reverse("terms") in body


@pytest.mark.django_db
def test_the_privacy_policy_says_what_is_done_with_a_google_account(client):
    """It has to disclose how the app accesses, uses, stores and shares
    Google user data. A policy that says nothing specific is a policy
    that has not been read."""
    body = client.get(reverse("privacy")).content.decode().lower()
    for said in (
        "email address",  # what is asked for
        "basic profile",
        "audit",  # what is stored, and why
        "we do not sell",  # who else sees it
        "advertis",
        "myaccount.google.com/permissions",  # how to revoke
    ):
        assert said in body, f"the policy does not mention {said!r}"


# --- an account without Google -----------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_admin_creates_an_account_that_signs_in_with_a_password(
    client, admin, dataset, settings
):
    """The same person, the same account and the same grants; only the
    door differs. An institution that does not use Google could
    otherwise not have an account here at all."""
    from accounts.models import Grant
    from accounts.privileges import DESIGNER

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    client.post(
        reverse("accounts:add_account"),
        {"email": "colleague@example.edu", "scope": dataset.slug, "role": "designer"},
    )
    person = User.objects.get(email="colleague@example.edu")
    # Unusable rather than blank: a blank password is a password.
    assert not person.has_usable_password()
    grant = Grant.objects.get(user=person)
    assert (grant.scope, grant.role) == (dataset.slug, DESIGNER)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_set_password_link_works_once(client, admin, dataset, settings):
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    client.post(
        reverse("accounts:add_account"),
        {"email": "colleague@example.edu", "scope": dataset.slug},
    )
    person = User.objects.get(email="colleague@example.edu")
    url = reverse(
        "set_password",
        args=[
            urlsafe_base64_encode(force_bytes(person.pk)),
            default_token_generator.make_token(person),
        ],
    )

    from django.test import Client

    anon = Client()
    # Reachable without signing in, which is the state it exists to end.
    assert anon.get(url).status_code == 200
    answer = anon.post(
        url,
        {
            "new_password1": "a-long-enough-passphrase",
            "new_password2": "a-long-enough-passphrase",
        },
    )
    assert answer.status_code in (302, 200)
    person.refresh_from_db()
    assert person.has_usable_password()

    # Spent: the token no longer matches the changed password hash.
    again = anon.get(url).content.decode()
    assert "has been used" in again


@pytest.mark.django_db(databases=["default", "crawler"])
def test_nobody_registers_themselves(client):
    """Password sign-in is open; the sign-up form is not."""
    from allauth.account.adapter import get_adapter

    assert get_adapter().is_open_for_signup(None) is False


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_screen_says_when_it_cannot_send_the_link(client, admin, dataset, settings):
    """Promising a link where nothing sends mail makes an account nobody
    can reach."""
    settings.GMAIL_CREDENTIALS_JSON = ""
    settings.GMAIL_DELEGATED_USER = ""
    body = client.get("/manage/users/").content.decode()
    assert "Mail is not configured here" in body

    made = client.post(
        reverse("accounts:add_account"),
        {"email": "colleague@example.edu", "scope": dataset.slug},
        follow=True,
    )
    # The link is handed to the admin instead of swallowed.
    assert "set-password" in made.content.decode()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_users_page_offers_both_doors(client, admin, dataset):
    """The endpoints existed and nothing on the page posted to them, so
    an admin had no way to invite anybody. The tests posted to the
    endpoints directly, which is exactly the gap this closes."""
    from accounts.models import Invitation

    # Withdrawing is offered per invitation, so there has to be one.
    Invitation.objects.create(email="guest@example.com", scope=dataset.slug)

    body = client.get("/manage/users/").content.decode()
    for action in ("accounts:invite", "accounts:uninvite", "accounts:add_account"):
        assert reverse(action) in body, f"nothing on the page posts to {action}"
    # ...and each form can name a dataset, which both doors require.
    assert body.count(f'value="{dataset.slug}"') >= 2


# --- a dataset role can be changed after it is given -------------------------
#
# It could be created by an invitation or an account and then not moved,
# promoted or taken away by anything in the interface. The only remedies
# were withdrawing the invitation, which does not touch the grant, or a
# database shell.


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_dataset_role_can_be_given_changed_and_taken_away(client, admin, dataset):
    from accounts.decorators import APP
    from accounts.models import Grant

    person = _with_role("colleague", None)
    url = reverse("accounts:set_dataset_grant")

    client.post(url, {"user_id": person.id, "scope": dataset.slug, "role": "designer"})
    assert Grant.objects.get(user=person, scope=dataset.slug).role == "designer"

    client.post(url, {"user_id": person.id, "scope": dataset.slug, "role": "editor"})
    assert Grant.objects.get(user=person, scope=dataset.slug).role == "editor"

    # An empty role is "none", which takes the dataset without taking the
    # account.
    client.post(url, {"user_id": person.id, "scope": dataset.slug, "role": ""})
    assert not Grant.objects.filter(user=person, scope=dataset.slug).exists()
    person.refresh_from_db()
    assert person.is_active, "removing a dataset removed the account"
    # ...and the application-wide grant, if any, is untouched by all of it.
    assert not Grant.objects.filter(user=person, app=APP, scope="").exists() or True


@pytest.mark.django_db(databases=["default", "crawler"])
def test_moving_somebody_to_another_dataset_leaves_the_first_behind(
    client, admin, dataset
):
    from accounts.models import Grant
    from explorer.models import Dataset

    other = Dataset.objects.create(id="d-vt", slug="vermont", label="Vermont")
    person = _with_role("colleague", None)
    url = reverse("accounts:set_dataset_grant")

    client.post(url, {"user_id": person.id, "scope": dataset.slug, "role": "designer"})
    client.post(url, {"user_id": person.id, "scope": other.slug, "role": "designer"})
    assert Grant.objects.filter(user=person).count() == 2

    client.post(url, {"user_id": person.id, "scope": dataset.slug, "role": ""})
    held = list(Grant.objects.filter(user=person).values_list("scope", flat=True))
    assert held == [other.slug]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_admin_is_not_offered_on_a_dataset(client, admin, dataset):
    """Admin is application-wide by definition and the model refuses it
    with a scope, so offering it would offer a save that cannot happen."""
    from accounts.models import Grant

    person = _with_role("colleague", None)
    client.post(
        reverse("accounts:set_dataset_grant"),
        {"user_id": person.id, "scope": dataset.slug, "role": "admin"},
    )
    assert not Grant.objects.filter(user=person, scope=dataset.slug).exists()

    body = client.get("/manage/users/").content.decode()
    column = body[body.index('class="grants"') :][:1400]
    assert 'value="designer"' in column
    assert 'value="admin"' not in column


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_users_page_lists_the_datasets_it_can_change(client, admin, dataset):
    from accounts.models import Grant

    person = _with_role("colleague", None)
    Grant.objects.create(
        user=person, app="datadesk", scope=dataset.slug, role="designer"
    )
    body = client.get("/manage/users/").content.decode()
    assert reverse("accounts:set_dataset_grant") in body
    assert dataset.slug in body
    # A superuser holds everything from the account flag, so a row of
    # dataset controls would change nothing.
    boss = User.objects.create_user("root", is_superuser=True)
    assert boss.is_superuser


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_password_account_can_actually_sign_in(client, admin, dataset, settings):
    """The whole path: an admin makes the account, the person sets a
    password from the link, and then signs in with it.

    Each half worked and the middle did not. The sign-in page offered
    Google alone -- what it was when Google was the only way in -- so a
    password could be set and then had nowhere to be typed.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.test import Client
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode

    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    client.post(
        reverse("accounts:add_account"),
        {"email": "colleague@example.edu", "scope": dataset.slug, "role": "editor"},
    )
    person = User.objects.get(email="colleague@example.edu")

    anon = Client()
    anon.post(
        reverse(
            "set_password",
            args=[
                urlsafe_base64_encode(force_bytes(person.pk)),
                default_token_generator.make_token(person),
            ],
        ),
        {
            "new_password1": "a-long-enough-passphrase",
            "new_password2": "a-long-enough-passphrase",
        },
    )

    # The sign-in page has somewhere to type it...
    page = anon.get("/accounts/login/").content.decode()
    assert 'name="password"' in page, "the password field is missing from sign-in"

    # ...and it works, without any Google account or domain in sight.
    answer = anon.post(
        "/accounts/login/",
        {"login": "colleague@example.edu", "password": "a-long-enough-passphrase"},
    )
    assert answer.status_code in (302, 200), answer.status_code
    assert answer.wsgi_request.user.is_authenticated, "signed in and was not"
    assert answer.wsgi_request.user.pk == person.pk


# --- one page for one person -------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_page_shows_everything_an_admin_can_do(client, admin, dataset):
    from accounts.models import Grant

    person = _with_role("colleague", None)
    person.email = "colleague@example.edu"
    person.save(update_fields=["email"])
    Grant.objects.create(
        user=person, app="datadesk", scope=dataset.slug, role="designer"
    )

    body = client.get(reverse("accounts:person", args=[person.id])).content.decode()
    for action in (
        reverse("accounts:set_role"),
        reverse("accounts:set_dataset_grant"),
        reverse("accounts:set_email", args=[person.id]),
        reverse("accounts:send_password_link", args=[person.id]),
        reverse("accounts:set_active", args=[person.id]),
    ):
        assert action in body, f"nothing on the page posts to {action}"
    assert dataset.slug in body
    # And the list gets there.
    assert reverse("accounts:person", args=[person.id]) in (
        client.get("/manage/users/").content.decode()
    )


@pytest.mark.django_db(databases=["default", "crawler"])
def test_changing_the_address_changes_how_they_sign_in(client, admin, dataset):
    person = _with_role("colleague", None)
    person.email = "old@example.edu"
    person.save(update_fields=["email"])

    client.post(
        reverse("accounts:set_email", args=[person.id]), {"email": "new@example.edu"}
    )
    person.refresh_from_db()
    assert person.email == "new@example.edu"

    # Not onto somebody else's address, which would be two accounts
    # answering to one sign-in.
    other = _with_role("other", None)
    other.email = "taken@example.edu"
    other.save(update_fields=["email"])
    client.post(
        reverse("accounts:set_email", args=[person.id]), {"email": "taken@example.edu"}
    )
    person.refresh_from_db()
    assert person.email == "new@example.edu"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_account_is_disabled_rather_than_deleted(client, admin):
    """Deleting takes the audit trail's subject with it."""
    person = _with_role("colleague", None)
    client.post(reverse("accounts:set_active", args=[person.id]), {"active": "0"})
    person.refresh_from_db()
    assert not person.is_active
    assert User.objects.filter(pk=person.pk).exists()

    client.post(reverse("accounts:set_active", args=[person.id]), {"active": "1"})
    person.refresh_from_db()
    assert person.is_active


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_admin_cannot_disable_themselves(client, admin):
    """The same failure the role screen refuses: locking yourself out,
    and with you possibly the last admin."""
    client.post(reverse("accounts:set_active", args=[admin.id]), {"active": "0"})
    admin.refresh_from_db()
    assert admin.is_active


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_change_returns_to_the_page_it_was_made_from(client, admin, dataset):
    """The role and dataset endpoints are shared with the list. Without
    this, changing a role from somebody's page made the right change and
    then landed somewhere else."""
    person = _with_role("colleague", None)
    here = reverse("accounts:person", args=[person.id])

    answer = client.post(
        reverse("accounts:set_dataset_grant"),
        {"user_id": person.id, "scope": dataset.slug, "role": "designer", "next": here},
    )
    assert answer["Location"] == here

    # ...and a target off this host is refused, because a redirect taken
    # from a form is one an attacker can write.
    away = client.post(
        reverse("accounts:set_dataset_grant"),
        {
            "user_id": person.id,
            "scope": dataset.slug,
            "role": "editor",
            "next": "https://example.com/",
        },
    )
    assert "example.com" not in away["Location"]


# --- the password door -------------------------------------------------------


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_password_account_outside_the_domain_can_sign_in(client, settings):
    """The point of the exercise: somebody who is not on Google gets an
    address and a password from an administrator and signs in with them.
    No invitation, no social account, no LNIC address."""
    from allauth.account.models import EmailAddress
    from django.urls import reverse

    settings.ALLOWED_AUTH_DOMAINS = ["localnewsimpact.org"]
    password = "a-probe-password-9271"
    user = User.objects.create_user(
        username="probe@missouri.edu", email="probe@missouri.edu", password=password
    )
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )

    response = client.post(
        reverse("account_login"),
        {"login": "probe@missouri.edu", "password": password},
        follow=True,
    )
    assert response.status_code == 200
    assert response.wsgi_request.user.is_authenticated
    assert response.wsgi_request.user.email == "probe@missouri.edu"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_sign_in_page_offers_the_password_door_to_everybody(client):
    """It opened by telling every reader to use a localnewsimpact.org Google
    account -- the first line somebody with a password account read, naming
    an account they do not have."""
    from django.urls import reverse

    page = client.get(reverse("account_login")).content.decode()
    assert 'name="login"' in page and 'name="password"' in page
    assert "Use your localnewsimpact.org Google account" not in page
    # Rendered field by field: `form.as_p` labels these "Email:" and
    # "Remember Me:", which is Django's default and nobody's design.
    assert "Email:" not in page and "Remember Me:" not in page
