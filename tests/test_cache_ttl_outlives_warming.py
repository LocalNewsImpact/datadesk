"""A cache that expires before the next warm is not a warm cache.

`warm_caches` runs on a 45-minute Cloud Scheduler cycle
(datadesk-warm-caches -> the datadesk-warm Cloud Run job). The paywall
counts entry had a 300-second life, so it was expired for 40 of every 45
minutes and nearly every visit paid the uncached cost: 104 seconds on
2026-08-29 against a warm 1.7 seconds.

The production database is db-g1-small on PD_HDD -- about 11 sustained
random read IOPS at 15GB -- so an uncached read here is not a slow page,
it is a minute and a half of one.
"""

WARM_INTERVAL_SECONDS = 45 * 60


def _warmed_ttls():
    """Every cache `warm_caches` fills, with the life it is given."""
    from explorer.costs import _CACHE_SECONDS as costs_ttl
    from explorer.crawler import _COUNTS_CACHE_SECONDS as counts_ttl
    from explorer.dashboard import _CACHE_SECONDS as dashboard_ttl
    from review.views import PAYWALL_COUNTS_CACHE_SECONDS as paywall_ttl

    return {
        "recorded costs": costs_ttl,
        "dataset row counts": counts_ttl,
        "corpus summary": dashboard_ttl,
        "paywall corpus counts": paywall_ttl,
    }


def test_every_warmed_cache_outlives_the_warm_cycle():
    too_short = {
        name: ttl
        for name, ttl in _warmed_ttls().items()
        if ttl <= WARM_INTERVAL_SECONDS
    }
    assert not too_short, (
        f"These caches expire before warm_caches runs again "
        f"({WARM_INTERVAL_SECONDS}s): {too_short}. A visitor arriving in "
        f"the gap pays the full uncached read."
    )


def test_the_paywall_counts_regression_specifically():
    """The one that had it, named so the fix is not quietly undone."""
    from review.views import PAYWALL_COUNTS_CACHE_SECONDS

    assert PAYWALL_COUNTS_CACHE_SECONDS > WARM_INTERVAL_SECONDS
