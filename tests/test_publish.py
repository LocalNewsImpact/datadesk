"""The publish path: refresh versioned data without moving any pin.

Three triggers, three different things (the sibling repos' model):
CI on a push to any branch, Deploy on a merge to main, and Publish when
the data behind a visual should be refreshed. Publishing is curation and
produces no git event, so the console dispatches one.

The rule these tests hold is SCOPE.md §2.7's: a published report must not
change under its readers. A scheduled refresh may take a new snapshot
version; only a person may decide embeds should serve it.
"""

from io import StringIO
from unittest import mock

import pytest
import yaml
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError

from accounts.models import DATADESK, Grant
from visuals.models import Visual

pytestmark = pytest.mark.django_db


def _user(username, role):
    user = User.objects.create_user(username, email=f"{username}@localnewsimpact.org")
    if role:
        Grant.objects.create(user=user, app=DATADESK, scope="", role=role)
    return user


@pytest.fixture
def admin():
    return _user("boss", "admin")


@pytest.fixture
def published(admin):
    """A published visual carrying inline data, pinned at v1."""
    from visuals.services import record_snapshot

    visual = Visual.objects.create(
        slug="story-map",
        title="Story geography",
        source_kind="inline",
        created_by=admin,
    )
    snapshot = record_snapshot(visual, admin, [{"a": 1}])
    visual.pinned_snapshot = snapshot
    visual.status = Visual.PUBLISHED
    visual.save()
    return visual


def _run(**kwargs):
    out, err = StringIO(), StringIO()
    call_command("publish_visuals", stdout=out, stderr=err, **kwargs)
    return out.getvalue()


# --- the actor is real, or the command refuses ------------------------------


def test_an_unknown_actor_is_refused(published):
    with pytest.raises(CommandError, match="No account"):
        _run(actor="nobody@localnewsimpact.org")


def test_a_non_admin_actor_is_refused(published):
    _user("reader", "viewer")
    with pytest.raises(CommandError, match="admin role"):
        _run(actor="reader@localnewsimpact.org")


def test_the_command_never_invents_an_actor(published):
    """A snapshot is an audited action (SCOPE.md §2.1), so --actor is
    required rather than defaulted to a machine account."""
    with pytest.raises(CommandError):
        call_command("publish_visuals", stdout=StringIO())


# --- refreshing does not re-pin ---------------------------------------------


def test_refresh_takes_a_version_without_moving_the_pin(published, admin):
    pinned_before = published.pinned_snapshot.version
    with mock.patch("visuals.services.fetch_source_data", return_value=[{"a": 2}]):
        output = _run(actor=admin.email)

    published.refresh_from_db()
    assert published.snapshots.count() == 2
    assert published.pinned_snapshot.version == pinned_before
    assert "embeds still serve" in output


def test_repin_moves_it_and_says_so(published, admin):
    with mock.patch("visuals.services.fetch_source_data", return_value=[{"a": 2}]):
        output = _run(actor=admin.email, repin=True)

    published.refresh_from_db()
    assert published.pinned_snapshot.version == 2
    assert "pinned" in output


def test_drafts_are_left_alone(published, admin):
    draft = Visual.objects.create(
        slug="draft", title="Draft", source_kind="inline", created_by=admin
    )
    with mock.patch("visuals.services.fetch_source_data", return_value=[{"a": 2}]):
        _run(actor=admin.email)
    assert draft.snapshots.count() == 0


def test_a_slug_narrows_the_run(published, admin):
    other = Visual.objects.create(
        slug="other",
        title="Other",
        source_kind="inline",
        created_by=admin,
        status=Visual.PUBLISHED,
    )
    with mock.patch("visuals.services.fetch_source_data", return_value=[{"a": 2}]):
        _run(actor=admin.email, slug=["story-map"])
    assert other.snapshots.count() == 0
    assert published.snapshots.count() == 2


def test_dry_run_writes_nothing(published, admin):
    output = _run(actor=admin.email, dry_run=True)
    assert "would refresh" in output
    assert published.snapshots.count() == 1


def test_one_unreachable_source_does_not_stop_the_rest(published, admin):
    from visuals.services import DataSourceError

    broken = Visual.objects.create(
        slug="broken",
        title="Broken",
        source_kind="inline",
        created_by=admin,
        status=Visual.PUBLISHED,
    )

    def fetch(visual):
        if visual.slug == "broken":
            raise DataSourceError("BigQuery said no")
        return [{"a": 2}]

    # The run still fails, so a red workflow reports the gap.
    with (
        mock.patch("visuals.services.fetch_source_data", side_effect=fetch),
        pytest.raises(CommandError, match="could not be refreshed"),
    ):
        _run(actor=admin.email)

    assert published.snapshots.count() == 2
    assert broken.snapshots.count() == 0


# --- the console's dispatch -------------------------------------------------


def test_publishing_dispatches_the_event(published, admin):
    from visuals.services import publish

    with mock.patch("visuals.services.notify_published") as notify:
        publish(published, admin)
    notify.assert_called_once_with("story-map")


def test_the_dispatch_is_a_no_op_when_unconfigured(monkeypatch):
    from visuals import dispatch

    monkeypatch.delenv("GITHUB_DISPATCH_REPO", raising=False)
    monkeypatch.delenv("GITHUB_DISPATCH_TOKEN", raising=False)
    assert dispatch.notify_published("story-map") is False


def test_an_unreachable_github_does_not_break_publishing(monkeypatch, published, admin):
    """The pin is already committed by then; a failed notification must
    not turn a successful publish into an error page."""
    from visuals import dispatch
    from visuals.services import publish

    monkeypatch.setenv("GITHUB_DISPATCH_REPO", "LocalNewsImpact/datadesk")
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "x")
    with mock.patch.object(
        dispatch.urllib.request, "urlopen", side_effect=OSError("no route")
    ):
        assert dispatch.notify_published("story-map") is False
        publish(published, admin)  # does not raise

    published.refresh_from_db()
    assert published.status == Visual.PUBLISHED


def test_the_dispatch_sends_the_slug_and_event_type(monkeypatch):
    from visuals import dispatch

    monkeypatch.setenv("GITHUB_DISPATCH_REPO", "LocalNewsImpact/datadesk")
    monkeypatch.setenv("GITHUB_DISPATCH_TOKEN", "secret")
    sent = {}

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout=None):
        sent["url"] = request.full_url
        sent["body"] = request.data.decode()
        sent["auth"] = request.get_header("Authorization")
        return Response()

    with mock.patch.object(dispatch.urllib.request, "urlopen", urlopen):
        assert dispatch.notify_published("story-map") is True

    assert sent["url"].endswith("/repos/LocalNewsImpact/datadesk/dispatches")
    assert '"event_type": "publish-visuals"' in sent["body"]
    assert '"slug": "story-map"' in sent["body"]
    assert sent["auth"] == "Bearer secret"


# --- the three workflows are three different triggers -----------------------


def _workflow(name):
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / ".github" / "workflows" / name
    # yaml parses the `on:` key as the boolean True.
    return yaml.safe_load(path.read_text())


def test_ci_runs_on_every_branch_and_on_pull_requests():
    triggers = _workflow("ci.yml")[True]
    assert "push" in triggers
    # No branch filter: a contributor sees failures before opening a PR.
    assert triggers["push"] is None
    assert "pull_request" in triggers
    assert "workflow_dispatch" in triggers


def test_ci_runs_the_gates_in_the_crawlers_order():
    lint = _workflow("ci.yml")["jobs"]["lint"]["steps"]
    names = [step.get("name") for step in lint if step.get("name")]
    assert names[-4:] == ["ruff", "black", "isort", "mypy"]
    tests = _workflow("ci.yml")["jobs"]["tests"]["steps"]
    assert any("pytest" in (step.get("run") or "") for step in tests)


def test_deploy_runs_only_on_main_and_skips_documentation():
    triggers = _workflow("deploy.yml")[True]
    assert triggers["push"]["branches"] == ["main"]
    ignored = triggers["push"]["paths-ignore"]
    assert "**.md" in ignored
    assert "docs/**" in ignored
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers


def test_publish_is_a_separate_trigger_from_deploy():
    triggers = _workflow("publish.yml")[True]
    assert triggers["repository_dispatch"]["types"] == ["publish-visuals"]
    assert triggers["workflow_run"]["workflows"] == ["Deploy"]
    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    # Publishing is not a push event: it must not run on one.
    assert "push" not in triggers


def test_the_dispatch_event_type_matches_the_workflow():
    from visuals.dispatch import EVENT_TYPE

    types = _workflow("publish.yml")[True]["repository_dispatch"]["types"]
    assert types == [EVENT_TYPE]


def test_publish_skips_rather_than_fails_when_unconfigured():
    condition = _workflow("publish.yml")["jobs"]["publish"]["if"]
    assert "vars.WIF_PROVIDER != ''" in condition
    assert "vars.PUBLISH_ACTOR != ''" in condition
    # And never publishes from a revision that failed to deploy.
    assert "workflow_run.conclusion == 'success'" in condition


# --- class D: internal use only ----------------------------------------------


INTERNAL_SPEC = {
    "roles": {"x": "cin_primary", "y": "cost_sum"},
    "measure": "cost_sum",
    "dimensions": ["cin_primary"],
}
PUBLISHABLE_SPEC = {
    "roles": {"x": "cin_primary", "y": "articles"},
    "measure": "articles",
    "dimensions": ["cin_primary"],
}


def _visual(admin, spec):
    from visuals.services import record_snapshot

    visual = Visual.objects.create(
        slug=f"v-{abs(hash(str(spec))) % 10000}",
        title="Spending by need",
        source_kind="inline",
        created_by=admin,
        spec=spec,
        config={"kind": "bar", "theme": "datadesk"},
    )
    record_snapshot(visual, admin, [{"a": 1}])
    return visual


@pytest.mark.django_db(databases=["default", "crawler"])
def test_an_internal_field_cannot_be_published(admin):
    """Class D is internal use only. Build it, look at it, take the CSV --
    it may not go on a page a reader can reach, because what these say is a
    fact about our pipeline that a reader meets as a fact about the
    journalism."""
    from visuals.services import NotPublishable, publish

    visual = _visual(admin, INTERNAL_SPEC)
    with pytest.raises(NotPublishable) as raised:
        publish(visual, admin)
    # Named, because "cannot publish" without saying which field is a dead
    # end when several were chosen.
    assert "Cost (sum, USD)" in str(raised.value)
    visual.refresh_from_db()
    assert visual.status != Visual.PUBLISHED


@pytest.mark.django_db(databases=["default", "crawler"])
def test_a_published_visual_cannot_acquire_one_by_editing(admin):
    """Republishing is the same door."""
    from visuals.services import NotPublishable, publish

    visual = _visual(admin, PUBLISHABLE_SPEC)
    publish(visual, admin)
    visual.refresh_from_db()
    assert visual.status == Visual.PUBLISHED
    pinned = visual.pinned_snapshot_id

    visual.spec = INTERNAL_SPEC
    visual.save(update_fields=["spec"])
    with pytest.raises(NotPublishable):
        publish(visual, admin)
    # What is already serving is left alone: it was published under the
    # rules of its day, and pulling it is a separate decision.
    visual.refresh_from_db()
    assert visual.pinned_snapshot_id == pinned


def test_narrowing_by_an_internal_field_counts_as_using_it():
    """Filtering to the stories a gate excluded and publishing the chart
    publishes the gate's opinion, even though the reason never appears on
    an axis."""
    from visuals.corpus import internal_fields

    assert internal_fields(
        {
            "roles": {"x": "cin_primary"},
            "measure": "articles",
            "only": {"content_gate_reason": ["boilerplate"]},
        }
    ) == ["Why the gate excluded it"]
    # ...and an ordinary spec is not caught by it.
    assert internal_fields(PUBLISHABLE_SPEC) == []


@pytest.mark.django_db(databases=["default", "crawler"])
def test_everything_but_publishing_still_works(admin):
    """The rule is about one door, not about the field: a snapshot of an
    internal report can still be taken and read in the console."""
    from visuals.services import record_snapshot

    visual = _visual(admin, INTERNAL_SPEC)
    snapshot = record_snapshot(visual, admin, [{"Cost (sum, USD)": 1.25}])
    assert snapshot.version >= 1
    assert visual.snapshots.count() >= 1
