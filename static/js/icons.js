/**
 * Toadstone icon system
 * ----------------------
 * A dependency-free inline SVG sprite. Every icon is drawn on the same
 * 24x24 grid with a 1.5 stroke, round caps and joins, `currentColor` and no
 * fills, so an icon inherits the colour of the text it sits next to and
 * severity colour always comes from a CSS token (--danger / --warn / --info),
 * never from the icon itself.
 *
 * Public API (all on `window`):
 *   icon(name, options)        -> SVG string, safe inside a template literal
 *   iconElement(name, options) -> a real <svg> DOM node
 *   renderIcons(root)          -> swaps every [data-icon] placeholder for its SVG
 *   LibreIcons                 -> { names, has, resolve, paths, VERSION }
 *
 * options: { size = 16, className = '', title = '', strokeWidth = 1.5 }
 *
 * Nothing here fetches a remote asset and nothing here mutates the page on
 * load except one <style> block holding the `.icon-spin` keyframes.
 */
(function (global) {
    'use strict';

    const VERSION = '1.0.0';
    const VIEWBOX = '0 0 24 24';
    const DEFAULT_SIZE = 16;
    const DEFAULT_STROKE = 1.5;

    // ---------------------------------------------------------------- geometry

    function n(value) {
        return +Number(value).toFixed(3);
    }

    /** A full circle as a single path (two semicircular arcs). */
    function circle(cx, cy, r) {
        return 'M' + n(cx) + ' ' + n(cy - r) +
            'a' + n(r) + ' ' + n(r) + ' 0 1 0 0 ' + n(r * 2) +
            'a' + n(r) + ' ' + n(r) + ' 0 1 0 0 ' + n(-r * 2);
    }

    /** A rounded rectangle as a single closed path. */
    function rect(x, y, w, h, r) {
        const ix = n(w - r * 2);
        const iy = n(h - r * 2);
        return 'M' + n(x + r) + ' ' + n(y) +
            'h' + ix +
            'a' + n(r) + ' ' + n(r) + ' 0 0 1 ' + n(r) + ' ' + n(r) +
            'v' + iy +
            'a' + n(r) + ' ' + n(r) + ' 0 0 1 ' + n(-r) + ' ' + n(r) +
            'h' + n(-ix) +
            'a' + n(r) + ' ' + n(r) + ' 0 0 1 ' + n(-r) + ' ' + n(-r) +
            'v' + n(-iy) +
            'a' + n(r) + ' ' + n(r) + ' 0 0 1 ' + n(r) + ' ' + n(-r) + 'z';
    }

    /** Evenly spaced radial ticks, used for the gear teeth and the sun rays. */
    function radial(cx, cy, inner, outer, count, offsetDeg) {
        const out = [];
        const offset = ((offsetDeg || 0) * Math.PI) / 180;
        for (let i = 0; i < count; i++) {
            const a = offset + (Math.PI * 2 * i) / count;
            const cos = Math.cos(a);
            const sin = Math.sin(a);
            out.push(
                'M' + n(cx + cos * inner) + ' ' + n(cy + sin * inner) +
                'L' + n(cx + cos * outer) + ' ' + n(cy + sin * outer)
            );
        }
        return out;
    }

    // ------------------------------------------------------------------ icons

    const ICONS = {
        /* ---- crawl controls ---- */
        'play':            ['M8 5.4 19 12 8 18.6Z'],
        'stop':            [rect(6.5, 6.5, 11, 11, 2)],
        'pause':           ['M9.5 5.5v13', 'M14.5 5.5v13'],
        'refresh':         ['M20 6.9v4.6h-4.6', 'M20 11.5A8 8 0 1 0 14 19.1'],
        'loader':          ['M20.5 12A8.5 8.5 0 1 1 12 3.5'],

        /* ---- data in / out ---- */
        'download':        ['M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3', 'M12 4v11', 'M7.5 10.5 12 15l4.5-4.5'],
        'upload':          ['M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3', 'M12 16V5', 'M7.5 9.5 12 5l4.5 4.5'],
        'save':            ['M4.5 5.5a2 2 0 0 1 2-2h9L19.5 7.5v11a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2z', 'M8 3.5V8h6.5V3.5', 'M8 20.5v-6h8v6'],
        'folder':          ['M3.5 7.5A1.5 1.5 0 0 1 5 6h4l2 2.5h8a1.5 1.5 0 0 1 1.5 1.5v8a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5z'],
        'copy':            [rect(9, 9, 11, 11, 2), 'M6 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v1'],
        'share':           [circle(18, 5.5, 2.5), circle(6, 12, 2.5), circle(18, 18.5, 2.5), 'M8.2 10.8 15.8 6.7', 'M8.2 13.2 15.8 17.3'],
        'trash':           ['M4 7h16', 'M6.5 7 7.5 19a2 2 0 0 0 2 1.9h5a2 2 0 0 0 2-1.9L17.5 7', 'M9.5 7V5.1A1.1 1.1 0 0 1 10.6 4h2.8a1.1 1.1 0 0 1 1.1 1.1V7', 'M10.4 10.8v6', 'M13.6 10.8v6'],

        /* ---- chrome & navigation ---- */
        'settings':        [circle(12, 12, 2.9), circle(12, 12, 6.6)].concat(radial(12, 12, 6.6, 8.5, 8, 22.5)),
        'sliders':         ['M4 7h8.8', 'M17.2 7h2.8', 'M4 17h2.8', 'M11.2 17h8.8', circle(15, 7, 2.2), circle(9, 17, 2.2)],
        'dashboard':       [rect(3.5, 3.5, 7, 7, 1.5), rect(13.5, 3.5, 7, 7, 1.5), rect(3.5, 13.5, 7, 7, 1.5), rect(13.5, 13.5, 7, 7, 1.5)],
        'menu':            ['M4 7h16', 'M4 12h16', 'M4 17h16'],
        'more-horizontal': [circle(6, 12, 1), circle(12, 12, 1), circle(18, 12, 1)],
        'search':          [circle(10.5, 10.5, 6.5), 'M15.6 15.6 20 20'],
        'filter':          ['M4 5h16l-6.2 7.4V19l-3.6-1.9v-4.7z'],
        'plus':            ['M12 5v14', 'M5 12h14'],
        'minus':           ['M5 12h14'],
        'x':               ['M6.5 6.5 17.5 17.5', 'M17.5 6.5 6.5 17.5'],
        'log-out':         ['M15.5 16.5 20 12l-4.5-4.5', 'M20 12H9.5', 'M11 20.5H6a2 2 0 0 1-2-2v-13a2 2 0 0 1 2-2h5'],
        'user':            [circle(12, 8, 3.8), 'M4.5 20.5a7.5 7.5 0 0 1 15 0'],

        /* ---- chevrons & arrows ---- */
        'chevron-up':      ['M6.5 14.5 12 9l5.5 5.5'],
        'chevron-down':    ['M6.5 9.5 12 15l5.5-5.5'],
        'chevron-left':    ['M14.5 6.5 9 12l5.5 5.5'],
        'chevron-right':   ['M9.5 6.5 15 12l-5.5 5.5'],
        'chevrons-up-down':['M8 10.5 12 6.5l4 4', 'M8 13.5 12 17.5l4-4'],
        'arrow-up':        ['M12 19.5v-15', 'M6.5 10 12 4.5 17.5 10'],
        'arrow-down':      ['M12 4.5v15', 'M6.5 14 12 19.5 17.5 14'],
        'arrow-left':      ['M19.5 12h-15', 'M10 6.5 4.5 12 10 17.5'],
        'arrow-right':     ['M4.5 12h15', 'M14 6.5 19.5 12 14 17.5'],
        'arrow-up-right':  ['M7 17 17 7', 'M8.6 7h8.4v8.4'],
        'sort-asc':        ['M6 19.5V5', 'M3.5 7.5 6 5l2.5 2.5', 'M11 6h3', 'M11 11h6', 'M11 16h9'],
        'sort-desc':       ['M6 4.5V19', 'M3.5 16.5 6 19l2.5-2.5', 'M11 6h9', 'M11 11h6', 'M11 16h3'],

        /* ---- status ---- */
        'check':           ['M5 12.8 9.5 17.3 19 7'],
        'check-circle':    [circle(12, 12, 8.5), 'M8 12.2 11 15.2 16.2 9'],
        'x-circle':        [circle(12, 12, 8.5), 'M9 9 15 15', 'M15 9 9 15'],
        'alert-triangle':  ['M12 4.4 20.8 19.8H3.2z', 'M12 10v4', 'M12 17.2h.01'],
        'alert-circle':    [circle(12, 12, 8.5), 'M12 7.6v5', 'M12 16.2h.01'],
        'info':            [circle(12, 12, 8.5), 'M12 16.4v-5', 'M12 7.8h.01'],
        'help-circle':     [circle(12, 12, 8.5), 'M9.7 9.6a2.4 2.4 0 0 1 4.7.7c0 1.6-2.4 2.1-2.4 3.4', 'M12 16.6h.01'],
        'circle':          [circle(12, 12, 8.5)],
        'shield':          ['M12 3 4.5 6.2v5.6c0 4.5 3.2 7.6 7.5 9.2 4.3-1.6 7.5-4.7 7.5-9.2V6.2z'],
        'bug':             ['M7 13.5V11a5 5 0 0 1 10 0v2.5a5 5 0 0 1-10 0z', 'M12 9v8.5', 'M7 12H4', 'M20 12h-3', 'M7.5 8.4 5.2 6.1', 'M16.5 8.4 18.8 6.1', 'M7.5 16.4 5.2 18.7', 'M16.5 16.4 18.8 18.7', 'M9.5 7 8.5 4.8', 'M14.5 7 15.5 4.8'],
        'zap':             ['M13.5 3 5.5 13.5h6l-1 7.5 8-10.5h-6z'],
        'key':             [circle(8.5, 15.5, 4), 'M11.4 12.6 20.5 3.5', 'M16.9 7.1 18.5 8.7', 'M14.4 9.6 16 11.2'],
        'lock':            ['M7.5 10V7.5a4.5 4.5 0 0 1 9 0V10', rect(4.5, 10, 15, 10.5, 2), 'M12 14v2.6'],

        /* ---- pages, links & structure ---- */
        'link':            ['M10.5 13.5a4 4 0 0 0 5.66 0l3-3a4 4 0 1 0-5.66-5.66l-1.5 1.5', 'M13.5 10.5a4 4 0 0 0-5.66 0l-3 3a4 4 0 1 0 5.66 5.66l1.5-1.5'],
        'link-broken':     ['M12.5 11.5a4 4 0 0 0 5.66 0l1.5-1.5a4 4 0 1 0-5.66-5.66', 'M11.5 12.5a4 4 0 0 0-5.66 0l-1.5 1.5a4 4 0 1 0 5.66 5.66', 'M13.4 13.4 15 15', 'M10.6 10.6 9 9'],
        'external-link':   ['M14 4h6v6', 'M20 4 11.5 12.5', 'M18 14.5V18a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h3.5'],
        'globe':           [circle(12, 12, 8.5), 'M3.5 12h17', 'M12 3.5a12.4 12.4 0 0 1 0 17a12.4 12.4 0 0 1 0-17'],
        'file-text':       ['M13.8 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.2z', 'M13.8 3v5.2H19', 'M8.5 13h7', 'M8.5 16.8h7', 'M8.5 9.2h2.2'],
        'code':            ['M9 8 5 12l4 4', 'M15 8l4 4-4 4', 'M13.4 6.4 10.6 17.6'],
        'image':           [rect(3, 4, 18, 16, 2), circle(8.4, 9.4, 1.4), 'M3.6 17.6 8.6 12.6a2 2 0 0 1 2.83 0l4.57 4.57', 'M14.6 15.6 16.5 13.7a2 2 0 0 1 2.83 0L20.5 14.9'],
        'sitemap':         [rect(9, 3, 6, 4, 1), rect(3, 17, 6, 4, 1), rect(15, 17, 6, 4, 1), 'M12 7v7', 'M6 17v-3h12v3'],
        'git-branch':      [circle(6.5, 5, 2.2), circle(6.5, 19, 2.2), circle(17.5, 5, 2.2), 'M6.5 7.2v9.6', 'M17.5 7.2v3.3a3 3 0 0 1-3 3H6.5'],
        'git-compare':     [circle(5.5, 6, 2.5), circle(18.5, 18, 2.5), 'M11 6h4a3 3 0 0 1 3 3v6.5', 'M9.5 4.5 11 6 9.5 7.5', 'M13 18H9a3 3 0 0 1-3-3V8.5', 'M14.5 16.5 13 18l1.5 1.5'],
        'list-checks':     ['M9.5 6H20', 'M9.5 12H20', 'M9.5 18H20', 'M3.5 5.6 4.9 7 7.2 4.6', 'M3.5 11.6 4.9 13l2.3-2.4', 'M4 18h2.6'],
        'tag':             ['M3.4 4.4a1.4 1.4 0 0 1 1.4-1.4h7.8a2 2 0 0 1 1.4.6l7 7a2 2 0 0 1 0 2.8l-7.2 7.2a2 2 0 0 1-2.8 0l-7-7a2 2 0 0 1-.6-1.4z', circle(7.4, 7.4, 1.1)],
        'bookmark':        ['M6 4.6A1.6 1.6 0 0 1 7.6 3h8.8A1.6 1.6 0 0 1 18 4.6V21l-6-4.2L6 21z'],

        /* ---- infrastructure & data ---- */
        'server':          [rect(3, 4, 18, 7, 2), rect(3, 13, 18, 7, 2), 'M6.6 7.5h1.8', 'M6.6 16.5h1.8'],
        'database':        ['M12 3a8 3 0 1 0 0 6a8 3 0 1 0 0-6', 'M4 6v6a8 3 0 0 0 16 0V6', 'M4 12v6a8 3 0 0 0 16 0v-6'],
        'table':           [rect(3, 4, 18, 16, 2), 'M3 9.2h18', 'M3 14.6h18', 'M9.2 9.2V20'],
        'columns':         [rect(3, 4, 18, 16, 2), 'M12 4v16'],
        'rows':            [rect(3, 4, 18, 16, 2), 'M3 12h18'],
        'rows-compact':    [rect(3, 4, 18, 16, 2), 'M3 9.3h18', 'M3 14.7h18'],
        'monitor':         [rect(2.5, 4, 19, 12, 2), 'M8.5 20.5h7', 'M12 16.5v4'],
        'bot':             [rect(4, 8, 16, 12, 2.5), circle(12, 3.6, 1.5), 'M12 8V5.1', 'M9.2 13v1.8', 'M14.8 13v1.8', 'M2.2 13v3', 'M21.8 13v3'],
        'eye':             ['M3 12a11 11 0 0 1 18 0 11 11 0 0 1-18 0z', circle(12, 12, 3.2)],
        'eye-off':         ['M10.6 5.9a9.7 9.7 0 0 1 1.4-.1c5.5 0 9.2 6.2 9.2 6.2a18.7 18.7 0 0 1-2.9 3.8', 'M6.5 8.1A18.6 18.6 0 0 0 2.8 12S6.5 18.2 12 18.2a9.5 9.5 0 0 0 3.6-.7', 'M10.3 10.3a2.5 2.5 0 0 0 3.4 3.4', 'M3.5 3.5 20.5 20.5'],

        /* ---- measurement ---- */
        'clock':           [circle(12, 12, 8.5), 'M12 7v5.2l3.4 2'],
        'calendar':        [rect(3, 5, 18, 16, 2), 'M3 10h18', 'M8 3v4', 'M16 3v4'],
        'gauge':           ['M3.5 16a8.5 8.5 0 0 1 17 0', 'M12 16 16.4 10.6', circle(12, 16, 1.1)],
        'trending-up':     ['M3.5 16.5 9.5 10.5l3.5 3.5L20.5 7', 'M15.5 7h5v5'],
        'bar-chart':       ['M6 20V10', 'M12 20V4.5', 'M18 20v-6'],
        'pie-chart':       ['M12 12V3.5A8.5 8.5 0 1 0 20.5 12z'],

        /* ---- theme ---- */
        'sun':             [circle(12, 12, 4), 'M12 2.8v2.4', 'M12 18.8v2.4', 'M2.8 12h2.4', 'M18.8 12h2.4', 'M5.5 5.5 7.2 7.2', 'M16.8 16.8 18.5 18.5', 'M18.5 5.5 16.8 7.2', 'M7.2 16.8 5.5 18.5'],
        'moon':            ['M20.5 14.5A9 9 0 1 1 9.5 3.5 8 8 0 0 0 20.5 14.5z']
    };

    /** Second names for the same drawing. */
    const ALIASES = {
        'start':        'play',
        'square':       'stop',
        'clear':        'trash',
        'delete':       'trash',
        'export':       'download',
        'import':       'upload',
        'gear':         'settings',
        'grid':         'dashboard',
        'layout-grid':  'dashboard',
        'close':        'x',
        'more':         'more-horizontal',
        'ellipsis':     'more-horizontal',
        'warning':      'alert-triangle',
        'error':        'alert-circle',
        'success':      'check-circle',
        'robot':        'bot',
        'spinner':      'loader',
        'rotate-cw':    'refresh',
        'reload':       'refresh',
        'unlink':       'link-broken',
        'broken-link':  'link-broken',
        'network':      'git-branch',
        'compare':      'git-compare',
        'issues':       'list-checks',
        'speed':        'gauge',
        'performance':  'gauge',
        'density':      'rows',
        'theme':        'moon',
        'desktop':      'monitor',
        'mobile':       'monitor',
        'file':         'file-text',
        'page':         'file-text',
        'html':         'code',
        'chevron-sort': 'chevrons-up-down',
        'logout':       'log-out',
        'account':      'user',
        'dot':          'circle'
    };

    /** Rendered when a name is unknown: a neutral "?" tile, never a throw. */
    const FALLBACK = Object.freeze([
        rect(4, 4, 16, 16, 2.5),
        'M10.1 10.1a2 2 0 0 1 3.9.6c0 1.3-1.9 1.7-1.9 2.8',
        'M12 16.4h.01'
    ]);

    /** Icons that carry the spin class by default. */
    const SPINNING = { 'loader': true };

    Object.keys(ICONS).forEach(function (key) { Object.freeze(ICONS[key]); });
    Object.freeze(ICONS);
    Object.freeze(ALIASES);

    const NAMES = Object.freeze(Object.keys(ICONS).sort());
    const ALIAS_NAMES = Object.freeze(Object.keys(ALIASES).sort());

    // -------------------------------------------------------------- internals

    const warned = Object.create(null);

    function warnOnce(name) {
        if (warned[name]) return;
        warned[name] = true;
        if (global.console && typeof console.warn === 'function') {
            console.warn('[icons] unknown icon "' + name + '" - rendering placeholder. Known names: window.ToadstoneIcons.names');
        }
    }

    function resolve(name) {
        const key = String(name == null ? '' : name).trim().toLowerCase();
        if (ICONS[key]) return key;
        if (ALIASES[key] && ICONS[ALIASES[key]]) return ALIASES[key];
        return null;
    }

    function escapeAttr(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function toSize(value) {
        const size = Number(value);
        if (!isFinite(size) || size <= 0) return DEFAULT_SIZE;
        return n(Math.min(size, 512));
    }

    function toStroke(value) {
        const stroke = Number(value);
        if (!isFinite(stroke) || stroke <= 0) return DEFAULT_STROKE;
        return n(Math.min(stroke, 4));
    }

    /**
     * Build the SVG markup for `name`.
     * @param {string} name
     * @param {{size?:number, className?:string, title?:string, strokeWidth?:number}} [options]
     * @returns {string} a single-line SVG string
     */
    function icon(name, options) {
        const opts = options || {};
        const key = resolve(name);
        if (!key) warnOnce(String(name));

        const paths = key ? ICONS[key] : FALLBACK;
        const size = toSize(opts.size === undefined ? DEFAULT_SIZE : opts.size);
        const stroke = toStroke(opts.strokeWidth === undefined ? DEFAULT_STROKE : opts.strokeWidth);
        const title = typeof opts.title === 'string' ? opts.title.trim() : '';

        const classes = ['icon', 'icon-' + (key || 'unknown')];
        if (SPINNING[key]) classes.push('icon-spin');
        if (!key) classes.push('icon-missing');
        if (opts.className) classes.push(String(opts.className));

        const a11y = title
            ? 'role="img"'
            : 'aria-hidden="true" focusable="false"';

        const body = (title ? '<title>' + escapeAttr(title) + '</title>' : '') +
            paths.map(function (d) { return '<path d="' + d + '"/>'; }).join('');

        return '<svg xmlns="http://www.w3.org/2000/svg" class="' + escapeAttr(classes.join(' ')) + '"' +
            ' width="' + size + '" height="' + size + '" viewBox="' + VIEWBOX + '"' +
            ' fill="none" stroke="currentColor" stroke-width="' + stroke + '"' +
            ' stroke-linecap="round" stroke-linejoin="round"' +
            ' data-icon="' + escapeAttr(key || String(name)) + '" ' + a11y + '>' +
            body + '</svg>';
    }

    /**
     * The same icon as a DOM node, for code that appends instead of interpolating.
     * @returns {SVGElement}
     */
    function iconElement(name, options) {
        const template = document.createElement('template');
        template.innerHTML = icon(name, options);
        return template.content.firstElementChild;
    }

    const PLACEHOLDER_SELECTOR = '[data-icon]:not(svg)';

    /**
     * Replace every `<i data-icon="name">` placeholder inside `root` with its SVG.
     * Idempotent: rendered icons are <svg> elements and are skipped on re-runs.
     *
     * Placeholder attributes that are honoured:
     *   data-icon        the icon name (required)
     *   data-icon-size   pixel size, default 16
     *   data-icon-title  accessible name; omit for decorative icons
     *   data-icon-stroke stroke width, default 1.5
     *   class / id / style / title are copied onto the SVG
     *
     * @param {Document|Element} [root=document]
     * @returns {number} how many placeholders were replaced
     */
    function renderIcons(root) {
        const scope = root || document;
        if (!scope || typeof scope.querySelectorAll !== 'function') return 0;

        const nodes = [];
        if (scope.matches && scope.matches(PLACEHOLDER_SELECTOR)) nodes.push(scope);
        const found = scope.querySelectorAll(PLACEHOLDER_SELECTOR);
        for (let i = 0; i < found.length; i++) nodes.push(found[i]);

        let count = 0;
        nodes.forEach(function (node) {
            if (!node.parentNode) return;
            const name = node.getAttribute('data-icon');
            const svg = iconElement(name, {
                size: node.getAttribute('data-icon-size') || DEFAULT_SIZE,
                strokeWidth: node.getAttribute('data-icon-stroke') || DEFAULT_STROKE,
                title: node.getAttribute('data-icon-title') || node.getAttribute('aria-label') || '',
                className: node.getAttribute('class') || ''
            });
            if (!svg) return;
            if (node.id) svg.setAttribute('id', node.id);
            const style = node.getAttribute('style');
            if (style) svg.setAttribute('style', style);
            const tooltip = node.getAttribute('title');
            if (tooltip) svg.setAttribute('data-tooltip', tooltip);
            node.parentNode.replaceChild(svg, node);
            count++;
        });
        return count;
    }

    // The one style this module owns: the spin keyframes for `.icon-spin`.
    // Colour, size and alignment belong to styles.css, never here.
    function injectSpinKeyframes() {
        if (!document.head || document.getElementById('toadstone-icon-style')) return;
        const style = document.createElement('style');
        style.id = 'toadstone-icon-style';
        style.textContent =
            '@keyframes toadstone-icon-spin{to{transform:rotate(360deg)}}' +
            '.icon{flex:none;vertical-align:-0.15em}' +
            '.icon-spin{transform-origin:50% 50%;animation:toadstone-icon-spin 900ms linear infinite}' +
            '@media (prefers-reduced-motion: reduce){.icon-spin{animation-duration:2.4s}}';
        document.head.appendChild(style);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            injectSpinKeyframes();
            renderIcons(document);
        });
    } else {
        injectSpinKeyframes();
        renderIcons(document);
    }

    // ----------------------------------------------------------------- exports

    global.icon = icon;
    global.iconElement = iconElement;
    global.renderIcons = renderIcons;
    // Named LibreIcons before the rename. Both names point at the same frozen
    // object, because a plugin written against either should keep working.
    global.ToadstoneIcons = global.LibreIcons = Object.freeze({
        VERSION: VERSION,
        names: NAMES,
        aliases: ALIAS_NAMES,
        aliasMap: ALIASES,
        paths: ICONS,
        has: function (name) { return resolve(name) !== null; },
        resolve: resolve,
        icon: icon,
        element: iconElement,
        render: renderIcons
    });
})(window);
