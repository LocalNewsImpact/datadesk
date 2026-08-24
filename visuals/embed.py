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


def snippet(visual, host=EMBED_HOST, version=None, theme=None):
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

    A theme pins its colours the same way. Without one the embed follows
    the reader's own setting, which is right for a page of ours and wrong
    inside somebody else's article: a light article on a reader's dark
    laptop got a dark chart dropped into the middle of it. The publisher
    knows what their page looks like; the reader's laptop does not.
    """
    # Unset means "whatever the visual was built as", not "ask the
    # reader": somebody who chose light in the builder chose it for the
    # embed too, and having to choose again on the way out is how the
    # setting gets forgotten.
    if theme is None:
        theme = (visual.config or {}).get("theme_mode") or None

    attrs = ""
    params = []
    if version is not None:
        attrs += f' data-version="{version}"'
        params.append(f"v={version}")
    if theme in ("light", "dark"):
        attrs += f' data-theme="{theme}"'
        params.append(f"theme={theme}")
    query = f"?{'&'.join(params)}" if params else ""
    return (
        f'<div class="datadesk-visual" data-visual="{visual.uuid}"{attrs}>'
        f'<a href="https://{host}/visuals/{visual.uuid}/{query}">{visual.title}</a>'
        f"</div>\n"
        f'<script src="https://{host}/static/js/datadesk-embed.js" async></script>'
    )
