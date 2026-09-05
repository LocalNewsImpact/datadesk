"""The extraction review's schema, where the publisher record's is.

The schema page already draws the line this needs: which fields a record
must have is a change somebody reviews; the words a field accepts grow on
a Tuesday, and making somebody ship a deploy for one word means the word
waits for a deploy.

An extraction review has the same two halves. The verbs, and the status
each content type writes, are the pipeline's — a type with no status
behind it cannot be written at all, and the submit path refuses it. What
a type or a flag is CALLED is words. "Out of scope" named the status;
"Non-local" names what a reviewer is saying, and that correction was a
deploy.
"""

import pytest
from django.urls import reverse

from datasets.models import VocabularyTerm
from review import dispositions
from review import vocabulary as review_vocabulary


@pytest.fixture
def admin_user(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("boss", email="boss@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture(autouse=True)
def _forget_between_tests():
    review_vocabulary.forget()
    yield
    review_vocabulary.forget()


# --- what the page shows -----------------------------------------------------


@pytest.mark.django_db
def test_the_page_shows_the_verbs_and_what_they_read_as(client, admin_user):
    client.force_login(admin_user)
    body = client.get(reverse("review:schema")).content.decode()
    assert "What an extraction review is" in body
    for verb in ("accept", "restore", "reject", "reextract"):
        assert verb in body


@pytest.mark.django_db
def test_the_page_shows_each_type_and_the_status_it_writes(client, admin_user):
    """The status is the half that cannot be edited here, so it has to be
    visible: a reviewer renaming a word should see what it does."""
    client.force_login(admin_user)
    body = client.get(reverse("review:schema")).content.decode()
    assert "photo_gallery" in body
    assert "not_article" in body
    assert "back to where the stage rewinds to" in body


@pytest.mark.django_db
def test_the_page_shows_every_flag_a_row_can_be_raised_on(client, admin_user):
    client.force_login(admin_user)
    body = client.get(reverse("review:schema")).content.decode()
    for flag in ("paywall_stub", "scope_excluded", "minimal_capture"):
        assert flag in body
    assert "Story cut off by login prompt" in body


# --- revising the words ------------------------------------------------------


@pytest.mark.django_db
def test_a_type_can_be_renamed_without_a_deploy(client, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("review:schema"),
        {
            "vocabulary": review_vocabulary.TYPE_WORDS,
            "value": "out_of_scope",
            "label": "Somewhere else",
        },
    )
    offered = {t["value"]: t["label"] for t in review_vocabulary.content_types()}
    assert offered["out_of_scope"] == "Somewhere else"


@pytest.mark.django_db
def test_renaming_a_type_does_not_change_what_it_writes(client, admin_user):
    """The word is words; the status is the pipeline's."""
    client.force_login(admin_user)
    client.post(
        reverse("review:schema"),
        {
            "vocabulary": review_vocabulary.TYPE_WORDS,
            "value": "video",
            "label": "Video page",
        },
    )
    assert dispositions.TYPE_BECOMES["video"] == "not_article"


@pytest.mark.django_db
def test_renaming_the_same_type_twice_takes_the_second_name(client, admin_user):
    """A word re-said is a revision. get_or_create alone would find the
    row, leave it, and report success."""
    client.force_login(admin_user)
    for name in ("First name", "Second name"):
        client.post(
            reverse("review:schema"),
            {
                "vocabulary": review_vocabulary.TYPE_WORDS,
                "value": "opinion",
                "label": name,
            },
        )
    offered = {t["value"]: t["label"] for t in review_vocabulary.content_types()}
    assert offered["opinion"] == "Second name"


@pytest.mark.django_db
def test_a_flag_key_is_stored_as_it_is_written(client, admin_user):
    """`fold_value` is for words on a record -- "Digital Native" and
    "digital native" are one word. A flag is a key the pipeline writes,
    and folding turns `paywall_stub` into `paywall stub`, which matches
    nothing."""
    client.force_login(admin_user)
    client.post(
        reverse("review:schema"),
        {
            "vocabulary": review_vocabulary.FLAG_WORDS,
            "value": "paywall_stub",
            "label": "Paywalled stub",
        },
    )
    assert VocabularyTerm.objects.filter(
        vocabulary=review_vocabulary.FLAG_WORDS, value="paywall_stub"
    ).exists()


@pytest.mark.django_db
def test_a_flags_words_can_be_revised(client, admin_user):
    client.force_login(admin_user)
    client.post(
        reverse("review:schema"),
        {
            "vocabulary": review_vocabulary.FLAG_WORDS,
            "value": "paywall_stub",
            "label": "Paywalled stub",
            "spelling": "Only the first paragraph arrived",
        },
    )
    label, hint = review_vocabulary.flag_words("paywall_stub", "paywall_stub", "old")
    assert label == "Paywalled stub"
    assert hint == "Only the first paragraph arrived"


@pytest.mark.django_db
def test_a_revision_is_audited(client, admin_user):
    from audit.models import AuditLogEntry

    client.force_login(admin_user)
    client.post(
        reverse("review:schema"),
        {
            "vocabulary": review_vocabulary.FLAG_WORDS,
            "value": "minimal_capture",
            "label": "Almost empty",
        },
    )
    assert AuditLogEntry.objects.filter(action="schema:term").exists()


@pytest.mark.django_db
def test_an_unknown_vocabulary_is_refused(client, admin_user):
    """The handler takes a vocabulary name from a form."""
    client.force_login(admin_user)
    response = client.post(
        reverse("review:schema"),
        {"vocabulary": "something_else", "value": "x", "label": "y"},
    )
    assert response.status_code == 404


# --- the declaration answers where nothing is kept ---------------------------


@pytest.mark.django_db
def test_with_no_rows_the_shipped_words_are_used(client, admin_user):
    assert not VocabularyTerm.objects.filter(
        vocabulary=review_vocabulary.TYPE_WORDS
    ).exists()
    offered = {t["value"]: t["label"] for t in review_vocabulary.content_types()}
    assert offered["out_of_scope"] == "Non-local"
    assert offered["news"] == "News"


@pytest.mark.django_db
def test_a_row_for_a_type_that_no_longer_exists_is_ignored(client, admin_user):
    """A renamed word outliving its type would be a button whose value
    the submit path refuses."""
    VocabularyTerm.objects.create(
        vocabulary=review_vocabulary.TYPE_WORDS,
        value="a_type_that_was_removed",
        label="Gone",
    )
    review_vocabulary.forget()
    offered = {t["value"] for t in review_vocabulary.content_types()}
    assert "a_type_that_was_removed" not in offered
    assert offered == {t["value"] for t in dispositions.CONTENT_TYPES}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_offers_the_revised_words(client, admin_user, crawler_schema):
    """The point of revising them. A word changed on this page and not on
    the queue is a page that lies."""
    from review import kernel

    VocabularyTerm.objects.create(
        vocabulary=review_vocabulary.TYPE_WORDS,
        value="wire",
        label="Syndicated copy",
    )
    review_vocabulary.forget()

    # A row with a body: Reject is offered on those, and Reject is the
    # verb that carries the list.
    class Row:
        status = "not_article"
        text = "A captured body worth reading."
        content = text
        metadata = {}

    verbs = {v.name: v for v in kernel.get("extraction").offered(Row())}
    assert "reject" in verbs, f"offered: {sorted(verbs)}"
    labels = {entry["label"] for entry in verbs["reject"].values}
    assert "Syndicated copy" in labels
