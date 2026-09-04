"""Choosing a type takes the article out of enrichment.

The rule, as the queue's users state it: if anything is chosen in
"Actually it is", the article is not enriched any further and does not
stay in the export -- with one exception.

    out of scope       not local
    not an article     not processed, not exported
    obituary           not processed, not exported
    opinion            not processed, not exported
    wire               not processed, not exported
    weather            not processed, not exported
    paywalled stub     EXPORTED, not enriched any further

The exception is the paywalled stub. Its text is a teaser or a login
wall, but the CIN label and the byline are real observations, and
excluding it would throw them away. `enrichment_skipped` is the status
that means exactly that: in the export, not enriched.

Writing the `paywall` status instead -- which is what "record the type
the reviewer chose" did before -- took it out of the export and lost
them.
"""

import pytest
from django.contrib.auth.models import User

from explorer.models import Article, CandidateLink, Source
from review.dispositions import (
    EXPORTED_STATUSES,
    EXTRACTION,
    TYPE_BECOMES,
    record,
)
from review.models import ReviewDecision

#: Not processed and not exported. The whole list except the stub.
EXCLUDING = ("not_article", "obituary", "weather", "opinion", "wire", "out_of_scope")


@pytest.fixture
def reviewer(db):
    return User.objects.create_user("ed", email="ed@localnewsimpact.org")


def _article(pk, crawler_schema, status="labeled"):
    source, _ = Source.objects.get_or_create(
        id="s1", defaults={"host": "a.example", "host_norm": "a.example"}
    )
    link = CandidateLink.objects.create(id=f"c{pk}", source_id=source.id, url="u")
    return Article.objects.create(
        id=pk,
        candidate_link=link,
        status=status,
        wire_check_status="complete",
        content="A captured body.",
        text="A captured body.",
        enrichment_attempts=0,
    )


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize("chosen", EXCLUDING)
def test_a_chosen_type_takes_it_out_of_the_export(chosen, reviewer, crawler_schema):
    article = _article(f"a-{chosen}", crawler_schema)
    record(
        article,
        decision="accept",
        stage=EXTRACTION,
        user=reviewer,
        content_type=chosen,
    )
    article.refresh_from_db()
    assert article.status == chosen
    assert article.status not in EXPORTED_STATUSES


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_paywalled_stub_stays_in_the_export(reviewer, crawler_schema):
    """Its text is a teaser and its label and byline are real. Writing
    the `paywall` status would take it out and lose them."""
    article = _article("a-paywall", crawler_schema)
    record(
        article,
        decision="accept",
        stage=EXTRACTION,
        user=reviewer,
        content_type="paywall",
    )
    article.refresh_from_db()
    assert article.status == "enrichment_skipped"
    assert article.status in EXPORTED_STATUSES


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize("chosen", (*EXCLUDING, "paywall"))
def test_no_chosen_type_leaves_it_where_enrichment_looks(
    chosen, reviewer, crawler_schema
):
    """`labeled` is what enrichment selects. Every type has to move the
    article off it, or "not enriched further" is not true of any of them."""
    article = _article(f"b-{chosen}", crawler_schema)
    record(
        article,
        decision="accept",
        stage=EXTRACTION,
        user=reviewer,
        content_type=chosen,
    )
    article.refresh_from_db()
    assert article.status != "labeled"


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize("chosen", (*EXCLUDING, "paywall"))
def test_the_type_is_recorded_and_not_only_acted_on(chosen, reviewer, crawler_schema):
    """The status says where the article went. This says what somebody
    decided it is, which is the thing worth counting against a detector
    that keeps getting it wrong."""
    article = _article(f"c-{chosen}", crawler_schema)
    record(
        article,
        decision="accept",
        stage=EXTRACTION,
        user=reviewer,
        content_type=chosen,
    )
    assert ReviewDecision.objects.get(subject_id=article.id).wrote["content_type"] == (
        chosen
    )


def test_every_offered_type_has_a_status():
    """A type in the list with no mapping would raise on submit, which is
    a form the page offers and the server refuses."""
    from review.dispositions import CONTENT_TYPES

    for offered in CONTENT_TYPES:
        assert offered["value"] in TYPE_BECOMES, f"{offered['value']} does nothing"


def test_only_the_stub_is_exported():
    exported = {
        chosen for chosen, status in TYPE_BECOMES.items() if status in EXPORTED_STATUSES
    }
    assert exported == {"paywall"}
