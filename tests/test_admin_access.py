"""Admin sections are enforced by the views, not by the sidebar.

Hiding a navigation link is presentation. The contract these tests hold
is that a viewer or an editor who types an admin URL, or follows a stale
bookmark, is refused by the view itself.

The list walked here is accounts.sections.SECTION_GROUPS — the same list
the sidebar renders — so a section cannot be added to the navigation
without its guard being proven. Each group declares the role it requires;
these tests check that declaration against the decorator on the view, so
moving a link between groups cannot quietly widen or narrow access.
"""

import pytest
from django.contrib.auth.models import User
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts.models import DATADESK, Grant
from accounts.sections import (
    ADMIN,
    ANY,
    EDITOR,
    SECTION_GROUPS,
    external_sections,
    groups_for,
    internal_sections,
)

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])


def _urls_requiring(role):
    return [
        section["url"] for section, requires in internal_sections() if requires == role
    ]


ADMIN_URLS = _urls_requiring(ADMIN)
EDITOR_URLS = _urls_requiring(EDITOR)
OPEN_URLS = _urls_requiring(ANY)


# What a section's declared requirement implies about the view behind it.
# `requires(privilege)` records the privilege on the view; the admin guard
# sets a flag, because administration is not a privilege over a dataset.
def _carries_guard(view, requirement):
    """Administration is the only requirement that is not a privilege.

    Imports used to be a second exception here. They are `create` now --
    an import adds records that were not there, which is a power rather
    than a volume -- so the generic branch answers for them.
    """
    if requirement == ADMIN:
        return getattr(view, "requires_admin", False)
    return getattr(view, "required_privilege", None) == requirement


# Admin URLs that no section links to directly, but which must be guarded
# just as hard: the role-assignment endpoint is the console's own escalation
# path.
OTHER_ADMIN_URLS = [
    "accounts:set_role",
    "datasets:create",
]


def _user(client, role, username=None):
    username = username or (role or "norole")
    user = User.objects.create_user(username, email=f"{username}@localnewsimpact.org")
    if role:
        Grant.objects.create(user=user, app=DATADESK, scope="", role=role)
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


@pytest.mark.parametrize(
    "url_name,requires",
    [(section["url"], requires) for section, requires in internal_sections()],
)
def test_a_sections_group_matches_the_guard_on_its_view(url_name, requires):
    """The guard belongs beside the link. A section listed under a group
    whose role its view does not enforce is the bug this catches — the
    sidebar would promise one thing and the view do another."""
    view = _view_for(url_name)
    assert _carries_guard(view, requires), (
        f"{url_name} sits in a {requires} group but its view does not " f"demand it"
    )


def _all_patterns(resolver=None, prefix=""):
    resolver = resolver or get_resolver()
    for entry in resolver.url_patterns:
        if isinstance(entry, URLResolver):
            yield from _all_patterns(entry, prefix + str(entry.pattern))
        elif isinstance(entry, URLPattern):
            yield prefix + str(entry.pattern), entry


def test_every_view_under_manage_is_guarded():
    """/manage/ is the administrative mount. Nothing unguarded may live
    there, whether or not the sidebar links to it.

    Guarded, not necessarily by `requires_admin`. Editing a source is a
    dataset privilege — an editor holds it on their own datasets, and an
    application admin is not required to correct a county. Proposing a
    change is weaker still, and deliberately open to anyone who can see the
    record. What must never appear here is a view that asks for nothing.
    """
    unguarded = [
        route
        for route, pattern in _all_patterns()
        if route.startswith("manage/")
        and not getattr(pattern.callback, "requires_admin", False)
        and getattr(pattern.callback, "required_privilege", None) is None
    ]
    assert unguarded == []


def test_open_sections_are_open_to_any_assigned_role(client, crawler_schema):
    """The other half of the contract: a viewer is not locked out of the
    everyday surface."""
    _user(client, "viewer")
    for url_name in OPEN_URLS:
        response = client.get(reverse(url_name))
        assert response.status_code == 200, f"viewer refused at {url_name}"


@pytest.mark.parametrize("url_name", EDITOR_URLS)
def test_a_viewer_is_refused_every_editor_section(client, url_name, crawler_schema):
    _user(client, "viewer")
    assert client.get(reverse(url_name)).status_code == 403


@pytest.mark.parametrize("url_name", EDITOR_URLS)
def test_an_editor_reaches_every_editor_section(client, url_name, crawler_schema):
    _user(client, "editor")
    assert client.get(reverse(url_name)).status_code == 200


# --- the sidebar reflects the same list ------------------------------------


def test_the_sidebar_hides_every_admin_page_from_non_admins(client, crawler_schema):
    """The pages, not the header.

    A viewer sees no Admin header at all -- nothing under it reaches
    them. An editor sees the header, because Cost lives there and is
    theirs to open, and sees nothing else in it. The rule the sidebar
    follows is that a group appears when the role reaches at least one
    of its sections, which is what makes that possible: the header is a
    consequence of what is under it, not a claim about the person.
    """
    _user(client, "viewer", username="nav-viewer")
    content = client.get("/").content.decode()
    assert ">Admin<" not in content

    _user(client, "editor", username="nav-editor")
    content = client.get("/").content.decode()
    assert ">Admin<" in content, "Cost is under this header and an editor may open it"
    assert reverse("explorer:costs") in content
    for url_name in ADMIN_URLS:
        assert reverse(url_name) not in content, url_name


def test_the_sidebar_hides_the_editor_groups_from_a_viewer(client, crawler_schema):
    _user(client, "viewer")
    content = client.get("/").content.decode()
    assert ">Sources<" not in content
    for url_name in EDITOR_URLS:
        assert reverse(url_name) not in content, url_name


def test_the_sidebar_shows_every_section_to_an_admin(client, crawler_schema):
    _user(client, "admin")
    content = client.get("/").content.decode()
    for group in SECTION_GROUPS:
        assert f">{group['label']}<" in content, group["label"]
        for section in group["sections"]:
            href = section.get("site") or reverse(section["url"])
            assert href in content, section["label"]


# --- links to other LNIC consoles ------------------------------------------


def test_a_link_to_another_console_is_absolute_and_https():
    """A relative or plain-http entry here would send a reviewer to the
    wrong host, or over the wire in the clear."""
    for section in external_sections():
        assert section["site"].startswith("https://"), section["label"]
        assert section["site"].endswith("/"), section["label"]


def test_a_link_to_another_console_is_marked_as_leaving(client, crawler_schema):
    """The destination signs the reader in itself, so the nav says so
    rather than letting a click look like an in-console page."""
    _user(client, "editor")
    content = client.get("/").content.decode()
    for section in external_sections():
        assert section["site"] in content, section["label"]
    assert "nav-external" in content


def test_the_source_directory_sits_under_sources():
    group = next(g for g in SECTION_GROUPS if g["label"] == "Sources")
    labels = [s["label"] for s in group["sections"]]
    assert "Source directory" in labels


def test_the_groups_read_in_the_order_the_sidebar_shows_them(client, crawler_schema):
    """Data, Review, Sources, Extraction, Processing, then Admin.

    Review sits second because it is what somebody signs in to do. The
    groups below it are the reference material the review is made
    against, and Admin is last because it is the least often wanted.

    Cost had a group of its own, because ROADMAP item 1 put spend on
    `write` and a group labelled Admin containing a page an editor can
    open would be a lie. It is under Admin now with its own `requires`,
    which keeps the page open to editors -- one section is not a group,
    and a header hosting a single link is a header a reader has to read
    before they can skip it.
    """
    assert [g["label"] for g in SECTION_GROUPS] == [
        "Data",
        "Review",
        "Sources",
        "Extraction",
        # Operational rather than editorial: a reader looking for work to
        # do passes it, and a reader asking whether the machine is running
        # knows to look near the bottom.
        "Processing",
        "Admin",
    ]
    _user(client, "admin")
    content = client.get("/").content.decode()
    positions = [content.index(f">{g['label']}<") for g in SECTION_GROUPS]
    assert positions == sorted(positions)


def test_a_user_with_no_role_sees_no_groups():
    assert groups_for(None) == ()


def test_the_queues_are_one_group_named_for_their_stage():
    """Reviewing is one activity, so its queues are one group.

    They were "Review queue" under two different headers, which put the
    same words in the sidebar twice and made the reader carry the header
    down to the link to tell them apart. Under one header the label can
    do the work, so each is named for the stage it reviews -- and those
    read in pipeline order: a publisher is decided before a URL is
    discovered, and a URL before its article is extracted.
    """
    from accounts.sections import SECTION_GROUPS

    review = next(g for g in SECTION_GROUPS if g["label"] == "Review")
    by_url = {s["url"]: s["label"] for s in review["sections"]}

    assert by_url["review:proposals"] == "Sources"
    assert by_url["review:queue"] == "Extraction"
    # No two sections in one group may share a label -- the group header
    # is no longer available to tell them apart.
    labels = [s["label"] for s in review["sections"]]
    assert len(labels) == len(set(labels))


def test_review_sits_between_data_and_sources():
    """Where it was asked to be, and pinned so a later addition does not
    quietly push it down the sidebar.

    Above Sources because reviewing is the work; the groups below it are
    what the review is made against.
    """
    order = [g["label"] for g in SECTION_GROUPS]
    assert order.index("Data") < order.index("Review") < order.index("Sources")


def test_extraction_problems_stayed_where_it_was():
    """Only the queues moved. Extraction problems is a different kind of
    page -- publishers whose parser is broken, not records awaiting a
    decision -- and it keeps its group until somebody decides otherwise.
    """
    group = next(g for g in SECTION_GROUPS if g["label"] == "Extraction")
    assert [s["url"] for s in group["sections"]] == ["review:extraction_problems"]


def test_cost_stays_open_to_an_editor_under_the_admin_header():
    """Moving it must not take the page away from the people it was
    opened to. The section carries its own `requires`, so the Admin
    group's ADMIN does not reach it."""
    from accounts.sections import EDITOR, requires_for

    admin = next(g for g in SECTION_GROUPS if g["label"] == "Admin")
    cost = next(s for s in admin["sections"] if s["url"] == "explorer:costs")
    assert requires_for(admin, cost) == EDITOR
    # And everything else under that header still needs admin.
    others = [s for s in admin["sections"] if s["url"] != "explorer:costs"]
    assert all(requires_for(admin, s) == "administration" for s in others)
