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
from django.contrib.auth.models import Group, User


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
def test_a_role_is_a_group_so_the_other_console_can_read_it():
    """The directory gates its admin on is_staff; Datadesk gates on
    these groups. Once the user table is shared, is_staff has to be
    derived from the role rather than set by hand, or the two consoles
    disagree about who is an editor — and that disagreement is invisible
    until somebody is wrongly let in or shut out.

    This pins the vocabulary the derivation will read.
    """
    from accounts.roles import ADMIN, EDITOR, ROLES, VIEWER, role_for_user

    assert set(ROLES) == {VIEWER, EDITOR, ADMIN}
    for role in ROLES:
        assert Group.objects.filter(name=role).exists(), role

    user = User.objects.create_user("e", email="e@localnewsimpact.org")
    user.groups.add(Group.objects.get(name=EDITOR))
    assert role_for_user(user) == EDITOR
