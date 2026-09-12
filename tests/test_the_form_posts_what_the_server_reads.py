"""The contract between the page, the scripts and `posted()`.

THREE PRODUCTION FAILURES CAME THROUGH THIS GAP. The view tests post a
body written by hand --

    client.post(QUEUE, {"d-a1": "set_place", "v-a1-set_place": "Linn, MO"})

-- which proves the server handles a form this file CLAIMS the page
produces. It never proved the page produces it. The real form posted
`v-<id>-set_place: "set_place"` and no `d-` key at all, so `posted()`
returned nothing, every submission was a silent 302, and all the
server-side tests stayed green.

The cause was a selector. `review-queue.js` found the decision field as
"the first hidden input in the row" while its own comment said
`name="d-<id>"`; the geography queue's chip field hides the value boxes,
which made one of those the first hidden input.

So these tests render the real page, apply the scripts' OWN selectors to
it with a real CSS engine, and model the one DOM change the chip script
makes. No browser, but the same question a browser would answer: does
the thing the script grabs turn out to be the thing the server reads?
"""

import datetime as dt
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse
from django.utils import timezone

pytestmark = pytest.mark.django_db(databases=["default", "crawler"])

ROOT = Path(__file__).resolve().parents[1]
QUEUE = reverse("review:geography")


@pytest.fixture
def reviewer(db):
    from django.contrib.auth.models import User

    user = User.objects.create_user("ed", email="ed@localnewsimpact.org")
    user.is_superuser = user.is_staff = True
    user.save()
    return user


@pytest.fixture
def unplaced(crawler_schema):
    from explorer.models import (
        Article,
        ArticleEnrichment,
        CandidateLink,
        Dataset,
        DatasetSource,
        Source,
    )

    dataset = Dataset.objects.create(
        id="d1", slug="mo", label="Missouri", meta={"default_state": "MO"}
    )
    source = Source.objects.create(
        id="s1",
        host="u.example",
        host_norm="u.example",
        canonical_name="The Unterrified Democrat",
        county="Osage",
        meta={"state": "MO"},
    )
    DatasetSource.objects.create(id="ds1", dataset=dataset, source=source)
    link = CandidateLink.objects.create(
        id="c1", source=source, url="https://u.example/1"
    )
    article = Article.objects.create(
        id="a1",
        status="enrichment_skipped",
        candidate_link=link,
        dataset_id=dataset.id,
        title="A story nobody placed",
        publish_date=timezone.make_aware(dt.datetime(2026, 3, 10, 12)),
    )
    ArticleEnrichment.objects.create(
        article=article, scope="local", skip_reason="paywall_stub", cost_usd="0.00"
    )
    return article


def _props(client):
    """Every decision row on the rendered page, as the browser sees it."""
    page = client.get(QUEUE + "?days=all")
    soup = BeautifulSoup(page.content.decode(), "lxml")
    props = soup.select(".prop[data-id]")
    assert props, "the page rendered no decision rows to test"
    return props


def _js_selector(filename, pattern):
    """A selector taken from the script itself, not retyped here.

    Retyping it would test this file against itself: the script could
    change and these would go on asserting the old contract.
    """
    source = (ROOT / "static/js" / filename).read_text()
    found = re.search(pattern, source)
    assert found, f"{filename} no longer matches {pattern!r}"
    return found.group(1)


def test_the_decision_field_is_what_the_script_grabs(client, reviewer, unplaced):
    """THE BUG. `store()` resolved to the value box, so the pressed verb
    was written into `v-<id>-set_place` and `d-<id>` stayed empty."""
    client.force_login(reviewer)
    selector = _js_selector(
        "review-queue.js",
        r"const store = \(row\) =>\s*row\.querySelector\(\s*'([^']+)'",
    )

    for prop in _props(client):
        found = prop.select_one(selector)
        assert found is not None, f"{selector} matches nothing in a row"
        assert found.get("name", "").startswith("d-"), (
            f"{selector} found {found.get('name')!r}, which `posted()` "
            "does not read -- it reads only 'd-' keys"
        )


def test_the_selector_still_holds_once_the_chip_field_hides_the_boxes(
    client, reviewer, unplaced
):
    """`geography-suggest.js` sets `store.type = "hidden"` on every
    `.fixval` text box. That one line is what broke the selector, because
    it made a value box the first hidden input in the row.

    Modelled here rather than run, so the interaction between two scripts
    is asserted by something other than a person trying it.
    """
    client.force_login(reviewer)
    selector = _js_selector(
        "review-queue.js",
        r"const store = \(row\) =>\s*row\.querySelector\(\s*'([^']+)'",
    )

    for prop in _props(client):
        # What the chip script does to the DOM before any submit.
        for box in prop.select('input.fixval[type="text"]'):
            box["type"] = "hidden"

        found = prop.select_one(selector)
        assert found is not None
        assert found.get("name", "").startswith("d-"), (
            "after the chip field hides the value boxes, the script grabs "
            f"{found.get('name')!r} instead of the decision field"
        )


def test_every_name_the_page_posts_is_one_the_server_reads(client, reviewer, unplaced):
    """The other half: a field the page posts that `posted()` ignores is
    a decision that silently disappears."""
    from review.submit import DECISION_PREFIX, VALUE_PREFIX

    client.force_login(reviewer)
    for prop in _props(client):
        subject = prop["data-id"]
        names = [i.get("name") for i in prop.select("input[name]")]
        assert f"{DECISION_PREFIX}{subject}" in names, (
            f"the row posts no {DECISION_PREFIX}{subject} field, so "
            "`posted()` reads no decision for it"
        )
        for name in names:
            assert name.startswith(
                (DECISION_PREFIX, VALUE_PREFIX)
            ), f"{name!r} is posted and read by nothing"


def test_the_value_box_is_named_for_its_own_verb(client, reviewer, unplaced):
    """`posted()` looks for `v-<id>-<verb>` first. A row whose verbs take
    different vocabularies posts one box each, and a box named for the
    wrong verb is read as the wrong answer."""
    client.force_login(reviewer)
    for prop in _props(client):
        subject = prop["data-id"]
        for box in prop.select("input.fixval[name], select.fixval[name]"):
            verb = box.get("data-verb")
            assert verb, "a value box with no verb cannot be matched to one"
            assert box["name"] == f"v-{subject}-{verb}"


def test_a_verb_button_names_a_verb_the_queue_declares(client, reviewer, unplaced):
    """A button posting a verb the queue does not offer is refused by
    `submit`, silently, as "somebody else got there first"."""
    from review import kernel

    client.force_login(reviewer)
    declared = {v.name for v in kernel.get("geography").verbs}
    for prop in _props(client):
        pressed = {b["data-verb"] for b in prop.select("button.verb[data-verb]")}
        assert pressed
        assert pressed <= declared, f"{pressed - declared} is not a geography verb"
