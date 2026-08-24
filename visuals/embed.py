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


def snippet(visual, host=EMBED_HOST, version=None):
    """The placeholder and the script, as plain text.

    Addressed by uuid, never by slug. The slug is unique, editable and
    generated from the title, so renaming a visual would break every
    snippet already pasted -- and the person who pasted it would never
    find out. The uuid cannot be renamed, which is the only property this
    address actually needs.

    No height anywhere: the framed page reports its own and the script
    resizes to match (ROADMAP item 22). The link inside the placeholder is
    what a reader sees if the script never loads, so it is not decoration.

    A version pins the snippet to one snapshot for good. Without one the
    embed follows what is published, which is what most people want and
    why it is the default.
    """
    pin = f' data-version="{version}"' if version is not None else ""
    query = f"?v={version}" if version is not None else ""
    return (
        f'<div class="datadesk-visual" data-visual="{visual.uuid}"{pin}>'
        f'<a href="https://{host}/visuals/{visual.uuid}/{query}">{visual.title}</a>'
        f"</div>\n"
        f'<script src="https://{host}/static/js/datadesk-embed.js" async></script>'
    )
