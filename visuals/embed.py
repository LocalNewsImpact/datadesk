"""The snippet a publisher pastes, written once.

It existed twice -- as a readonly field on the Django admin and as literal
markup in the builder's template -- and the two drifted the first time one
changed: the admin moved to data.localnewsimpact.org and a responsive
script, and the builder went on handing out an iframe at height 480 aimed
at the console.

An embed URL is written into somebody else's article and cannot be moved
afterwards, so a stale snippet is not a cosmetic problem. Whatever a
publisher pasted is what their page loads for good.
"""

#: Where an embed points. Its own name because it is a promise: once
#: pasted, it cannot be changed (ROADMAP item 24).
EMBED_HOST = "data.localnewsimpact.org"


def snippet(visual, host=EMBED_HOST):
    """The placeholder and the script, as plain text.

    No height anywhere: the framed page reports its own and the script
    resizes to match (ROADMAP item 22). The link inside the placeholder is
    what a reader sees if the script never loads, so it is not decoration.
    """
    return (
        f'<div class="datadesk-visual" data-visual="{visual.slug}">'
        f'<a href="https://{host}/visuals/{visual.slug}/">{visual.title}</a>'
        f"</div>\n"
        f'<script src="https://{host}/static/js/datadesk-embed.js" async></script>'
    )
