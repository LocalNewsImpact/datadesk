"""The arithmetic of the geography ICR, with no database in it.

Pure functions, so each can be checked against a hand-worked case
(docs/GEOGRAPHY_ICR_DATADESK.md, G-WP7). `review.geography_icr` gathers
the answers, claims and weights and calls these.

numpy does the sums. Intervals are 95%, from a seeded bootstrap over
stories -- the unit the draw sampled -- so a figure recomputed from the
same answers is the same figure. Shares are weighted by 1 / inclusion
probability, so a stratum that was oversampled counts for what it is in
the corpus.
"""

from __future__ import annotations

import math

import numpy as np

#: Bootstrap resamples for a share, a ratio and an alpha. numpy makes
#: an alpha resample a pair of matrix products, so alpha takes the same
#: number as everything else.
RESAMPLES = 2000
ALPHA_RESAMPLES = RESAMPLES


# -------------------------------------------------------------- distance


def nominal(a, b):
    return 0.0 if a == b else 1.0


def masi(a, b):
    """MASI distance between two sets (Passonneau 2006): 1 - J * M, where
    J is the Jaccard overlap and M is 1 for identical sets, 2/3 where one
    holds the other, 1/3 where they overlap otherwise, 0 where disjoint.
    The usual distance for answers that are sets of things."""
    a, b = frozenset(a), frozenset(b)
    if a == b:
        return 0.0
    union = a | b
    common = a & b
    if not common:
        return 1.0
    jaccard = len(common) / len(union)
    monotonicity = 2 / 3 if (a <= b or b <= a) else 1 / 3
    return 1.0 - jaccard * monotonicity


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


# ----------------------------------------------------------------- alpha


def _resample_counts(n, resamples, seed):
    """(resamples x n) matrix: how many times each item is drawn in each
    bootstrap resample. Seeded, so the same answers give the same
    interval."""
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, n, size=(resamples, n))
    counts = np.zeros((resamples, n))
    np.add.at(counts, (np.arange(resamples)[:, None], picks), 1)
    return counts


def _bounds(draws):
    draws = np.sort(np.asarray([d for d in draws if d is not None and np.isfinite(d)]))
    if not len(draws):
        return {"low": None, "high": None}
    return {
        "low": float(draws[int(0.025 * len(draws))]),
        "high": float(draws[min(len(draws) - 1, int(0.975 * len(draws)))]),
    }


class _Alpha:
    """Krippendorff's alpha over units of values with any distance.

        alpha = 1 - (n - 1) * sum_u D_u / (t . D . t)

    where D is the distance between every pair of distinct values, t the
    count of each value over the pairable units, n = sum(t), and D_u =
    (c_u . D . c_u) / (m_u - 1) for a unit with value counts c_u and m_u
    values. With the nominal distance this is the CIN figure
    (`cin_metrics.krippendorff_alpha`). The distances and each unit's
    disagreement are computed once; a bootstrap resample only re-sums.
    """

    def __init__(self, units, distance):
        units = [list(values) for values in units if len(values) >= 2]
        index = {}
        for values in units:
            for value in values:
                index.setdefault(value, len(index))
        k = len(index)
        self.counts = np.zeros((len(units), k))
        for u, values in enumerate(units):
            for value in values:
                self.counts[u, index[value]] += 1
        if distance is nominal:
            self.distance = 1.0 - np.eye(k)
        else:
            values = list(index)
            self.distance = np.zeros((k, k))
            for i in range(k):
                for j in range(i + 1, k):
                    d = distance(values[i], values[j])
                    self.distance[i, j] = self.distance[j, i] = d
        m = self.counts.sum(axis=1)
        self.observed = np.einsum(
            "uk,kl,ul->u", self.counts, self.distance, self.counts
        ) / np.maximum(m - 1, 1)
        self.n_units = len(units)

    def _alpha(self, weights):
        """alpha for each row of `weights` (resamples x units)."""
        totals = weights @ self.counts
        n = totals.sum(axis=1)
        expected = np.einsum("rk,kl,rl->r", totals, self.distance, totals)
        observed = weights @ self.observed
        with np.errstate(divide="ignore", invalid="ignore"):
            value = 1 - (n - 1) * observed / expected
        return np.where((n >= 2) & (expected > 0), value, np.nan)

    def alpha(self):
        if not self.n_units:
            return None
        value = self._alpha(np.ones((1, self.n_units)))[0]
        return None if np.isnan(value) else float(value)


def alpha(units, distance=nominal):
    """Krippendorff's alpha, or None where it cannot be computed (fewer
    than two values in pairable units, or no variation at all)."""
    return _Alpha(units, distance).alpha()


def alpha_with_interval(units, distance=nominal, *, resamples=ALPHA_RESAMPLES, seed=0):
    """{rate, low, high, n}: alpha with a bootstrap interval over units.
    `n` is the units with two or more values."""
    calc = _Alpha(units, distance)
    point = calc.alpha()
    if point is None:
        return {"rate": None, "low": None, "high": None, "n": calc.n_units}
    draws = calc._alpha(_resample_counts(calc.n_units, resamples, seed))
    return {"rate": point, **_bounds(draws), "n": calc.n_units}


# ------------------------------------------------------- weighted shares


def weighted_share(items, *, resamples=RESAMPLES, seed=0):
    """items: [(hit: bool, weight)]. {rate, low, high, n, hits}: the
    weighted share of hits with a bootstrap interval over items."""
    items = list(items)
    n = len(items)
    if not n:
        return {"rate": None, "low": None, "high": None, "n": 0, "hits": 0}
    hit = np.array([1.0 if h else 0.0 for h, _ in items])
    weight = np.array([float(w) for _, w in items])
    counts = _resample_counts(n, resamples, seed)
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = (counts @ (hit * weight)) / (counts @ weight)
    return {
        "rate": float((hit * weight).sum() / weight.sum()),
        **_bounds(draws),
        "n": n,
        "hits": int(hit.sum()),
    }


def weighted_ratio(items, *, resamples=RESAMPLES, seed=0):
    """items: [(numerator, denominator, weight)] per story. The pooled
    ratio sum(w * num) / sum(w * den) with a bootstrap over stories: a
    county precision or recall, where a story's counties are not
    independent of each other."""
    items = [item for item in items if item[1]]
    n = len(items)
    if not n:
        return {"rate": None, "low": None, "high": None, "n": 0}
    num = np.array([float(x) * w for x, _, w in items])
    den = np.array([float(d) * w for _, d, w in items])
    counts = _resample_counts(n, resamples, seed)
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = (counts @ num) / (counts @ den)
    return {"rate": float(num.sum() / den.sum()), **_bounds(draws), "n": n}


# ------------------------------------------------------------ distances


def percentile(values, q):
    """The q-th percentile (0-100), linear between ranks, or None."""
    values = sorted(values)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    rank = (len(values) - 1) * q / 100
    low = math.floor(rank)
    high = math.ceil(rank)
    return values[low] + (values[high] - values[low]) * (rank - low)


def spread(values):
    """{n, median, p90} of a list of distances."""
    values = list(values)
    return {
        "n": len(values),
        "median": percentile(values, 50),
        "p90": percentile(values, 90),
    }


# ----------------------------------------------------------- calibration


def brier(items):
    """items: [(forecast probability, outcome: bool, weight)].

    {score, constant, rate, n, table}: the weighted Brier score; the
    Brier of a forecaster who always says the observed rate (what the
    score has to beat to be worth anything); the observed rate; and a
    reliability table by tenth of forecast -- the mean forecast against
    the share that came true in each.
    """
    items = [(float(f), bool(o), w) for f, o, w in items if f is not None]
    total = sum(w for _, _, w in items)
    if not total:
        return {"score": None, "constant": None, "rate": None, "n": 0, "table": []}
    score = sum(w * (f - o) ** 2 for f, o, w in items) / total
    rate = sum(w for _, o, w in items if o) / total
    table = []
    for tenth in range(10):
        low, high = tenth / 10, (tenth + 1) / 10
        inside = [
            (f, o, w)
            for f, o, w in items
            if low <= f < high or (tenth == 9 and f == 1.0)
        ]
        weight = sum(w for _, _, w in inside)
        if not inside:
            continue
        table.append(
            {
                "from": low,
                "to": high,
                "n": len(inside),
                "forecast": sum(w * f for f, _, w in inside) / weight,
                "observed": sum(w for _, o, w in inside if o) / weight,
            }
        )
    return {
        "score": score,
        "constant": rate * (1 - rate),
        "rate": rate,
        "n": len(items),
        "table": table,
    }
