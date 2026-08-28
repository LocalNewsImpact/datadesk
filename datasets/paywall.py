"""What a publisher's paywall costs, and how to get past it.

Read by the publisher record and by the paywalls review page, which is
where somebody usually decides it: they are looking at the site to answer
whether it has a paywall, and the price and the sign-in page are on the
same screen they are looking at.

One validator, so the two pages cannot disagree about what an amount is.
"""

#: The paywall panel. Not schema fields: a checkbox, an amount and the
#: period it covers are three shapes the schema's rules do not have, and
#: they belong together on the record as one question -- is this behind a
#: paywall, what does it cost, and where does a person sign in.
PAYWALL_FIELDS = (
    "has_paywall",
    "subscription_cost",
    "subscription_period",
    "login_url",
)

PERIODS = ("monthly", "annual")


def paywall_from_form(post, errors):
    """The paywall panel's values, checked.

    No credentials. The username and password for a publisher live in
    Secret Manager under `auth_secret_name`, which is what the crawler
    does and why `auth_config` carries its comment that credentials are
    never stored in the table: a password column would be readable by
    every role holding SELECT on sources.
    """
    from decimal import Decimal, InvalidOperation

    from datasets.schema import OPTIONAL, URL, FieldSpec, check

    out = {
        # Unticked is False rather than null. "Nobody has looked" and
        # "there is no paywall" are different answers and a null cannot
        # tell them apart, so the box says what somebody decided.
        "has_paywall": bool(post.get("has_paywall")),
        "subscription_cost": None,
        "subscription_period": "",
        "login_url": "",
    }

    cost = (post.get("subscription_cost") or "").strip().lstrip("$").replace(",", "")
    if cost:
        try:
            amount = Decimal(cost)
        except InvalidOperation:
            errors.append(f"Subscription cost: {cost!r} is not an amount.")
        else:
            if amount < 0:
                errors.append(
                    "Subscription cost: a subscription cannot cost less than nothing."
                )
            else:
                # As a string. The column is a Decimal and Django coerces
                # it back on save, but the audit entry beside it is JSON,
                # and a Decimal is not JSON -- so an amount somebody typed
                # raised on the way to being recorded rather than saved.
                out["subscription_cost"] = str(amount)

    period = (post.get("subscription_period") or "").strip().lower()
    if period and period not in PERIODS:
        errors.append(f"Subscription period: {period!r} is not monthly or annual.")
    elif period:
        out["subscription_period"] = period

    url = (post.get("login_url") or "").strip()
    if url:
        ok, why = check(
            FieldSpec("login_url", "Login page", need=OPTIONAL, rule=URL), url
        )
        if ok:
            out["login_url"] = url
        else:
            errors.append(f"Login page: {why}")

    # An amount with no period is a number nobody can read: $12 a month
    # and $12 a year are different subscriptions.
    if out["subscription_cost"] is not None and not out["subscription_period"]:
        errors.append("Subscription cost: say whether that is monthly or annual.")
    return out


def paywall_of(source):
    """The paywall panel as the record holds it, for the form.

    `requires_login` and what it needs are the crawler's and are shown
    rather than edited: they are how the extractor signs in, configured
    when somebody automates a publisher, and the secret named there is
    the one thing on this page that must not be settable from a form
    field.
    """
    return {
        "has_paywall": bool(getattr(source, "has_paywall", False)),
        "subscription_cost": (
            "" if source.subscription_cost is None else str(source.subscription_cost)
        ),
        "subscription_period": source.subscription_period or "",
        "login_url": source.login_url or "",
        "periods": PERIODS,
        # What the crawler already knows how to do here, read-only.
        "requires_login": bool(getattr(source, "requires_login", False)),
        "auth_type": source.auth_type or "",
        "secret_name": source.auth_secret_name or "",
        # A login page the extractor was configured with, where the
        # record itself has none: better than showing nothing and letting
        # somebody type in what is already known.
        "configured_login_url": ((source.auth_config or {}).get("login_url") or ""),
    }
