"""Two syndicators shown as five.

Each detection method writes what it found: a byline says "Associated
Press", a dateline "AP National", a canonical tag "tvinsider.com". So
March 2026 reads

    The Associated Press   1,725
    Associated Press         598
    AP National              330
    tvinsider.com            438
    TV Insider               437

and the service filter offers every spelling separately, each finding
part of the work.
"""

import pytest

from review.syndicators import SEED, label_for, spellings_of


@pytest.mark.django_db
@pytest.mark.parametrize(
    "written,label",
    [
        ("The Associated Press", "The Associated Press"),
        ("Associated Press", "The Associated Press"),
        ("AP National", "The Associated Press"),
        ("tvinsider.com", "TV Insider"),
        ("TV Insider", "TV Insider"),
        ("CNN NewsSource", "CNN"),
        ("Missouri Independent", "The Missouri Independent"),
    ],
)
def test_every_spelling_reads_as_one_label(written, label):
    assert label_for(written) == label


@pytest.mark.django_db
@pytest.mark.parametrize("host", ["liveinformed.com", "fooddrinklife.com", "NPR"])
def test_anything_ungrouped_is_its_own_label(host):
    """287 hosts appear that nothing else records. The domain standing as
    its own name is the rule, not a gap waiting to be filled."""
    assert label_for(host) == host


@pytest.mark.django_db
def test_the_filter_finds_every_spelling_of_a_label():
    """The list offers one Associated Press and the corpus holds three
    names for it. A filter matching only the label finds 1,725 of 2,653
    and looks like the answer."""
    found = {s.lower() for s in spellings_of("The Associated Press")}
    assert {"associated press", "ap national", "the associated press"} <= found


@pytest.mark.django_db
def test_an_ungrouped_name_filters_on_itself():
    assert spellings_of("liveinformed.com") == ["liveinformed.com"]


@pytest.mark.django_db
def test_a_vocabulary_row_overrides_the_seed():
    """Groups are seeded in code and edited in rows, the pattern
    `datasets/publishers.py` uses. Seeded rather than counted, because a
    run of records spelt badly must not make the bad spelling canonical
    -- here the two AP variants are 928 rows against the right name."""
    from datasets.models import VocabularyTerm
    from review.syndicators import SYNDICATOR_WORDS

    VocabularyTerm.objects.create(
        vocabulary=SYNDICATOR_WORDS, value="talker.news", label="Talker News"
    )
    assert label_for("talker.news") == "Talker News"


def test_the_seed_only_groups_what_the_corpus_spells_twice():
    """A name appearing once needs no group, and inventing groups for the
    287 domain-only hosts would be the controlled vocabulary this
    deliberately does not have."""
    for label, spellings in SEED:
        assert len(spellings) > 1, f"{label} groups a single spelling"


# --- maintained on the schema page ------------------------------------------


@pytest.fixture
def an_admin(db):
    from django.contrib.auth.models import User

    from accounts.models import DATADESK, Grant

    user = User.objects.create_user("adm", email="adm@localnewsimpact.org")
    Grant.objects.create(user=user, app=DATADESK, scope="", role="admin")
    return user


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_groups_are_on_the_schema_page(client, an_admin):
    """Seeded groups appear too, or the page says the corpus folds
    nothing while it is folding five names into two."""
    from django.urls import reverse

    client.force_login(an_admin)
    body = client.get(reverse("review:schema")).content.decode()
    assert "Syndicators" in body
    assert "The Associated Press" in body
    assert "ap national" in body


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_spelling_can_be_added_from_the_page(client, an_admin):
    """A new spelling arrives on a Tuesday and should not wait for a
    deploy -- the argument the publisher vocabularies already make."""
    from django.urls import reverse

    from datasets.models import VocabularyTerm

    client.force_login(an_admin)
    client.post(
        reverse("review:schema"),
        {"vocabulary": "syndicator", "label": "Talker News", "value": "Talker.News"},
    )
    # Folded on save: "Talker.News" and "talker.news" are one spelling.
    term = VocabularyTerm.objects.get(vocabulary="syndicator", value="talker.news")
    assert term.label == "Talker News"
    assert label_for("talker.news") == "Talker News"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_spelling_can_be_retired_and_stops_folding(client, an_admin):
    """Retired, never deleted: the spelling is still on records written
    while it was folded."""
    from django.urls import reverse

    from datasets.models import VocabularyTerm
    from review.syndicators import SYNDICATOR_WORDS

    VocabularyTerm.objects.create(
        vocabulary=SYNDICATOR_WORDS, value="talker.news", label="Talker News"
    )
    client.force_login(an_admin)
    client.post(
        reverse("review:schema"),
        {"vocabulary": "syndicator", "retire": "talker.news"},
    )
    assert VocabularyTerm.objects.get(value="talker.news").retired is True
    assert label_for("talker.news") == "talker.news"
