# Outlet map

A chart type for the outlet registry (`outlet_registry`): a dot for every
news outlet on the map, coloured by whether we collect from it, each county
shaded by how many outlets are located there.

It began as a "Newsrooms" layer inside the story map, chosen on the story
map's Data step. That drew, but it could not be managed: the category
colours, the key and the rings were fixed in the renderer, so an outlet map
looked like nothing else made in the builder and could not be restyled from
it. It is now its own type, `outletmap`.

## Same code paths, same style rules

- Drawn by `renderStoryMap` in `static/js/datadesk-chart.js`: the same
  boundaries, framing, county shading, bands, tooltips, legend and theme
  tokens as a story map.
- Styled from the same Look step as every chart: title, subtitle, palette,
  light or dark, source line.
- Published, pinned and embedded like every visual.

## What differs

| | Story map | Outlet map |
|---|---|---|
| A dot is | a place stories are set, sized by stories | an outlet |
| Data | a slice of the corpus (Data and Newsrooms steps) | the whole registry |
| Walk | type, Look, Data, Newsrooms, Fields, Publish | type, Look, Publish |
| Shading counts | stories mentioning the county | outlets located there |

## Its options (Look step)

| Option | Values |
|---|---|
| Draw | which kinds: collected, not collected, print, replica, social. None ticked draws all. |
| Colour: *kind* | Automatic, or a slot of the visual's palette. A slot, not a hex, so a colour follows the theme in light and dark. Automatic keeps the renderer's choice, which stays 30° clear of the shading ramp's hue. |
| Outlines | ink ring on everything we do not collect (default), or none. |
| Shade counties by | the drawn outlets located there (default), the ones we collect from, or no shading. |
| Shading steps | as a story map. |

Which kinds are drawn and what the shading counts are applied in the feed
(`visuals.outlets.run_outlet_map`), because an outlet with no coordinates is
counted in its county but has no dot to recount from.
