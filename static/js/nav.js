// Shell navigation
//
// Everything the new shell needs that is not a crawl concern: the mega menu
// and its crawl switcher, the tab strip's ARIA, the filter rail's keyboard
// path and drawer, the sub bar (view title, row count, filter chips) and the
// theme / density toggles.
//
// It never edits the app's own functions: it wraps them the way router.js
// does, so the inline onclick attributes the router matches on stay exactly
// as they are in the markup.

(function () {
    'use strict';

    const TAB_LABELS = {
        'overview': 'Overview',
        'internal': 'Internal',
        'external': 'External',
        'status-codes': 'Status Codes',
        'links': 'Links',
        'issues': 'Issues',
        'pagespeed': 'PageSpeed',
        'visualization': 'Visualization',
        'sitemap': 'Sitemap',
        'compare': 'Compare',
        'rules': 'Rules'
    };

    const $ = (sel, root) => (root || document).querySelector(sel);
    const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

    function safely(fn) {
        try { return fn(); } catch (error) { console.error('nav:', error); }
    }

    function num(value) {
        return (value == null || isNaN(value)) ? '—' : Number(value).toLocaleString();
    }

    // Wrap a global the app defines, running `callback` after it. Same shape as
    // router.js's `after`, including promise-returning functions.
    function after(name, callback) {
        const original = window[name];
        if (typeof original !== 'function') return;
        window[name] = function (...args) {
            const result = original.apply(this, args);
            if (result && typeof result.then === 'function') {
                return result.then(value => { safely(() => callback(args)); return value; });
            }
            safely(() => callback(args));
            return result;
        };
    }

    // ---- state helpers ---------------------------------------------------

    function state() {
        return (typeof crawlState !== 'undefined') ? crawlState : null;
    }

    function scrollers() {
        return (typeof virtualScrollers !== 'undefined') ? virtualScrollers : null;
    }

    function activeTabName() {
        const btn = $('.tab-btn.active');
        if (!btn) return 'overview';
        const match = btn.getAttribute('onclick')?.match(/switchTab\('([^']+)'\)/);
        return match ? match[1] : (btn.dataset.pluginId || 'overview');
    }

    function tabLabel(name) {
        if (TAB_LABELS[name]) return TAB_LABELS[name];
        const btn = $(`.tab-btn[data-plugin-id="${CSS.escape(name)}"]`);
        return btn ? btn.textContent.trim() : name;
    }

    // Rows on screen for the active view, so the sub bar can say "98 rows".
    function rowCountFor(tab) {
        const s = scrollers();
        const cs = state();
        if (!s || !cs) return null;
        switch (tab) {
            case 'overview': return s.overview ? s.overview.data.length : cs.urls.length;
            case 'internal': return s.internal ? s.internal.data.length : null;
            case 'external': return s.external ? s.external.data.length : null;
            case 'issues': return s.issues ? s.issues.data.length : cs.issues.length;
            case 'links': {
                const a = s.internalLinks ? s.internalLinks.data.length : 0;
                const b = s.externalLinks ? s.externalLinks.data.length : 0;
                return a + b;
            }
            case 'status-codes': {
                const body = document.getElementById('statusCodesTableBody');
                return body ? body.children.length : null;
            }
            default: return null;
        }
    }

    function countFor(tab) {
        const cs = state();
        if (!cs) return null;
        switch (tab) {
            case 'overview': return cs.urls.length;
            case 'internal': return Number(document.getElementById('internal-count')?.textContent) || 0;
            case 'external': return Number(document.getElementById('external-count')?.textContent) || 0;
            case 'status-codes': {
                const body = document.getElementById('statusCodesTableBody');
                return body ? body.children.length : null;
            }
            case 'links': return cs.links.length;
            case 'issues': return (window.currentIssues || cs.issues || []).length;
            default: return null;
        }
    }

    // Theme and density live in app.js (setupPreferenceControls): it owns
    // #themeToggle, #densityComfortable and #densityCompact, the localStorage
    // keys, the forced-comfortable rule below 768px and the scroller nudge.
    // nav.js deliberately does not touch them — two listeners on one button
    // would toggle twice.

    // Active-filter chips are app.js's: it renders one chip per facet into
    // #filterChips (the rail is multi-select), wires #clearFiltersBtn and keeps
    // #railToggleCount. nav.js only ships the markup they land in.

    // ---- sub bar + status bar -------------------------------------------

    function refreshSubbar() {
        const tab = activeTabName();

        const title = document.getElementById('subbarTitle');
        if (title) title.textContent = tabLabel(tab);

        const rows = document.getElementById('subbarRowCount');
        if (rows) {
            const count = rowCountFor(tab);
            rows.textContent = count == null ? '' : `${count.toLocaleString()} ${count === 1 ? 'row' : 'rows'}`;
        }

        const cs = state();
        const summary = document.getElementById('statusSummary');
        if (summary && cs) {
            const id = window.Router ? window.Router.currentCrawlId() : null;
            const parts = [];
            if (id) parts.push(`Crawl #${id}`);
            if (cs.urls.length) parts.push(`${cs.urls.length.toLocaleString()} URLs`);
            if (cs.links.length) parts.push(`${cs.links.length.toLocaleString()} links`);
            const issues = (window.currentIssues || cs.issues || []).length;
            if (issues) parts.push(`${issues.toLocaleString()} issues`);
            summary.textContent = parts.join(' · ');
        }

        const label = document.getElementById('megaTriggerLabel');
        if (label && label.textContent !== tabLabel(tab)) label.textContent = tabLabel(tab);

        refreshRail();
        refreshCurrentCrawl();
    }

    // ---- tab strip -------------------------------------------------------

    function refreshTabStrip() {
        const buttons = $$('.tab-header .tab-btn');
        buttons.forEach(btn => {
            const name = btn.getAttribute('onclick')?.match(/switchTab\('([^']+)'\)/)?.[1] || btn.dataset.pluginId;
            btn.setAttribute('role', 'tab');
            if (name) btn.setAttribute('aria-controls', `${name}-tab`);
            const active = btn.classList.contains('active');
            btn.setAttribute('aria-selected', String(active));
            btn.tabIndex = active ? 0 : -1;
        });
        if (buttons.length && !buttons.some(b => b.tabIndex === 0)) buttons[0].tabIndex = 0;

        $$('.tab-pane').forEach(pane => {
            pane.setAttribute('role', 'tabpanel');
            if (!pane.hasAttribute('tabindex')) pane.tabIndex = 0;
        });
    }

    function wireTabStrip() {
        const strip = $('.tab-header');
        if (!strip) return;

        strip.addEventListener('keydown', event => {
            const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
            if (!keys.includes(event.key)) return;
            const buttons = $$('.tab-header .tab-btn');
            const index = buttons.indexOf(document.activeElement);
            if (index === -1) return;
            event.preventDefault();
            let next = index;
            if (event.key === 'ArrowLeft') next = (index - 1 + buttons.length) % buttons.length;
            if (event.key === 'ArrowRight') next = (index + 1) % buttons.length;
            if (event.key === 'Home') next = 0;
            if (event.key === 'End') next = buttons.length - 1;
            buttons[next].focus();
            buttons[next].click();
        });

        // Plugin tabs are appended by plugin-loader.js long after load.
        new MutationObserver(() => { safely(refreshTabStrip); safely(refreshMegaPlugins); })
            .observe(strip, { childList: true });
    }

    // ---- filter rail -----------------------------------------------------

    // Only the zero-count state. Selection is app.js's: the rail is
    // multi-select, so `.active` and `aria-pressed` follow
    // crawlState.filters.activeList, which syncRailFilterUI() owns.
    function refreshRail() {
        $$('.sidebar .filter-item').forEach(item => {
            const count = Number(item.querySelector('.filter-count')?.textContent.replace(/[^\d]/g, '')) || 0;
            item.classList.toggle('is-empty', count === 0);
        });
    }

    function wireRail() {
        const rail = document.getElementById('filterRail');
        if (rail) {
            rail.addEventListener('keydown', event => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                const item = event.target.closest('.filter-item');
                if (!item || !rail.contains(item)) return;
                event.preventDefault();
                item.click();
            });
        }

        const toggle = document.getElementById('railToggle');
        const close = document.getElementById('railClose');
        const scrim = document.getElementById('railScrim');

        function setDrawer(open) {
            if (!rail) return;
            rail.classList.toggle('is-open', open);
            if (scrim) scrim.hidden = !open;
            if (toggle) toggle.setAttribute('aria-expanded', String(open));
            if (open) {
                rail.setAttribute('aria-modal', 'true');
                // Zero-count facets are pointer-events:none, so start on the
                // close button and fall back to the first live facet.
                (close || $('.filter-item:not(.is-empty)', rail) || $('.filter-item', rail))?.focus();
            } else {
                rail.removeAttribute('aria-modal');
            }
        }

        toggle?.addEventListener('click', () => setDrawer(!rail.classList.contains('is-open')));
        close?.addEventListener('click', () => { setDrawer(false); toggle?.focus(); });
        scrim?.addEventListener('click', () => setDrawer(false));
        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && rail && rail.classList.contains('is-open')) {
                setDrawer(false);
                toggle?.focus();
            }
        });
    }

    // ---- mega menu -------------------------------------------------------

    const mega = {
        panel: null,
        triggers: [],
        opener: null,
        crawls: [],
        crawlsFetchedAt: 0
    };

    function megaItems() {
        return $$('.mm-item', mega.panel).filter(el => !el.hasAttribute('disabled'));
    }

    function megaColumns() {
        return $$('.mm-col', mega.panel).map(col => $$('.mm-item', col).filter(el => !el.hasAttribute('disabled')));
    }

    function isMegaOpen() {
        return !!mega.panel && mega.panel.classList.contains('active');
    }

    // Crawl status names the API uses -> the states styles.css draws.
    function dotState(status) {
        if (status === 'completed' || status === 'complete') return 'complete';
        if (status === 'running' || status === 'paused') return 'running';
        if (status === 'failed' || status === 'error') return 'failed';
        return 'idle';
    }

    function openMega(opener) {
        if (!mega.panel || isMegaOpen()) return;
        mega.opener = opener || mega.triggers[0];
        // Two mechanisms on purpose: [hidden] keeps it out of the a11y tree
        // and hides it if the stylesheet is missing; .active is what
        // styles.css transitions (it also sets visibility, so focus only
        // lands inside the panel once the class is on).
        mega.panel.hidden = false;
        mega.panel.classList.add('active');
        mega.triggers.forEach(t => t.setAttribute('aria-expanded', 'true'));
        refreshMega();
        loadCrawls();
        const items = megaItems();
        const first = items.find(el => el.getAttribute('aria-current') === 'page') || items[0];
        requestAnimationFrame(() => first?.focus());
    }

    function closeMega(restoreFocus) {
        if (!mega.panel || !isMegaOpen()) return;
        mega.panel.classList.remove('active');
        mega.panel.hidden = true;
        mega.triggers.forEach(t => t.setAttribute('aria-expanded', 'false'));
        if (restoreFocus) (mega.opener || mega.triggers[0])?.focus();
        mega.opener = null;
    }

    function toggleMega(opener) {
        if (isMegaOpen()) closeMega(true); else openMega(opener);
    }

    function megaKeydown(event) {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeMega(true);
            return;
        }
        const navKeys = ['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'];
        if (!navKeys.includes(event.key)) return;

        const columns = megaColumns();
        let colIndex = -1;
        let rowIndex = -1;
        columns.forEach((items, c) => {
            const r = items.indexOf(document.activeElement);
            if (r !== -1) { colIndex = c; rowIndex = r; }
        });
        if (colIndex === -1) return;
        event.preventDefault();

        const column = columns[colIndex];
        let target = null;
        switch (event.key) {
            case 'ArrowDown': target = column[(rowIndex + 1) % column.length]; break;
            case 'ArrowUp': target = column[(rowIndex - 1 + column.length) % column.length]; break;
            case 'Home': target = column[0]; break;
            case 'End': target = column[column.length - 1]; break;
            case 'ArrowRight':
            case 'ArrowLeft': {
                const step = event.key === 'ArrowRight' ? 1 : -1;
                const total = columns.length;
                for (let i = 1; i <= total; i++) {
                    const next = columns[((colIndex + step * i) % total + total) % total];
                    if (next && next.length) {
                        target = next[Math.min(rowIndex, next.length - 1)];
                        break;
                    }
                }
                break;
            }
        }
        target?.focus();
    }

    function refreshMega() {
        if (!mega.panel) return;
        const active = activeTabName();

        const label = document.getElementById('megaTriggerLabel');
        if (label) label.textContent = tabLabel(active);

        $$('.mm-item[data-tab]', mega.panel).forEach(item => {
            const tab = item.dataset.tab;
            const isActive = tab === active;
            item.classList.toggle('active', isActive);
            if (isActive) item.setAttribute('aria-current', 'page');
            else item.removeAttribute('aria-current');

            const countEl = item.querySelector('.mm-item-count');
            const name = item.querySelector('.mm-item-label')?.textContent || tab;
            if (countEl) {
                const count = countFor(tab);
                countEl.textContent = count == null ? '—' : num(count);
                item.setAttribute('aria-label', count == null ? name : `${name}, ${num(count)}`);
            }
        });

        refreshCurrentCrawl();
    }

    // Mirror the plugin tabs the loader appended into the menu's PLUGINS group.
    function refreshMegaPlugins() {
        const host = document.getElementById('mmPlugins');
        if (!host) return;
        const plugins = $$('.tab-header .tab-btn[data-plugin-id]');
        const signature = plugins.map(p => p.dataset.pluginId).join('|');
        if (host.dataset.signature === signature) return;
        host.dataset.signature = signature;
        host.textContent = '';

        if (!plugins.length) {
            const empty = document.createElement('p');
            empty.className = 'mm-empty';
            empty.textContent = 'No plugins installed';
            host.appendChild(empty);
            return;
        }
        plugins.forEach(tabBtn => {
            const item = document.createElement('button');
            item.type = 'button';
            item.className = 'mm-item';
            item.dataset.tab = tabBtn.dataset.pluginId;
            const label = document.createElement('span');
            label.className = 'mm-item-label';
            label.textContent = tabBtn.textContent.trim();
            const count = document.createElement('span');
            count.className = 'mm-item-count';
            count.textContent = '—';
            item.append(label, count);
            host.appendChild(item);
        });
        refreshMega();
    }

    function crawlStatusOf(record) {
        const cs = state();
        if (cs && cs.isRunning && record && record.id === (window.Router && window.Router.currentCrawlId())) {
            return cs.isPaused ? 'paused' : 'running';
        }
        return (record && record.status) || 'idle';
    }

    function refreshCurrentCrawl() {
        const host = document.getElementById('mmCurrentCrawl');
        const chipLabel = document.getElementById('crawlChipLabel');
        const chipDot = document.getElementById('crawlChipDot');
        const cs = state();
        const id = window.Router ? window.Router.currentCrawlId() : null;

        if (!id) {
            if (chipLabel) chipLabel.textContent = 'No crawl';
            if (chipDot) chipDot.dataset.state = 'idle';
            if (host && host.dataset.signature !== 'none') {
                host.innerHTML = '<p class="mm-empty">No crawl open</p>';
                host.dataset.signature = 'none';
            }
            return;
        }

        const record = mega.crawls.find(c => c.id === id) || null;
        const status = crawlStatusOf(record || { id });
        const dot9 = dotState(status);
        const domain = (record && (record.base_domain || record.base_url)) ||
            (cs && cs.baseUrl) || '';
        const host9 = String(domain).replace(/^https?:\/\//, '').replace(/\/$/, '');

        if (chipLabel) chipLabel.textContent = `#${id}${host9 ? ' ' + host9 : ''}`;
        if (chipDot) chipDot.dataset.state = dot9;

        if (!host) return;
        const crawled = cs ? cs.stats.crawled : 0;
        const discovered = cs ? cs.stats.discovered : 0;
        const speed = cs ? cs.stats.speed : 0;
        const time = document.getElementById('crawlTime')?.textContent || '00:00';

        const signature = [id, status, host9, crawled, discovered, speed, time].join('|');
        if (host.dataset.signature === signature) return;
        host.dataset.signature = signature;

        host.innerHTML = '';
        const line1 = document.createElement('p');
        line1.className = 'mm-crawl-head';
        const dot = document.createElement('span');
        dot.className = 'crawl-dot';
        dot.dataset.state = dot9;
        dot.setAttribute('aria-hidden', 'true');
        const name = document.createElement('strong');
        name.textContent = `#${id}${host9 ? ' ' + host9 : ''}`;
        line1.append(dot, name, document.createTextNode(` · ${status}`));

        const line2 = document.createElement('p');
        line2.className = 'mm-crawl-meta';
        const progress = discovered > crawled ? `${num(crawled)}/${num(discovered)}` : `${num(crawled)} URLs`;
        line2.textContent = `${progress} · ${time} · ${speed} URL/s`;

        host.append(line1, line2);
    }

    async function loadCrawls(force) {
        const host = document.getElementById('mmRecentCrawls');
        if (!host) return;
        if (!force && Date.now() - mega.crawlsFetchedAt < 15000 && mega.crawls.length) {
            renderCrawls();
            return;
        }
        try {
            const response = await fetch('/api/crawls/list?limit=6');
            const data = await response.json();
            mega.crawls = (data && data.success && Array.isArray(data.crawls)) ? data.crawls : [];
            mega.crawlsFetchedAt = Date.now();
        } catch (error) {
            console.error('nav: could not read the crawl list', error);
            mega.crawls = [];
        }
        renderCrawls();
        refreshCurrentCrawl();
    }

    function ago(iso) {
        const then = new Date(iso);
        if (isNaN(then)) return '';
        const seconds = Math.max(0, (Date.now() - then.getTime()) / 1000);
        if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
        if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
        return `${Math.round(seconds / 86400)}d`;
    }

    function renderCrawls() {
        const host = document.getElementById('mmRecentCrawls');
        if (!host) return;
        const openId = window.Router ? window.Router.currentCrawlId() : null;
        const rows = mega.crawls.filter(c => c.id !== openId).slice(0, 5);

        // Rebuilding drops focus, and this runs every time the menu opens.
        const signature = rows.map(c => `${c.id}:${c.status}:${c.urls_crawled}`).join('|') || 'empty';
        if (host.dataset.signature === signature) return;
        host.dataset.signature = signature;

        host.textContent = '';
        if (!rows.length) {
            const empty = document.createElement('p');
            empty.className = 'mm-empty';
            empty.textContent = mega.crawls.length ? 'No other crawls' : 'No saved crawls';
            host.appendChild(empty);
            return;
        }

        rows.forEach(crawl => {
            const link = document.createElement('a');
            link.className = 'mm-item mm-crawl';
            link.href = `/crawl/${crawl.id}`;
            const domain = String(crawl.base_domain || crawl.base_url || '')
                .replace(/^https?:\/\//, '').replace(/\/$/, '');
            const head = document.createElement('span');
            head.className = 'mm-crawl-head';
            const dot = document.createElement('span');
            dot.className = 'crawl-dot';
            dot.dataset.state = dotState(crawl.status);
            dot.setAttribute('aria-hidden', 'true');
            const label = document.createElement('span');
            label.className = 'mm-item-label';
            label.textContent = `#${crawl.id} ${domain}`;
            head.append(dot, label);
            const meta = document.createElement('span');
            meta.className = 'mm-crawl-meta';
            meta.textContent = `${ago(crawl.started_at)} · ${num(crawl.urls_crawled || 0)} URLs`;
            link.append(head, meta);
            link.setAttribute('aria-label',
                `Open crawl ${crawl.id}, ${domain || 'unknown domain'}, ${crawl.status || 'unknown'}, ${num(crawl.urls_crawled || 0)} URLs`);
            host.appendChild(link);
        });
    }

    function wireMega() {
        mega.panel = document.getElementById('megaPanel');
        mega.triggers = [document.getElementById('megaTrigger'), document.getElementById('crawlChip')].filter(Boolean);
        if (!mega.panel) return;

        mega.triggers.forEach(trigger => {
            trigger.addEventListener('click', event => {
                event.preventDefault();
                toggleMega(trigger);
            });
        });

        document.getElementById('mmClose')?.addEventListener('click', () => closeMega(true));

        mega.panel.addEventListener('keydown', megaKeydown);

        mega.panel.addEventListener('click', event => {
            const item = event.target.closest('.mm-item');
            if (!item || !mega.panel.contains(item)) return;
            if (item.dataset.tab) {
                event.preventDefault();
                if (typeof window.switchTab === 'function') window.switchTab(item.dataset.tab);
                closeMega(true);
                return;
            }
            if (item.tagName === 'A' && item.classList.contains('mm-crawl')) return; // let it navigate
            // Actions carry their own inline onclick, which has already run and
            // may have taken focus (Settings focuses its first field).
            closeMega(false);
        });

        // Closing when focus leaves the panel is what makes the Tab order work.
        mega.panel.addEventListener('focusout', event => {
            const next = event.relatedTarget;
            if (!next) return;
            if (mega.panel.contains(next) || mega.triggers.includes(next)) return;
            closeMega(false);
        });

        document.addEventListener('pointerdown', event => {
            if (!isMegaOpen()) return;
            if (mega.panel.contains(event.target)) return;
            if (mega.triggers.some(t => t.contains(event.target))) return;
            closeMega(false);
        });

        document.addEventListener('keydown', event => {
            if (event.altKey && (event.key === 'm' || event.key === 'M')) {
                event.preventDefault();
                toggleMega(mega.triggers[0]);
            } else if (event.key === 'Escape' && isMegaOpen()) {
                event.preventDefault();
                closeMega(true);
            }
        });

        window.addEventListener('popstate', () => closeMega(false));
    }

    // ---- wiring ----------------------------------------------------------

    function refreshAll() {
        safely(refreshTabStrip);
        safely(refreshSubbar);
        safely(refreshMega);
    }

    function installHooks() {
        ['switchTab', 'toggleFilter', 'filterIssues', 'clearCrawlData', 'applyFilter',
            'clearActiveFilters', 'filterInternalLinks', 'filterExternalLinks',
            'searchInternalLinks', 'searchExternalLinks', 'updateIssuesTable',
            'updateStatistics', 'updateCrawlButtons']
            .forEach(name => after(name, refreshAll));
    }

    function init() {
        wireTabStrip();
        wireRail();
        wireMega();
        refreshMegaPlugins();
        refreshAll();
        loadCrawls(true);   // the topbar chip needs the open crawl's domain

        // Counts, the row total and the crawl line move while a crawl runs;
        // every write is compared first, so an idle app writes nothing.
        setInterval(() => safely(refreshSubbar), 1000);
        setInterval(() => { if (isMegaOpen()) safely(refreshMega); }, 1000);
    }

    installHooks();

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => safely(init));
    } else {
        safely(init);
    }

    window.ToadstoneNav = {
        openMega: () => openMega(mega.triggers[0]),
        closeMega: () => closeMega(false),
        isMegaOpen,
        refresh: refreshAll
    };
})();
