"""Grants: who may do what, where (ROADMAP item 1).

The model these replace was three global groups, where a role granted the
same access to every dataset and precedence decided what someone with two
groups could do. The tests that matter here are the ones about the edges
of that replacement: an application-wide grant versus a scoped one, a
person with standing in one application and none in another, and the
constraint that stops precedence being needed again.
"""

import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, transaction

from accounts.access import (
    ALL_SCOPES,
    has_any_grant,
    has_privilege,
    may_import,
    permitted_scopes,
    roles_for,
)
from accounts.models import DATADESK, SOURCES, WHOLE_APPLICATION, Grant
from accounts.privileges import (
    ADMIN,
    DESIGN,
    DESIGNER,
    EDITOR,
    READ,
    REVIEWER,
    VIEWER,
    WRITE,
    role_may_import,
    role_permits,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def person(db):
    return User.objects.create_user("person", "person@localnewsimpact.org", "x")


def grant(user, role, app=DATADESK, scope=WHOLE_APPLICATION):
    return Grant.objects.create(user=user, app=app, scope=scope, role=role)


# --- the vocabulary ---------------------------------------------------------


def test_a_designer_does_not_hold_write():
    """Authoring a visual is not permission to change the records it
    draws on. This is the distinction that makes `design` a privilege of
    its own rather than a flavour of write."""
    assert role_permits(DESIGNER, DESIGN)
    assert role_permits(DESIGNER, READ)
    assert not role_permits(DESIGNER, WRITE)


def test_reviewer_and_editor_are_indistinguishable_by_privilege():
    """Both hold exactly read and write. This is why the import paths
    test the role instead -- no privilege separates these two, and
    pretending one does would mean inventing a fourth."""
    from accounts.privileges import privileges_for_role

    assert privileges_for_role(REVIEWER) == privileges_for_role(EDITOR)
    assert role_may_import(EDITOR)
    assert not role_may_import(REVIEWER)


def test_an_unknown_role_carries_nothing():
    """A role name that is not in the table grants no privilege, rather
    than raising -- a grant row surviving a rename must fail closed."""
    assert not role_permits("emperor", READ)


# --- scope ------------------------------------------------------------------


def test_an_application_wide_grant_answers_for_every_dataset(person):
    grant(person, EDITOR)
    assert has_privilege(person, DATADESK, WRITE, scope="mizzou")
    assert has_privilege(person, DATADESK, WRITE, scope="anything-at-all")


def test_a_scoped_grant_answers_only_for_its_own_dataset(person):
    grant(person, EDITOR, scope="mizzou")
    assert has_privilege(person, DATADESK, WRITE, scope="mizzou")
    assert not has_privilege(person, DATADESK, WRITE, scope="lehigh")


def test_a_scoped_grant_does_not_answer_the_unscoped_question(person):
    """Asking without a scope asks about the whole application. Holding
    one dataset is not holding the application."""
    grant(person, EDITOR, scope="mizzou")
    assert not has_privilege(person, DATADESK, WRITE)


def test_scopes_accumulate_across_grants(person):
    grant(person, EDITOR, scope="mizzou")
    grant(person, VIEWER, scope="lehigh")
    assert permitted_scopes(person, DATADESK, WRITE) == {"mizzou"}
    assert permitted_scopes(person, DATADESK, READ) == {"mizzou", "lehigh"}


def test_an_application_wide_grant_reports_all_scopes(person):
    """Not a list of every slug in the corpus: a caller filtering a
    queryset must be able to skip the filter rather than enumerate."""
    grant(person, VIEWER)
    assert permitted_scopes(person, DATADESK, READ) is ALL_SCOPES


def test_permitted_scopes_ignores_roles_without_the_privilege(person):
    grant(person, DESIGNER, scope="mizzou")
    assert permitted_scopes(person, DATADESK, DESIGN) == {"mizzou"}
    assert permitted_scopes(person, DATADESK, WRITE) == frozenset()


# --- per application --------------------------------------------------------


def test_a_grant_in_one_application_says_nothing_about_another(person):
    """The point of granting per application: one set of users, and what
    they may do is answered separately in each console."""
    grant(person, ADMIN, app=SOURCES)
    assert has_privilege(person, SOURCES, WRITE)
    assert not has_privilege(person, DATADESK, READ)
    assert not has_any_grant(person, DATADESK)


def test_the_same_person_can_hold_different_roles_in_each(person):
    grant(person, EDITOR, app=DATADESK)
    grant(person, REVIEWER, app=SOURCES)
    assert may_import(person, DATADESK)
    assert not may_import(person, SOURCES)


# --- one role per scope -----------------------------------------------------


def test_a_person_cannot_hold_two_roles_on_one_scope(person):
    """The constraint that retires precedence. Three global groups needed
    a rule for who wins when someone is both viewer and editor; one role
    per scope means the question never arises."""
    grant(person, VIEWER, scope="mizzou")
    with pytest.raises(IntegrityError), transaction.atomic():
        grant(person, EDITOR, scope="mizzou")


def test_the_same_role_name_is_fine_on_two_scopes(person):
    grant(person, EDITOR, scope="mizzou")
    grant(person, EDITOR, scope="lehigh")
    assert permitted_scopes(person, DATADESK, WRITE) == {"mizzou", "lehigh"}


def test_application_wide_and_scoped_can_coexist(person):
    """Different scopes, so the constraint does not fire. The wide grant
    answers everywhere; the scoped one adds nothing but is not refused."""
    grant(person, VIEWER)
    grant(person, EDITOR, scope="mizzou")
    assert has_privilege(person, DATADESK, WRITE, scope="mizzou")
    assert has_privilege(person, DATADESK, READ, scope="lehigh")


# --- superusers and strangers -----------------------------------------------


def test_a_superuser_needs_no_rows(db):
    root = User.objects.create_superuser("root", "root@localnewsimpact.org", "x")
    assert has_privilege(root, DATADESK, DESIGN, scope="anything")
    assert has_privilege(root, SOURCES, WRITE)
    assert may_import(root, DATADESK)
    assert permitted_scopes(root, DATADESK, READ) is ALL_SCOPES
    assert has_any_grant(root, DATADESK)
    assert Grant.objects.filter(user=root).count() == 0


def test_a_signed_in_person_with_no_grant_has_nothing(person):
    """Not a viewer with an empty list -- no standing at all. The landing
    page tells them access has not been granted yet."""
    assert not has_any_grant(person, DATADESK)
    assert not has_privilege(person, DATADESK, READ)
    assert permitted_scopes(person, DATADESK, READ) == frozenset()
    assert roles_for(person, DATADESK) == frozenset()


def test_anonymous_users_are_refused_without_touching_the_database(
    django_assert_num_queries,
):
    from django.contrib.auth.models import AnonymousUser

    nobody = AnonymousUser()
    with django_assert_num_queries(0):
        assert not has_privilege(nobody, DATADESK, READ)
        assert not may_import(nobody, DATADESK)
        assert not has_any_grant(nobody, DATADESK)
        assert permitted_scopes(nobody, DATADESK, READ) == frozenset()
