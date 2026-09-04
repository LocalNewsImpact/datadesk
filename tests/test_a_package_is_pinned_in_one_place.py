"""A dependency pinned in two files is pinned by whichever runs last.

`Dockerfile.base` installed lnic-contracts from an ARG defaulting to
v0.1.0 while `requirements.txt` pinned it too. Bumping the requirements
pin changed the base image's hash, so the base rebuilt, installed v0.2.0
-- and then overwrote it with v0.1.0 from the default ARG, because that
RUN came second.

The image reported version 0.2.0 and carried v0.1.0's code. `/review/queue/`
raised AttributeError on submit for a function that was in the version
the metadata claimed, and no test could see it: the suite installs
requirements.txt and never builds the image.

So the check is on the files.
"""

import re
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)


def _pinned_in_requirements():
    """Package names pinned in any requirements file."""
    names = set()
    for path in ROOT.glob("requirements*.txt"):
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            name = re.split(r"[<>=!@\[ ]", line, maxsplit=1)[0].strip()
            if name:
                names.add(name.lower())
    return names


def _installed_in_dockerfiles():
    """Package names a Dockerfile installs directly, by file."""
    found = {}
    for path in ROOT.glob("Dockerfile*"):
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "pip install" not in stripped:
                continue
            # A requirements file is the pin, not a second one.
            for quoted in re.findall(r'"([^"]+)"', stripped):
                name = re.split(r"[<>=!@\[ ]", quoted, maxsplit=1)[0].strip()
                if name and not name.startswith("-"):
                    found.setdefault(name.lower(), set()).add(path.name)
    return found


def test_no_package_is_pinned_in_both_a_requirements_file_and_a_dockerfile():
    """Whichever runs last wins, and the Dockerfile runs last."""
    both = {
        name: files
        for name, files in _installed_in_dockerfiles().items()
        if name in _pinned_in_requirements()
    }
    assert not both, (
        "pinned twice, and the Dockerfile's copy overwrites the "
        "requirements one: "
        + "; ".join(
            f"{name} in {', '.join(sorted(files))}" for name, files in both.items()
        )
    )


def test_the_contract_is_pinned_in_requirements():
    """Named, because this is the one it happened to. The base image's
    hash is computed from requirements.txt, so a pin anywhere else does
    not even change which image gets built."""
    assert "lnic-contracts" in _pinned_in_requirements()


def test_the_base_image_does_not_install_the_contract():
    base = (ROOT / "Dockerfile.base").read_text()
    installs = [
        line
        for line in base.splitlines()
        if "pip install" in line and not line.strip().startswith("#")
    ]
    assert not any("lnic-contracts" in line for line in installs)


def test_the_base_tag_comes_from_the_shared_action():
    """One definition of "what should this image be tagged", used by every
    repository in the suite.

    It was computed inside the Cloud Build config -- a file no other
    repository reads -- and it was missing Dockerfile.base, so editing the
    file that builds the image rebuilt nothing.
    """
    deploy = (ROOT / ".github/workflows/deploy.yml").read_text()
    assert "lnic-contracts/.github/actions/image-tag@" in deploy
    assert "_BASE_TAG=" in deploy, "the computed tag is not passed to the build"

    hashed = deploy[deploy.index("image-tag@") :]
    for part in ("Dockerfile.base", "requirements.txt", "DIRECTORY_VERSION"):
        assert part in hashed, f"{part} decides what is in the base and is not hashed"
