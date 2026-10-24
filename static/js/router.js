// URL Router
// Keeps the address bar in sync with what is on screen, so any view can be
// reloaded, bookmarked, shared or reached with Back/Forward:
//
//   /crawl/<id>/<tab>?filter=4xx&severity=error&page=<url>&settings=requests
//
//   path      /crawl/<id>        crawl shown (loaded from the database if needed)
//             /crawl/<id>/<tab>  results tab; omitted for "overview"
//             /?tab=<tab>        tab when no crawl is shown yet
//   filter    sidebar filter set, comma-joined (internal, 4xx, 4xx,html, ...)
//   severity  Issues tab filter (error, warning, info)
//   links_status, links_q, ext_status, ext_q   Links tab filters and searches
//   page      URL whose details popup is open
//   settings  Settings popup, open on that tab
//   dashboard Dashboard popup is open
//
// It wraps the app's global UI functions (switchTab, toggleFilter, ...) rather
// than editing them, so upstream Toadstone changes merge cleanly.

(function () {
    const DEFAULT_TAB = 'overview';
    const SEVERITIES = ['all', 'error', 'warning', 'info'];
    const NAME_RE = /^[\w-]+$/;
    // ?filter= carries a set: one name, or several comma-joined in the order
    // they were switched on. A link written before multi-select still works.
    const NAME_LIST_RE = /^[\w-]+(,[\w-]+)*$/;

    let crawlId = null;     // crawl currently on screen
    let applying = false;   // restoring state from the URL: don't write it back

    // ---- URL <-> view ----------------------------------------------------

    function readUrl() {
        const match = location.pathname.match(/^\/crawl\/(\d+)(?:\/([\w-]+))?\/?$/);
        const q = new URLSearchParams(location.search);
        return {
            crawlId: match ? Number(match[1]) : null,
            tab: (match && match[2]) || q.get('tab') || DEFAULT_TAB,
            filter: q.get('filter'),
            severity: q.get('severity') || 'all',
            linksStatus: q.get('links_status') || 'all',
            linksQuery: q.get('links_q') || '',
            extStatus: q.get('ext_status') || 'all',
            extQuery: q.get('ext_q') || '',
            page: q.get('page'),
            settings: q.get('settings'),
            dashboard: q.has('dashboard')
        };
    }

    function buildUrl(view) {
        const path = view.crawlId
            ? `/crawl/${view.crawlId}` + (view.tab !== DEFAULT_TAB ? `/${view.tab}` : '')
            : '/';
        const q = new URLSearchParams();
        if (!view.crawlId && view.tab !== DEFAULT_TAB) q.set('tab', view.tab);
        if (view.filter) q.set('filter', view.filter);
        if (view.severity !== 'all') q.set('severity', view.severity);
        if (view.linksStatus !== 'all') q.set('links_status', view.linksStatus);
        if (view.linksQuery) q.set('links_q', view.linksQuery);
        if (view.extStatus !== 'all') q.set('ext_status', view.extStatus);
        if (view.extQuery) q.set('ext_q', view.extQuery);
        if (view.dashboard) q.set('dashboard', '1');
        if (view.settings) q.set('settings', view.settings);
        if (view.page) q.set('page', view.page);
        const query = q.toString();
        return path + (query ? `?${query}` : '');
    }

    function nameFromOnclick(el, fn) {
        const m = el?.getAttribute('onclick')?.match(new RegExp(`${fn}\\('([^']+)'\\)`));
        return m ? m[1] : null;
    }

    function railFilterElement(name) {
        return NAME_RE.test(name)
            ? document.querySelector(`.filter-item[onclick="toggleFilter('${name}')"]`)
            : null;
    }

    function isOpen(id) {
        const el = document.getElementById(id);
        return !!el && el.style.display !== 'none' && el.style.display !== '';
    }

    function currentView() {
        const activeTab = document.querySelector('.tab-btn.active');
        const links = crawlState.filters.linksFilter;
        return {
            crawlId,
            tab: nameFromOnclick(activeTab, 'switchTab') || activeTab?.dataset.pluginId || DEFAULT_TAB,
            filter: crawlState.filters.active || null,
            severity: crawlState.filters.issueFilter || 'all',
            linksStatus: links.internalStatusCode || 'all',
            linksQuery: links.internalSearch || '',
            extStatus: links.externalStatusCode || 'all',
            extQuery: links.externalSearch || '',
            page: document.querySelector('.details-modal-overlay')?.dataset.url || null,
            settings: isOpen('settingsModal')
                ? nameFromOnclick(document.querySelector('.settings-tab-btn.active'), 'switchSettingsTab')
                : null,
            dashboard: isOpen('dashboardModal')
        };
    }

    // Write the on-screen view to the address bar. "push" adds a history
    // entry (Back returns here); "replace" is for high-frequency changes like
    // typing in a search box.
    function sync(mode = 'push') {
        if (applying) return;
        const url = buildUrl(currentView());
        if (url === location.pathname + location.search) return;
        history[mode === 'replace' ? 'replaceState' : 'pushState'](null, '', url);
    }

    // ---- Crawl selection -------------------------------------------------

    async function fetchStatus() {
        try {
            const response = await fetch('/api/crawl_status?stats_only=1');
            return response.ok ? await response.json() : null;
        } catch (error) {
            console.error('Router: could not read crawl status', error);
            return null;
        }
    }

    // Make the session show crawl `wanted` (or nothing). Returns the crawl id
    // actually on screen afterwards.
    async function showCrawl(wanted) {
        const status = await fetchStatus();
        const serverId = status?.crawl_id ?? null;
        const running = status?.status === 'running';

        if (!wanted) {
            // A reload of "/" while a crawl runs brings the running crawl back
            if (running && serverId) {
                await hydrateFromServer();
                return serverId;
            }
            if (crawlId && !running) {
                clearCrawlData();
            }
            return running ? crawlId : null;
        }

        if (wanted === crawlId && wanted === serverId) {
            return wanted;
        }

        if (wanted !== serverId) {
            if (running && !confirm(`Crawl #${serverId} is still running. Stop it and open crawl #${wanted}?`)) {
                return serverId;
            }
            let result = {};
            try {
                const response = await fetch(`/api/crawls/${wanted}/load`, { method: 'POST' });
                result = await response.json();
            } catch (error) {
                result = { error: error.message };
            }
            if (!result.success) {
                showNotification(`Crawl #${wanted} could not be opened: ${result.error || result.message || 'unknown error'}`, 'error');
                return crawlId === serverId ? crawlId : null;
            }
        }

        await hydrateFromServer();
        return wanted;
    }

    // ---- Applying a URL to the screen ------------------------------------

    async function applyView(view) {
        // Tab
        const tab = NAME_RE.test(view.tab) && tabButtonFor(view.tab) && document.getElementById(`${view.tab}-tab`)
            ? view.tab : DEFAULT_TAB;
        if (currentView().tab !== tab) switchTab(tab);

        // Sidebar filter set. Unknown names are dropped, so a stale or
        // hand-typed link degrades to the filters that do exist.
        const wanted = view.filter && NAME_LIST_RE.test(view.filter)
            ? view.filter.split(',').filter(name => railFilterElement(name))
            : [];
        if ((crawlState.filters.active || '') !== wanted.join(',')) {
            if (typeof setActiveFilters === 'function') {
                setActiveFilters(wanted);
            } else {
                // Older app.js without a filter set: fall back to one filter
                if (crawlState.filters.active) clearActiveFilters();
                document.querySelectorAll('.filter-item[onclick^="toggleFilter"]').forEach(el => el.classList.remove('active'));
                if (wanted.length) toggleFilter(wanted[0]);
            }
        }

        // Issues severity
        const severity = SEVERITIES.includes(view.severity) ? view.severity : 'all';
        if (crawlState.filters.issueFilter !== severity) filterIssues(severity);

        // Links tab filters and searches
        applyLinksView(view);

        // Popups
        if (view.dashboard && !isOpen('dashboardModal')) await openDashboard();
        if (!view.dashboard && isOpen('dashboardModal')) closeDashboard();

        if (view.settings) {
            if (!isOpen('settingsModal')) await openSettings();
            if (isOpen('settingsModal') && NAME_RE.test(view.settings) &&
                document.getElementById(`${view.settings}-settings`)) {
                switchSettingsTab(view.settings);
            }
        } else if (isOpen('settingsModal')) {
            closeSettings();
        }

        const openPage = document.querySelector('.details-modal-overlay')?.dataset.url || null;
        if (view.page !== openPage) {
            closeUrlDetails();
            if (view.page) {
                if (crawlState.urls.some(u => u.url === view.page)) {
                    showUrlDetails(view.page);
                } else {
                    showNotification('That page is not part of this crawl', 'error');
                }
            }
        }
    }

    function applyLinksView(view) {
        const links = crawlState.filters.linksFilter;
        const wanted = {
            internalStatusCode: view.linksStatus,
            internalSearch: view.linksQuery,
            externalStatusCode: view.extStatus,
            externalSearch: view.extQuery
        };
        if (Object.keys(wanted).every(key => links[key] === wanted[key])) return;

        Object.assign(links, wanted);
        const setValue = (selector, value) => {
            const el = document.querySelector(selector);
            if (el) el.value = value;
        };
        setValue('select[onchange="filterInternalLinks(this.value)"]', view.linksStatus);
        setValue('input[oninput="searchInternalLinks(this.value)"]', view.linksQuery);
        setValue('select[onchange="filterExternalLinks(this.value)"]', view.extStatus);
        setValue('input[oninput="searchExternalLinks(this.value)"]', view.extQuery);
        applyLinksFilter();
    }

    async function restore(view) {
        applying = true;
        try {
            crawlId = await showCrawl(view.crawlId);
            await applyView(view);
        } finally {
            applying = false;
        }
        // Normalise the address bar (drops invalid values, fills in the crawl)
        history.replaceState(null, '', buildUrl(currentView()));
    }

    // ---- Hooking the app's UI functions ----------------------------------

    function after(name, callback) {
        const original = window[name];
        if (typeof original !== 'function') return;
        window[name] = function (...args) {
            const result = original.apply(this, args);
            if (result && typeof result.then === 'function') {
                return result.then(value => { callback(args); return value; });
            }
            callback(args);
            return result;
        };
    }

    // Record the crawl the session now shows (dashboard load/resume). Replaces
    // the entry the dashboard's own close just pushed, so Back returns to the
    // dashboard rather than to the previous crawl without it.
    async function refreshCrawlId() {
        if (applying) return;
        const status = await fetchStatus();
        if (status?.crawl_id && status.crawl_id !== crawlId) {
            crawlId = status.crawl_id;
        }
        sync('replace');
    }

    function installHooks() {
        ['switchTab', 'toggleFilter', 'closeUrlDetails', 'openDashboard',
            'closeDashboard', 'openSettings', 'closeSettings', 'switchSettingsTab',
            'filterInternalLinks', 'filterExternalLinks']
            .forEach(name => after(name, () => sync('push')));
        ['filterIssues', 'searchInternalLinks', 'searchExternalLinks']
            .forEach(name => after(name, () => sync('replace')));

        // Tag the details popup with its URL so the view can be read back
        after('showUrlDetails', ([url]) => {
            const overlay = document.querySelector('.details-modal-overlay');
            if (overlay) overlay.dataset.url = url;
            sync('push');
        });

        after('clearCrawlData', () => {
            if (crawlState.isRunning) return;  // user cancelled the confirm
            crawlId = null;
            sync('push');
        });
        after('loadCrawlFromDashboard', refreshCrawlId);
        after('resumeCrawlFromDashboard', refreshCrawlId);

        window.addEventListener('popstate', () => restore(readUrl()));
    }

    window.Router = {
        // Called once by initializeApp() after the app is set up
        async init() {
            installHooks();
            await restore(readUrl());
        },
        // Crawl shown on screen (null when none)
        currentCrawlId() {
            return crawlId;
        },
        // A new crawl started: its id becomes part of the URL
        setCrawl(id) {
            if (!id) return;
            crawlId = id;
            sync('push');
        },
        // Write the current view to the address bar (for UI changes that are
        // not made through one of the hooked functions)
        sync(mode = 'push') {
            sync(mode);
        },
        // Run several UI changes as a single history entry: the hooks stay
        // quiet while `changes` runs, then the result is recorded once. Used
        // by "Clear all", which resets six controls at the same time.
        batch(changes, mode = 'push') {
            const wasApplying = applying;
            applying = true;
            try {
                changes();
            } finally {
                applying = wasApplying;
            }
            sync(mode);
        }
    };
})();
