/**
 * About Toadstone
 * =============================================================================
 * The story, in the app rather than only in the README — because the people who
 * end up reading a crawler's findings are rarely the ones who cloned it, and the
 * name is a warning aimed at this tool itself.
 *
 * Partly live: the coverage line asks this install's own catalogue how many
 * distinct checks it has actually reported, so the claim on screen is about
 * your crawls rather than about somebody's marketing.
 */

(function () {
    'use strict';

    const LINKS = {
        repo: 'https://github.com/spcaeo/toadstone',
        labsmedia: 'https://www.labsmedia.com/',
        spaceo: 'https://www.spaceo.ca/',
        contact: 'https://www.labsmedia.com/contact/',
        upstream: 'https://librecrawl.com',
        licence: 'https://www.elastic.co/licensing/elastic-license',
    };

    const CSS = `
    .ab-wrap { max-width: 860px; margin: 0 auto; padding: var(--sp-8, 24px); }
    .ab-hero { display: flex; gap: var(--sp-6, 20px); align-items: flex-start;
               padding-bottom: var(--sp-6, 20px);
               border-bottom: 1px solid var(--border, #e5e7eb); }
    .ab-mark { flex: 0 0 auto; width: 56px; height: 56px; }
    .ab-mark .t { fill: var(--accent, #4f46e5); }
    .ab-mark .e { fill: var(--bg-base, #fff); }
    .ab-mark .s { fill: #e0b254; }
    .ab-hero h2 { margin: 0 0 4px; font-size: var(--fs-2xl, 22px);
                  color: var(--text-primary, #111); }
    .ab-tag { margin: 0; color: var(--text-secondary, #666);
              font-size: var(--fs-md, 14px); line-height: 1.55; }
    .ab-sec { padding: var(--sp-6, 20px) 0;
              border-bottom: 1px solid var(--border, #e5e7eb); }
    .ab-sec:last-child { border-bottom: 0; }
    .ab-sec h3 { margin: 0 0 var(--sp-3, 10px); font-size: var(--fs-md, 14px);
                 letter-spacing: .04em; text-transform: uppercase;
                 color: var(--text-tertiary, #888); font-weight: 600; }
    .ab-sec p { margin: 0 0 var(--sp-4, 14px); line-height: 1.65;
                color: var(--text-primary, #222); font-size: var(--fs-md, 14px); }
    .ab-sec p:last-child { margin-bottom: 0; }
    .ab-pull { border-left: 3px solid var(--accent, #4f46e5);
               padding: 2px 0 2px var(--sp-4, 14px); margin: var(--sp-5, 16px) 0;
               color: var(--text-primary, #222); font-size: var(--fs-md, 14px);
               line-height: 1.6; }
    .ab-nums { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
               gap: var(--sp-4, 14px); margin-top: var(--sp-4, 14px); }
    .ab-num { border: 1px solid var(--border, #e5e7eb);
              border-radius: var(--r-lg, 8px); padding: var(--sp-4, 14px); }
    .ab-num b { display: block; font-size: 24px; line-height: 1.1;
                color: var(--text-primary, #111);
                font-family: var(--font-mono, ui-monospace, monospace); }
    .ab-num span { display: block; margin-top: 4px; font-size: var(--fs-sm, 12px);
                   color: var(--text-secondary, #666); line-height: 1.45; }
    .ab-links { display: flex; flex-wrap: wrap; gap: var(--sp-3, 10px);
                margin-top: var(--sp-4, 14px); }
    .ab-links a { display: inline-flex; align-items: center; gap: 6px;
                  padding: 7px 12px; border-radius: var(--r-md, 6px);
                  border: 1px solid var(--border, #e5e7eb);
                  color: var(--text-primary, #222); font-size: var(--fs-sm, 13px);
                  text-decoration: none; }
    .ab-links a:hover { background: var(--bg-hover, #f3f4f6);
                        border-color: var(--accent-border, #c7d2fe); }
    .ab-links a.pri { background: var(--accent, #4f46e5); color: #fff;
                      border-color: var(--accent, #4f46e5); }
    .ab-links a.pri:hover { background: var(--accent-hover, #4338ca); }
    .ab-foot { padding-top: var(--sp-5, 16px); font-size: var(--fs-sm, 12px);
               color: var(--text-tertiary, #888); line-height: 1.6; }
    .ab-foot a { color: var(--accent-text, #4338ca); }
    `;

    const MARK = `
    <svg class="ab-mark" viewBox="0 0 32 32" aria-hidden="true">
      <g transform="rotate(4 9 25)">
        <path class="t" d="M2.6 25 q0 -8.2 6.4 -8.2 q6.4 0 6.4 8.2 z"/>
        <circle class="t" cx="5.9" cy="16.6" r="2.5"/><circle class="t" cx="11.5" cy="16.2" r="2.5"/>
        <circle class="e" cx="5.9" cy="16.3" r="0.95"/><circle class="e" cx="11.5" cy="15.9" r="0.95"/>
        <path class="e" d="M4.3 21.3 q4.5 2.5 9 0 q-4.5 0.9 -9 0 z"/>
      </g>
      <g transform="rotate(-4 23 25)">
        <path class="t" d="M16.6 25 q0 -8.2 6.4 -8.2 q6.4 0 6.4 8.2 z"/>
        <circle class="t" cx="20.5" cy="16.2" r="2.5"/><circle class="t" cx="26.1" cy="16.6" r="2.5"/>
        <circle class="e" cx="20.5" cy="15.9" r="0.95"/><circle class="e" cx="26.1" cy="16.3" r="0.95"/>
        <path class="e" d="M18.7 21.3 q4.5 2.5 9 0 q-4.5 0.9 -9 0 z"/>
      </g>
      <path class="s" d="M16 4.6 l3.1 3.6 l-3.1 3.6 l-3.1 -3.6 z"/>
    </svg>`;

    ToadstonePlugin.register({
        id: 'about',
        name: 'About Toadstone',
        version: '1.0.0',
        author: 'LabsMedia',
        description: 'What a toadstone was, why this tool is named after one, and who built it.',

        tab: { label: 'About', iconName: 'info', position: 'end' },

        onLoad() { this.injectStyles(); },

        onTabActivate(container) {
            this.injectStyles();
            this.render(container);
            this.loadCoverage(container);
        },

        injectStyles() {
            if (document.getElementById('about-plugin-css')) return;
            const style = document.createElement('style');
            style.id = 'about-plugin-css';
            style.textContent = CSS;
            document.head.appendChild(style);
        },

        /**
         * The counts come from this install, not from a constant: how many
         * distinct checks it has ever reported, and how many pages they hit.
         * A story about proving things should not itself be unproven.
         */
        async loadCoverage(container) {
            const slot = container.querySelector('#ab-live');
            if (!slot) return;
            try {
                const response = await fetch('/api/checks');
                const body = await response.json();
                const checks = (body && body.checks) || [];
                if (!checks.length) {
                    slot.innerHTML = '<span>Run a crawl and this fills in with what '
                                   + 'your own install has seen fire.</span>';
                    return;
                }
                const pages = checks.reduce((n, c) => n + (c.pages || 0), 0);
                const errors = checks.filter(c => c.severity === 'error').length;
                slot.innerHTML = `
                    <div class="ab-num"><b>${checks.length}</b>
                      <span>distinct checks this install has actually reported</span></div>
                    <div class="ab-num"><b>${errors}</b>
                      <span>of them at error severity</span></div>
                    <div class="ab-num"><b>${pages.toLocaleString()}</b>
                      <span>pages they have fired on here</span></div>`;
            } catch (e) {
                slot.innerHTML = '<span>Could not read this install\'s check '
                               + 'catalogue just now.</span>';
            }
        },

        render(container) {
            container.innerHTML = `
            <div class="ab-wrap">
              <div class="ab-hero">
                ${MARK}
                <div>
                  <h2>Toadstone</h2>
                  <p class="ab-tag">A toadstone was a gem people wore to detect poison.
                  It never detected anything. This one is named after that, on purpose.</p>
                </div>
              </div>

              <div class="ab-sec">
                <h3>The name</h3>
                <p>For about five hundred years, educated people across Europe believed a
                jewel grew inside the head of an old toad. Pliny mentions it; so does
                Shakespeare. You were supposed to get one by laying a toad on a red cloth
                until it gave the stone up. They were set into rings and worn, and they had
                one job: <strong>held near something harmful, a toadstone was said to grow
                warm, sweat, or change colour.</strong> You wore it so that you would know.</p>
                <p>The stones were real objects. They were sold, inherited, listed in wills,
                and they turn up in museum collections today. They are the fossilised tooth
                plates of <em>Lepidotes</em>, a Jurassic fish — button-shaped, domed, faintly
                polished, and nothing whatever to do with toads. They never detected
                anything. People wore them for centuries, and felt safer, and were not.</p>
              </div>

              <div class="ab-sec">
                <h3>Why that is the name of a crawler</h3>
                <p>A crawler is a detector. You point it at a site and it tells you what is
                wrong, and the entire value of it rests on one assumption: that when it says
                nothing is wrong, nothing is wrong.</p>
                <p>On the day this fork was named, somebody measured that assumption for the
                first time. The project had around 225 checks. <strong>Eighty-four of them
                had ever been observed to fire.</strong> The rest passed their unit tests and
                sat inert — eight AMP checks reading markup that was discarded before they
                ran, every PDF check dead the moment JavaScript rendering was switched on, a
                raw-versus-rendered comparison quietly comparing a document against itself,
                and a failed PageSpeed call that reported as a clean bill of performance.</p>
                <div class="ab-pull">None of that looked broken. Every report came back clean
                and confident, and clean was the wrong answer. The tool was a fossil fish
                tooth in a ring: worn in good faith, detecting nothing, and all the more
                dangerous for how reassuring it was.</div>
                <p>So there is now a site in <code>tests/</code> built to be wrong in as many
                ways as possible, crawled by the real crawler, recording which checks actually
                fire. That number is published, including the parts that are stubbed and the
                checks that cannot honestly be reached. A detector that has never been shown
                to detect is just jewellery.</p>
              </div>

              <div class="ab-sec">
                <h3>On this install</h3>
                <div class="ab-nums" id="ab-live">
                  <div class="ab-num"><b>…</b><span>reading this install's catalogue</span></div>
                </div>
              </div>

              <div class="ab-sec">
                <h3>Who built it</h3>
                <p>Toadstone is built and maintained by
                <a href="${LINKS.labsmedia}" target="_blank" rel="noopener">LabsMedia</a>,
                the digital marketing division of
                <a href="${LINKS.spaceo}" target="_blank" rel="noopener">Space-O Technologies</a>,
                and led by Rakesh Patel, founder of both.</p>
                <p>We build links and run SEO for other people's sites, which means we spend
                our days reading crawl reports and betting client money on whether they are
                true. This fork started because a report came back clean on a site we already
                knew was broken.</p>
                <div class="ab-links">
                  <a class="pri" href="${LINKS.labsmedia}" target="_blank" rel="noopener">
                    <i data-icon="external-link" data-icon-size="13"></i>LabsMedia — AI link building</a>
                  <a href="${LINKS.contact}" target="_blank" rel="noopener">
                    <i data-icon="user" data-icon-size="13"></i>Talk to us</a>
                  <a href="${LINKS.spaceo}" target="_blank" rel="noopener">
                    <i data-icon="globe" data-icon-size="13"></i>Space-O Technologies</a>
                  <a href="${LINKS.repo}" target="_blank" rel="noopener">
                    <i data-icon="code" data-icon-size="13"></i>Source on GitHub</a>
                </div>
              </div>

              <div class="ab-sec">
                <h3>Licence and credit</h3>
                <p class="ab-foot">Toadstone is a fork of
                <a href="${LINKS.upstream}" target="_blank" rel="noopener">LibreCrawl</a>
                by Phiality, released under the MIT License — which is also what permits this
                work to be distributed under different terms. Upstream stays MIT, and a solo
                developer's work is the reason this project exists at all.<br><br>
                Toadstone itself is licensed under the
                <a href="${LINKS.licence}" target="_blank" rel="noopener">Elastic License 2.0</a>:
                source-available, not OSI open source. Self-host it, modify it, use it on your
                own and your clients' sites; you may not offer it to third parties as a hosted
                service without a commercial licence. Accessibility checks are
                <a href="https://github.com/dequelabs/axe-core" target="_blank" rel="noopener">axe-core</a>
                by Deque Systems (MPL-2.0), bundled unmodified.<br><br>
                Copyright 2026 Space-O Technologies. Author: Rakesh Patel.</p>
              </div>
            </div>`;
            // The placeholders are <i data-icon>; this swaps them for SVG.
            // The global is renderIcons (see web/static/js/icons.js) - calling
            // a plausible-looking window.Icons.render instead fails silently
            // and you get buttons with no icons and no error.
            if (typeof window.renderIcons === 'function') window.renderIcons(container);
        },
    });
})();
