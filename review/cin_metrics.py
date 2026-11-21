"""Every agreement calculation the CIN procedure uses, in one place.

Pure functions over answers, with no database in them, so each can be
checked against a hand-worked case and so the same code scores the 2023
V1 coding and a V2 round. A comparison between two codebooks computed by
two pieces of code is a comparison of the code.

An **answer** is a dict: `coder`, `label` (one of the ten categories,
or "" for a rejection), `secondary` (or ""). A **story** is its answers
in the order they were given. See docs/CIN_V2_DATADESK.md, WP7.

THE SCORING RULE
    Only answers within the ten categories are scored. Two coders who
    choose the same category match. A category against a rejection is a
    mismatch: they disagreed about whether the story should be labelled
    at all. Two rejections are not scored either way: a story neither
    coder would label is not a story to label, and agreeing about that
    says nothing about the definitions.
"""

import math
import random
import statistics

#: z for a two-sided 95% interval.
Z95 = 1.959963984540054


def score(a, b):
    """True, False, or None where the pair is not scored."""
    if not a["label"] and not b["label"]:
        return None
    return a["label"] == b["label"]


def first_pair(answers):
    """The first two answers on a story, or None.

    Agreement is read from the first pair wherever a third coder is
    added only on a disagreement: third-coder pairs then exist only on
    stories two coders already split on, and counting them would measure
    the rule that added them.
    """
    return (answers[0], answers[1]) if len(answers) >= 2 else None


def wilson(hits, n):
    """95% interval for a share of `n` independent stories."""
    if not n:
        return None, None
    p = hits / n
    centre = p + Z95**2 / (2 * n)
    spread = Z95 * math.sqrt(p * (1 - p) / n + Z95**2 / (4 * n * n))
    denominator = 1 + Z95**2 / n
    return (centre - spread) / denominator, (centre + spread) / denominator


def _rate(hits, n):
    low, high = wilson(hits, n)
    return {
        "hits": hits,
        "n": n,
        "rate": hits / n if n else None,
        "low": low,
        "high": high,
    }


def first_pair_agreement(stories, label=None):
    """M2. The share of stories whose first two coders chose the same
    category, by the scoring rule.

    With `label`, over the stories either of them put in that category:
    the category's own rate. A story is one trial, so the interval is an
    ordinary binomial one.
    """
    hits = n = 0
    for answers in stories.values():
        pair = first_pair(answers)
        if pair is None:
            continue
        if label and label not in (pair[0]["label"], pair[1]["label"]):
            continue
        result = score(*pair)
        if result is None:
            continue
        n += 1
        hits += result
    return _rate(hits, n)


def pairwise_agreement(stories, label=None):
    """M1. The share of scored coder pairs that match, over every pair.

    For rounds where every story has the same number of coders. Pairs on
    one story are not independent, so this carries no interval; use
    `first_pair_agreement` where one is needed.
    """
    hits = n = 0
    for answers in stories.values():
        for i, a in enumerate(answers):
            for b in answers[i + 1 :]:
                if label and label not in (a["label"], b["label"]):
                    continue
                result = score(a, b)
                if result is None:
                    continue
                n += 1
                hits += result
    return {"hits": hits, "n": n, "rate": hits / n if n else None}


def weighted_agreement(groups):
    """M2 over several groups of stories, each counted at a fixed share.

    `groups` is [(stories, weight)], weights summing to 1. For a pilot
    that holds every civic story and only some of the others: scored as
    drawn, the civic stories would weigh more than they do in the
    population the result is about.
    """
    rate = variance = 0.0
    n = 0
    for stories, weight in groups:
        part = first_pair_agreement(stories)
        if not part["n"]:
            return {"rate": None, "low": None, "high": None, "n": 0}
        p = part["rate"]
        rate += weight * p
        variance += weight**2 * p * (1 - p) / part["n"]
        n += part["n"]
    spread = Z95 * math.sqrt(variance)
    return {
        "rate": rate,
        "low": max(0.0, rate - spread),
        "high": min(1.0, rate + spread),
        "n": n,
    }


def paired_change(now, before, *, resamples=2000, seed=0):
    """M3. Agreement now minus agreement before, on the same stories.

    Both are M2. The interval is a bootstrap over stories, resampling a
    story's two results together, because the same story is easy or hard
    under both codebooks and the two rates are not independent.
    """
    rows = []
    for story in now.keys() & before.keys():
        a, b = first_pair(now[story]), first_pair(before[story])
        rows.append((score(*a) if a else None, score(*b) if b else None))

    def difference(sample):
        now_hits = sum(1 for x, _ in sample if x)
        now_n = sum(1 for x, _ in sample if x is not None)
        before_hits = sum(1 for _, y in sample if y)
        before_n = sum(1 for _, y in sample if y is not None)
        if not now_n or not before_n:
            return None
        return now_hits / now_n - before_hits / before_n

    point = difference(rows)
    if point is None:
        return {"change": None, "low": None, "high": None, "n": len(rows)}
    rng = random.Random(seed)
    draws = sorted(
        d
        for d in (difference(rng.choices(rows, k=len(rows))) for _ in range(resamples))
        if d is not None
    )
    return {
        "change": point,
        "low": draws[int(0.025 * len(draws))],
        "high": draws[min(len(draws) - 1, int(0.975 * len(draws)))],
        "n": len(rows),
    }


#: How a rejection appears to alpha: one value of its own. A story every
#: coder rejected is dropped first, by the scoring rule.
REJECTED = "(rejected)"


def krippendorff_alpha(stories, label=None):
    """M4. Krippendorff's alpha for nominal data.

    Handles a different number of coders on each story, which this queue
    has. With `label`, the category against everything else. None where
    it cannot be computed: fewer than two stories, or every answer the
    same.
    """
    units = []
    for answers in stories.values():
        values = [a["label"] or REJECTED for a in answers]
        if len(values) < 2 or all(v == REJECTED for v in values):
            continue
        if label:
            values = [v if v == label else "(other)" for v in values]
        units.append(values)

    disagreement = 0.0
    totals = {}
    for values in units:
        counts = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        m = len(values)
        for value, count in counts.items():
            totals[value] = totals.get(value, 0) + count
            disagreement += count * (m - count) / (m - 1)
    n = sum(totals.values())
    if n < 2:
        return None
    expected = sum(totals[c] * totals[k] for c in totals for k in totals if c != k) / (
        n - 1
    )
    if not expected:
        return None
    return 1 - disagreement / expected


def alpha_with_interval(stories, label=None, *, resamples=500, seed=0, measure=None):
    """M4 with a 95% interval: {rate, low, high, n}. `measure` is the
    alpha to compute: the strict one, or `weighted_alpha`.

    The interval is a bootstrap over stories, the unit alpha is computed
    over. `n` is the stories with two or more answers, not all of them
    rejections: the ones alpha can use.
    """
    keys = [
        key
        for key, answers in stories.items()
        if len(answers) >= 2 and any(a["label"] for a in answers)
    ]
    measure = measure or krippendorff_alpha
    point = measure({k: stories[k] for k in keys}, label)
    if point is None:
        return {"rate": None, "low": None, "high": None, "n": len(keys)}
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sample = {i: stories[k] for i, k in enumerate(rng.choices(keys, k=len(keys)))}
        value = measure(sample, label)
        if value is not None:
            draws.append(value)
    draws.sort()
    if not draws:
        return {"rate": point, "low": None, "high": None, "n": len(keys)}
    return {
        "rate": point,
        "low": draws[int(0.025 * len(draws))],
        "high": draws[min(len(draws) - 1, int(0.975 * len(draws)))],
        "n": len(keys),
    }


#: What the label rule makes of a crossover -- one coder's category is
#: the other's secondary -- beside a full match: a label at 0.7 against
#: one at 0.9.
FULL_MATCH = 0.9
CROSSOVER = 0.7
#: So a crossover is 0.7 / 0.9 of a match, and this much disagreement.
CROSSOVER_DISTANCE = 1 - CROSSOVER / FULL_MATCH


def _crossover_distance(a, b):
    """How far apart two (category, secondary) answers are: 0 where the
    categories match, `CROSSOVER_DISTANCE` where one's category is the
    other's secondary, 1 otherwise."""
    if a[0] == b[0]:
        return 0.0
    if (a[0] != REJECTED and a[0] == b[1]) or (b[0] != REJECTED and b[0] == a[1]):
        return CROSSOVER_DISTANCE
    return 1.0


def weighted_alpha(stories, label=None):
    """M4w. Krippendorff's alpha with part credit for a crossover.

    The strict figure (`krippendorff_alpha`) reads the category alone,
    and scores two coders who each chose the other's category as their
    secondary the same as two who saw nothing in common. The label rule
    does not: it keeps that story, at 0.7. This is alpha with that
    difference as its distance, so it measures the agreement the
    training labels are built on. It is reported beside the strict
    figure and passes or fails nothing.

    With `label`, the category against everything else, in both places.
    """
    units = []
    for answers in stories.values():
        values = [(a["label"] or REJECTED, a.get("secondary") or "") for a in answers]
        if len(values) < 2 or all(v[0] == REJECTED for v in values):
            continue
        if label:
            values = [
                (p if p == label else "(other)", s if s == label else "")
                for p, s in values
            ]
        units.append(values)

    totals = {}
    observed = 0.0
    for values in units:
        m = len(values)
        for value in values:
            totals[value] = totals.get(value, 0) + 1
        for i, a in enumerate(values):
            for b in values[i + 1 :]:
                observed += 2 * _crossover_distance(a, b) / (m - 1)
    n = sum(totals.values())
    if n < 2:
        return None
    kinds = list(totals)
    expected = sum(
        totals[a] * totals[b] * _crossover_distance(a, b)
        for a in kinds
        for b in kinds
        if a != b
    ) / (n - 1)
    if not expected:
        return None
    return 1 - observed / expected


def cohen_kappa(pairs):
    """M5. Kappa for two raters over [(first, second)] labels."""
    pairs = list(pairs)
    n = len(pairs)
    if not n:
        return None
    observed = sum(a == b for a, b in pairs) / n
    left, right = {}, {}
    for a, b in pairs:
        left[a] = left.get(a, 0) + 1
        right[b] = right.get(b, 0) + 1
    chance = sum(left[k] * right.get(k, 0) for k in left) / (n * n)
    return None if chance == 1 else (observed - chance) / (1 - chance)


def settled_label(answers):
    """M6. (confidence, label) for a story, by the thesis rule.

    0.9 where two coders chose the same category. 0.7 where one coder's
    category is another's secondary. (None, None) otherwise, including
    where what two coders agreed on was a rejection: the story is left
    out of training, and nobody decides it by hand.
    """
    counts = {}
    for answer in answers:
        if answer["label"]:
            counts[answer["label"]] = counts.get(answer["label"], 0) + 1
    if counts:
        label, count = max(counts.items(), key=lambda item: item[1])
        if count >= 2:
            return 0.9, label
    rejections = sum(1 for a in answers if not a["label"])
    if rejections >= 2:
        return None, None
    secondaries = {a["secondary"] for a in answers if a.get("secondary")}
    for answer in answers:
        if answer["label"] and answer["label"] in secondaries:
            return 0.7, answer["label"]
    return None, None


def left_out(stories, needed=2):
    """M7. label -> stories with enough answers and no label, counted
    against every category any coder chose for them.

    A category with many is a sign its definition needs work. It is not
    a list of stories for somebody to decide.
    """
    counts = {}
    for answers in stories.values():
        if len(answers) < needed or settled_label(answers)[1]:
            continue
        for label in {a["label"] for a in answers if a["label"]}:
            counts[label] = counts.get(label, 0) + 1
    return counts


def coder_agreement(stories):
    """M8. coder -> M2 over the first pairs that coder was in.

    First pairs only, never a tiebreak: a third coder is given only the
    stories two others split on, and would look worse than they are.
    """
    hits, n = {}, {}
    for answers in stories.values():
        pair = first_pair(answers)
        if pair is None:
            continue
        result = score(*pair)
        if result is None:
            continue
        for answer in pair:
            n[answer["coder"]] = n.get(answer["coder"], 0) + 1
            hits[answer["coder"]] = hits.get(answer["coder"], 0) + result
    return {coder: _rate(hits[coder], n[coder]) for coder in n}


def coder_pace(answers_by_coder):
    """M9. coder -> answers, median seconds per story, rejection rate.

    `answers_by_coder` is coder -> [(seconds or None, rejected)].
    """
    out = {}
    for coder, rows in answers_by_coder.items():
        seconds = [s for s, _ in rows if s]
        out[coder] = {
            "answers": len(rows),
            "median_seconds": statistics.median(seconds) if seconds else None,
            "rejection_rate": (
                sum(1 for _, r in rows if r) / len(rows) if rows else None
            ),
        }
    return out


def stories_needed(before, after, *, confidence=0.95, power=0.80):
    """M10. Stories per group to tell `before` from `after`.

    The two-proportion sample size in docs/CIN_ROUNDS.md. Shown before a
    round opens, because the answer is often a surprise.
    """
    za = statistics.NormalDist().inv_cdf(1 - (1 - confidence) / 2)
    zb = statistics.NormalDist().inv_cdf(power)
    mean = (before + after) / 2
    top = za * math.sqrt(2 * mean * (1 - mean)) + zb * math.sqrt(
        before * (1 - before) + after * (1 - after)
    )
    return math.ceil(top**2 / (before - after) ** 2)


def pause_threshold(n, normal_miss_rate, chance=0.01):
    """M11. The number of misses out of `n` check stories at which a
    coder is paused.

    The smallest k a coder working normally would reach by chance no
    more than `chance` of the time. At a normal miss rate of 1 in 10
    over 40 stories it is 10.
    """
    p = normal_miss_rate
    tail = 1.0
    for k in range(n + 1):
        if tail <= chance:
            return k
        tail -= math.comb(n, k) * p**k * (1 - p) ** (n - k)
    return n + 1


def raw_agreement_for_alpha(alpha, chance_agreement):
    """The raw agreement a reliability of `alpha` needs, given how often
    coders would match by chance."""
    return alpha + (1 - alpha) * chance_agreement
