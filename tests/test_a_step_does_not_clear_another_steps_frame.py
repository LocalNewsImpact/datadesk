"""Renaming a story map reset where it was looking.

`steps.py` states the contract: "Every step writes its own keys and none
clears another's, so going back changes one choice and keeps the rest --
which is the difference between a tool people explore with and a form
they fill in once."

The Look step broke it. Its frame branch read

    if config.get("frame_on"):        ...resolve and set focus/frame...
    else:                             config["focus"] = ""
                                      config["frame"] = []

off the panel's OWN freshly-built dict. `frame_on` only lands in that
dict when the chart declares it as an option -- a flow map does, a story
map does not -- so for a story map the key was never there, the else ran
every time, and the save wrote `focus: ""` and `frame: []` over the
newsroom step's choice.

So changing the title and saving reset the map from a state to every
marker in the corpus, and the only step that did NOT do it was the one
that had set the frame. The author's fix was to go back and set it again,
which is precisely the "form you fill in once" the contract exists to
prevent.

The branch now runs only where the chart frames from this step.
"""

from types import SimpleNamespace

import pytest

from visuals import panels

#: What the newsrooms step wrote, and what the Look step must not touch.
FRAMED = {
    "focus": "29",
    "focus_name": "Missouri",
    "focus_level": "state",
    "extent": "whole_state",
    "frame": ["29019", "29095"],
}
OWNED_BY_NEWSROOMS = ("focus", "focus_name", "focus_level", "extent", "frame")


def _visual(kind, **config):
    return SimpleNamespace(
        config={"kind": kind, "title": "Before", **FRAMED, **config},
        spec={"roles": {}},
        source_kind="corpus",
        datasets=[],
        slug="a-map",
    )


class TestRenamingAStoryMapKeepsItsFrame:
    def test_the_look_step_writes_none_of_the_newsroom_keys(self):
        """THE REGRESSION. Any of these appearing in what Look writes is
        the bug, because the caller merges them over the stored value."""
        written = panels.theme_panel(_visual("storymap"), {"title": "After"})
        config = written["config"]
        assert [k for k in OWNED_BY_NEWSROOMS if k in config] == []

    def test_the_title_is_still_written(self):
        """The fix must not cost the step its own job."""
        written = panels.theme_panel(_visual("storymap"), {"title": "After"})
        assert written["config"]["title"] == "After"

    @pytest.mark.parametrize("kind", ["storymap", "table", "bar"])
    def test_no_chart_without_a_framing_control_is_touched(self, kind):
        """Not a story map special case: any chart framed from elsewhere
        would have been cleared the same way."""
        written = panels.theme_panel(_visual(kind), {"title": "After"})
        assert [k for k in OWNED_BY_NEWSROOMS if k in written["config"]] == []


class TestAFlowMapStillFramesFromTheLookStep:
    """The branch is guarded, not removed. A flow map declares `frame_on`
    and is framed here, and clearing it when the control is emptied is
    the behaviour a previous fix deliberately added -- writing the blank
    rather than popping the key, because `update` cannot take one away."""

    def test_choosing_a_frame_sets_focus(self, monkeypatch):
        """`frame_on` is validated against the states actually present in
        the data -- `flow_frames` -- not a static list, because validating
        against the static tuple rejected every state the picker had just
        shown. Stubbed here so the test exercises the branch rather than
        the corpus."""
        monkeypatch.setattr(
            panels, "flow_frames", lambda visual: [("Missouri", "Missouri")]
        )
        written = panels.theme_panel(
            _visual("flowmap"), {"title": "After", "opt-frame_on": "Missouri"}
        )
        assert written["config"]["focus"] == "29"

    def test_emptying_the_control_clears_the_focus(self):
        """Once `focus` was set, a save that cleared `frame_on` had to
        unset it too, or the map went on framing on a state the author
        had just deselected."""
        written = panels.theme_panel(
            _visual("flowmap"), {"title": "After", "opt-frame_on": ""}
        )
        assert written["config"]["focus"] == ""
        assert written["config"]["frame"] == []


class TestTheContractIsStated:
    def test_steps_still_declare_who_owns_the_frame(self):
        """If these move to another step, the guard above is wrong and
        this test should be what says so."""
        from visuals.steps import BY_SLUG

        owns = set(BY_SLUG["newsrooms"].owns)
        for key in (
            "config:focus",
            "config:focus_name",
            "config:focus_level",
            "config:extent",
            "config:frame",
        ):
            assert key in owns

    def test_the_look_step_does_not_claim_them(self):
        from visuals.steps import BY_SLUG

        owns = set(BY_SLUG["theme"].owns)
        assert not {o for o in owns if o.startswith("config:focus")}
        assert "config:frame" not in owns
        assert "config:extent" not in owns
