/**
 * The four capabilities that had no door
 * --------------------------------------
 * Server log analysis, the fixer, link metrics and the MCP server were all
 * built, tested and reachable only by curl: no button, no menu entry, no
 * mention anywhere in the UI. A feature nobody can find is a feature nobody
 * has, whatever the test count says.
 *
 * This file is the front end for all four. Two of them are modals (logs,
 * fixer) and two live in Settings > Integrations (link metrics keys, MCP),
 * because keys belong with settings and one-off jobs do not.
 */
(function (global) {
    'use strict';

    function $(id) { return document.getElementById(id); }

    function show(id) {
        var node = $(id);
        if (node) { node.style.display = 'flex'; }
    }

    function hide(id) {
        var node = $(id);
        if (node) { node.style.display = 'none'; }
    }

    function esc(text) {
        return String(text == null ? '' : text).replace(/[&<>"']/g, function (ch) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
                     '"': '&quot;', "'": '&#39;' }[ch];
        });
    }

    /* ---- crawl pickers --------------------------------------------------- */

    /** Fill a <select> with saved crawls, newest first. */
    function fillCrawlPicker(selectId, onReady) {
        var select = $(selectId);
        if (!select) { return; }
        fetch('/api/crawls/list', { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var crawls = (data && (data.crawls || data.items)) || [];
                if (!crawls.length) {
                    select.innerHTML = '<option value="">No saved crawls</option>';
                    return;
                }
                select.innerHTML = crawls.map(function (c) {
                    var id = c.id != null ? c.id : c.crawl_id;
                    var label = (c.base_url || c.url || 'crawl') +
                                ' · #' + id +
                                (c.created_at ? ' · ' + String(c.created_at).slice(0, 16) : '');
                    return '<option value="' + esc(id) + '">' + esc(label) + '</option>';
                }).join('');
                if (typeof onReady === 'function') { onReady(); }
            })
            .catch(function () {
                select.innerHTML = '<option value="">Could not list crawls</option>';
            });
    }

    /* ---- server logs ----------------------------------------------------- */

    function openLogs() {
        show('logsModal');
        $('logResults').innerHTML = '';
        fillCrawlPicker('logCrawlId');
        fetch('/api/logs/formats', { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data || !data.formats) { return; }
                var names = data.formats.map(function (f) { return f.name; });
                var mb = Math.round((data.max_bytes || 0) / (1024 * 1024));
                $('logFormats').textContent =
                    names.join(', ') + (mb ? ' · up to ' + mb + 'MB' : '');
            })
            .catch(function () { /* the hint stays as written in the markup */ });
    }

    function closeLogs() { hide('logsModal'); }

    function analyseLog() {
        var input = $('logFile');
        var file = input && input.files && input.files[0];
        var out = $('logResults');
        if (!file) {
            out.innerHTML = '<p class="log-note log-note-warn">Choose a log file first.</p>';
            return;
        }
        var body = new FormData();
        body.append('log', file);
        var crawlId = $('logCrawlId').value;
        if (crawlId) { body.append('crawl_id', crawlId); }
        if ($('logKeep').checked) { body.append('keep', 'true'); }

        var button = $('logAnalyseBtn');
        button.disabled = true;
        out.innerHTML = '<p class="log-note">Reading…</p>';

        fetch('/api/logs/analyse', {
            method: 'POST', body: body, credentials: 'same-origin'
        })
            .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
            .then(function (pair) {
                var ok = pair[0], data = pair[1];
                if (!ok || !data.success) {
                    out.innerHTML = '<p class="log-note log-note-warn">' +
                        esc((data && data.error) || 'That did not work.') + '</p>';
                    return;
                }
                renderLogFindings(out, data);
            })
            .catch(function (err) {
                out.innerHTML = '<p class="log-note log-note-warn">' +
                    esc(String(err)) + '</p>';
            })
            .finally(function () { button.disabled = false; });
    }

    function renderLogFindings(out, data) {
        var read = data.lines_read || 0;
        var unparsed = data.lines_unparsed || 0;
        var issues = data.issues || [];
        var head = '<p class="log-note">' +
            esc(read.toLocaleString()) + ' lines read, ' +
            esc((data.requests_understood || 0).toLocaleString()) + ' requests understood' +
            (unparsed ? ', ' + esc(unparsed.toLocaleString()) + ' unreadable' : '') +
            (data.pages_compared ? ', compared against ' +
                esc(data.pages_compared) + ' crawled pages' : '') +
            (data.truncated ? ' · the file was longer than we read' : '') +
            '</p>';
        if (data.kept) {
            head += '<p class="log-note log-note-ok">Findings were saved onto crawl ' +
                    esc(data.crawl_id) + '.</p>';
        }
        if (!issues.length) {
            out.innerHTML = head +
                '<p class="log-note">Nothing to report from this log.</p>';
            return;
        }
        out.innerHTML = head + '<ul class="log-list">' + issues.map(function (i) {
            return '<li class="log-item log-sev-' + esc(i.type || 'info') + '">' +
                     '<span class="log-item-title">' + esc(i.issue || i.check) + '</span>' +
                     '<code class="log-item-slug">' + esc(i.check) + '</code>' +
                     '<span class="log-item-detail">' + esc(i.details || '') + '</span>' +
                   '</li>';
        }).join('') + '</ul>';
    }

    /* ---- fixer ----------------------------------------------------------- */

    function openFixer() {
        show('fixerModal');
        $('fixerBrief').value = '';
        $('fixerSummary').textContent = '';
        $('fixerRunBtn').disabled = true;
        fillCrawlPicker('fixerCrawlId');
    }

    function closeFixer() { hide('fixerModal'); }

    function previewFixerBrief() {
        var crawlId = $('fixerCrawlId').value;
        if (!crawlId) {
            $('fixerSummary').textContent = 'Pick a crawl first.';
            return;
        }
        var params = new URLSearchParams({
            crawl_id: crawlId,
            repo_path: $('fixerRepoPath').value || ''
        });
        $('fixerSummary').textContent = 'Building the brief…';
        fetch('/api/fixer/brief?' + params.toString(), { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success) {
                    $('fixerSummary').textContent = data.error || 'That did not work.';
                    return;
                }
                $('fixerBrief').value = data.brief || '';
                $('fixerSummary').textContent =
                    data.issues_fixable + ' of ' + data.issues_total +
                    ' findings are the kind a code change can fix. ' + (data.note || '');
                $('fixerRunBtn').disabled = !data.issues_fixable;
            })
            .catch(function (err) { $('fixerSummary').textContent = String(err); });
    }

    function runFixer() {
        var crawlId = $('fixerCrawlId').value;
        if (!crawlId) { return; }
        var button = $('fixerRunBtn');
        button.disabled = true;
        $('fixerSummary').textContent = 'Running… this asks a model and can take a while.';
        fetch('/api/fixer/run', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                crawl_id: Number(crawlId),
                repo_path: $('fixerRepoPath').value || ''
            })
        })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success) {
                    $('fixerSummary').textContent = data.error || 'That did not work.';
                    return;
                }
                // `ran: false` is an ordinary answer - no model configured, or
                // nothing worth fixing - and the brief still came back.
                $('fixerSummary').textContent = data.ran
                    ? 'Done. ' + (data.summary || '')
                    : 'Not run: ' + (data.why || data.reason ||
                                     'no model is configured for this install.');
                if (data.brief) { $('fixerBrief').value = data.brief; }
            })
            .catch(function (err) { $('fixerSummary').textContent = String(err); })
            .finally(function () { button.disabled = false; });
    }

    /* ---- MCP ------------------------------------------------------------- */

    function refreshMcp() {
        var field = $('mcpEndpoint');
        if (!field) { return; }
        var url = global.location.origin + '/api/mcp';
        field.value = url;
        $('mcpSnippet').value = JSON.stringify({
            mcpServers: {
                toadstone: {
                    type: 'http',
                    url: url
                }
            }
        }, null, 2);

        fetch('/api/mcp', { credentials: 'same-origin' })
            .then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); })
            .then(function (pair) {
                var status = $('mcpStatus');
                if (!status) { return; }
                if (pair[0]) {
                    // The server block is nested, and there is no tool list
                    // on this endpoint - reading `info.tools.length` off the
                    // top level silently printed nothing at all.
                    var info = (pair[1] || {}).server || {};
                    var guards = (pair[1] || {}).guardrails || {};
                    status.textContent = 'Reachable' +
                        (info.protocolVersion ? ' · protocol ' + info.protocolVersion : '') +
                        (guards.mode ? ' · ' + guards.mode : '');
                    status.className = 'mcp-status mcp-status-ok';
                } else {
                    status.textContent = 'Not reachable from this browser';
                    status.className = 'mcp-status mcp-status-warn';
                }
            })
            .catch(function () {
                var status = $('mcpStatus');
                if (status) {
                    status.textContent = 'Not reachable from this browser';
                    status.className = 'mcp-status mcp-status-warn';
                }
            });
    }

    function copyMcpSnippet() {
        var box = $('mcpSnippet');
        if (!box) { return; }
        box.select();
        try {
            global.navigator.clipboard.writeText(box.value);
        } catch (e) {
            try { document.execCommand('copy'); } catch (e2) { /* nothing else to try */ }
        }
    }

    /* ---- registration ---------------------------------------------------- */

    global.openLogs = openLogs;
    global.closeLogs = closeLogs;
    global.analyseLog = analyseLog;
    global.openFixer = openFixer;
    global.closeFixer = closeFixer;
    global.previewFixerBrief = previewFixerBrief;
    global.runFixer = runFixer;
    global.refreshMcp = refreshMcp;
    global.copyMcpSnippet = copyMcpSnippet;

    document.addEventListener('DOMContentLoaded', function () {
        if (typeof global.registerCommand !== 'function') { return; }
        global.registerCommand({
            id: 'tool:logs', title: 'Server logs', group: 'Tools',
            hint: 'what search engines actually fetched',
            keywords: 'log access apache nginx w3c googlebot crawl budget',
            run: openLogs
        });
        global.registerCommand({
            id: 'tool:fixer', title: 'Fixer', group: 'Tools',
            hint: 'turn findings into a brief an agent can act on',
            keywords: 'agent fix repair brief code claude',
            run: openFixer
        });
        global.registerCommand({
            id: 'settings:integrations', title: 'Integrations: link metrics & MCP',
            group: 'Settings',
            hint: 'Moz, Majestic, Ahrefs keys and the MCP endpoint',
            keywords: 'moz majestic ahrefs authority backlinks mcp agent api keys',
            run: function () {
                // openSettings() is async; switching before it resolves leaves
                // the modal on whatever panel it opened with.
                var opened = typeof global.openSettings === 'function'
                    ? global.openSettings() : null;
                Promise.resolve(opened).then(function () {
                    if (typeof global.switchSettingsTab === 'function') {
                        global.switchSettingsTab('integrations');
                    }
                });
            }
        });
    });
}(window));
