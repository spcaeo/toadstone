/**
 * Toadstone command palette
 * -------------------------
 * ⌘K / Ctrl-K, type a few letters, Enter. Every destination in the app is
 * reachable from here: the eleven crawl views, the nine settings panels that
 * otherwise sit behind one unlabelled "Settings" button, and the actions.
 *
 * Why this exists next to the mega menu rather than instead of it: a menu
 * teaches you what a tool can do, and a palette gets you there once you
 * already know. The menu is for the first week, the palette for every week
 * after. Neither replaces the other, and this app had only the first.
 *
 * Matching is subsequence, not substring, so "stcd" finds "Status Codes" and
 * "gugl" finds Google. Ranking prefers a prefix hit on the title, then a
 * tight match, then whatever the last-used order was — recency is kept in
 * localStorage per viewer and is purely a convenience, so every read of it
 * tolerates being refused.
 *
 * Public API (on `window`):
 *   openPalette() / closePalette() / togglePalette()
 *   registerCommand({ id, title, group, hint, keywords, run, available })
 *
 * Nothing here fetches anything, and the only global key binding is the one
 * that opens it.
 */
(function (global) {
    'use strict';

    var RECENT_KEY = 'toadstone.palette.recent';
    var MAX_RECENT = 12;
    var commands = [];
    var el = {};               // built lazily on first open
    var results = [];          // currently shown, in order
    var cursor = 0;
    var lastFocus = null;

    /* ---- the registry ---------------------------------------------------- */

    function registerCommand(spec) {
        if (!spec || !spec.id || typeof spec.run !== 'function') { return; }
        var existing = commands.findIndex(function (c) { return c.id === spec.id; });
        var entry = {
            id: spec.id,
            title: String(spec.title || spec.id),
            group: String(spec.group || 'Other'),
            hint: spec.hint ? String(spec.hint) : '',
            keywords: String(spec.keywords || '').toLowerCase(),
            run: spec.run,
            // A command that cannot run right now is shown greyed with its
            // reason rather than hidden: "why can't I export" is a question
            // the menu should answer, not dodge.
            available: typeof spec.available === 'function' ? spec.available : null
        };
        if (existing >= 0) { commands[existing] = entry; } else { commands.push(entry); }
    }

    function recent() {
        try {
            var raw = global.localStorage.getItem(RECENT_KEY);
            var list = raw ? JSON.parse(raw) : [];
            return Array.isArray(list) ? list : [];
        } catch (e) { return []; }
    }

    function remember(id) {
        try {
            var list = recent().filter(function (x) { return x !== id; });
            list.unshift(id);
            global.localStorage.setItem(RECENT_KEY,
                JSON.stringify(list.slice(0, MAX_RECENT)));
        } catch (e) { /* private window, blocked storage: not worth a word */ }
    }

    /* ---- matching -------------------------------------------------------- */

    /**
     * Subsequence cost within one field, or NO_MATCH.
     *
     * The sentinel is deliberately not a negative number. Scores carry
     * bonuses as negative values - a title prefix is worth -60 - so a
     * sentinel of -1 and a test of `cost < 0` threw away every strongest
     * match as though it had not matched at all. Typing "exp" returned
     * nothing while "Export data" sat in the registry, and "goog" returned
     * every settings panel except Google. An in-band sentinel sharing a
     * range with real values is how a search quietly hides its best answers.
     */
    var NO_MATCH = null;

    function costIn(query, hay) {
        var qi = 0, first = -1, gaps = 0, prev = -1;
        for (var i = 0; i < hay.length && qi < query.length; i++) {
            if (hay[i] !== query[qi]) { continue; }
            if (first < 0) { first = i; }
            if (prev >= 0 && i !== prev + 1) { gaps++; }
            prev = i; qi++;
        }
        if (qi < query.length) { return NO_MATCH; }
        return gaps * 4 + first;
    }

    /**
     * Returns a score, or NO_MATCH. Lower is better.
     *
     * Each field is matched on its own rather than against title + group +
     * keywords joined together. Joining them lets a query borrow letters
     * from different fields and match almost anything: "gugl" came back
     * with "Log out", assembled from lo(g) o(u)t ... si(g)n ... (l)eave.
     * Technically a subsequence, useless as a result.
     */
    function score(query, command) {
        if (!query) { return 0; }
        var title = command.title.toLowerCase();
        var best = costIn(query, title);
        if (best !== NO_MATCH) {
            if (title.indexOf(query) === 0) { best -= 60; }
            else if (title.indexOf(query) > 0) { best -= 25; }
            return best;
        }
        // A keyword hit still counts, but never beats a title hit.
        var viaKeywords = costIn(query, command.keywords);
        if (viaKeywords !== NO_MATCH) { return viaKeywords + 40; }
        var viaGroup = costIn(query, command.group.toLowerCase());
        return viaGroup !== NO_MATCH ? viaGroup + 70 : NO_MATCH;
    }

    function search(query) {
        var q = (query || '').trim().toLowerCase();
        var order = recent();
        var scored = [];
        for (var i = 0; i < commands.length; i++) {
            var s = score(q, commands[i]);
            if (s === NO_MATCH) { continue; }
            var seen = order.indexOf(commands[i].id);
            scored.push({
                command: commands[i],
                rank: s - (seen >= 0 ? (MAX_RECENT - seen) * 2 : 0),
                index: i
            });
        }
        scored.sort(function (a, b) {
            return a.rank - b.rank || a.index - b.index;
        });
        return scored.map(function (x) { return x.command; });
    }

    /* ---- markup ---------------------------------------------------------- */

    function build() {
        if (el.root) { return; }
        var root = document.createElement('div');
        root.className = 'cp-scrim';
        root.id = 'commandPalette';
        root.hidden = true;
        root.innerHTML =
            '<div class="cp-panel" role="dialog" aria-modal="true" aria-label="Command palette">' +
              '<div class="cp-field">' +
                '<span class="cp-prompt" aria-hidden="true">&rsaquo;</span>' +
                '<input type="text" class="cp-input" id="cpInput" autocomplete="off" ' +
                       'spellcheck="false" placeholder="Go to a view, a setting, an action…" ' +
                       'aria-controls="cpList" aria-expanded="true" role="combobox">' +
                '<kbd class="cp-esc">esc</kbd>' +
              '</div>' +
              '<div class="cp-list" id="cpList" role="listbox" aria-label="Commands"></div>' +
              '<div class="cp-foot">' +
                '<span><kbd>↑</kbd><kbd>↓</kbd> move</span>' +
                '<span><kbd>↵</kbd> open</span>' +
                '<span class="cp-foot-right" id="cpCount"></span>' +
              '</div>' +
            '</div>';
        document.body.appendChild(root);
        el.root = root;
        el.input = root.querySelector('#cpInput');
        el.list = root.querySelector('#cpList');
        el.count = root.querySelector('#cpCount');

        el.input.addEventListener('input', function () { render(el.input.value); });
        el.input.addEventListener('keydown', onKeyInPalette);
        root.addEventListener('mousedown', function (event) {
            if (event.target === root) { close(); }
        });
    }

    function render(query) {
        results = search(query);
        cursor = 0;
        if (!results.length) {
            el.list.innerHTML = '<p class="cp-empty">Nothing matches that.</p>';
            el.count.textContent = '';
            return;
        }
        var html = '';
        var group = null;
        for (var i = 0; i < results.length; i++) {
            var c = results[i];
            if (c.group !== group) {
                group = c.group;
                html += '<div class="cp-group" role="presentation">' + esc(group) + '</div>';
            }
            var why = c.available ? c.available() : true;
            var off = why !== true;
            html += '<div class="cp-row' + (off ? ' is-off' : '') + '" role="option" ' +
                    'data-index="' + i + '" id="cp-row-' + i + '" aria-selected="false">' +
                      '<span class="cp-row-title">' + esc(c.title) + '</span>' +
                      (off ? '<span class="cp-row-why">' + esc(String(why)) + '</span>'
                           : (c.hint ? '<span class="cp-row-hint">' + esc(c.hint) + '</span>' : '')) +
                    '</div>';
        }
        el.list.innerHTML = html;
        el.count.textContent = results.length + (results.length === 1 ? ' result' : ' results');
        Array.prototype.forEach.call(el.list.querySelectorAll('.cp-row'), function (row) {
            row.addEventListener('mouseenter', function () {
                cursor = Number(row.dataset.index) || 0; mark();
            });
            row.addEventListener('click', function () {
                cursor = Number(row.dataset.index) || 0; run();
            });
        });
        mark();
    }

    function mark() {
        var rows = el.list.querySelectorAll('.cp-row');
        for (var i = 0; i < rows.length; i++) {
            var on = i === cursor;
            rows[i].classList.toggle('is-on', on);
            rows[i].setAttribute('aria-selected', on ? 'true' : 'false');
            if (on) {
                el.input.setAttribute('aria-activedescendant', rows[i].id);
                var box = el.list.getBoundingClientRect();
                var r = rows[i].getBoundingClientRect();
                if (r.bottom > box.bottom) { el.list.scrollTop += r.bottom - box.bottom; }
                else if (r.top < box.top) { el.list.scrollTop -= box.top - r.top; }
            }
        }
    }

    function move(step) {
        if (!results.length) { return; }
        cursor = (cursor + step + results.length) % results.length;
        mark();
    }

    function run() {
        var c = results[cursor];
        if (!c) { return; }
        if (c.available && c.available() !== true) { return; }
        remember(c.id);
        close();
        try { c.run(); }
        catch (e) { console.error('[palette] "' + c.id + '" failed:', e); }
    }

    function onKeyInPalette(event) {
        if (event.key === 'ArrowDown') { event.preventDefault(); move(1); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); move(-1); }
        else if (event.key === 'Enter') { event.preventDefault(); run(); }
        else if (event.key === 'Escape') { event.preventDefault(); close(); }
        else if (event.key === 'Tab') { event.preventDefault(); move(event.shiftKey ? -1 : 1); }
    }

    function esc(text) {
        return String(text).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    /* ---- open / close ---------------------------------------------------- */

    function open(prefill) {
        build();
        lastFocus = document.activeElement;
        el.root.hidden = false;
        document.body.classList.add('cp-open');
        el.input.value = prefill || '';
        render(el.input.value);
        el.input.focus();
        el.input.select();
    }

    function close() {
        if (!el.root || el.root.hidden) { return; }
        el.root.hidden = true;
        document.body.classList.remove('cp-open');
        if (lastFocus && typeof lastFocus.focus === 'function') {
            try { lastFocus.focus(); } catch (e) { /* element went away */ }
        }
        lastFocus = null;
    }

    function toggle() {
        if (el.root && !el.root.hidden) { close(); } else { open(); }
    }

    /* The only global binding. Deliberately not bound while the viewer is
       typing into a field, because ⌘K is a browser shortcut in some places
       and stealing it mid-sentence is worse than not having it. */
    document.addEventListener('keydown', function (event) {
        var k = (event.key || '').toLowerCase();
        if ((event.metaKey || event.ctrlKey) && k === 'k') {
            event.preventDefault();
            toggle();
        }
    });

    global.openPalette = open;
    global.closePalette = close;
    global.togglePalette = toggle;
    global.registerCommand = registerCommand;
    global.ToadstonePalette = {
        commands: commands,
        search: search,
        VERSION: '1.0.0'
    };
}(window));
