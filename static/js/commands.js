/**
 * What the command palette can reach
 * ----------------------------------
 * One registration per destination, kept apart from palette.js so that file
 * stays a mechanism and this one stays a list. Adding a screen means adding
 * a line here; forgetting to is the bug this file exists to make obvious.
 *
 * The nine settings panels each get their own entry. They used to be
 * reachable only by opening Settings and reading nine tabs, which is how a
 * person ends up asking where the Google connection lives when it has been
 * there all along.
 */
(function (global) {
    'use strict';

    function add(spec) {
        if (typeof global.registerCommand === 'function') { global.registerCommand(spec); }
    }

    function go(tab) {
        return function () {
            if (typeof global.switchTab === 'function') { global.switchTab(tab); }
        };
    }

    /**
      * openSettings() is async - it awaits the user's tier before showing the
      * modal - so switching the tab in the next statement ran before the modal
      * existed and the panel stayed on Crawler. Wait for it.
      */
    function settings(panel) {
        return function () {
            var opened = typeof global.openSettings === 'function'
                ? global.openSettings() : null;
            Promise.resolve(opened).then(function () {
                if (typeof global.switchSettingsTab === 'function') {
                    global.switchSettingsTab(panel);
                }
            });
        };
    }

    /** A crawl has to exist before most of these mean anything. */
    function needsCrawl() {
        var state = global.crawlState;
        var has = state && Array.isArray(state.urls) && state.urls.length > 0;
        return has ? true : 'needs a crawl';
    }

    var VIEWS = [
        ['overview', 'Overview', 'every crawled URL'],
        ['internal', 'Internal', 'pages on this site'],
        ['external', 'External', 'links leaving the site'],
        ['status-codes', 'Status Codes', '2xx, 3xx, 4xx, 5xx, no response'],
        ['links', 'Links', 'anchors, targets, nofollow'],
        ['issues', 'Issues', 'everything the checks found'],
        ['pagespeed', 'PageSpeed', 'Core Web Vitals, lab and field'],
        ['visualization', 'Visualization', 'the link graph'],
        ['sitemap', 'Sitemap', 'what the sitemaps claim'],
        ['compare', 'Compare', 'this crawl against an earlier one'],
        ['rules', 'Rules', 'your own consistency rules']
    ];

    VIEWS.forEach(function (row) {
        add({
            id: 'view:' + row[0],
            title: row[1],
            group: 'Views',
            hint: row[2],
            keywords: 'tab view ' + row[0].replace('-', ' '),
            run: go(row[0]),
            available: row[0] === 'overview' ? null : needsCrawl
        });
    });

    var PANELS = [
        ['crawler', 'Crawler settings', 'depth, limits, what to fetch'],
        ['google', 'Google: Search Console & Analytics', 'connect an account, pick a property'],
        ['requests', 'Requests', 'user agent, timeouts, rate limit'],
        ['filters', 'Filters', 'include and exclude patterns'],
        ['export', 'Export settings', 'formats and columns'],
        ['javascript', 'JavaScript rendering', 'Chromium, viewports, waits'],
        ['issues', 'Issue exclusion', 'checks to silence'],
        ['customcss', 'Custom CSS', 'your own styling'],
        ['advanced', 'Advanced', 'everything with sharp edges']
    ];

    PANELS.forEach(function (row) {
        add({
            id: 'settings:' + row[0],
            title: row[1],
            group: 'Settings',
            hint: row[2],
            keywords: 'settings preferences options config ' + row[0],
            run: settings(row[0])
        });
    });

    add({
        id: 'action:start',
        title: 'Start or stop the crawl',
        group: 'Actions',
        hint: 'same as the Start button',
        keywords: 'run begin halt cancel',
        run: function () {
            if (typeof global.toggleCrawl === 'function') { global.toggleCrawl(); }
        }
    });

    add({
        id: 'action:dashboard',
        title: 'Dashboard',
        group: 'Actions',
        hint: 'crawls, schedules, history',
        keywords: 'home crawls history',
        run: function () {
            if (typeof global.openDashboard === 'function') { global.openDashboard(); }
        }
    });

    add({
        id: 'action:export',
        title: 'Export data',
        group: 'Actions',
        hint: 'CSV, JSON, Excel',
        keywords: 'download csv json excel save report',
        run: function () {
            if (typeof global.exportData === 'function') { global.exportData(); }
        },
        available: needsCrawl
    });

    add({
        id: 'action:save',
        title: 'Save crawl',
        group: 'Actions',
        keywords: 'store keep persist',
        run: function () {
            if (typeof global.saveCrawl === 'function') { global.saveCrawl(); }
        },
        available: needsCrawl
    });

    add({
        id: 'action:load',
        title: 'Load a saved crawl',
        group: 'Actions',
        keywords: 'open restore previous',
        run: function () {
            if (typeof global.loadCrawl === 'function') { global.loadCrawl(); }
        }
    });

    add({
        id: 'action:theme',
        title: 'Switch theme',
        group: 'Actions',
        hint: 'dark, light, follow the system',
        keywords: 'dark light appearance contrast',
        run: function () {
            if (typeof global.cycleTheme === 'function') { global.cycleTheme(); }
        }
    });

    add({
        id: 'action:docs-name',
        title: 'Why "Toadstone"?',
        group: 'Help',
        hint: 'the stone that was supposed to detect poison',
        keywords: 'about name story toad stone help licence credits labsmedia',
        // This used to open /docs/about-the-name, which has never been a route:
        // the command was in the palette, looked right, and opened a 404. The
        // story lives in the About tab now, so send people there - and click
        // the tab the way a person would rather than reaching past it.
        run: function () {
            const btn = document.querySelector('.tab-btn[data-plugin-id="about"]');
            if (btn) { btn.click(); return; }
            global.open('https://github.com/spcaeo/toadstone#the-number-that-matters',
                        '_blank', 'noopener');
        }
    });

    add({
        id: 'action:logout',
        title: 'Log out',
        group: 'Actions',
        keywords: 'sign out leave',
        run: function () {
            if (typeof global.logout === 'function') { global.logout(); }
        }
    });
}(window));
