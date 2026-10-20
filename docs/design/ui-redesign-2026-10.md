> **Historical spec.** Written before the work, implemented in `f5f07b8`, and
> kept for the reasoning rather than as a description of the current interface.
> It is in the imperative throughout ("330px of header furniture becomes
> 154px") and ends with a build order, so read it as the brief it was. Where it
> and the running app disagree, the app is right.
>
> Its reference table extracts palettes and type choices from four named
> commercial products. That is normal working practice for a design brief and
> is reproduced here as analysis, not as a claim on anyone's design.

# Toadstone UI/UX redesign — design spec

Target: `web/templates/index.html`, `web/static/css/styles.css`, and CSS-adjacent JS.
No framework, no build step. One stylesheet, CSS custom properties, `<html data-theme>` + `<html data-density>`.

---

## 1. Direction

Toadstone is a console for reading 500,000 rows, not a landing page. The redesign takes the
near-black, hairline-bordered, chrome-minimal surface of a modern dev tool and spends every
recovered pixel on rows: 330px of header furniture becomes 154px, a 53px row becomes 26px, and
the one accent colour is reserved for the active view and the primary action so that red, amber
and green always mean a status code and nothing else. Type is Inter for chrome and a monospace
face for URLs, status codes, byte sizes and millisecond counts, because a crawler's payload is
machine strings that must align and be scannable at 12px.

**References**

| Reference | What it earns its place for |
|---|---|
| `linear-app` — bg `#0a0a0a`, Inter Variable, accent `#e9d890` | The embedded product screenshot is the exact shape we need: a narrow left rail of labels + counts, ~24px rows, hairline separators, no zebra, chrome that recedes to near-black so content is the only lit thing. Our neutral ramp and the "one accent, used sparingly" rule come from here. |
| `codepen-io` — bg `#1e1e1e`, accent `#47cf73` | A dev tool whose *entire* nav is a fixed rail of icon+label rows, and whose single saturated green marks the one primary action on the page. Confirms the filter rail as permanent furniture (not a drawer) and the discipline of exactly one filled button per surface. |
| `motherduck-com` — Aeonik Mono, light `#bfb9b7` paper | The light theme's argument: a data product that stays technical in light mode by putting mono type on warm-grey paper rather than white. Our light tokens use a warm off-white (`#fbfbfa`), not `#fff`, and our mono-for-data rule comes from here. |
| `redis-io` — dark `#143444`, single hot `#ee4239` | One saturated red against a cold dark field reads instantly as *state*. Licenses our rule that `--danger` is spent only on 5xx / errors, never on a Logout button (which today is a full red pill, the loudest thing on screen). |

What we take: composition and restraint. What we leave: all four are marketing pages — no hero,
no 96px section rhythm, no display type above 28px anywhere in this app.

---

## 2. Design tokens

Put this at the top of `styles.css`. Dark is the default under `prefers-color-scheme: dark`;
`[data-theme]` on `<html>` overrides both ways; the toggle writes `localStorage['toadstone.theme']`.

```css
:root {
  /* ---- neutrals: LIGHT (default) ---- */
  --bg-sunken:#f2f2f0; --bg-base:#fbfbfa; --bg-surface:#ffffff; --bg-raised:#ffffff;
  --bg-overlay:#ffffff; --bg-hover:rgba(16,18,24,.045); --bg-active:rgba(16,18,24,.075);
  --bg-selected:rgba(79,70,229,.09); --scrim:rgba(16,18,24,.45);
  --border-subtle:#ebebe8; --border:#dcdcd7; --border-strong:#bdbdb6;
  --text-primary:#16181d; --text-secondary:#5c6270; --text-tertiary:#878d9b; --text-disabled:#b2b7c1;
  --text-inverse:#ffffff;

  /* ---- accent (indigo) ---- */
  --accent:#4f46e5; --accent-hover:#4338ca; --accent-text:#4338ca;
  --accent-bg:rgba(79,70,229,.10); --accent-border:rgba(79,70,229,.35);

  /* ---- status: 2xx / 3xx / 4xx / 5xx / no-response ---- */
  --ok:#047857;   --ok-bg:rgba(4,120,87,.10);   --ok-border:rgba(4,120,87,.30);
  --info:#1d4ed8; --info-bg:rgba(29,78,216,.10); --info-border:rgba(29,78,216,.30);
  --warn:#b45309; --warn-bg:rgba(180,83,9,.11);  --warn-border:rgba(180,83,9,.30);
  --danger:#c2231f;--danger-bg:rgba(194,35,31,.10);--danger-border:rgba(194,35,31,.30);
  --muted:#6b7280; --muted-bg:rgba(107,114,128,.10); --muted-border:rgba(107,114,128,.28);

  /* ---- type ---- */
  --font-sans:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --font-mono:ui-monospace,'SF Mono','JetBrains Mono',Menlo,Consolas,monospace;
  --fs-2xs:10px; --lh-2xs:14px;   /* th labels, micro-meta */
  --fs-xs:11px;  --lh-xs:15px;    /* badges, counts, help text */
  --fs-sm:12px;  --lh-sm:16px;    /* compact table cells, rail labels */
  --fs-md:13px;  --lh-md:18px;    /* body default, inputs, buttons, comfy cells */
  --fs-lg:14px;  --lh-lg:20px;    /* section headings, modal fields */
  --fs-xl:16px;  --lh-xl:22px;    /* modal title */
  --fs-2xl:20px; --lh-2xl:26px;   /* stat tile value */
  --fs-3xl:28px; --lh-3xl:32px;   /* KPI value (dashboard only) */
  --fw-regular:400; --fw-medium:500; --fw-semibold:600; --fw-bold:700;
  --tracking-caps:.06em;

  /* ---- space (4px base) ---- */
  --sp-1:2px; --sp-2:4px; --sp-3:6px; --sp-4:8px; --sp-5:12px; --sp-6:16px;
  --sp-7:20px; --sp-8:24px; --sp-9:32px; --sp-10:40px; --sp-11:48px;

  /* ---- radius / shadow / motion ---- */
  --r-xs:3px; --r-sm:4px; --r-md:6px; --r-lg:8px; --r-xl:12px; --r-full:999px;
  --shadow-sm:0 1px 2px rgba(16,18,24,.08);
  --shadow-md:0 4px 12px -2px rgba(16,18,24,.12),0 1px 2px rgba(16,18,24,.06);
  --shadow-lg:0 18px 44px -10px rgba(16,18,24,.22),0 2px 6px rgba(16,18,24,.08);
  --focus-ring:0 0 0 2px var(--bg-base),0 0 0 4px var(--accent);
  --dur-fast:100ms; --dur:150ms; --ease:cubic-bezier(.2,0,.2,1);

  /* ---- shell metrics ---- */
  --h-topbar:48px; --h-cmdbar:44px; --h-subbar:36px; --h-statusbar:26px;
  --h-thead:30px; --w-rail:232px;
  --ctl-sm:24px; --ctl-md:28px; --ctl-lg:32px;
  --z-rail:10; --z-topbar:60; --z-mega:70; --z-modal:100; --z-toast:1001;

  /* ---- density (comfortable default) ---- */
  --row-h:34px; --row-pad-y:8px; --row-pad-x:10px; --cell-fs:var(--fs-md); --cell-lh:var(--lh-md);
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { /* dark values — see block below */ }
}
:root[data-theme="dark"] { /* same dark values */ }
```

Dark values (paste into both dark blocks):

```css
  --bg-sunken:#08090b; --bg-base:#0b0c0f; --bg-surface:#111318; --bg-raised:#171a21;
  --bg-overlay:#1c2028; --bg-hover:rgba(255,255,255,.045); --bg-active:rgba(255,255,255,.08);
  --bg-selected:rgba(129,140,248,.14); --scrim:rgba(0,0,0,.66);
  --border-subtle:#1b1f27; --border:#272c37; --border-strong:#3b4250;
  --text-primary:#e8eaf0; --text-secondary:#9aa3b2; --text-tertiary:#6b7484; --text-disabled:#4a515e;
  --text-inverse:#0b0c0f;
  --accent:#6366f1; --accent-hover:#7c85f7; --accent-text:#a5b0ff;
  --accent-bg:rgba(99,102,241,.16); --accent-border:rgba(99,102,241,.42);
  --ok:#34d399;   --ok-bg:rgba(52,211,153,.13);   --ok-border:rgba(52,211,153,.32);
  --info:#60a5fa; --info-bg:rgba(96,165,250,.13); --info-border:rgba(96,165,250,.32);
  --warn:#fbbf24; --warn-bg:rgba(251,191,36,.13); --warn-border:rgba(251,191,36,.32);
  --danger:#f87171;--danger-bg:rgba(248,113,113,.13);--danger-border:rgba(248,113,113,.32);
  --muted:#94a3b8; --muted-bg:rgba(148,163,184,.12); --muted-border:rgba(148,163,184,.28);
  --shadow-sm:0 1px 2px rgba(0,0,0,.45);
  --shadow-md:0 4px 14px -2px rgba(0,0,0,.55),0 1px 2px rgba(0,0,0,.4);
  --shadow-lg:0 20px 48px -12px rgba(0,0,0,.72),0 2px 8px rgba(0,0,0,.5);
```

Density override: `:root[data-density="compact"]{--row-h:26px;--row-pad-y:4px;--cell-fs:var(--fs-sm);--cell-lh:var(--lh-sm)}`
Delete the `body` background gradient, all `backdrop-filter`, and every `::after` gradient underline
(`.app-title::after`, `.input-label::after`) — they cost paint and say nothing.

---

## 3. Layout

```
1440 / 1024 ───────────────────────────────────────────────────────────────────
┌──────────────────────────────────────────────────────────────────── 48px ───┐
│ ◧ Toadstone │ [Reports ▾]  Overview Internal External …  │ ● crawl #5 ▾ │☾ ⋯│ topbar
├──────────────────────────────────────────────────────────────────── 44px ───┤
│ https://example.com ………………………………………… │ ▶Start │ ■Stop │ Clear │ ▰▰▰▱ 62% │ cmdbar
├──────────────┬──────────────────────────────────────────────────── 36px ───┤
│ FILTERS  ⌄   │ Issues · 10 rows │ [chips: 4xx ×] [Clear] │ ⌕ │ ≡ density │ ⇩ │ subbar
│ Internal  98 ├─────────────────────────────────────────────────────────────┤
│ External   0 │ URL              TYPE   CATEGORY  ISSUE          DETAILS  ⇅ │ thead 30px
│ RESPONSE     │ ─────────────────────────────────────────────────────────── │
│  2xx      98 │ https://…/       ⚠ warn Performance  Moderate…   1370 ms    │ 26/34px rows
│  4xx       0 │ …                                                           │
│ CONTENT      │                                                             │
│  HTML     92 │                                                             │
│ ── stats ──  │                                                             │
│ Crawled   98 │                                                             │
├──────────────┴──────────────────────────────────────────────────── 26px ───┤
│ Loaded crawl #5 · 98 URLs · 8,013 links · 10 issues          00:00 · idle  │ statusbar
└─────────────────────────────────────────────────────────────────────────────┘
      232px                              flex:1, min-width:0
```

| Width | Shell |
|---|---|
| **1440** | As drawn. Rail 232px fixed. Inline tab strip shows all 11 tabs + plugin tabs; overflow scrolls horizontally inside the strip (never wraps to 2 lines — today it wraps and cuts the E-E-A-T plugin tab in half). Table container scrolls x and y. |
| **1024** | Rail 200px. Topbar drops the inline tab strip's labels below the 8th tab into the mega menu's overflow; strip still scrolls. Stats + Data-Size panels in the rail collapse to `<details>`, closed by default. |
| **768** | Rail becomes an off-canvas drawer (left, 280px, `--shadow-lg`, scrim) opened by a `Filters (2)` button in the subbar. Cmdbar wraps: URL input full width row 1, buttons row 2 (`--h-cmdbar` becomes `auto`, min 44px each row). Tab strip scrolls. |
| **390** | Topbar = logo + `Reports ▾` + status dot + `⋯`. Cmdbar two rows. Rail drawer full-width-minus-48px. Table: `--row-h:34px` forced (comfortable), first column `position:sticky;left:0` with `--bg-surface` and a right hairline, horizontal scroll for the rest. Links tab's two stacked sub-tables become one full-height table with an Internal/External segmented control. Modals go full-screen (`inset:0;border-radius:0`). |

Fixes this layout makes, in order of how much they hurt today: the 15-column Overview table stops
truncating every cell to `htt…` (give the table an explicit `min-width` per tab — Overview `1680px`,
Internal/External `980px`, Issues `1120px`, Links `1180px` — and scroll the container); the Links
tab stops nesting two 5-row scrollers inside a third scroller; the Sitemap tab's third stat tile
stops overflowing the right edge (grid `repeat(auto-fit,minmax(200px,1fr))` inside the pane's
`padding-inline:var(--sp-6)`); and the mobile layout stops giving 280px of a 390px screen to the rail.

---

## 4. Mega menu

Trigger in the topbar: `<button id="megaTrigger" class="mm-trigger" aria-haspopup="true"
aria-expanded="false" aria-controls="megaPanel">Reports <svg…/></button>` — label is the current
tab's name, so the trigger doubles as a "you are here" indicator.

```
┌─ #megaPanel ─ full-bleed under topbar, max-width 1160px, 4 cols, --shadow-lg ──┐
│ CRAWL DATA      ANALYSIS         SHAPE & SPEED    THIS CRAWL                   │
│ Overview   98   Links     8,013  PageSpeed     —  ● Running · #5 example.com      │
│ Internal   98   Issues       10  Visualization —    98/120 · 2:41 · 4.2 URL/s   │
│ External    0   Sitemap    91/1  Compare       3    ───────────────────────     │
│ Status…     1   Rules        12  ── PLUGINS ──      ⌕ Switch crawl…             │
│                                  E-E-A-T        —   #4 example.com  2d  98 ✓      │
│                                                     #3 example.com  3d  96 ✓      │
│                                                     ───────────────────────     │
│                                                     Dashboard   Settings        │
│                                                     Export ▾    Save   Load     │
│                                                     Workshop ↗  Log out         │
└────────────────────────────────────────────────────────────────────────────────┘
```

- **Contents.** Columns 1–3 are proxies for the 11 tabs plus every plugin tab, each with its live
  count taken from the same numbers the rail and tab badges use. Column 4 is the crawl switcher
  (current crawl first, with a status dot — `--ok` complete, `--accent` running + 1.4s pulse,
  `--danger` failed, `--muted` idle — the crawl id, host, page count, elapsed time and speed),
  then the last 5 crawls from `/api/crawls`, then Dashboard, Settings, Export, Save Crawl, Load
  Crawl, Workshop, Log out.
- **Opening.** Click or `Enter`/`Space` on the trigger; `Alt+M` from anywhere. Opens on click only,
  never on hover. Closes on `Esc` (focus returns to the trigger), on outside click, on selecting an
  item, and on `popstate`. Animation: 120ms opacity + 4px translateY, skipped under
  `prefers-reduced-motion`.
- **Keyboard.** Disclosure pattern, not `role="menu"`: the panel is `role="group"
  aria-labelledby="megaTrigger"` and holds real `<button>`s in DOM order, so `Tab` walks them and
  closing happens when focus leaves the panel (`focusout` with `relatedTarget` outside). On top of
  that: `↓`/`↑` move within a column, `→`/`←` move to the same index in the next/previous column,
  `Home`/`End` jump to first/last item in the column.
- **Screen reader.** `aria-expanded` on the trigger; each column headed by a real `<h2
  class="mm-col-title">`; the active item carries `aria-current="page"`; the crawl status line is
  `aria-live="polite"` while a crawl runs; counts are inside the button text (`Issues, 10`), not a
  decorative span.
- **Wiring.** Mega items must **not** carry the class `tab-btn` (router reads `.tab-btn.active`).
  They carry `class="mm-item" data-tab="issues"`, and one delegated listener calls
  `window.switchTab(el.dataset.tab)`. Active state is derived after every switch by reading
  `document.querySelector('.tab-btn.active')`.
- **Responsive.** ≥1024 four columns; 768 two columns, panel `inset-inline:var(--sp-5)`; 390 a
  full-screen sheet, one column, sections stacked, sticky close button.

---

## 5. Filters

Rail sections, top to bottom: **Scope** (Internal, External) · **Response codes** (2xx, 3xx, 4xx,
5xx, No response) · **Content type** (HTML, CSS, JavaScript, Images) · `<details>` **Statistics** ·
`<details>` **Data size**.

Each facet row: 26px tall, `--fs-sm`, `padding: 0 var(--sp-5)`, a 14px checkbox on the left, the
label, and a right-aligned count in `--font-mono` `--fs-xs` `--text-tertiary` inside a 
`--muted-bg` pill. Zero-count rows drop to `--text-disabled` and `pointer-events:none`. Hover
`--bg-hover`; active row gets `--bg-selected`, `--text-primary`, and a 2px `--accent` bar on its
left edge via `box-shadow:inset 2px 0 0 var(--accent)`. Section headers are `--fs-2xs`, uppercase,
`--tracking-caps`, `--text-tertiary`, 24px tall.

**Active filter set.** A chip row lives in the subbar, left of the search box:
`[Internal ×] [4xx ×] [severity: error ×] [q: "pricing" ×]  Clear all`. Chip: 20px tall, `--r-full`,
`--accent-bg` + `--accent-border`, `--fs-xs`, with an 12px `×` button (`aria-label="Remove filter
Internal"`). `Clear all` is a ghost button that calls `toggleFilter` with the current filter (to
un-toggle), `filterIssues('all')`, and resets the four Links-tab controls.

**URL mapping** (names are exactly what `web/static/js/router.js` reads):

| Control | Param | Values |
|---|---|---|
| Rail facet | `filter` | `internal external 2xx 3xx 4xx 5xx no_response html css js images` |
| Issues severity | `severity` | `all error warning info` (`all` is omitted from the URL) |
| Links internal status `<select>` | `links_status` | `all 2xx 3xx 4xx 5xx` |
| Links internal search `<input>` | `links_q` | free text |
| Links external status `<select>` | `ext_status` | `all 2xx 3xx 4xx 5xx` |
| Links external search `<input>` | `ext_q` | free text |
| Tab | path `/crawl/<id>/<tab>` or `?tab=` | tab id |
| Settings / Dashboard / detail popup | `settings` `dashboard` `page` | tab id / flag / URL |

**Severity filter (Issues tab).** The four pills stay `<button class="filter-item"
data-filter="all|error|warning|info" onclick="filterIssues('…')">` — `filterIssues` reads
`#issues-tab .filter-item` and `querySelector('[data-filter="all"]')` — but they move out of the
centred bar into the subbar, left-aligned, as a segmented control: 24px tall, single 1px
`--border` hairline around the group, dividers between segments, active segment `--bg-active` +
`--text-primary`. Each carries its severity dot (`--danger` / `--warn` / `--info`) and a mono count.
**Delete the inline `style="…"` attributes on all four** — they are ~300 characters each and they
beat every stylesheet rule. `filterIssues` also writes inline colours onto these buttons
(`app.js:1159`); that block must be replaced by a single `classList.toggle('active')` in the same
step, or the inline colours will keep overriding the new design.

**Multi-select.** The rail is drawn for multi-select (checkboxes), but `crawlState.filters.active`
is a single string and the router's `NAME_RE` is `/^[\w-]+$/`. Step 1 therefore ships single-select
with checkbox affordances behaving as radio-within-group. Real multi-select is build-order step 5:
`filter` becomes a comma-joined, order-stable list (`?filter=4xx,html`), `NAME_RE` becomes
`/^[\w-]+(,[\w-]+)*$/`, `applyFilter` takes a `Set` and ANDs across groups / ORs within a group,
and `toggleFilter` stops clearing the whole `.filter-item` set.

> **Known coupling, do not "fix" by accident:** `toggleFilter` clears `.active` from
> `document.querySelectorAll('.filter-item')` — *global*, so clicking a rail facet also blanks the
> active Issues severity pill. Preserve the class (both `filterIssues` and the router depend on it)
> and, in the same step that rewrites `filterIssues`, scope that line to
> `.sidebar .filter-item` / `#filterRail .filter-item`.

---

## 6. Components

**Button** `.btn` — `--ctl-md` (28px) default; `.btn-sm` 24px / `--fs-xs` / `padding-inline:var(--sp-4)`;
`.btn-lg` 32px / `--fs-md` / `padding-inline:var(--sp-6)`. Radius `--r-md`, weight `--fw-medium`,
`gap:var(--sp-3)`, icon 14px, `letter-spacing:0`. Variants: `.btn-primary` solid `--accent` /
`--text-inverse`; `.btn-secondary` `--bg-raised` + 1px `--border` + `--text-primary`; `.btn-ghost`
transparent, `--text-secondary`, `--bg-hover` on hover; `.btn-danger` `--danger-bg` +
`--danger-border` + `--danger` text (solid `--danger` only inside a destructive confirm dialog);
`.btn-success` same shape with `--ok`. States: hover = next surface step (no `translateY`, it
shifts a 26px row grid); active = `--bg-active`, no transform; focus-visible = `box-shadow:
var(--focus-ring)`; disabled = `opacity:.45; cursor:not-allowed`; `[aria-busy=true]` swaps the icon
for a 12px spinner. Exactly one `.btn-primary` per surface. **Log out becomes `.btn-ghost`.**

**Input** `.input` / `.url-input` — 28px (URL bar 32px), `--bg-surface`, 1px `--border`, `--r-md`,
`padding:0 var(--sp-5)`, `--fs-md`, mono when the value is a URL. Focus: `border-color:var(--accent)`
+ `--focus-ring`. Error: `--danger-border` + a 16px `--fs-xs` `--danger` message below. A leading
16px icon slot (`padding-left:var(--sp-9)`) for search fields; a trailing `×` clear button when
non-empty. Placeholder `--text-tertiary`. The `URL to Crawl` label becomes a `--fs-2xs` uppercase
prefix *inside* the field's left gutter, killing the 20px label row.

**Select** `.select` — same box as input, native `<select>`, `appearance:none`, 16px chevron at
`right:var(--sp-4)`, `padding-right:var(--sp-9)`. Never taller than its input siblings.

**Checkbox / radio** — 14px box, `--r-xs` (radio `--r-full`), 1px `--border-strong`, checked =
`--accent` fill + white 10px tick, focus ring as above, 6px gap to a `--fs-md` label, whole label
clickable, 20px minimum hit row. Indeterminate = `--accent` fill + 8×2px white bar.

**Tabs** `.tab-header` — 32px strip, `overflow-x:auto`, `scrollbar-width:none`, `flex-wrap:nowrap`,
`scroll-snap-type:x proximity`. `.tab-btn`: 32px tall, `padding-inline:var(--sp-5)`, `--fs-md`,
`--text-secondary`, no background; `.active` = `--text-primary` + 2px `--accent` underline drawn
with `box-shadow:inset 0 -2px 0 var(--accent)` (not a pseudo-element — plugin tabs are created by
JS and get no extra markup). A count badge sits inline, `--fs-xs`, mono, `--muted-bg`; Issues' badge
turns `--danger-bg`/`--danger` when errors > 0. `.tab-pane` stays `display:none` / `.active
{display:flex; flex-direction:column; min-height:0}`.

**Table** `.data-table` — `table-layout:fixed` (kept: `column-resize.js` writes `th.style.width`),
`border-collapse:separate; border-spacing:0`, `width:100%`, per-tab `min-width` (see §3),
`background:transparent`, no `border-radius`, no `overflow:hidden` (it clips the sticky header).
- `thead th`: height `--h-thead` (30px), `position:sticky; top:0; z-index:2`, opaque
  `--bg-surface` background (transparency lets rows show through), `border-bottom:1px solid
  var(--border)`, `--fs-2xs`, uppercase, `--tracking-caps`, `--fw-semibold`, `--text-tertiary`,
  `padding:0 var(--row-pad-x)`, `position:relative` (the resize grip is absolutely positioned
  inside it — **do not make `th` a flex container**; wrap label + sort chevron in an inner
  `<span class="th-inner">` that is `inline-flex`).
- Sort affordance: `th[aria-sort]` shows a 10px chevron at the right, `opacity:.25` when
  `none`, `1` + `--text-primary` when `ascending`/`descending`; the whole `th-inner` is the hit
  target and carries `role="button" tabindex="0"`.
- `tbody td`: height `--row-h`, `padding:var(--row-pad-y) var(--row-pad-x)`, `font-size:var(--cell-fs)`,
  `line-height:var(--cell-lh)`, `--fw-regular`, `--text-primary`, `white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis`, `border-bottom:1px solid var(--border-subtle)`.
- **No zebra.** Remove `nth-child(even)` — the virtual scroller inserts spacer `<tr>`s at both ends
  of `<tbody>`, so row parity flips as you scroll and the stripes crawl. Rows are separated by the
  hairline; hover is `--bg-hover`; keyboard-focused row is `--bg-selected` + `box-shadow:inset 2px 0
  0 var(--accent)`.
- Selection (future): 32px leading checkbox column, `aria-selected` row, header checkbox with
  indeterminate state, and a floating action bar when ≥1 row is selected.
- URL cells: `--font-mono`, `--fs-sm`, `direction:ltr`, and `text-overflow:ellipsis` at the *end*;
  the full URL is the cell's `title`.
- Empty: reuse `.empty-state` / `.empty-state-content` (already styled and referenced by id
  `#issuesEmptyState` and by `reports.js`) — 40px icon `--text-disabled`, `--fs-lg`
  `--fw-semibold` heading, `--fs-md` `--text-secondary` body, optional button. Loading: 10
  skeleton rows (see below) rather than a spinner.

**Badge** `.badge` — 18px tall, `--r-sm`, `padding:0 var(--sp-3)`, `--fs-xs`, `--fw-medium`, 1px
border, tinted background: `.badge-error`(`--danger-*`), `.badge-warning`(`--warn-*`),
`.badge-info`(`--info-*`), `.badge-neutral`(`--muted-*`), `.badge-success`(`--ok-*`). A 6px round
dot leads the label; **drop the ❌/⚠️/ℹ️ emoji** (they render at different sizes per platform and
break the 26px row). Status codes use `.status-badge.status-2xx|3xx|4xx|5xx` (emitted by
`app.js:2400`) mapped to `--ok/--info/--warn/--danger`, mono, and must be promoted out of the
`.cy-tooltip` scope where they currently live to global rules.

**Card / stat tile** `.stat-tile` — `--bg-surface`, 1px `--border`, `--r-lg`, `padding:var(--sp-5)
var(--sp-6)`, label `--fs-xs` uppercase `--tracking-caps` `--text-tertiary`, value `--fs-2xl`
`--fw-semibold` mono, optional delta `--fs-xs` with `--ok`/`--danger`. Grid:
`repeat(auto-fit,minmax(200px,1fr))`, `gap:var(--sp-5)`. Rail stats use the compact form: one 22px
row, label left `--fs-sm` `--text-secondary`, value right mono `--fs-sm` `--text-primary`.

**Modal** `.modal` — `position:fixed; inset:0; background:var(--scrim)`, `display:flex` *set
inline by JS*; `.modal-content` `--bg-overlay`, 1px `--border`, `--r-xl`, `--shadow-lg`,
`max-width:760px` (Dashboard 1100px), `max-height:min(86vh,780px)`, grid
`auto 1fr auto`. Header 48px, `--fs-xl` `--fw-semibold`, close button 28px ghost. Body scrolls,
`padding:var(--sp-6) var(--sp-8)`. Footer 56px, `--bg-surface`, top hairline, buttons right.
Settings body becomes a **two-column form grid** (`repeat(auto-fit,minmax(300px,1fr))`,
`gap:var(--sp-5) var(--sp-8)`) — today one Crawler tab is ~2,100px of scroll; `.setting-group`
becomes label (`--fs-md --fw-medium`, 20px) + control (28px) + `.setting-help` (`--fs-xs
--text-tertiary`, 16px) = 68px instead of 107px. `.setting-group` that JS shows/hides must keep
working with `style.display='block'` (see §8). Focus is trapped; `Esc` closes.

**Drawer** `.drawer` — off-canvas rail at ≤768: `position:fixed; inset-block:0; left:0; width:280px`,
`--bg-surface`, right hairline, `--shadow-lg`, `transform:translateX(-100%)` → `0` in 180ms,
scrim behind, focus trapped, `Esc` closes, `aria-modal="true"`.

**Toast** `.notification` — bottom-right stack (`inset:auto var(--sp-6) var(--sp-6) auto`), 280–380px,
`--bg-overlay`, 1px border tinted per type, `--shadow-lg`, `--r-lg`, `padding:var(--sp-5)`, leading
14px icon, `--fs-md`, `--z-toast`, auto-dismiss 4s (errors 8s, with a close button), enters with
opacity + 8px translateY. **`settings.js:showNotification` writes `style.cssText` with
`position:fixed;top:20px;right:20px` and gradient backgrounds** — inline styles win, so either
rewrite that function in the same step or every rule here needs `!important`. Rewriting it is the
correct move; the class names `notification` and `notification-<type>` must survive.

**Progress** `.progress-bar` — 3px tall, `--r-full`, track `--bg-active`, `.progress-fill`
solid `--accent` (no gradient), `transition:width 240ms linear`. It lives inline at the right end
of the cmdbar, with `.progress-text` in mono `--fs-xs` `--text-secondary` beside it; indeterminate
state = 30%-wide bar sliding on a 1.2s loop, disabled under `prefers-reduced-motion`.

**Skeleton** `.skeleton` — `--bg-active`, `--r-sm`, height matches the text it replaces (12px for
cells, 18px for values); shimmer is a 1.4s linear-gradient sweep, reduced-motion falls back to a
1.6s opacity pulse between `.5` and `.9`.

---

## 7. Density

| Token | Comfortable (default) | Compact |
|---|---|---|
| `--row-h` | 34px | 26px |
| `--row-pad-y` / `--row-pad-x` | 8px / 10px | 4px / 10px |
| `--cell-fs` / `--cell-lh` | 13px / 18px | 12px / 16px |
| `thead` | 30px, 10px label | 26px, 10px label |
| Rows in a 900px-tall window | ~20 | ~26 |

(Today: 53px rows, 13 visible. Compact doubles the payload.)

Toggle: a 24px two-segment control in the subbar (`≡` comfortable / `≣` compact), `aria-pressed`,
writing `document.documentElement.dataset.density` and `localStorage['toadstone.density']`.
Below 768px the app forces comfortable (touch targets), ignoring the stored value.

Two things the toggle must do beyond swapping tokens:
1. Call `Object.values(virtualScrollers).forEach(s => s && s.scheduleRender())`. The scroller
   re-measures painted row height after each render, but its `ResizeObserver` watches the
   *container*, whose height does not change — without the nudge the spacer math stays on the old
   estimate until the next scroll.
2. The `rowHeight` options in `app.js` (`100` for overview, `80` for the other five) are initial
   estimates only, but they are 3–4× the new reality and cause a visible first-paint jump. Set them
   to `34` (overview `40`) in the same step.

---

## 8. What must not break — the JS contract

Everything in this section is read by existing JS. Changing a name, a quote style, or the
whitespace inside an `onclick` attribute breaks the app silently.

**Inline handlers, verbatim.** `router.js` matches attributes with regexes and *exact* attribute
selectors (`` `.filter-item[onclick="toggleFilter('${filterType}')"]` ``,
`select[onchange="filterInternalLinks(this.value)"]`,
`input[oninput="searchInternalLinks(this.value)"]`, and the same for the external pair;
`.settings-tab-btn[onclick="switchSettingsTab('${tabName}')"]`;
`` `.tab-btn[onclick="switchTab('${tabName}')"]` ``). Single quotes inside, double quotes outside,
no spaces. Keep all 65:

- `switchTab('…')` × 11: `overview internal external status-codes links issues pagespeed visualization sitemap compare rules`
- `toggleFilter('…')` × 11: `internal external 2xx 3xx 4xx 5xx no_response html css js images`
- `filterIssues('…')` × 4: `all error warning info` (each also `data-filter="…"`)
- `switchSettingsTab('…')` × 8: `crawler requests filters export javascript issues customcss advanced`
- `filterInternalLinks(this.value)` · `searchInternalLinks(this.value)` · `filterExternalLinks(this.value)` · `searchExternalLinks(this.value)`
- `toggleCrawl()` `stopCrawl()` `clearCrawlData()` `handleUrlKeypress(event)` `exportData()` `saveCrawl()` `loadCrawl()` `logout()`
- `openDashboard()` `closeDashboard()` `openSettings()` `closeSettings()` `saveSettings()` `resetSettings()` `resetIssueExclusions()`
- `loadSitemapReport()` `loadRules()` `testRules()` `saveRules()` `loadCompareOptions()` `runComparison()`
- `changeLayout(this.value)` `filterVisualization(this.value)` `resetVisualization()` `exportVisualizationImage()`

**Element ids** (all read by `getElementById`): `urlInput startBtn stopBtn clearBtn exportBtn
saveCrawlBtn loadCrawlBtn userInfo progressContainer progressFill progressText statusText crawlTime
discoveredCount crawledCount crawlDepth crawlSpeed memCurrent memPeak memEstimate1M memAvailable
internal-count external-count 2xx-count 3xx-count 4xx-count 5xx-count no_response-count html-count
css-count js-count images-count overviewTableBody internalTableBody externalTableBody
statusCodesTableBody internalLinksTableBody externalLinksTableBody issuesTableBody issuesTable
issuesEmptyState issues-all-count issues-error-count issues-warning-count issues-info-count
pagespeedResults cy vizLayout vizFilter sitemapReport compareAgainst compareReport rulesDomain
rulesEditor rulesStatus rulesPreview acceptedIssues dashboardModal dashboardContent settingsModal
custom-user-styles demoLimitNotification demoLimitDismiss` — plus the tab panes
`overview-tab internal-tab external-tab status-codes-tab links-tab issues-tab pagespeed-tab
visualization-tab sitemap-tab compare-tab rules-tab` and the settings panes
`crawler-settings requests-settings filters-settings export-settings javascript-settings
issues-settings customcss-settings advanced-settings`.

**Settings field ids** — `populateSettingsForm` does `getElementById(settingsKey)` and
`collectSettingsFromForm` walks a hard-coded list; every id below must exist with the right input
type: `maxDepth maxUrls crawlDelay followRedirects crawlExternalLinks ignoreTrackingParams
stripQueryParams keepLastCrawls auditSitemaps analyseLinkGraph auditCms cmsExpectedAuthor
renderedChecks webhookUrl staleAfterMonths userAgent timeout retries acceptLanguage
respectRobotsTxt allowCookies discoverSitemaps enablePageSpeed googleApiKey includeExtensions
excludeExtensions includePatterns excludePatterns maxFileSize crawlImages enableDuplicationCheck
duplicationThreshold exportFormat concurrency memoryLimit logLevel saveSession enableProxy proxyUrl
customHeaders enableJavaScript jsWaitTime jsTimeout jsBrowser jsHeadless jsUserAgent
jsViewportWidth jsViewportHeight jsMaxConcurrentPages customCSS issueExclusionPatterns`.
Plus the 22 `<input type="checkbox" name="exportFields" value="…">` with their current values
(`url status_code title meta_description h1 keywords word_count content_type response_time
canonical_url og_tags twitter_tags json_ld analytics internal_links external_links images
links_detailed lang charset viewport robots issues_detected`).

**Classes** — `tab-btn tab-pane tab-header tab-content plugin-tab filter-item filter-count
data-table table-container internal-links-container external-links-container settings-tabs
settings-tab-btn settings-tab-content setting-group setting-help checkbox-label checkbox-grid
details-modal-overlay details-modal details-header details-content details-sections details-section
details-subsection details-grid details-url details-btn close-btn modal modal-content modal-header
modal-body modal-footer modal-close empty-state empty-state-content graph-placeholder btn
btn-primary btn-secondary btn-danger btn-success url-link status-badge status-2xx status-3xx
status-4xx status-5xx column-resize-grip notification notification-<type> metric metric-value
metric-label json-preview tooltip-url tooltip-info tooltip-status cy-tooltip dimmed highlighted
score-good score-needs-improvement score-poor score-unknown legend-item legend-color
pagespeed-container pagespeed-header pagespeed-description pagespeed-results pagespeed-results-grid
pagespeed-page-card pagespeed-page-header pagespeed-page-url pagespeed-analysis-date
pagespeed-device-result pagespeed-metrics pagespeed-score pagespeed-error pagespeed-placeholder`.
`.active` is the only state class JS toggles; never rename it.

**Structural invariants**

1. `.tab-header` must be a real element that accepts appended `<button class="tab-btn"
   data-plugin-id="…">`, and `.tab-content` must accept appended `<div id="<id>-tab" class="tab-pane
   plugin-tab" data-plugin-id="…">` (`plugin-loader.js:236,264`). Plugin tabs have **no** `onclick`
   attribute — the router falls back to `activeTab.dataset.pluginId`, so `data-plugin-id` must
   stay on the button, and the button must be a direct child of `.tab-header`.
2. Every tab pane's id is `<tabName>-tab`; `switchTab` calls `.getElementById(tabName + '-tab')`
   unguarded and throws if it is missing.
3. Virtual-scroller containers are found by these exact selectors and must each be the single
   scrolling element containing one `<table>` with `thead tr` and `tbody`:
   `#overview-tab .table-container`, `#internal-tab .table-container`,
   `#external-tab .table-container`, `#issues-tab .table-container`,
   `#links-tab .internal-links-container`, `#links-tab .external-links-container`.
   Do **not** add a second `.table-container` inside any of those panes — `querySelector` takes the
   first. The scroller sets `overflow-x/y:auto` and `position:relative` on them itself and inserts
   spacer `<tr>` elements as the first and last children of `<tbody>`.
4. `column-resize.js` appends an absolutely-positioned `.column-resize-grip` into every `th` except
   the last and sets `th.style.position='relative'` and `th.style.width` in px. Keep `th` a
   table-cell, keep `table-layout:fixed`, and do not put `overflow:hidden` on the `<table>` or the
   grip's 8px overhang (`right:-4px`) is clipped.
5. **Modals are opened by inline style.** `router.js:isOpen()` returns true only when
   `el.style.display !== 'none' && el.style.display !== ''`, and `openSettings` sets
   `style.display='flex'`. `#settingsModal` and `#dashboardModal` must ship with inline
   `style="display: none;"` and must not be converted to `[hidden]`, `.is-open`, or `<dialog>`.
   Same for `#progressContainer`, `#issuesEmptyState`, `#proxySettings` and the eight JS groups
   `jsSettings jsTimeoutGroup jsBrowserGroup jsHeadlessGroup jsUserAgentGroup jsViewportGroup
   jsConcurrencyGroup jsWarning` — these are toggled with `style.display = 'block' | 'none'`, so
   their visible layout must be correct at `display:block` (use an inner wrapper if you need flex).
6. `applyTierRestrictions` shows/hides `.settings-tab-btn` with `style.display='inline-block'` and
   re-finds the first visible one with `.settings-tab-btn[style*="inline-block"]`. A flex
   `.settings-tabs` is fine (flex blockifies the child), but never hide settings tabs with a class.
7. `.details-modal-overlay` is created by `showUrlDetails` and the router stores the URL on
   `overlay.dataset.url`; the element must remain queryable at document level.
8. `#custom-user-styles` is a `<style>` appended to `<head>` at runtime — the main stylesheet must
   be linked *before* it (it already is) so user CSS keeps winning.
9. Tab order in `.tab-header` is the crawl-tab order the plugin loader inserts into by numeric
   index; keep the 11 core tabs as the first 11 children.

---

## 9. Build order

Each step leaves the app fully usable.

1. **Tokens + reset.** Add the `:root` blocks, the dark/light media query and the `[data-theme]`
   overrides at the top of `styles.css`; add the theme toggle (topbar button, `localStorage`) and
   an early inline script in `<head>` that sets `data-theme`/`data-density` before first paint.
   Rewrite the existing rules to consume the tokens, colour by colour. No markup changes.
   *Result: same layout, new palette, light mode works.*
2. **Density + table.** Row/header/cell rules, sticky opaque header, zebra removed, per-tab table
   `min-width`, sticky first column, mono URL cells, sort chevrons (visual only), skeletons,
   `--row-h` density tokens, the density toggle, the `scheduleRender()` nudge and the `rowHeight`
   estimate fix in `app.js`. *Result: the tables become the product; Overview stops truncating.*
3. **Shell.** Collapse header + URL section into a 48px topbar and a 44px cmdbar; add the 36px
   subbar (tab title, row count, chips, search, density, export); shrink the status bar to 26px;
   rebuild the filter rail (checkbox rows, counts, `<details>` stats); make `.tab-header` a
   single-line scroll strip. *Result: +176px of table on every screen.*
4. **Mega menu.** Topbar trigger, the four-column panel, the crawl switcher, the keyboard model,
   and the `mm-item`→`switchTab` delegation. Inline tab strip stays for one-click switching.
   *Result: navigation and crawl switching stop living in seven header buttons.*
5. **Filters, properly.** Chip row wired to the router params, `Clear all`, severity segmented
   control in the subbar with its inline styles deleted and `filterIssues` rewritten to
   class-toggle, the `.sidebar .filter-item` scoping fix, then multi-select
   (`?filter=4xx,html`, `NAME_RE`, `applyFilter(Set)`).
6. **Components sweep.** Buttons/inputs/selects/checkboxes to the new specs, badges (emoji out,
   `.status-badge` promoted to global), stat tiles, modals — including the two-column Settings form
   — drawer, toast rewrite in `settings.js`, progress bar, empty states.
7. **Responsive.** The 1024 / 768 / 390 rules, the rail drawer, full-screen modals, the Links tab's
   segmented Internal/External table, and a Playwright pass at all four widths asserting: no
   horizontal page scroll, no clipped tab, every `.tab-pane .table-container` scrolls, the mega
   menu opens and closes on `Esc`, and every URL param in §5 still round-trips.
