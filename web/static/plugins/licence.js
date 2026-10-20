/**
 * Licence
 * =============================================================================
 * A source-available licence has terms people need to know before they build on
 * it, and "it's in LICENSE in the repo" is not where somebody running the app
 * will look. This is that file, in plain words, with the parts that actually
 * restrict you first and the obligations we owe other people after.
 *
 * The dependency table is generated from NOTICE rather than retyped, because a
 * licence list that drifts from the shipped artefact is worse than none.
 */

(function () {
    'use strict';

    const CSS = `
    .lc-wrap { max-width: 860px; margin: 0 auto; padding: var(--sp-8, 24px); }
    .lc-head { display: flex; align-items: baseline; gap: var(--sp-4, 14px);
               flex-wrap: wrap; padding-bottom: var(--sp-5, 16px);
               border-bottom: 1px solid var(--border, #e5e7eb); }
    .lc-head h2 { margin: 0; font-size: var(--fs-2xl, 22px);
                  color: var(--text-primary, #111); }
    .lc-pill { font-size: var(--fs-sm, 12px); padding: 3px 9px;
               border-radius: 999px; border: 1px solid var(--border, #e5e7eb);
               color: var(--text-secondary, #666);
               font-family: var(--font-mono, ui-monospace, monospace); }
    .lc-sec { padding: var(--sp-6, 20px) 0;
              border-bottom: 1px solid var(--border, #e5e7eb); }
    .lc-sec:last-child { border-bottom: 0; }
    .lc-sec h3 { margin: 0 0 var(--sp-3, 10px); font-size: var(--fs-md, 14px);
                 letter-spacing: .04em; text-transform: uppercase;
                 color: var(--text-tertiary, #888); font-weight: 600; }
    .lc-sec p { margin: 0 0 var(--sp-3, 10px); line-height: 1.65;
                font-size: var(--fs-md, 14px); color: var(--text-primary, #222); }
    .lc-sec p:last-child { margin-bottom: 0; }
    .lc-grid { display: grid; grid-template-columns: 1fr 1fr;
               gap: var(--sp-4, 14px); margin-top: var(--sp-2, 8px); }
    @media (max-width: 760px) { .lc-grid { grid-template-columns: 1fr; } }
    .lc-card { border: 1px solid var(--border, #e5e7eb);
               border-radius: var(--r-lg, 8px); padding: var(--sp-4, 14px); }
    .lc-card h4 { margin: 0 0 var(--sp-2, 8px); font-size: var(--fs-md, 14px);
                  color: var(--text-primary, #111); }
    .lc-card ul { margin: 0; padding-left: 18px; }
    .lc-card li { margin: 4px 0; line-height: 1.5; font-size: var(--fs-sm, 13px);
                  color: var(--text-secondary, #555); }
    .lc-yes { border-color: rgba(34,150,83,.45); }
    .lc-no  { border-color: rgba(200,60,60,.45); }
    .lc-table { width: 100%; border-collapse: collapse; margin-top: var(--sp-3, 10px);
                font-size: var(--fs-sm, 13px); }
    .lc-table th { text-align: left; padding: 6px 10px 6px 0;
                   color: var(--text-tertiary, #888); font-weight: 600;
                   border-bottom: 1px solid var(--border, #e5e7eb); }
    .lc-table td { padding: 5px 10px 5px 0; vertical-align: top;
                   border-bottom: 1px solid var(--border-subtle, #f0f0f0);
                   color: var(--text-secondary, #555); }
    .lc-table td.n { font-family: var(--font-mono, ui-monospace, monospace);
                     color: var(--text-primary, #222); white-space: nowrap; }
    .lc-copyleft { color: var(--text-primary, #222); font-weight: 600; }
    .lc-links { display: flex; flex-wrap: wrap; gap: var(--sp-3, 10px);
                margin-top: var(--sp-4, 14px); }
    .lc-links a { display: inline-flex; align-items: center; gap: 6px;
                  padding: 7px 12px; border-radius: var(--r-md, 6px);
                  border: 1px solid var(--border, #e5e7eb);
                  color: var(--text-primary, #222);
                  font-size: var(--fs-sm, 13px); text-decoration: none; }
    .lc-links a:hover { background: var(--bg-hover, #f3f4f6); }
    .lc-links a.pri { background: var(--accent, #4f46e5); color: #fff;
                      border-color: var(--accent, #4f46e5); }
    .lc-foot { font-size: var(--fs-sm, 12px); color: var(--text-tertiary, #888);
               line-height: 1.6; }
    `;

    // Mirrors NOTICE. The two MPL-2.0 entries are the only copyleft ones, and
    // certifi is the one that surprised us: it arrives with `requests`, so it
    // is in every install and every image built from this repository.
    const DEPS = [
        ['axe-core', 'MPL-2.0', true, 'bundled, unmodified — every accessibility check'],
        ['certifi', 'MPL-2.0', true, 'via requests — the CA bundle'],
        ['requests · bcrypt · playwright', 'Apache-2.0', false, 'HTTP, password hashing, the browser'],
        ['cryptography', 'Apache-2.0 / BSD-3', false, 'encrypts the stored Google tokens'],
        ['flask · psutil · lxml · markdown', 'BSD-3-Clause', false, 'the app, memory, parsing'],
        ['python-dotenv · nest-asyncio', 'BSD', false, 'configuration, event loops'],
        ['beautifulsoup4 · urllib3 · openpyxl', 'MIT', false, 'parsing, transport, xlsx export'],
        ['flask-compress', 'MIT', false, 'response compression'],
        ['waitress', 'ZPL-2.1', false, 'the production WSGI server'],
    ];

    ToadstonePlugin.register({
        id: 'licence',
        name: 'Licence',
        version: '1.0.0',
        author: 'LabsMedia',
        description: 'What the Elastic License 2.0 lets you do, what it does not, and every third-party licence that travels with this software.',

        tab: { label: 'Licence', iconName: 'shield', position: 'end' },

        onLoad() { this.injectStyles(); },
        onTabActivate(container) { this.injectStyles(); this.render(container); },

        injectStyles() {
            if (document.getElementById('licence-plugin-css')) return;
            const style = document.createElement('style');
            style.id = 'licence-plugin-css';
            style.textContent = CSS;
            document.head.appendChild(style);
        },

        render(container) {
            const rows = DEPS.map(([name, lic, copyleft, why]) => `
                <tr><td class="n">${name}</td>
                    <td class="${copyleft ? 'lc-copyleft' : ''}">${lic}${copyleft ? ' *' : ''}</td>
                    <td>${why}</td></tr>`).join('');

            container.innerHTML = `
            <div class="lc-wrap">
              <div class="lc-head">
                <h2>Licence</h2>
                <span class="lc-pill">Elastic License 2.0</span>
                <span class="lc-pill">source-available</span>
              </div>

              <div class="lc-sec">
                <p>Toadstone is licensed under the <strong>Elastic License 2.0</strong>.
                It is <strong>not</strong> an OSI-approved open-source licence, and saying
                so plainly matters more than the badge looking good.</p>
                <div class="lc-grid">
                  <div class="lc-card lc-yes">
                    <h4>You may</h4>
                    <ul>
                      <li>Use it, privately or commercially</li>
                      <li>Run it on your own sites and your clients' sites</li>
                      <li>Read, modify and build on the source</li>
                      <li>Redistribute it, with these terms attached</li>
                      <li>Self-host as many installs as you like, free</li>
                    </ul>
                  </div>
                  <div class="lc-card lc-no">
                    <h4>You may not</h4>
                    <ul>
                      <li>Offer Toadstone to third parties as a hosted or
                          managed service</li>
                      <li>Remove or obscure these licensing and copyright
                          notices</li>
                      <li>Circumvent licence-key functionality, where any
                          exists</li>
                    </ul>
                  </div>
                </div>
                <p style="margin-top:var(--sp-4,14px)">Running it for your own
                clients as part of your own work is use, not resale, and is fine.
                Selling access to this software itself is what needs a commercial
                licence.</p>
              </div>

              <div class="lc-sec">
                <h3>Upstream: LibreCrawl, MIT</h3>
                <p>Toadstone is a fork of <a href="https://librecrawl.com" target="_blank" rel="noopener">LibreCrawl</a>
                by Phiality, released under the MIT License. Those portions
                <strong>remain MIT</strong>, and MIT's sublicensing grant is what
                permits the combined work to ship under different terms. Nothing
                here takes anything away from upstream: anyone may still take
                LibreCrawl from its own authors under MIT.</p>
                <p class="lc-foot">Toadstone is not affiliated with, endorsed by, or
                supported by Phiality or LibreCrawl. The MIT licence grants no
                trademark rights and we claim none.</p>
              </div>

              <div class="lc-sec">
                <h3>What travels with it</h3>
                <table class="lc-table">
                  <thead><tr><th>Component</th><th>Licence</th><th>What it does here</th></tr></thead>
                  <tbody>${rows}</tbody>
                </table>
                <p class="lc-foot" style="margin-top:var(--sp-3,10px)">
                * File-level copyleft (MPL-2.0). Neither places any condition on
                Toadstone's own code. Both are shipped unmodified. Note that a
                Docker image you publish is a binary redistribution and must carry
                these licence texts with it — they are already inside the image
                under <code>site-packages</code>. The full list, including
                transitive dependencies, is in <code>NOTICE</code>.</p>
              </div>

              <div class="lc-sec">
                <h3>Two files this licence does not cover</h3>
                <p><code>src/core/vendor/axe.min.js</code> is axe-core, bundled
                unmodified under the <strong>MPL-2.0</strong>, and the Elastic
                License does <em>not</em> apply to it — it remains yours under the
                MPL, with the full text shipped beside it. The same goes for
                <code>certifi</code>, which arrives with <code>requests</code>.
                That is not generosity: MPL-2.0 section 3.3 allows a larger work
                to ship under other terms only if the covered files keep their
                own, so saying this is how the condition is met.</p>
              </div>

              <div class="lc-sec">
                <h3>Commercial licence</h3>
                <p>If you want to offer Toadstone to others as a hosted or managed
                service, that is the one thing ELv2 reserves — and it is available
                under a separate commercial licence. The licensor is Space-O
                Technologies.</p>
                <div class="lc-links">
                  <a class="pri" href="https://www.labsmedia.com/contact/" target="_blank" rel="noopener">
                    <i data-icon="user" data-icon-size="13"></i>Ask about a commercial licence</a>
                  <a href="https://www.elastic.co/licensing/elastic-license" target="_blank" rel="noopener">
                    <i data-icon="file-text" data-icon-size="13"></i>Read ELv2 in full</a>
                  <a href="https://github.com/spcaeo/toadstone/blob/main/LICENSE" target="_blank" rel="noopener">
                    <i data-icon="code" data-icon-size="13"></i>LICENSE in the repo</a>
                  <a href="https://github.com/spcaeo/toadstone/blob/main/NOTICE" target="_blank" rel="noopener">
                    <i data-icon="list-checks" data-icon-size="13"></i>NOTICE</a>
                </div>
              </div>

              <div class="lc-sec">
                <p class="lc-foot">Other products named anywhere in this project's
                documentation — Screaming Frog, Sitebulb, Ahrefs, Semrush, Google and
                the rest — are trademarks of their respective owners, used only to
                identify what is being compared. Toadstone is not affiliated with,
                endorsed by or sponsored by any of them.</p>
                <p class="lc-foot">Copyright 2026 Space-O Technologies. Author: Rakesh Patel.
                Built by <a href="https://www.labsmedia.com/" target="_blank" rel="noopener">LabsMedia</a>,
                the digital marketing division of
                <a href="https://www.spaceo.ca/" target="_blank" rel="noopener">Space-O Technologies</a>.
                This panel is a summary written to be read; where it and
                <code>LICENSE</code> differ, <code>LICENSE</code> governs.</p>
              </div>
            </div>`;
            if (typeof window.renderIcons === 'function') window.renderIcons(container);
        },
    });
})();
