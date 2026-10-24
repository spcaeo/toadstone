# Toadstone icons

One inline SVG sprite, no dependency, no build step, no network call:
[`web/static/js/icons.js`](../js/icons.js).

Every icon is drawn on the same 24x24 grid with **1.5 stroke, round caps and joins,
`fill="none"` and `stroke="currentColor"`**, so an icon always takes the colour of the text
it sits next to and never carries colour of its own.

Open [`preview.html`](preview.html) (served at `/static/icons/preview.html`) to see all 80
icons at 16 / 20 / 24px in both themes.

---

## Rules

1. **Severity colour comes from the CSS token, never from the icon.** Put the colour on the
   element around the icon — `color: var(--danger)` / `var(--warn)` / `var(--info)` /
   `var(--ok)` / `var(--accent)` — and the stroke follows. There is no red icon and no green
   icon in this set: `alert-circle` inside a `--danger` badge *is* the error icon.
2. **No emoji in the UI.** Emoji render at a different size and weight on every platform and
   break a 26px row. Anywhere the app used ❌ ⚠️ ℹ️ ✅ ⬇ 🚀 📊 🔗 ⚡ ☾, use an icon.
3. **Default size is 16px** (the size that lines up with `--fs-md` text and a 24–28px
   control). Use 14px inside `.badge`, 20px in empty states' headings, 24px+ only for
   `.empty-state` illustrations and stat tiles.
4. **Decorative by default.** An icon next to a text label is `aria-hidden="true"`; only an
   icon that *is* the label (an icon-only button) gets a `title`.
5. **Do not add a `<style>`, a `fill`, or a hard-coded `stroke` to an icon.** Sizing and
   colour belong to the CSS of the element that holds it.

---

## API

```js
icon(name, options)          // -> SVG string, safe inside a template literal
iconElement(name, options)   // -> a real <svg> DOM node
renderIcons(root)            // -> replaces every [data-icon] placeholder under root
window.LibreIcons            // -> { VERSION, names, aliases, aliasMap, paths, has, resolve }
```

`options`:

| Option | Default | Meaning |
|---|---|---|
| `size` | `16` | Pixel width and height (the viewBox never changes). |
| `className` | `''` | Extra classes, appended after `icon icon-<name>`. |
| `title` | `''` | Accessible name. Empty = decorative (`aria-hidden="true"`). |
| `strokeWidth` | `1.5` | Only raise it (to ~1.75) if you render below 14px on a low-DPI screen. |

**In a template literal** — the common case in `app.js` / `reports.js`:

```js
row.innerHTML = `<button class="btn btn-ghost btn-sm">${icon('download')} Export</button>`;
```

**As a DOM node** — for code that appends:

```js
button.appendChild(iconElement('x', { size: 14, title: 'Close' }));
```

**Declaratively in markup** — `index.html` and plugin HTML can stay readable:

```html
<button class="btn btn-primary">
  <i data-icon="play"></i> Start
</button>
<i data-icon="alert-triangle" data-icon-size="14" data-icon-title="Errors"></i>
```

`renderIcons()` runs once on `DOMContentLoaded` and swaps each placeholder for its SVG.
It is idempotent — rendered icons are `<svg>` elements and are skipped on later runs — so
**call `renderIcons(container)` yourself after you inject HTML that contains placeholders**
(tab panes, modals, virtual-scroller rows, plugin markup). It returns how many it replaced.

Placeholder attributes: `data-icon` (name), `data-icon-size`, `data-icon-title`,
`data-icon-stroke`. `class`, `id` and `style` are copied onto the SVG.

**Unknown names never throw.** They render a neutral `?` tile (`.icon-missing`) and warn once
per name in the console.

**Spinner.** `icon('loader')` carries `.icon-spin`; `icons.js` injects the keyframes it needs
(`#toadstone-icon-style`) and slows the animation under `prefers-reduced-motion`. Add
`.icon-spin` to any other icon to spin it.

**Load order.** `<script src="/static/js/icons.js"></script>` must come *before* any script
that calls `icon()` at load time.

---

## The set

### Crawl controls
| Name | Icon |
|---|---|
| `play` | Right-pointing triangle — Start crawl. |
| `stop` | Rounded square — Stop crawl. |
| `pause` | Two vertical bars — pause a running job. |
| `refresh` | Circular arrow — reload a report, re-run a check. |
| `loader` | Three-quarter ring, spins by default — in-flight state. |

### Data in / out
| Name | Icon |
|---|---|
| `download` | Arrow into a tray — Export, download CSV/JSON. |
| `upload` | Arrow out of a tray — Load crawl, import a file. |
| `save` | Floppy disk — Save crawl. |
| `folder` | Folder — saved crawls, a group of files. |
| `copy` | Two stacked sheets — copy a URL to the clipboard. |
| `share` | Three linked nodes — share a report. |
| `trash` | Bin with lid and two slots — Clear crawl data, delete a rule. |

### Chrome and navigation
| Name | Icon |
|---|---|
| `settings` | Gear — Settings modal. |
| `sliders` | Two labelled sliders — filters, density, tuning. |
| `dashboard` | Four rounded squares — Dashboard. |
| `menu` | Three lines — mobile drawer / rail toggle. |
| `more-horizontal` | Three dots — overflow menu. |
| `search` | Magnifier — search box, find in table. |
| `filter` | Funnel — filter rail, active filter chips. |
| `plus` | Plus — add a rule, add a crawl. |
| `minus` | Minus — remove, collapse. |
| `x` | Cross — close, remove a chip. |
| `log-out` | Arrow leaving a bracket — Log out. |
| `user` | Head and shoulders — the signed-in account. |

### Chevrons and arrows
| Name | Icon |
|---|---|
| `chevron-up` / `chevron-down` | Disclosure, `<details>`, select arrow, mega-menu trigger. |
| `chevron-left` / `chevron-right` | Pagination, horizontal tab-strip scroll. |
| `chevrons-up-down` | Stacked pair — an unsorted, sortable column header. |
| `arrow-up` / `arrow-down` | Direction, delta, jump to top. |
| `arrow-left` / `arrow-right` | Back, forward, redirect chain. |
| `arrow-up-right` | Outbound direction — external destination, "opens elsewhere". |
| `sort-asc` | Up arrow beside short-to-long bars — ascending sort. |
| `sort-desc` | Down arrow beside long-to-short bars — descending sort. |

### Status
| Name | Icon |
|---|---|
| `check` | Tick — done, passed. |
| `check-circle` | Tick in a circle — 2xx, a passing audit (`--ok`). |
| `x-circle` | Cross in a circle — failed, 5xx, no response (`--danger`). |
| `alert-triangle` | Triangle with a bang — warning (`--warn`). |
| `alert-circle` | Bang in a circle — error (`--danger`). |
| `info` | "i" in a circle — informational issue (`--info`). |
| `help-circle` | "?" in a circle — help text, an explained setting. |
| `circle` | Empty circle — a status dot outline, a neutral marker. |
| `shield` | Shield — security headers, HTTPS, robots protection. |
| `bug` | Beetle — issues, a defect found by a check. |
| `zap` | Lightning bolt — PageSpeed, performance, a fast action. |
| `key` | Key — API key fields. |
| `lock` | Padlock — HTTPS, a locked or tier-restricted setting. |

### Pages, links and structure
| Name | Icon |
|---|---|
| `link` | Two chain halves joined — an internal link, a canonical. |
| `link-broken` | Chain halves pulled apart with break marks — a broken link, 404. |
| `external-link` | Box with an arrow leaving it — external URL, opens in a new tab. |
| `globe` | Globe with meridians — external domain, host, language/region. |
| `file-text` | Page with a folded corner and text lines — an HTML page, a report. |
| `code` | Angle brackets with a slash — source, JSON-LD, a rendered-HTML view. |
| `image` | Frame with a sun and hills — the Images filter, image audits. |
| `sitemap` | Parent box branching to two children — the Sitemap tab, site structure. |
| `git-branch` | Two nodes off a trunk — the link graph, crawl depth. |
| `git-compare` | Two nodes and two arrows — the Compare tab, crawl diffing. |
| `list-checks` | Three lines, two ticked — the Issues tab, a checklist. |
| `tag` | Price tag with a hole — meta tags, labels, categories. |
| `bookmark` | Ribbon — a saved view or a pinned URL. |

### Infrastructure and data
| Name | Icon |
|---|---|
| `server` | Two stacked units with status lights — the crawler backend, response headers. |
| `database` | Three-band cylinder — stored crawls, the datastore. |
| `table` | Grid with a header row — a data table, the table view. |
| `columns` | Box split vertically — column chooser / column resize. |
| `rows` | Box split once horizontally — comfortable density. |
| `rows-compact` | Box split twice — compact density. |
| `monitor` | Screen on a stand — desktop viewport, a rendered check. |
| `bot` | Robot head with antenna — user-agent, robots.txt, AI crawlers. |
| `eye` | Open eye — show, preview, visible. |
| `eye-off` | Struck-through eye — hide, excluded, noindex. |

### Measurement
| Name | Icon |
|---|---|
| `clock` | Clock face — response time, elapsed time, stale content. |
| `calendar` | Calendar page — dates, last-modified, a crawl's timestamp. |
| `gauge` | Speedometer dial with a needle — PageSpeed score, crawl rate. |
| `trending-up` | Rising line with an arrow — an improving metric. |
| `bar-chart` | Three bars of different heights — counts by category. |
| `pie-chart` | Circle with a slice cut out — share of total. |

### Theme
| Name | Icon |
|---|---|
| `sun` | Disc with eight rays — light theme. |
| `moon` | Crescent — dark theme. |

### Aliases

The same drawing under a second name, so calling code can use the word it means:

`start`→play · `square`→stop · `clear`,`delete`→trash · `export`→download · `import`→upload ·
`gear`→settings · `grid`,`layout-grid`→dashboard · `close`→x · `more`,`ellipsis`→more-horizontal ·
`warning`→alert-triangle · `error`→alert-circle · `success`→check-circle · `robot`→bot ·
`spinner`→loader · `rotate-cw`,`reload`→refresh · `unlink`,`broken-link`→link-broken ·
`network`→git-branch · `compare`→git-compare · `issues`→list-checks · `speed`,`performance`→gauge ·
`density`→rows · `theme`→moon · `desktop`,`mobile`→monitor · `file`,`page`→file-text · `html`→code ·
`chevron-sort`→chevrons-up-down · `logout`→log-out · `account`→user · `dot`→circle

---

## Adding an icon

1. **Draw it on the 24x24 grid** in `icons.js`, inside the matching section of the `ICONS`
   map. Keep the artwork inside a 2px margin (x and y between 2 and 22), snap to whole or
   half units, and build it from the same primitives as its neighbours — there are helpers
   for the shapes that are easy to get wrong:

   ```js
   circle(cx, cy, r)                                   // a full circle as one path
   rect(x, y, w, h, r)                                 // a rounded rectangle as one path
   radial(cx, cy, inner, outer, count, offsetDeg)      // evenly spaced ticks (gear, sun)
   ```

   A value is an array of `d` strings — one entry per `<path>`:

   ```js
   'flag': ['M6 21V4.5', 'M6 5h11l-2.2 3.5L17 12H6z'],
   ```

2. **Never add `fill`, `stroke`, `stroke-width` or a `<style>`** to the artwork. The wrapper
   sets all of that, and a hard-coded colour would break both themes at once.
3. **Add an alias** in `ALIASES` if the UI calls it something else.
4. **Add it to a group** in `preview.html`'s `GROUPS` array (ungrouped names still show up,
   under "Ungrouped", so nothing can be silently missed).
5. **Check it at 16px in both themes** in `preview.html` — the top strip renders every icon
   at 16px in a row, which is where an over-detailed icon turns to mush. If it does, remove
   detail rather than thickening the stroke.
6. Run `node --check web/static/js/icons.js`.

## Design notes

- **Optical weight over geometric truth.** Circular icons use r=8.5 while square ones use a
  16-unit box, because a circle at the same nominal size reads smaller.
- **Detail budget.** An icon that carries more than ~10 paths stops resolving at 16px.
  `bug`, `settings` and `git-compare` sit at the limit; anything busier belongs at 20px+.
- **Two icons that share a silhouette must differ at 16px.** That is why `link-broken` has a
  visible gap plus break marks rather than only a gap, and why `settings` (ring, hub and
  teeth) cannot be confused with `sun` (disc and separated rays).
