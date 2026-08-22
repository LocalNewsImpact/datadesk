"""Admin sections are enforced by the views, not by the sidebar.

Hiding a navigation link is presentation. The contract these tests hold
is that a viewer or an editor who types an admin URL, or follows a stale
bookmark, is refused by the view itself.

The list walked here is accounts.sections.ADMIN_SECTIONS — the same list
the sidebar renders — so a section cannot be added to the navigation
without its guard being proven.
"""

import pytest
from django.contrib.auth.models import Group, User
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.sections import ADMIN_SECTIONS, WORK_SECTIONS

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

ADMIN_URLS = [section["url"] for section in ADMIN_SECTIONS]

# Admin URLs that no section links to directly, but which must be guarded
# just as hard: the role-assignment endpoint is the console's own escalation
# path.
OTHER_ADMIN_URLS = [
    "accounts:set_role",
    "datasets:create",
    "datasets:source_create",
]


def _user(client, role, username=None):
    username = username or (role or "norole")
    user = User.objects.create_user(username, email=f"{username}@localnewsimpact.org")
    if role:
        user.groups.add(Group.objects.get(name=role))
    client.force_login(user)
    return user


@pytest.mark.parametrize("url_name", ADMIN_URLS + OTHER_ADMIN_URLS)
@pytest.mark.parametrize("role", ["viewer", "editor", None])
def test_non_admins_are_refused_every_admin_url(client, url_name, role):
    _user(client, role)
    path = reverse(url_name)
    assert client.get(path).status_code == 403, f"{role} reached {path}"
    assert client.post(path).status_code == 403, f"{role} posted to {path}"


@pytest.mark.parametrize("url_name", ADMIN_URLS)
def test_an_admin_reaches_every_admin_section(client, url_name, crawler_schema):
    _user(client, "admin")
    response = client.get(reverse(url_name))
    assert response.status_code == 200, f"admin refused at {url_name}"


@pytest.mark.parametrize("url_name", ADMIN_URLS + OTHER_ADMIN_URLS)
def test_anonymous_is_sent_to_sign_in(client, url_name):
    response = client.get(reverse(url_name))
    assert response.status_code == 302
    assert "/accounts/login/" in response["Location"]


# --- the guard cannot be forgotten -----------------------------------------


def _view_for(url_name):
    """The callable a URL name resolves to, unwrapped of nothing.

    accounts.decorators marks the wrapper it returns, so the mark is what
    is inspected here rather than the inner view.
    """
    from django.urls import resolve

    return resolve(reverse(url_name)).func


@pytest.mark.parametrize("url_name", ADMIN_URLS + OTHER_ADMIN_URLS)
def test_every_admin_view_carries_the_guard(url_name):
    """Belt to the 403 tests' braces: the decorator is present, so a view
    added later cannot rely on the sidebar hiding it."""
    view = _view_for(url_name)
    assert getattr(view, "requires_admin", False), f"{url_name} is not admin_required"


def _all_patterns(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            yield from _all_patterns(entry, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry


def test_every_view_under_manage_is_guarded():
    """/manage/ is the administrative mount. Nothing unguarded may live
    there, whether or not the sidebar links to it."""
    unguarded = [
        route
        for route, pattern in _all_patterns()
        if route.startswith("manage/")
        and not getattr(pattern.callback, "requires_admin", False)
    ]
    assert unguarded == []


def test_work_sections_are_open_to_any_assigned_role(client, crawler_schema):
    """The other half of the contract: a viewer is not locked out of the
    everyday surface."""
    _user(client, "viewer")
    for section in WORK_SECTIONS:
        response = client.get(reverse(section["url"]))
        assert response.status_code == 200, f"viewer refused at {section['url']}"


# --- the sidebar reflects the same list ------------------------------------


def test_the_sidebar_hides_admin_from_non_admins(client, crawler_schema):
    for role in ("viewer", "editor"):
        _user(client, role, username=f"nav-{role}")
        content = client.get("/").content.decode()
        assert ">Admin<" not in content
        for section in ADMIN_SECTIONS:
            assert reverse(section["url"]) not in content, section["url"]


def test_the_sidebar_shows_every_admin_section_to_an_admin(client, crawler_schema):
    _user(client, "admin")
    content = client.get("/").content.decode()
    assert ">Admin<" in content
    for section in ADMIN_SECTIONS:
        assert reverse(section["url"]) in content, section["url"]
    for section in WORK_SECTIONS:
        assert reverse(section["url"]) in content, section["url"]
