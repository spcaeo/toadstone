// Sitemap and Compare tabs.
//
// Both read the reporting API (src/api/reporting.py) for the crawl currently
// open, so they work on a saved crawl reached by its URL as well as on one
// that just finished. Nothing here holds state of its own: the crawl id comes
// from the router, and every filter the user picks is reflected in the URL.

(function () {
    const escape = (value) => String(value ?? '').replace(/[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const count = (value) => Number(value || 0).toLocaleString();

    function currentCrawlId() {
        return window.Router?.currentCrawlId() || null;
    }

    function notice(container, message) {
        container.innerHTML = `<div class="empty-state"><div class="empty-state-content">
            <h3>${escape(message)}</h3></div></div>`;
        paint(container);
    }

    // icons.js makes one automatic pass on DOMContentLoaded; every report here
    // is built long after it, so each container asks for its own
    function paint(container) {
        if (container && typeof renderIcons === 'function') renderIcons(container);
    }

    async function getJson(url) {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json();
    }

    // ---- Sitemap coverage -------------------------------------------------

    async function loadSitemapReport() {
        const container = document.getElementById('sitemapReport');
        if (!container) return;

        const crawlId = currentCrawlId();
        if (!crawlId) {
            notice(container, 'Run or open a crawl to see its sitemap coverage');
            return;
        }

        container.innerHTML = '<p class="report-note">Loading…</p>';
        try {
            const data = await getJson(`/api/crawls/${crawlId}/sitemaps`);
            const files = data.files || [];
            const coverage = data.coverage || {};
            if (!files.length) {
                notice(container, 'No sitemap was found for this crawl');
                return;
            }

            const inBoth = coverage.in_both || [];
            const crawledOnly = coverage.crawled_only || [];
            const sitemapOnly = coverage.sitemap_only || [];

            container.innerHTML = `
                ${coverageVenn(crawlId, inBoth.length, crawledOnly.length, sitemapOnly.length)}
                <div class="stat-grid">
                    ${card('In sitemap and crawled', inBoth.length, 'ok')}
                    ${card('Crawled, not in sitemap', crawledOnly.length, 'warn')}
                    ${card('In sitemap, not crawled', sitemapOnly.length, 'info')}
                </div>
                <div class="table-container report-block">
                    <table class="data-table">
                        <thead><tr>
                            <th class="col-45"><span class="th-inner">Sitemap file</span></th>
                            <th><span class="th-inner">Status</span></th>
                            <th><span class="th-inner">URLs</span></th>
                            <th><span class="th-inner">Size</span></th>
                            <th><span class="th-inner">Index</span></th>
                            <th><span class="th-inner">Problems</span></th>
                        </tr></thead>
                        <tbody>${files.map(fileRow).join('')}</tbody>
                    </table>
                </div>
                <div class="report-actions">
                    ${coverageLink(crawlId, 'crawled_only', 'Crawled, not in sitemap', crawledOnly.length)}
                    ${coverageLink(crawlId, 'sitemap_only', 'In sitemap, not crawled', sitemapOnly.length)}
                    ${coverageLink(crawlId, 'in_both', 'Covered by both', inBoth.length)}
                    ${exportLink(crawlId, 'sitemap-coverage', 'xlsx', 'All coverage (XLSX)')}
                    ${exportLink(crawlId, 'sitemap-files', 'csv', 'Sitemap files (CSV)')}
                </div>`;
            paint(container);
        } catch (error) {
            notice(container, `Could not load the sitemap report: ${error.message}`);
        }
    }

    // tone is one of ok / warn / info / danger / muted (spec §6, stat tile)
    const card = (label, value, tone) => `
        <div class="stat-tile tone-${tone}">
            <div class="stat-tile-label">${escape(label)}</div>
            <div class="stat-tile-value">${value}</div>
        </div>`;

    function fileRow(file) {
        const problems = (file.errors || []).join('; ');
        const kb = file.bytes ? `${(file.bytes / 1024).toFixed(1)} KB` : '—';
        return `<tr>
            <td class="cell-url"><a href="${escape(file.url)}" target="_blank"
                class="url-link">${escape(file.url)}</a></td>
            <td><span class="badge ${file.status === 200 ? 'badge-success' : 'badge-error'}">${escape(file.status ?? '—')}</span></td>
            <td class="mono">${escape(file.entries ?? 0)}</td>
            <td class="mono">${kb}</td>
            <td>${file.is_index ? 'yes' : 'no'}</td>
            <td class="${problems ? 'text-warn' : 'text-muted'}">${escape(problems || 'none')}</td>
        </tr>`;
    }

    // Downloads come from the exports API as a plain navigation, so a large
    // crawl streams to disk instead of being built as a data: URI in the tab.
    function exportLink(crawlId, dataset, format, label) {
        const href = `/api/crawls/${crawlId}/export/${dataset}?format=${encodeURIComponent(format)}`;
        return `<a class="btn btn-secondary btn-sm" download href="${href}"
                   >${glyph('download', 14)} ${escape(label)}</a>`;
    }

    // One coverage list, narrowed server-side by the rows' own state column
    function coverageHref(crawlId, state) {
        return `/api/crawls/${crawlId}/export/sitemap-coverage?format=csv&state=${encodeURIComponent(state)}`;
    }

    function coverageLink(crawlId, state, label, total) {
        if (!total) return '';
        return `<a class="btn btn-secondary btn-sm" download href="${coverageHref(crawlId, state)}"
                   >${glyph('download', 14)} ${escape(label)} (${count(total)})</a>`;
    }

    function reportLink(crawlId, format, label, baseline) {
        const params = new URLSearchParams({ format });
        if (baseline) params.set('baseline', baseline);
        return `<a class="btn btn-secondary btn-sm" download href="/api/crawls/${crawlId}/report?${params}"
                   >${glyph('file-text', 14)} ${escape(label)}</a>`;
    }

    // icons.js, with an empty string when it is not loaded
    function glyph(name, size) {
        return typeof icon === 'function' ? icon(name, { size: size || 16 }) : '';
    }

    // ---- The coverage diagram ---------------------------------------------
    //
    // A real Euler diagram, solved against the numbers rather than drawn once
    // and reused: circle areas are proportional to the two sets, and the
    // centre distance is solved so the lens area equals the overlap. Every
    // region is an <a download>, so a click on "crawled only" fetches exactly
    // that list as CSV.
    //
    // Two honesty rules the maths cannot give us on its own:
    //   * a region with 0 URLs is never drawn with area — containment stays
    //     containment, disjoint stays disjoint;
    //   * a region that is non-zero but would come out a hair wide is widened
    //     to MIN_BAND so it can be read and clicked, and the caption says so.

    const R = 100;         // radius of the larger of the two sets
    const MIN_BAND = 26;   // thinnest band worth drawing, in the same units

    const f = (n) => Math.round(n * 100) / 100;

    function lensArea(d, rA, rB) {
        if (d >= rA + rB) return 0;
        if (d <= Math.abs(rA - rB)) return Math.PI * Math.min(rA, rB) ** 2;
        const a = rA * rA * Math.acos((d * d + rA * rA - rB * rB) / (2 * d * rA));
        const b = rB * rB * Math.acos((d * d + rB * rB - rA * rA) / (2 * d * rB));
        const wedge = 0.5 * Math.sqrt((-d + rA + rB) * (d + rA - rB) * (d - rA + rB) * (d + rA + rB));
        return a + b - wedge;
    }

    // lensArea falls monotonically as the circles move apart, so bisection is
    // both correct and quick — 64 halvings is exact to well under a pixel.
    function solveDistance(rA, rB, target) {
        let lo = Math.abs(rA - rB);
        let hi = rA + rB;
        if (target <= 0) return hi;
        if (target >= Math.PI * Math.min(rA, rB) ** 2) return lo;
        for (let i = 0; i < 64; i += 1) {
            const mid = (lo + hi) / 2;
            if (lensArea(mid, rA, rB) > target) lo = mid; else hi = mid;
        }
        return (lo + hi) / 2;
    }

    function vennLayout(both, aOnly, bOnly) {
        const setA = both + aOnly;
        const setB = both + bOnly;
        const big = Math.max(setA, setB);
        const unit = (Math.PI * R * R) / big;     // diagram area per URL
        let rA = R * Math.sqrt(setA / big);
        let rB = R * Math.sqrt(setB / big);
        let d = solveDistance(rA, rB, both * unit);
        let widened = false;

        if (!both) {
            d = rA + rB + MIN_BAND * 0.8;                       // disjoint
        } else if (!aOnly && !bOnly) {
            d = 0;                                               // identical
        } else if (!bOnly) {                                     // sitemap ⊂ crawl
            if (rB > rA - MIN_BAND) { rB = Math.max(rA - MIN_BAND, R * 0.3); widened = true; }
            d = Math.min(rA - rB, MIN_BAND * 0.4);
        } else if (!aOnly) {                                     // crawl ⊂ sitemap
            if (rA > rB - MIN_BAND) { rA = Math.max(rB - MIN_BAND, R * 0.3); widened = true; }
            d = Math.min(rB - rA, MIN_BAND * 0.4);
        } else {
            const lo = Math.max(0, MIN_BAND - rA + rB, MIN_BAND + rA - rB);
            const hi = Math.max(lo, rA + rB - MIN_BAND);
            const fitted = Math.min(Math.max(d, lo), hi);
            if (Math.abs(fitted - d) > 0.5) widened = true;
            d = fitted;
        }
        return { rA, rB, d, widened };
    }

    // The circle as a path, so it can also serve as a clip subpath.
    const circlePath = (cx, cy, r) =>
        `M${f(cx - r)},${f(cy)}a${f(r)},${f(r)} 0 1,0 ${f(2 * r)},0a${f(r)},${f(r)} 0 1,0 ${f(-2 * r)},0`;

    // The intersection of the two circles: A's near arc, then B's near arc.
    function lensPath(rA, rB, d) {
        if (d <= Math.abs(rA - rB)) return circlePath(rA <= rB ? 0 : d, 0, Math.min(rA, rB));
        if (d >= rA + rB) return '';
        const x = (d * d + rA * rA - rB * rB) / (2 * d);
        const y = Math.sqrt(Math.max(0, rA * rA - x * x));
        const largeA = x > 0 ? 0 : 1;
        const largeB = (x - d) < 0 ? 0 : 1;
        return `M${f(x)},${f(-y)}A${f(rA)},${f(rA)} 0 ${largeA},1 ${f(x)},${f(y)}` +
               `A${f(rB)},${f(rB)} 0 ${largeB},1 ${f(x)},${f(-y)}Z`;
    }

    const chord = (r, cx, x) => 2 * Math.sqrt(Math.max(0, r * r - (x - cx) ** 2));

    function coverageSentence(both, aOnly, bOnly) {
        if (!both && !aOnly && !bOnly) return 'This crawl found no URLs at all.';
        if (!aOnly && !bOnly) {
            return `The two sets are identical — all ${count(both)} URLs were crawled and all ${count(both)} are in the sitemap.`;
        }
        if (!both) {
            return 'The two sets do not meet: not one crawled URL appears in the sitemap.';
        }
        if (!bOnly) {
            return `The sitemap sits inside the crawl: every one of its ${count(both)} URLs was crawled, and crawling found ${count(aOnly)} more that the sitemap does not list.`;
        }
        if (!aOnly) {
            return `The crawl sits inside the sitemap: all ${count(both)} crawled URLs are listed, and ${count(bOnly)} more are listed but were never reached.`;
        }
        return `${count(both)} URLs are in both, ${count(aOnly)} were crawled but are missing from the sitemap, and ${count(bOnly)} are listed but were never reached.`;
    }

    /**
     * The whole figure: <svg> + caption. `both`, `aOnly` and `bOnly` are the
     * three region counts; nothing else is needed to draw it.
     */
    function coverageVenn(crawlId, both, aOnly, bOnly) {
        const setA = both + aOnly;
        const setB = both + bOnly;
        if (!setA && !setB) {
            return `<figure class="venn"><figcaption class="venn-caption">
                <p class="venn-sentence">${escape(coverageSentence(both, aOnly, bOnly))}</p>
                </figcaption></figure>`;
        }

        const uid = `vn${Math.random().toString(36).slice(2, 8)}`;
        const { rA, rB, d, widened } = vennLayout(both, aOnly, bOnly);
        const hasA = setA > 0;
        const hasB = setB > 0;

        // Region extents along the x axis, from which both the label anchor
        // and the "is there room for a label in here" test are derived.
        const aRight = both ? Math.min(d - rB, rA) : rA;
        const bLeft = both ? Math.max(rA, d - rB) : d - rB;
        const spans = {
            crawled_only: hasA ? [-rA, aRight] : null,
            sitemap_only: hasB ? [bLeft, d + rB] : null,
            in_both: both ? [Math.max(-rA, d - rB), Math.min(rA, d + rB)] : null,
        };

        const regions = [
            { key: 'crawled_only', total: aOnly, tone: 'warn', name: 'Crawled only', side: 'left' },
            { key: 'in_both', total: both, tone: 'ok', name: 'In both', side: 'bottom' },
            { key: 'sitemap_only', total: bOnly, tone: 'info', name: 'Sitemap only', side: 'right' },
        ].map((region) => {
            const span = spans[region.key];
            const width = span ? Math.max(0, span[1] - span[0]) : 0;
            const anchorX = span ? (span[0] + span[1]) / 2 : 0;
            const height = !span ? 0
                : region.key === 'sitemap_only' ? chord(rB, d, anchorX)
                : region.key === 'crawled_only' ? chord(rA, 0, anchorX)
                : Math.min(chord(rA, 0, anchorX), chord(rB, d, anchorX));
            return {
                ...region,
                anchorX,
                width,
                height,
                inside: region.total > 0 && width >= 118 && height >= 52,
            };
        });

        const outside = regions.filter((r) => !r.inside);
        const xMin = Math.min(hasA ? -rA : d - rB, hasB ? d - rB : -rA);
        const xMax = Math.max(hasA ? rA : d + rB, hasB ? d + rB : rA);
        const yMax = Math.max(hasA ? rA : 0, hasB ? rB : 0);
        const padLeft = outside.some((r) => r.side === 'left') ? 140 : 16;
        const padRight = outside.some((r) => r.side === 'right') ? 140 : 16;
        const padBottom = outside.some((r) => r.side === 'bottom') ? 72 : 16;

        const viewBox = [f(xMin - padLeft), f(-yMax - 20),
            f((xMax - xMin) + padLeft + padRight), f(2 * yMax + 20 + padBottom)].join(' ');

        // Clip "everything except the other circle", so each exclusive region
        // is a real shape that takes hover, focus and clicks on its own area.
        const OUTER = 'M-4000,-4000H4000V4000H-4000Z';
        const defs = `<defs>
            <clipPath id="${uid}-nb"><path clip-rule="evenodd" d="${OUTER}${circlePath(d, 0, rB)}"/></clipPath>
            <clipPath id="${uid}-na"><path clip-rule="evenodd" d="${OUTER}${circlePath(0, 0, rA)}"/></clipPath>
        </defs>`;

        const shapes = {
            crawled_only: hasA
                ? `<circle class="venn-shape" cx="0" cy="0" r="${f(rA)}" clip-path="url(#${uid}-nb)"/>` : '',
            sitemap_only: hasB
                ? `<circle class="venn-shape" cx="${f(d)}" cy="0" r="${f(rB)}" clip-path="url(#${uid}-na)"/>` : '',
            in_both: both ? `<path class="venn-shape" d="${lensPath(rA, rB, d)}"/>` : '',
        };

        const body = regions.map((region) => {
            const label = vennLabel(region, { xMin, xMax, yMax });
            const inner = `${shapes[region.key] || ''}${label}`;
            if (!region.total) {
                return `<g class="venn-region venn-${region.tone} is-empty">${inner}</g>`;
            }
            const title = `${count(region.total)} ${region.name.toLowerCase()} — download as CSV`;
            return `<a class="venn-region venn-${region.tone}" href="${coverageHref(crawlId, region.key)}"
                download aria-label="Download the ${count(region.total)} URLs ${escape(region.name.toLowerCase())} as CSV"
                ><title>${escape(title)}</title>${inner}</a>`;
        }).join('');

        const outlines = `<g class="venn-outlines">
            ${hasA ? `<circle class="venn-outline venn-outline-a" cx="0" cy="0" r="${f(rA)}"/>` : ''}
            ${hasB ? `<circle class="venn-outline venn-outline-b" cx="${f(d)}" cy="0" r="${f(rB)}"/>` : ''}
        </g>`;

        const keys = `<div class="venn-key">
            ${vennKeyItem('warn', 'Crawled', setA)}
            ${vennKeyItem('info', 'In sitemap', setB)}
        </div>`;

        return `<figure class="venn">
            <div class="venn-figure">
                <svg class="venn-svg" viewBox="${viewBox}" preserveAspectRatio="xMidYMid meet"
                     aria-labelledby="${uid}-title">
                    <title id="${uid}-title">Sitemap coverage: ${count(both)} in both, ${count(aOnly)} crawled only, ${count(bOnly)} in the sitemap only</title>
                    ${defs}${body}${outlines}
                </svg>
            </div>
            <figcaption class="venn-caption">
                ${keys}
                <p class="venn-sentence">${escape(coverageSentence(both, aOnly, bOnly))}</p>
                <p class="venn-hint">${glyph('download', 13)} Click a region to download that list as CSV.${
                    widened ? ' Circle areas are proportional to the counts; the thinnest band is widened so it stays readable and clickable.' : ''
                }</p>
            </figcaption>
        </figure>`;
    }

    const vennKeyItem = (tone, label, total) => `<span class="venn-key-item venn-key-${tone}">
        <span class="venn-key-dot"></span>${escape(label)}
        <span class="venn-key-count mono">${count(total)}</span></span>`;

    // Inside the region when it is big enough to hold two lines, otherwise a
    // callout: a dot on the region, a leader, and the label clear of the art.
    function vennLabel(region, box) {
        const value = count(region.total);
        if (region.inside) {
            return `<g class="venn-label is-inside" transform="translate(${f(region.anchorX)},0)">
                <text class="venn-value" x="0" y="0" text-anchor="middle">${value}</text>
                <text class="venn-name" x="0" y="20" text-anchor="middle">${escape(region.name)}</text>
            </g>`;
        }

        let x = 0;
        let y = 0;
        let anchor = 'middle';
        let leader = '';
        if (region.side === 'left') {
            x = box.xMin - 16;
            anchor = 'end';
            leader = region.total ? `M${f(region.anchorX)},0L${f(x + 10)},-14` : '';
        } else if (region.side === 'right') {
            x = box.xMax + 16;
            anchor = 'start';
            leader = region.total ? `M${f(region.anchorX)},0L${f(x - 10)},-14` : '';
        } else {
            x = region.anchorX || 0;
            y = box.yMax + 34;
            leader = region.total ? `M${f(x)},0L${f(x)},${f(y - 22)}` : '';
        }

        const dot = region.total
            ? `<circle class="venn-dot" cx="${f(region.anchorX)}" cy="0" r="3"/>` : '';
        const line = leader ? `<path class="venn-leader" d="${leader}"/>` : '';
        return `<g class="venn-label${region.total ? '' : ' is-zero'}">
            ${line}${dot}
            <text class="venn-value" x="${f(x)}" y="${f(y - 8)}" text-anchor="${anchor}">${value}</text>
            <text class="venn-name" x="${f(x)}" y="${f(y + 8)}" text-anchor="${anchor}">${escape(region.name)}</text>
        </g>`;
    }

    // ---- Crawl comparison -------------------------------------------------

    async function loadCompareOptions() {
        const select = document.getElementById('compareAgainst');
        if (!select) return;

        const crawlId = currentCrawlId();
        const container = document.getElementById('compareReport');
        if (!crawlId) {
            notice(container, 'Open a crawl to compare it with an earlier one');
            select.innerHTML = '';
            return;
        }

        try {
            const data = await getJson('/api/crawls/list?limit=50');
            const others = (data.crawls || []).filter((c) => c.id !== crawlId && c.urls_crawled);
            if (!others.length) {
                notice(container, 'No other crawl to compare with yet');
                select.innerHTML = '';
                return;
            }
            select.innerHTML = others.map((c) =>
                `<option value="${c.id}">#${c.id} · ${escape(c.base_domain || c.base_url)} · ${escape((c.started_at || '').slice(0, 16))}</option>`
            ).join('');
            notice(container, 'Pick a crawl and press Compare');
        } catch (error) {
            notice(container, `Could not list crawls: ${error.message}`);
        }
    }

    async function runComparison() {
        const container = document.getElementById('compareReport');
        const select = document.getElementById('compareAgainst');
        const crawlId = currentCrawlId();
        const otherId = select?.value;
        if (!crawlId || !otherId) return;

        container.innerHTML = '<p class="report-note">Comparing…</p>';
        try {
            const data = await getJson(`/api/crawls/${crawlId}/diff/${otherId}`);
            const counts = data.counts || {};
            container.innerHTML = `
                <div class="stat-grid">
                    ${card('Fixed', counts.fixed ?? 0, 'ok')}
                    ${card('New', counts.new ?? 0, 'danger')}
                    ${card('Still open', counts.still_open ?? 0, 'muted')}
                </div>
                <div class="report-actions">
                    ${reportLink(crawlId, 'html', `Summary vs #${otherId} (HTML)`, otherId)}
                    ${reportLink(crawlId, 'md', `Summary vs #${otherId} (Markdown)`, otherId)}
                    ${exportLink(crawlId, 'issues', 'csv', 'Issues (CSV)')}
                </div>
                ${issueTable('New since crawl #' + otherId, data.new, 'danger')}
                ${issueTable('Fixed since crawl #' + otherId, data.fixed, 'ok')}
                ${issueTable('Still open', data.still_open, 'muted')}`;
            paint(container);
        } catch (error) {
            notice(container, `Could not compare: ${error.message}`);
        }
    }

    function issueTable(title, issues, tone) {
        if (!issues || !issues.length) return '';
        const rows = issues.slice(0, 500).map((issue) => {
            const severity = ['error', 'warning', 'info'].includes(issue.type) ? issue.type : 'info';
            return `
            <tr data-issue-type="${escape(issue.type)}">
                <td class="cell-url">${escape(issue.url)}</td>
                <td><span class="badge badge-${severity}">${escape(issue.type)}</span></td>
                <td>${escape(issue.issue || issue.check)}</td>
                <td class="text-secondary">${escape((issue.details || '').slice(0, 160))}</td>
            </tr>`;
        }).join('');
        const more = issues.length > 500 ? `<p class="report-note">…and ${issues.length - 500} more</p>` : '';
        return `
            <h3 class="report-heading tone-${tone}">${escape(title)} (${issues.length})</h3>
            <div class="table-container"><table class="data-table">
                <thead><tr>
                    <th class="col-40"><span class="th-inner">URL</span></th>
                    <th class="col-10"><span class="th-inner">Severity</span></th>
                    <th class="col-20"><span class="th-inner">Issue</span></th>
                    <th><span class="th-inner">Details</span></th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table></div>${more}`;
    }

    window.loadSitemapReport = loadSitemapReport;
    window.loadCompareOptions = loadCompareOptions;
    window.runComparison = runComparison;
})();

// ---- Rules editor and accepted issues -------------------------------------
//
// The rules file is plain text (a small YAML subset or JSON). It is checked
// before it is saved, and can be tried against the crawl that is open first,
// so a bad regex is caught before it is committed to a site's rules.

(function () {
    const escape = (value) => String(value ?? '').replace(/[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const EXAMPLE = `rules:
  - id: banned-claims
    severity: error
    forbid: ["500\\\\+ projects", "15\\\\+ years"]

  - id: cta-label
    severity: warning
    selector: "a.btn, button[type=submit]"
    must_equal: "Book a Discovery Call"

  - id: author
    severity: error
    expect_author: "Jane Doe"
`;

    // icons.js only sweeps the document once, on DOMContentLoaded
    function paint(container) {
        if (container && typeof renderIcons === 'function') renderIcons(container);
    }

    function glyph(name, size) {
        return typeof icon === 'function' ? icon(name, { size: size || 14 }) : '';
    }

    function domainOfCurrentCrawl() {
        try {
            return new URL(crawlState.baseUrl || location.href).hostname;
        } catch (error) {
            return '';
        }
    }

    // tone is one of muted / ok / warn / danger
    function status(message, tone = 'muted') {
        const el = document.getElementById('rulesStatus');
        if (el) el.innerHTML = `<span class="text-${tone}">${escape(message)}</span>`;
    }

    async function loadRules() {
        const editor = document.getElementById('rulesEditor');
        if (!editor) return;
        const domain = domainOfCurrentCrawl();
        document.getElementById('rulesDomain').textContent = domain || '(no crawl open)';
        try {
            const response = await fetch(`/api/rules?domain=${encodeURIComponent(domain)}`);
            const data = await response.json();
            editor.value = data.text || EXAMPLE;
            status(data.path ? `Loaded ${data.path}` : 'No rules file yet — this is an example to edit');
            if ((data.errors || []).length) {
                status(`Problems: ${data.errors.join('; ')}`, 'warn');
            }
        } catch (error) {
            status(`Could not load rules: ${error.message}`, 'danger');
        }
        loadAcceptedIssues();
    }

    async function testRules() {
        const crawlId = window.Router?.currentCrawlId();
        if (!crawlId) return status('Open a crawl first, then the rules run against it', 'warn');

        status('Running the rules against this crawl…');
        try {
            const response = await fetch('/api/rules/preview', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ crawl_id: crawlId, text: document.getElementById('rulesEditor').value }),
            });
            const data = await response.json();
            const output = document.getElementById('rulesPreview');
            if (!data.success) {
                output.innerHTML = `<p class="text-danger">${escape((data.errors || [data.error]).join('; '))}</p>`;
                return status('The rules file has problems — nothing was saved', 'danger');
            }
            output.innerHTML = (data.results || []).map((r) => `
                <div class="rules-result">
                    <strong>${escape(r.id)}</strong>
                    <span class="${r.total ? 'text-warn' : 'text-ok'}"> ${r.total} finding(s)</span>
                    <span class="text-muted"> · ${r.scanned_pages} of ${data.pages} pages in scope</span>
                    ${(r.notes || []).map((n) => `<div class="text-muted">${escape(n)}</div>`).join('')}
                    ${(r.issues || []).slice(0, 5).map((i) => `
                        <div class="rules-finding">
                            <span class="cell-url">${escape(i.url)}</span><br>${escape(i.details)}
                        </div>`).join('')}
                </div>`).join('') || '<p class="text-ok">No findings — every rule is satisfied.</p>';
            paint(output);
            status(`Tested against crawl #${crawlId}`, 'ok');
        } catch (error) {
            status(`Could not test the rules: ${error.message}`, 'danger');
        }
    }

    async function saveRules() {
        const domain = domainOfCurrentCrawl();
        try {
            const response = await fetch('/api/rules', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ domain, text: document.getElementById('rulesEditor').value }),
            });
            const data = await response.json();
            if (!data.success) {
                return status(`Not saved: ${(data.errors || [data.error]).join('; ')}`, 'danger');
            }
            status(`Saved ${data.rules} rule(s) to ${data.path}`, 'ok');
        } catch (error) {
            status(`Could not save: ${error.message}`, 'danger');
        }
    }

    // ---- Accepted (muted) issues ------------------------------------------
    //
    // /api/issues/accepted knows only the stable issue id, the reason, when it
    // was muted, by whom, and for which domain. Everything a reader actually
    // wants — which check, on which URL — lives in the crawl, so when a crawl
    // is open its issue list is pulled once and the two are joined on the id.
    // An id with no match is reported as such rather than left blank.

    const issueIndexCache = new Map();

    async function crawlIssueIndex(crawlId) {
        if (issueIndexCache.has(crawlId)) return issueIndexCache.get(crawlId);
        const index = new Map();
        try {
            const response = await fetch(`/api/crawls/${crawlId}/issues?include_accepted=true`);
            if (response.ok) {
                const data = await response.json();
                (data.issues || []).forEach((issue) => {
                    if (issue.id) index.set(issue.id, issue);
                });
            }
        } catch (error) {
            /* an unreachable crawl just means no join; the rows still render */
        }
        issueIndexCache.set(crawlId, index);
        return index;
    }

    const SEVERITY = { error: 'error', warning: 'warning', info: 'info' };

    function whoMuted(userId) {
        if (userId === null || userId === undefined || userId === '') return 'an anonymous session';
        const shown = (document.getElementById('userInfo')?.textContent || '').split(' (')[0].trim();
        return shown ? `${shown} (user #${userId})` : `user #${userId}`;
    }

    // "2026-09-22 22:49:17" -> "22 Sep 2026, 22:49 · 3 days ago"
    function whenMuted(value) {
        const text = String(value || '').trim();
        if (!text) return 'at an unrecorded time';
        const stamp = new Date(text.replace(' ', 'T') + (/[Z+]/.test(text) ? '' : 'Z'));
        if (Number.isNaN(stamp.getTime())) return text;
        const days = Math.floor((Date.now() - stamp.getTime()) / 86400000);
        const ago = days <= 0 ? 'today' : days === 1 ? 'yesterday' : `${days} days ago`;
        return `${stamp.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} · ${ago}`;
    }

    function emptyAccepted() {
        return `<div class="empty-state empty-state-wide"><div class="empty-state-content">
            <i data-icon="eye-off" data-icon-size="40"></i>
            <h3>Nothing is muted</h3>
            <p>Muting an issue hides that one finding — the same check on the same URL — from the
               issue list and from every export, on this account, for this crawl and every crawl
               after it. The check still runs; the finding just stops being noise.</p>
            <p>Use it for a limitation you have decided to live with, and always give a reason: it
               is the only record of why the next person is not seeing it.</p>
            <p class="report-note">Mute one from the Issues tab, or with
               <code>POST /api/issues/&lt;id&gt;/accept</code> and a <code>reason</code>.</p>
        </div></div>`;
    }

    async function loadAcceptedIssues() {
        const container = document.getElementById('acceptedIssues');
        if (!container) return;
        container.innerHTML = '<p class="report-note">Loading muted issues…</p>';
        try {
            const response = await fetch('/api/issues/accepted');
            const data = await response.json();
            const rows = data.accepted || [];
            if (!rows.length) {
                container.innerHTML = emptyAccepted();
                paint(container);
                return;
            }

            const crawlId = window.Router?.currentCrawlId() || null;
            const index = crawlId ? await crawlIssueIndex(crawlId) : new Map();
            const here = domainOfCurrentCrawl();

            const groups = new Map();
            rows.forEach((row) => {
                const key = row.base_domain || '';
                if (!groups.has(key)) groups.set(key, []);
                groups.get(key).push(row);
            });
            const order = [...groups.keys()].sort((a, b) => {
                if (a === here) return -1;
                if (b === here) return 1;
                if (!a) return 1;
                if (!b) return -1;
                return a.localeCompare(b);
            });

            const matched = rows.filter((row) => index.has(row.issue_key)).length;
            const summary = `<p class="muted-summary">
                <strong>${rows.length}</strong> muted ${rows.length === 1 ? 'issue' : 'issues'}
                across ${order.length} ${order.length === 1 ? 'scope' : 'scopes'}${
                    crawlId ? ` · <span class="text-muted">${matched} of them are findings in crawl #${crawlId}</span>` : ''
                }</p>`;

            container.innerHTML = summary + order.map((domain) => `
                <section class="muted-group">
                    <h4 class="muted-group-title">
                        ${glyph(domain ? 'globe' : 'database', 13)}
                        <span>${escape(domain || 'Every domain')}</span>
                        <span class="muted-group-count mono">${groups.get(domain).length}</span>
                        ${domain && domain === here ? '<span class="badge badge-info">this crawl</span>' : ''}
                    </h4>
                    ${groups.get(domain).map((row) => mutedRow(row, index, crawlId)).join('')}
                </section>`).join('');
            paint(container);
        } catch (error) {
            container.innerHTML = `<p class="text-danger">Could not load the muted issues: ${escape(error.message)}</p>`;
        }
    }

    function mutedRow(row, index, crawlId) {
        const id = String(row.issue_key || '');
        const issue = index.get(id);
        const severity = issue && SEVERITY[issue.type] ? issue.type : null;

        const what = issue
            ? `<h5 class="muted-title">${escape(issue.issue || issue.check || 'Muted finding')}</h5>
               ${issue.check ? `<span class="muted-check mono">${escape(issue.check)}</span>` : ''}`
            : `<h5 class="muted-title muted-title-unknown">Not a finding in the crawl that is open</h5>`;

        const where = issue
            ? `<a class="muted-url url-link mono" href="${escape(issue.url)}" target="_blank"
                  rel="noopener" title="${escape(issue.url)}">${escape(issue.url)}</a>`
            : `<p class="muted-unmatched">${escape(crawlId
                ? `No issue with this id in crawl #${crawlId} — it was muted from another crawl of this site, or the page it was found on has since been fixed or removed.`
                : 'Open a crawl to see which check and URL this id refers to.')}</p>`;

        return `<article class="muted-issue" data-issue-key="${escape(id)}">
            <div class="muted-issue-head">
                ${severity ? `<span class="badge badge-${escape(severity)}">${escape(severity)}</span>`
                    : '<span class="badge badge-neutral">muted</span>'}
                <div class="muted-issue-what">${what}</div>
            </div>
            ${where}
            <blockquote class="muted-reason">
                ${glyph('info', 13)}
                <span>${escape(row.reason || 'No reason was given.')}</span>
            </blockquote>
            <dl class="muted-meta">
                <div><dt>Muted</dt><dd>${escape(whenMuted(row.accepted_at))}</dd></div>
                <div><dt>By</dt><dd>${escape(whoMuted(row.user_id))}</dd></div>
                <div><dt>Scope</dt><dd>${escape(row.base_domain || 'every domain')}</dd></div>
                <div><dt>Id</dt><dd class="mono" title="${escape(id)}">${escape(id.slice(0, 12))}…</dd></div>
            </dl>
            <div class="muted-actions">
                <button type="button" class="btn btn-secondary btn-sm"
                        onclick="copyIssueId(this, '${escape(id)}')">
                    ${glyph('copy', 13)}<span class="btn-label">Copy id</span></button>
                <button type="button" class="btn btn-secondary btn-sm"
                        onclick="unacceptIssue('${escape(id)}')">
                    ${glyph('eye', 13)}<span class="btn-label">Un-mute</span></button>
            </div>
        </article>`;
    }

    async function copyIssueId(button, issueId) {
        let copied = true;
        try {
            await navigator.clipboard.writeText(issueId);
        } catch (error) {
            const shuttle = document.createElement('textarea');
            shuttle.value = issueId;
            shuttle.setAttribute('readonly', '');
            shuttle.className = 'copy-shuttle';
            document.body.appendChild(shuttle);
            shuttle.select();
            try { copied = document.execCommand('copy'); } catch (e) { copied = false; }
            shuttle.remove();
        }
        const label = button?.querySelector('.btn-label');
        if (!label) return;
        const before = label.textContent;
        label.textContent = copied ? 'Copied' : 'Press ⌘C';
        button.classList.add('is-copied');
        setTimeout(() => {
            label.textContent = before;
            button.classList.remove('is-copied');
        }, 1600);
    }

    async function unacceptIssue(issueId) {
        const row = document.querySelector(`.muted-issue[data-issue-key="${CSS.escape(String(issueId))}"]`);
        if (row) row.setAttribute('aria-busy', 'true');
        try {
            await fetch(`/api/issues/${issueId}/accept`, { method: 'DELETE' });
        } finally {
            issueIndexCache.clear();
            loadAcceptedIssues();
        }
    }

    window.loadRules = loadRules;
    window.testRules = testRules;
    window.saveRules = saveRules;
    window.loadAcceptedIssues = loadAcceptedIssues;
    window.unacceptIssue = unacceptIssue;
    window.copyIssueId = copyIssueId;
})();
