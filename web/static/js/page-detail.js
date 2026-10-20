// Page detail: everything known about one URL, on one screen.
//
// The old popup could only show what the browser happened to hold in memory.
// This asks the API instead (`/api/crawls/<id>/page`), so it can also show the
// issues raised against that page and what changed since the previous crawl —
// which is the question someone actually has when they open a row.
//
// It keeps the `.details-modal-overlay` element and its `data-url`, because
// router.js reads both to put the open popup in the address bar.

(function () {
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
        (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

    const ico = (name, size = 14) =>
        (typeof window.icon === 'function' ? window.icon(name, { size }) : '');

    function crawlId() {
        return window.Router?.currentCrawlId?.() || null;
    }

    /** Open the detail view for `url`. Returns false when it cannot handle it. */
    function open(url) {
        const id = crawlId();
        if (!id) return false;

        render(url, null, { loading: true });
        fetch(`/api/crawls/${id}/page?url=${encodeURIComponent(url)}`)
            .then((response) => response.ok ? response.json() : Promise.reject(
                new Error(`${response.status} ${response.statusText}`)))
            .then((data) => {
                if (!data.success) throw new Error(data.error || 'not found');
                render(url, data, {});
            })
            .catch((error) => render(url, null, { error: error.message }));
        return true;
    }

    function render(url, data, state) {
        document.querySelector('.details-modal-overlay')?.remove();

        const overlay = document.createElement('div');
        overlay.className = 'details-modal-overlay';
        overlay.dataset.url = url;              // router.js reads this
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) window.closeUrlDetails();
        });

        overlay.innerHTML = `
            <div class="details-modal" role="dialog" aria-modal="true" aria-label="Page detail">
                <div class="details-header">
                    <div class="details-title">
                        <span class="details-eyebrow">PAGE</span>
                        <a href="${esc(url)}" target="_blank" rel="noopener" class="url-link">${esc(url)}</a>
                    </div>
                    <button class="close-btn" onclick="closeUrlDetails()" aria-label="Close">
                        ${ico('x', 14)}
                    </button>
                </div>
                <div class="details-content">${body(data, state)}</div>
            </div>`;

        document.body.appendChild(overlay);
        if (typeof window.renderIcons === 'function') window.renderIcons(overlay);
    }

    function body(data, state) {
        if (state.loading) return '<p class="text-muted">Loading…</p>';
        if (state.error) {
            return `<p class="text-danger">Could not load this page: ${esc(state.error)}</p>`;
        }
        const page = data.page || {};
        return [
            diffSection(data.diff),
            issuesSection(data.issues),
            metaSection(page),
            headingsSection(page),
            linksSection(data.links),
            imagesSection(page),
            schemaSection(page),
        ].filter(Boolean).join('');
    }

    const section = (title, iconName, inner, extra = '') => `
        <section class="details-section">
            <h4>${ico(iconName, 16)}${esc(title)}${extra}</h4>
            ${inner}
        </section>`;

    const rows = (pairs) => `
        <table class="details-table"><tbody>${pairs.map(([label, value]) => `
            <tr><th>${esc(label)}</th><td>${value}</td></tr>`).join('')}</tbody></table>`;

    // ---- what changed since the previous crawl -----------------------------

    function diffSection(diff) {
        if (!diff || !diff.baseline_crawl) return '';
        const title = `Since crawl #${diff.baseline_crawl}`;

        if (diff.introduced) {
            return section(title, 'git-compare',
                '<p class="text-ok">This page is new: it was not in that crawl.</p>');
        }

        const changes = [];
        const named = {
            title_changed: 'Title', meta_description_changed: 'Meta description',
            canonical_changed: 'Canonical', status_changed: 'Status',
        };
        for (const [key, label] of Object.entries(named)) {
            const change = diff[key];
            if (change) {
                changes.push([label, `
                    <span class="diff-from">${esc(change.from ?? '—')}</span>
                    <span class="diff-arrow">${ico('arrow-right', 12)}</span>
                    <span class="diff-to">${esc(change.to ?? '—')}</span>`]);
            }
        }
        if (typeof diff.word_count_delta === 'number' && diff.word_count_delta !== 0) {
            const sign = diff.word_count_delta > 0 ? '+' : '';
            changes.push(['Words', `<span class="${diff.word_count_delta > 0 ? 'text-ok' : 'text-warn'}">
                ${sign}${diff.word_count_delta}</span>`]);
        }

        const lists = [
            ['Text removed', diff.removed_sentences, 'text-danger'],
            ['Text added', diff.added_sentences, 'text-ok'],
            ['Links removed', diff.links_removed, 'text-danger'],
            ['Links added', diff.links_added, 'text-ok'],
            ['Issues new', (diff.issues_new || []).map(labelOf), 'text-danger'],
            ['Issues fixed', (diff.issues_fixed || []).map(labelOf), 'text-ok'],
        ].filter(([, items]) => items && items.length);

        if (!changes.length && !lists.length) {
            return section(title, 'git-compare',
                '<p class="text-muted">Nothing changed on this page.</p>');
        }

        return section(title, 'git-compare', `
            ${changes.length ? rows(changes) : ''}
            ${lists.map(([label, items, tone]) => `
                <div class="details-subsection">
                    <h5>${esc(label)} <span class="count">${items.length}</span></h5>
                    <ul class="details-list ${tone}">
                        ${items.slice(0, 8).map((item) => `<li>${esc(textOf(item))}</li>`).join('')}
                    </ul>
                    ${items.length > 8 ? `<p class="text-muted">…and ${items.length - 8} more</p>` : ''}
                </div>`).join('')}`);
    }

    const labelOf = (issue) => issue?.issue || issue?.check || '';
    const textOf = (item) => typeof item === 'string' ? item
        : (item?.target_url || item?.url || item?.text || JSON.stringify(item));

    // ---- issues -----------------------------------------------------------

    function issuesSection(issues) {
        if (!issues || !issues.length) {
            return section('Issues', 'check-circle',
                '<p class="text-ok">No issues on this page.</p>');
        }
        const order = { error: 0, warning: 1, info: 2 };
        const sorted = [...issues].sort((a, b) => (order[a.type] ?? 3) - (order[b.type] ?? 3));
        return section('Issues', 'alert-triangle', `
            <ul class="details-issues">
                ${sorted.map((issue) => `
                    <li>
                        <span class="badge badge-${esc(issue.type)}">${esc(issue.type)}</span>
                        <strong>${esc(issue.issue || issue.check)}</strong>
                        <div class="text-muted">${esc(issue.details)}</div>
                        ${issue.how_to_fix ? `<div class="details-fix">${ico('zap', 12)}${esc(issue.how_to_fix)}</div>` : ''}
                        ${evidenceOf(issue.evidence)}
                    </li>`).join('')}
            </ul>`, ` <span class="count">${issues.length}</span>`);
    }

    function evidenceOf(evidence) {
        if (!evidence || !Object.keys(evidence).length) return '';
        const parts = [];
        if (evidence.snippet) parts.push(`<code class="details-snippet">${esc(evidence.snippet)}</code>`);
        if (evidence.selector) parts.push(`<code>${esc(evidence.selector)}</code>`);
        if (evidence.expected || evidence.found) {
            parts.push(`<span class="text-muted">expected ${esc(evidence.expected ?? '—')}, found ${esc(evidence.found ?? '—')}</span>`);
        }
        if (evidence.related_urls?.length) {
            parts.push(`<span class="text-muted">${evidence.related_urls.length} related URL(s)</span>`);
        }
        return parts.length ? `<div class="details-evidence">${parts.join(' ')}</div>` : '';
    }

    // ---- the page itself ---------------------------------------------------

    function metaSection(page) {
        const http = page.http || {};
        const ms = (value) => typeof value === 'number' ? `${Math.round(value)} ms` : '—';
        return section('Meta and response', 'file-text', rows([
            ['Title', esc(page.title) || '<span class="text-muted">none</span>'],
            ['Meta description', esc(page.meta_description) || '<span class="text-muted">none</span>'],
            ['H1', esc(page.h1) || '<span class="text-muted">none</span>'],
            ['Canonical', esc(page.canonical_url) || '<span class="text-muted">none</span>'],
            ['Robots', esc(page.robots) || '—'],
            ['Language', esc(page.lang) || '—'],
            ['Words', `${page.main_word_count ?? page.word_count ?? '—'}`],
            ['Status', `${http.status_code ?? '—'} · ${esc(http.content_type) || '—'}`],
            ['Response', `${ms(http.response_time)}${http.ttfb ? ` (TTFB ${ms(http.ttfb)})` : ''}`],
            ['Cache-Control', esc(http.cache_control) || '<span class="text-muted">none</span>'],
            ['Depth', `${page.depth ?? '—'}`],
        ]));
    }

    function headingsSection(page) {
        const tree = page.headings;
        if (!tree || !tree.length) return '';
        const defects = (page.heading_text_issues || []).reduce((map, defect) => {
            map[defect.dom_order] = defect.problem;
            return map;
        }, {});
        const branch = (nodes) => `
            <ul class="headings-tree">
                ${nodes.map((node) => `
                    <li class="heading-h${node.level}${node.is_visible === false ? ' is-hidden' : ''}">
                        <span class="heading-level">H${node.level}</span>
                        <span>${esc(node.text) || '<em class="text-muted">empty</em>'}</span>
                        ${defects[node.dom_order] ? `<span class="badge badge-info">${esc(defects[node.dom_order].replace(/_/g, ' '))}</span>` : ''}
                        ${node.is_visible === false ? '<span class="badge badge-muted">hidden</span>' : ''}
                        ${node.children?.length ? branch(node.children) : ''}
                    </li>`).join('')}
            </ul>`;
        return section('Headings', 'list-checks', branch(tree));
    }

    function linksSection(links) {
        if (!links) return '';
        const counts = links.counts || {};
        const list = (items, emptyText) => items && items.length ? `
            <ul class="details-link-list">
                ${items.slice(0, 15).map((link) => `
                    <li>
                        <a href="${esc(link.target_url || link.source_url)}" target="_blank"
                           rel="noopener" class="url-link">${esc(link.target_url || link.source_url)}</a>
                        ${link.anchor_text ? `<span class="text-muted">“${esc(link.anchor_text)}”</span>` : ''}
                        ${link.nofollow ? '<span class="badge badge-muted">nofollow</span>' : ''}
                        ${link.in_nav ? '<span class="badge badge-muted">nav</span>' : ''}
                    </li>`).join('')}
            </ul>` : `<p class="text-muted">${esc(emptyText)}</p>`;

        return section('Links', 'link', `
            <div class="details-subsection">
                <h5>Inbound <span class="count">${counts.inbound ?? (links.inbound || []).length}</span></h5>
                ${list(links.inbound, 'Nothing links to this page.')}
            </div>
            <div class="details-subsection">
                <h5>Outbound <span class="count">${counts.outbound ?? (links.outbound || []).length}</span>
                    <span class="text-muted">${counts.outbound_internal ?? '?'} internal ·
                    ${counts.outbound_external ?? '?'} external</span></h5>
                ${list(links.outbound, 'This page links nowhere.')}
            </div>`);
    }

    function imagesSection(page) {
        const images = page.images || [];
        if (!images.length) return '';
        const kb = (bytes) => bytes ? `${Math.round(bytes / 1024)} KB` : '';
        return section('Images', 'image', `
            <ul class="details-list">
                ${images.slice(0, 20).map((image) => `
                    <li>
                        <code>${esc((image.src || '').split('/').pop())}</code>
                        ${image.has_alt === false || !image.alt ? '<span class="badge badge-warn">no alt</span>' : ''}
                        ${image.loading === 'lazy' ? '<span class="badge badge-muted">lazy</span>' : ''}
                        ${image.bytes ? `<span class="text-muted">${kb(image.bytes)}</span>` : ''}
                    </li>`).join('')}
            </ul>
            ${images.length > 20 ? `<p class="text-muted">…and ${images.length - 20} more</p>` : ''}`,
            ` <span class="count">${images.length}</span>`);
    }

    function schemaSection(page) {
        const blocks = page.json_ld || [];
        const errors = page.json_ld_errors || [];
        if (!blocks.length && !errors.length) return '';
        const typesOf = (block) => {
            const type = block?.['@type'];
            return Array.isArray(type) ? type.join(', ') : (type || 'unknown');
        };
        return section('Structured data', 'code', `
            ${errors.length ? `<p class="text-danger">${errors.length} block(s) do not parse.</p>` : ''}
            <ul class="details-list">
                ${blocks.map((block) => `<li><code>${esc(typesOf(block))}</code></li>`).join('')}
            </ul>`, ` <span class="count">${blocks.length}</span>`);
    }

    window.PageDetail = { open };
})();
