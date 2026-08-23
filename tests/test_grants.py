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
    may_create_dataset,
    may_import,
    permitted_scopes,
    roles_for,
)
from accounts.models import (
    DATADESK,
    SOURCES,
    UNIVERSAL,
    WHOLE_APPLICATION,
    Grant,
)
from accounts.privileges import (
    ADMIN,
    CREATE,
    DESIGN,
    DESIGNER,
    EDITOR,
    READ,
    REVIEWER,
    VIEWER,
    WRITE,
    role_may_create,
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


def test_write_corrects_what_is_there_and_create_brings_new_data_in():
    """The reviewer/editor line, and it is a privilege rather than a
    special case. Both correct the records in front of them; only an
    editor may add records that were not there."""
    from accounts.privileges import privileges_for_role

    assert privileges_for_role(REVIEWER) < privileges_for_role(EDITOR)
    assert WRITE in privileges_for_role(REVIEWER)
    assert WRITE in privileges_for_role(EDITOR)
    assert CREATE not in privileges_for_role(REVIEWER)
    assert role_may_create(EDITOR)
    assert not role_may_create(REVIEWER)


def test_editor_and_admin_differ_by_reach_not_by_power():
    """Identical privilege sets. An admin is an editor with no scope
    limit, plus user administration -- which is why almost no check
    should ask "editor or admin?" rather than "may this person do this
    here?"."""
    from accounts.privileges import privileges_for_role

    assert privileges_for_role(EDITOR) == privileges_for_role(ADMIN)


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
    assert permitted_scopes(person, DATADESK, READ) == {
        "mizzou",
        "lehigh",
        UNIVERSAL,
    }


def test_an_application_wide_grant_reports_all_scopes(person):
    """Not a list of every slug in the corpus: a caller filtering a
    queryset must be able to skip the filter rather than enumerate."""
    grant(person, VIEWER)
    assert permitted_scopes(person, DATADESK, READ) is ALL_SCOPES


def test_permitted_scopes_ignores_roles_without_the_privilege(person):
    grant(person, DESIGNER, scope="mizzou")
    assert permitted_scopes(person, DATADESK, DESIGN) == {"mizzou", UNIVERSAL}
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


def test_a_signed_in_person_with_no_grant_has_only_reference_data(person):
    """No standing in anybody's corpus. The universal dataset is not
    somebody's corpus -- it is FIPS codes and census tables -- so it is
    there, and the landing page still says access has not been granted."""
    assert not has_any_grant(person, DATADESK)
    assert not has_privilege(person, DATADESK, READ)
    assert permitted_scopes(person, DATADESK, READ) == {UNIVERSAL}
    assert permitted_scopes(person, DATADESK, WRITE) == frozenset()
    assert roles_for(person, DATADESK) == frozenset()


def test_everyone_reads_and_designs_against_reference_data(person):
    """A rule rather than a row: nothing to create at sign-up, and
    nothing that can be revoked by accident."""
    assert has_privilege(person, DATADESK, READ, scope=UNIVERSAL)
    assert has_privilege(person, DATADESK, DESIGN, scope=UNIVERSAL)
    assert not has_privilege(person, DATADESK, WRITE, scope=UNIVERSAL)
    assert Grant.objects.filter(user=person).count() == 0


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


# --- designer, export, and holding several roles at once ---------------------


def test_a_designer_is_a_viewer_that_also_designs():
    """Not a separate track. A designer reads and exports everything a
    viewer does; design is added, nothing is taken away."""
    from accounts.privileges import privileges_for_role

    assert privileges_for_role(VIEWER) < privileges_for_role(DESIGNER)
    assert privileges_for_role(DESIGNER) - privileges_for_role(VIEWER) == {DESIGN}


def test_neither_viewer_nor_designer_sees_a_disposition(person):
    """The review queue is for `write`, and these two are not on it."""
    grant(person, DESIGNER, scope="mizzou")
    assert not has_privilege(person, DATADESK, WRITE, scope="mizzou")
    assert not may_import(person, DATADESK, scope="mizzou")


def test_export_follows_read_so_a_viewer_may_export(person):
    """Taking data away is what `read` is for -- the deliverable CSVs are
    the shape the research is published in. What limits a viewer is which
    datasets they can read, not whether they may export at all."""
    from accounts.privileges import EXPORT_PRIVILEGE

    grant(person, VIEWER, scope="mizzou")
    assert has_privilege(person, DATADESK, EXPORT_PRIVILEGE, scope="mizzou")
    assert not has_privilege(person, DATADESK, EXPORT_PRIVILEGE, scope="lehigh")


def test_an_editor_owns_one_dataset_and_views_others(person):
    """The arrangement the roles exist for: full rights on the dataset
    they started, a viewer on everyone else's. Three grants, three
    scopes, no precedence rule -- the three-group model could not say
    this at all."""
    grant(person, EDITOR, scope="mizzou")
    grant(person, VIEWER, scope="lehigh")
    grant(person, DESIGNER, scope="minnesota")

    # Everything on the one they own.
    assert has_privilege(person, DATADESK, WRITE, scope="mizzou")
    assert has_privilege(person, DATADESK, DESIGN, scope="mizzou")
    assert may_import(person, DATADESK, scope="mizzou")

    # Reads the one they were added to, and no more.
    assert has_privilege(person, DATADESK, READ, scope="lehigh")
    assert not has_privilege(person, DATADESK, WRITE, scope="lehigh")

    # Builds visuals on the third without touching its records.
    assert has_privilege(person, DATADESK, DESIGN, scope="minnesota")
    assert not has_privilege(person, DATADESK, WRITE, scope="minnesota")

    assert permitted_scopes(person, DATADESK, READ) == {
        "mizzou",
        "lehigh",
        "minnesota",
        UNIVERSAL,
    }
    assert permitted_scopes(person, DATADESK, WRITE) == {"mizzou"}
    assert permitted_scopes(person, DATADESK, DESIGN) == {
        "mizzou",
        "minnesota",
        UNIVERSAL,
    }


# --- starting a dataset, and what admin means --------------------------------


def test_an_editor_may_start_another_dataset(person):
    """Owning one is enough. Requiring an application-wide grant would
    mean someone could own a dataset and not be able to make a second."""
    grant(person, EDITOR, scope="mizzou")
    assert may_create_dataset(person, DATADESK)


def test_a_viewer_or_designer_may_not_start_one(person):
    grant(person, DESIGNER, scope="mizzou")
    assert not may_create_dataset(person, DATADESK)


def test_admin_is_application_level_and_cannot_name_a_dataset(person):
    """Full access to everything, but only here" is a contradiction, and
    would quietly behave like an editor. Editor is the dataset-level
    role; the database refuses the other reading."""
    with pytest.raises(IntegrityError), transaction.atomic():
        grant(person, ADMIN, scope="mizzou")


def test_an_application_wide_admin_is_fine(person):
    grant(person, ADMIN)
    assert has_privilege(person, DATADESK, DESIGN, scope="anything")
    assert may_create_dataset(person, DATADESK)


# --- the Source Directory's admin gate ---------------------------------------


def test_the_sources_admin_opens_for_any_standing_there(person):
    """Any grant in that application opens its admin. What somebody may
    do once inside is per-model and per-dataset, which the grants already
    answer -- the door is not the place to ask it."""
    from accounts.access import may_reach_sources_admin

    grant(person, VIEWER, app=SOURCES, scope="")
    assert may_reach_sources_admin(person)


def test_standing_in_datadesk_does_not_open_the_sources_admin(person):
    """The point of granting per application: one set of users, two
    consoles, and access answered separately in each."""
    from accounts.access import may_reach_sources_admin

    grant(person, ADMIN, app=DATADESK, scope="")
    assert not may_reach_sources_admin(person)


def test_is_staff_does_not_open_it(person):
    """Replaced rather than derived. `is_staff` is settable by hand in the
    Django admin, and leaving it able to open this console would be the
    second source of truth the replacement exists to remove."""
    from accounts.access import may_reach_sources_admin

    person.is_staff = True
    person.save(update_fields=["is_staff"])
    assert not may_reach_sources_admin(person)
