"""A corpus question is computed once and shared.

A story map of a month of Missouri stories takes about half a minute of
queries, and the builder asked for it four times at once: each request ran
the queries again, the four competed, and each took two to three and a half
minutes (2026-09-27). Publishing asked a fifth time.
"""

import threading
import time

import pytest
from django.core.cache import cache

from visuals import corpus
from visuals.corpus import _cache_key, answer_once


@pytest.fixture(autouse=True)
def fixed_version(monkeypatch):
    monkeypatch.setattr(corpus, "corpus_version", lambda: "v1")


def test_it_is_computed_once_and_kept():
    calls = []

    def compute():
        calls.append(1)
        return {"points": [1]}

    assert answer_once("t", ["q"], compute) == {"points": [1]}
    assert answer_once("t", ["q"], compute) == {"points": [1]}
    assert len(calls) == 1


def test_a_different_question_is_computed():
    assert answer_once("t", ["a"], lambda: 1) == 1
    assert answer_once("t", ["b"], lambda: 2) == 2


def test_a_moved_corpus_is_a_new_answer(monkeypatch):
    assert answer_once("t", ["q"], lambda: "old") == "old"
    monkeypatch.setattr(corpus, "corpus_version", lambda: "v2")
    assert answer_once("t", ["q"], lambda: "new") == "new"


def test_a_second_request_waits_for_the_first():
    """The copy that arrives while the first is computing takes its answer
    instead of running the same queries beside it."""
    started = threading.Event()
    calls = []

    def slow():
        calls.append("first")
        started.set()
        time.sleep(1.5)
        return "answer"

    got = {}

    def ask():
        got["a"] = answer_once("t", ["q"], slow)

    first = threading.Thread(target=ask)
    first.start()
    started.wait(5)
    got["b"] = answer_once("t", ["q"], lambda: calls.append("second") or "other")
    first.join(5)
    assert got == {"a": "answer", "b": "answer"}
    assert calls == ["first"]


def test_an_abandoned_claim_does_not_block():
    """A computation that died leaves no answer; once its claim is gone the
    next request computes rather than waiting out the whole lock."""
    key = _cache_key("t", ["q"])
    cache.add(f"{key}.running", 1, 1)
    start = time.monotonic()
    assert answer_once("t", ["q"], lambda: "recomputed") == "recomputed"
    assert time.monotonic() - start < 10


def test_the_live_story_map_goes_through_it():
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "visuals/services.py").read_text()
    assert '"visuals.storymap",' in src
    assert '"visuals.values",' in src
