"""`apply.sh` has to connect to the instance everything else uses.

Its default was `mizzou-db-prod`, and the database moved to
`mizzou-db-prod-ssd`. Every consumer was repointed -- the Cloud Build
deploy, the scheduled publish, the running service -- and this script was
not, so the one path that applies a permission change by hand pointed at
an instance that no longer exists.

Nothing caught it because nothing runs it: SQL changes here are rare, and
the gap between one and the next is long enough for the rest of the
repository to move underneath it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLY = ROOT / "infra/sql/apply.sh"
CLOUDBUILD = ROOT / "gcp/cloudbuild/cloudbuild-datadesk.yaml"
PUBLISH = ROOT / ".github/workflows/publish.yml"

INSTANCE = re.compile(r"mizzou-news-crawler:us-central1:[\w-]+")


def _instances(path):
    return set(INSTANCE.findall(path.read_text()))


def test_the_script_and_the_deploy_agree():
    """One instance name, in every place that connects to it."""
    applied = _instances(APPLY)
    deployed = _instances(CLOUDBUILD)
    assert applied, "apply.sh names no instance"
    assert deployed, "the deploy names no instance"
    assert applied == deployed, (
        f"apply.sh connects to {applied} and the deploy to {deployed}; "
        "the script that applies a grant by hand must reach the database "
        "the service is actually using"
    )


def test_the_scheduled_job_agrees_too():
    """publish.yml reads the crawler through the same proxy."""
    assert _instances(APPLY) == _instances(PUBLISH)


def test_nothing_still_points_at_the_instance_that_was_replaced():
    """`mizzou-db-prod` is gone. A name that resolves to nothing fails as
    a connection error, which reads like a network problem rather than a
    stale default."""
    for path in (APPLY, CLOUDBUILD, PUBLISH):
        for found in _instances(path):
            assert not found.endswith(":mizzou-db-prod"), f"{path.name}: {found}"
