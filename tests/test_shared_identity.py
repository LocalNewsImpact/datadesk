"""Datadesk owns identity for the suite (ROADMAP.md item 12).

The Source Directory is a second Django application, in its own
repository and its own deployment, reading the same user and session
tables from the same database. What carries a session between the two
subdomains is a cookie on the parent domain — no load balancer, no
shared origin.

None of that is checked by the directory's tests, because the settings
that make it work live here. These are the checks that would otherwise
have nowhere to run.
"""

import pytest
from django.contrib.auth.models import User

from accounts.models import DATADESK, Grant


def test_the_session_cookie_domain_comes_from_the_environment(monkeypatch):
    """A host-only cookie stops at datadesk.localnewsimpact.org. The
    parent domain is what lets sources.localnewsimpact.org see it, and
    it is deployment configuration rather than a constant, because
    locally there is no parent domain to share."""
    import importlib

    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", ".localnewsimpact.org")
    module = importlib.reload(importlib.import_module("datadesk.settings"))
    try:
        assert module.SESSION_COOKIE_DOMAIN == ".localnewsimpact.org"
        assert module.CSRF_COOKIE_DOMAIN == ".localnewsimpact.org"
    finally:
        monkeypatch.delenv("SESSION_COOKIE_DOMAIN")
        importlib.reload(module)


def test_csrf_follows_the_session_across_the_same_domain():
    """A session shared across subdomains and a CSRF cookie that is not
    fails every POST from the other console."""
    import datadesk.settings as module

    assert module.CSRF_COOKIE_DOMAIN == module.SESSION_COOKIE_DOMAIN


def test_the_cookie_is_renamed_so_the_old_one_cannot_shadow_it():
    """A cookie's domain is part of its identity. Widening the domain
    leaves the old host-only `sessionid` in the browser beside the new
    one, both are sent, and which the server reads is not something to
    leave to chance. The rename forces one more sign-in and then a clean
    cut."""
    import datadesk.settings as module

    assert module.SESSION_COOKIE_NAME == "lnic_session"
    assert module.CSRF_COOKIE_NAME == "lnic_csrf"
    assert module.SESSION_COOKIE_NAME != "sessionid"


def test_a_parent_domain_cookie_is_off_by_default():
    """Locally there is no parent domain to share, and a cookie scoped to
    one nobody is serving would simply never come back."""
    import os

    from datadesk.settings import SESSION_COOKIE_DOMAIN

    if not os.environ.get("SESSION_COOKIE_DOMAIN"):
        assert SESSION_COOKIE_DOMAIN is None


@pytest.mark.django_db
def test_a_grant_names_the_application_it_applies_to():
    """One user table, two consoles, and what somebody may do answered
    separately in each.

    This test pinned the old arrangement: three Django groups, which the
    directory was going to derive `is_staff` from. ROADMAP item 1 decided
    against deriving anything -- the directory's admin gate is replaced by
    the same grant check Datadesk uses -- so what needs pinning now is that
    a grant is per application and says so.
    """
    from accounts.models import SOURCES
    from accounts.privileges import ADMIN, EDITOR, ROLES, VIEWER

    assert {VIEWER, EDITOR, ADMIN} <= set(ROLES)

    user = User.objects.create_user("e", email="e@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role=EDITOR)

    from accounts.access import has_privilege_anywhere
    from accounts.privileges import WRITE

    assert has_privilege_anywhere(user, DATADESK, WRITE)
    assert not has_privilege_anywhere(user, SOURCES, WRITE)
