"""Wire was the largest exclusion in the corpus and the last one nobody
could check.

Measured 2026-09-07 against March 2026 Mizzou, the only cleaned window:
12,962 articles kept, 9,335 excluded as wire. Obituaries and minimal
captures were reviewable and have been reviewed in quantity; wire was
not, so a wrongly excluded article was simply absent -- a Type II error
with no other surface, which is the expensive kind, because nothing
downstream ever looks at what was removed.

The case does not doubt-rank. Several of the methods behind these
attributions are already at or above 99% precision, and a method that
good does not earn a reviewer's time -- so the axis that matters is
WHICH method decided, not how sure it was. `articles.wire` records the
syndication each row was attributed to, and working the queue one
syndication at a time is what makes precision per method fall out of the
review. That number is what says which of them can run unreviewed.
"""

import pytest

from explorer.models import Article, CandidateLink
from review import queue as q


def _article(**kw):
    source = kw.pop("source", None)
    link = CandidateLink.objects.create(
        id=kw.pop("link_id", "cl-1"),
        url=kw.pop("url", "https://a.example/one"),
        source=source,
    )
    fields = {
        "id": kw.pop("id", "a-1"),
        "candidate_link": link,
        "status": "wire",
        "wire_check_status": "complete",
    }
    fields.update(kw)
    return Article.objects.create(**fields)


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_case_is_offered_at_all(crawler_schema):
    """It has to be in all three maps or the queue cannot render it."""
    assert q.CASE_STATUS[q.WIRE_EXCLUSION] == "wire"
    assert q.CASE_LABELS[q.WIRE_EXCLUSION]
    assert q.CASE_NOTES[q.WIRE_EXCLUSION]


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_wire_article_reaches_the_queue(crawler_schema):
    _article(id="w1", wire=["NPR"])

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.WIRE_EXCLUSION))
        .values_list("id", flat=True)
    )

    assert ids == {"w1"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_case_does_not_narrow_to_a_doubted_subset(crawler_schema):
    """Unlike obituaries, which the detector scores. Nothing records how
    sure a wire decision was, so narrowing would be inventing a signal."""
    _article(id="w1", wire=["NPR"])
    _article(id="w2", link_id="cl-2", url="https://a.example/two", wire=None)

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.WIRE_EXCLUSION))
        .values_list("id", flat=True)
    )

    assert ids == {"w1", "w2"}, "a row with no recorded service is still excluded"


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_queue_can_be_worked_one_syndication_at_a_time(crawler_schema):
    """The axis the review is allocated along. A method at 99% precision
    should not bury one that is not."""
    _article(id="npr", wire=["NPR"])
    _article(
        id="ap",
        link_id="cl-2",
        url="https://a.example/two",
        wire=["The Associated Press"],
    )

    qs = q._apply_common(
        q.base_queryset_unscoped().filter(q._case_q(q.WIRE_EXCLUSION)),
        {"service": "NPR"},
    )

    assert set(qs.values_list("id", flat=True)) == {"npr"}


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_article_naming_several_syndications_answers_to_each(crawler_schema):
    """`articles.wire` is an array and rows carry more than one. Filtering
    on NPR must find "NPR, The Associated Press", or the counts per
    service are wrong in the direction that hides work."""
    _article(id="both", wire=["NPR", "The Associated Press"])

    for service in ("NPR", "The Associated Press"):
        qs = q._apply_common(
            q.base_queryset_unscoped().filter(q._case_q(q.WIRE_EXCLUSION)),
            {"service": service},
        )
        assert set(qs.values_list("id", flat=True)) == {"both"}, service


# --- obituaries: the headline is the signal -----------------------------------
#
# The obituary case used to select `confidence_score < 0.30` and no
# corroborating evidence, on the reading that a low score marks a doubtful
# call. Measured against the reviews it does not. Every obituary a person
# has ruled on scored 0.167, the floor, and 88.5% of them were right --
# so the number counts matched signals and does not rank.
#
# The headline does. An obituary headline is a name; a story about a death
# is a sentence, and a sentence has a lowercase word in it.


def _obit(title, status="obituary", **kw):
    from explorer.models import Article, CandidateLink

    link = CandidateLink.objects.create(
        id=kw.pop("link_id", f"cl-{abs(hash(title)) % 10**8}"),
        url=f"https://a.example/{abs(hash(title)) % 10**8}",
    )
    return Article.objects.create(
        id=kw.pop("id", f"a-{abs(hash(title)) % 10**8}"),
        candidate_link=link,
        status=status,
        wire_check_status="complete",
        title=title,
    )


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize(
    "title",
    [
        "Alice Theresa Kline",
        "ANNA LEE VANSKIKE",
        "Allen Ray Shaeffer (December 30, 1949 - March 9, 2026)",
        "Betty “Colleen” Minton",
        "Alice Ann Meyer - Warren County Record",
    ],
)
def test_a_death_notice_is_not_sent_to_review(crawler_schema, title):
    """These are the 745 the reviewer should not have to read."""
    _obit(title)

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.DOUBTED_CONTENT_TYPE))
        .values_list("id", flat=True)
    )

    assert ids == set(), title


@pytest.mark.django_db(databases=["default", "crawler"])
@pytest.mark.parametrize(
    "title",
    [
        "Carthage man killed in motorcycle crash on Gum Road",
        "Community pays tribute to Missouri deputies killed in line of duty",
        "Former SEMO Sports Information Director Ron Hines passes away at 82",
        "Funeral and procession set for former Columbia fire captain",
        "JEFFTRAN transit services to close March 13",
    ],
)
def test_a_story_about_a_death_is_sent_to_review(crawler_schema, title):
    """These are the mistakes. Roughly half of what the rule surfaces."""
    article = _obit(title)

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.DOUBTED_CONTENT_TYPE))
        .values_list("id", flat=True)
    )

    assert ids == {article.id}, title


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_particle_inside_a_name_is_not_a_sentence(crawler_schema):
    """ "van", "de" and "Jr" are lowercase and belong to the name. Reading
    them as verbs would send every Dutch surname to review."""
    _obit("Hendrik van der Berg")
    _obit("Maria de la Cruz", link_id="cl-x", id="a-x")

    ids = set(
        q.base_queryset_unscoped()
        .filter(q._case_q(q.DOUBTED_CONTENT_TYPE))
        .values_list("id", flat=True)
    )

    assert ids == set()


# --- the case holds what has no evidence behind it -----------------------------
#
# Measured on 2026-09-08 against every wire decision a reviewer had made:
# each method that recorded a reason was right every time it was checked,
# and both wrong calls sat in the rows that recorded nothing. Showing all
# 47,441 asked a reviewer to read 46 correct exclusions for every doubtful
# one; March alone was 9,455 rows against 1,010 worth reading.


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_wire_call_that_recorded_its_method_is_not_surfaced(crawler_schema):
    """`wire_detection` is the wire writers' own key."""
    _article(
        id="a-method",
        link_id="cl-method",
        url="https://a.example/method",
        metadata={"wire_detection": {"AP": {"detected_by": "canonical_cross_domain"}}},
    )
    assert not Article.objects.filter(q._case_q(q.WIRE_EXCLUSION)).exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_detector_that_named_the_service_is_not_surfaced(crawler_schema):
    """The content-type detector records the same fact under a different
    key -- `content_type_detection.evidence.detected_services` -- when it
    reaches the verdict by byline, dateline, metadata or URL."""
    _article(
        id="a-tier",
        link_id="cl-tier",
        url="https://a.example/tier",
        metadata={
            "content_type_detection": {
                "reason": "wire_service_detected",
                "evidence": {
                    "detection_tier": "byline",
                    "detected_services": ["Associated Press"],
                },
            }
        },
    )
    assert not Article.objects.filter(q._case_q(q.WIRE_EXCLUSION)).exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_wire_call_with_no_reason_is_surfaced(crawler_schema):
    """25,043 rows record nothing at all. Both wrong calls found so far
    were among them."""
    _article(
        id="a-silent",
        link_id="cl-silent",
        url="https://a.example/silent",
        metadata={"extraction_method": "mcmetadata"},
    )
    assert Article.objects.filter(q._case_q(q.WIRE_EXCLUSION)).count() == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_the_chip_counts_what_the_list_shows(crawler_schema):
    """The count and the rows come from one selector, so a narrowed case
    cannot advertise a number it will not show."""
    _article(id="a-1", link_id="cl-1", url="https://a.example/1", metadata={})
    _article(
        id="a-2",
        link_id="cl-2",
        url="https://a.example/2",
        metadata={"wire_detection": {"AP": {"detected_by": "meta_author"}}},
    )
    selector = q._case_q(q.WIRE_EXCLUSION)
    assert Article.objects.filter(selector).count() == 1


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_byline_naming_a_service_is_not_doubtful(crawler_schema):
    """The first cut of this case surfaced 703 March rows and a reviewer
    found them obvious: 500 named a wire service or a syndicator in the
    byline. The detector had recorded nothing, so the rule called them
    unexplained -- but the explanation was in the author field."""
    for i, byline in enumerate(
        [
            "JON GAMBRELL, DAVID RISING and SAMY MAGDY Associated Press",
            "Sandee LaMotte, CNN",
            # Eight spellings of one newsroom reached production; a rule
            # needing the punctuation to agree would catch a quarter.
            "Rudi Keller Missouri Independent",
            "Rudi Keller | Missouri Independent",
            "Rudi Keller - MISSOURI INDEPENDENT",
            "Annelise Hanshaw ~ Missouri Independent",
            "NBC Olympics",
        ]
    ):
        _article(
            id=f"a-named-{i}",
            link_id=f"cl-named-{i}",
            url=f"https://a.example/named/{i}",
            author=byline,
            metadata={"extraction_method": "mcmetadata"},
        )
    assert not Article.objects.filter(q._case_q(q.WIRE_EXCLUSION)).exists()


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_byline_that_explains_nothing_is_still_doubtful(crawler_schema):
    """`Dave Skretta` is an AP writer whose byline lost the agency. That
    the reviewer can recognise him is not something the row records, so
    it stays in the queue where somebody decides it."""
    _article(
        id="a-bare",
        link_id="cl-bare",
        url="https://a.example/bare",
        author="Dave Skretta",
        metadata={"extraction_method": "mcmetadata"},
    )
    assert Article.objects.filter(q._case_q(q.WIRE_EXCLUSION)).count() == 1
