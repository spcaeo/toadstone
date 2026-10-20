/**
 * E-E-A-T Analyzer for Toadstone
 * =============================================================================
 * Scores Experience, Expertise, Authoritativeness and Trust SEPARATELY, from the
 * data the crawler actually stores, and shows the evidence behind every number
 * so you can argue with it. Each signal scores yes (full weight), partial (half)
 * or no (zero) and keeps its reason, so a page's score is a list of findings
 * rather than a number.
 *
 * WHAT EACH PILLAR MEASURES
 * Experience (15% of overall) - evidence a human with first-hand involvement
 *   made this page. Hardest pillar to see from markup, so it carries the LOWEST
 *   weight: a low score means "no evidence found", not "the author has none".
 * Expertise (25%) - subject knowledge and sourced claims: author credentials,
 *   cited sources, a structured argument, content-bearing schema.
 * Authoritativeness (25%) - whether the site and the entity behind it vouch for
 *   this page: editorial inbound links, a publisher, an author profile, a place
 *   in the hierarchy.
 * Trust (35%) - HTTPS, indexability, canonical, dates, freshness, and whether
 *   the site has contact/about/policy pages. Weighted highest because Google
 *   treats it as the most important and a crawler can measure it most fully.
 *
 * HOW THE WEIGHTS WERE CHOSEN
 * 1. Evidence strength sets the weight inside a pillar: `strong` when presence
 *    is hard to fake and means what it says (a resolvable author with a bio),
 *    `moderate` when it is good evidence with exceptions (cited sources), `weak`
 *    when it only correlates (word count, first-person phrasing). Weak signals
 *    are capped low AND labelled "weak" in the UI, so nobody mistakes them for
 *    proof.
 * 2. Nothing scores for things that are not evidence. Length is not expertise
 *    (15/100 of that pillar, as "necessary, not sufficient"); "some JSON-LD" is
 *    not structured data - the type has to describe the content; a footer link
 *    is not a citation, so only links marked `in_main` count.
 * 3. Signals that cannot apply to a page are dropped from BOTH sides of the
 *    fraction rather than scored 0 - a privacy policy is not penalised for
 *    having no author - and the page kind (article / page / listing / utility /
 *    profile) is shown next to every score.
 * 4. Site-level signals (organisation identity, contact/about/policy pages) are
 *    computed once and applied to every page: they belong to the publisher.
 *
 * DATA USED, all from the crawler's own records: json_ld (parsed or raw string),
 * schema_org, meta_tags, og_tags, headings, main_text, main_word_count, images,
 * robots, canonical_url, status_code, content_type, linked_from, last_modified,
 * and the link records' in_main / is_internal / target_domain.
 *
 * @author Toadstone Community
 * @version 2.0.0
 */

(function () {
    'use strict';

    // Everything the plugin draws is styled from the app's design tokens, in
    // one stylesheet injected once. No hardcoded colours, so both themes work.
    const PLUGIN_CSS = `
.eeat { flex:1 1 auto; min-height:0; overflow-y:auto; padding:var(--sp-6); display:flex; flex-direction:column; gap:var(--sp-7); }
.eeat-head h2 { margin:0 0 var(--sp-2); font-size:var(--fs-xl); line-height:var(--lh-xl); font-weight:var(--fw-semibold); color:var(--text-primary); } .eeat-head p { margin:0; font-size:var(--fs-md); line-height:var(--lh-md); color:var(--text-secondary); }
.eeat-note { font-size:var(--fs-xs); line-height:var(--lh-xs); color:var(--text-tertiary); } .eeat-section { display:flex; flex-direction:column; gap:var(--sp-5); } .eeat-section-head { display:flex; align-items:baseline; gap:var(--sp-5); flex-wrap:wrap; }
.eeat-section-head h3 { margin:0; font-size:var(--fs-lg); line-height:var(--lh-lg); font-weight:var(--fw-semibold); color:var(--text-primary); } .eeat-panel { background:var(--bg-surface); border:1px solid var(--border); border-radius:var(--r-lg); }
.eeat-panel-pad { padding:var(--sp-5) var(--sp-6); } .eeat-tile .eeat-tile-sub { font-size:var(--fs-xs); line-height:var(--lh-xs); color:var(--text-tertiary); } .eeat-kpi .stat-tile-value { font-size:var(--fs-3xl); line-height:var(--lh-3xl); }
.eeat-chips { display:flex; flex-wrap:wrap; gap:var(--sp-3); }
/* quality ramp - the only colour decision in the plugin, always from tokens */
.eeat-meter { height:4px; border-radius:var(--r-full); background:var(--bg-active); overflow:hidden; margin-top:var(--sp-2); } .eeat-meter > span { display:block; height:100%; border-radius:var(--r-full); background:var(--muted); }
.eeat-q-good { color:var(--ok); } .eeat-q-ok { color:var(--warn); } .eeat-q-bad { color:var(--danger); } .eeat-q-good > span { background:var(--ok); } .eeat-q-ok > span { background:var(--warn); } .eeat-q-bad > span { background:var(--danger); }
.eeat-strength { display:inline-flex; align-items:center; gap:var(--sp-2); font-size:var(--fs-2xs); line-height:var(--lh-2xs); text-transform:uppercase; letter-spacing:var(--tracking-caps); color:var(--text-tertiary); white-space:nowrap; }
.eeat-strength::before { content:''; width:6px; height:6px; border-radius:var(--r-full); background:currentColor; } .eeat-strength.is-strong { color:var(--text-secondary); } .eeat-strength.is-weak { opacity:.75; }
/* tables */
.eeat-table-wrap { overflow-x:auto; border:1px solid var(--border); border-radius:var(--r-lg); background:var(--bg-surface); } .eeat-table { width:100%; border-collapse:separate; border-spacing:0; min-width:860px; }
.eeat-table thead th { height:var(--h-thead); padding:0 var(--row-pad-x); text-align:left; background:var(--bg-surface); border-bottom:1px solid var(--border); font-size:var(--fs-2xs); line-height:var(--lh-2xs); text-transform:uppercase; letter-spacing:var(--tracking-caps); font-weight:var(--fw-semibold); color:var(--text-tertiary); white-space:nowrap; }
.eeat-table tbody td { padding:var(--sp-4) var(--row-pad-x); border-bottom:1px solid var(--border-subtle); font-size:var(--fs-sm); line-height:var(--lh-sm); color:var(--text-primary); vertical-align:middle; }
.eeat-table tbody tr:last-child td { border-bottom:0; } .eeat-table .eeat-num { font-family:var(--font-mono); text-align:right; white-space:nowrap; }
.eeat-table .eeat-gaps { max-width:320px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.eeat-table .eeat-url { font-family:var(--font-mono); font-size:var(--fs-sm); max-width:420px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .eeat-row { cursor:pointer; } .eeat-row:focus-visible { outline:none; }
.eeat-row:hover > td { background:var(--bg-hover); } .eeat-row:focus-visible > td { background:var(--bg-selected); } .eeat-row[aria-expanded="true"] > td { background:var(--bg-active); }
.eeat-row .eeat-caret { display:inline-block; width:12px; color:var(--text-tertiary); transition:transform var(--dur) var(--ease); } .eeat-row[aria-expanded="true"] .eeat-caret { transform:rotate(90deg); }
/* per-page signal breakdown */
.eeat-detail > td { background:var(--bg-sunken); padding:var(--sp-5) var(--sp-6) var(--sp-6); } .eeat-detail-url { margin-bottom:var(--sp-4); overflow-wrap:anywhere; }
.eeat-detail-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); gap:var(--sp-5); }
.eeat-detail-col h4 { margin:0 0 var(--sp-3); font-size:var(--fs-2xs); line-height:var(--lh-2xs); text-transform:uppercase; letter-spacing:var(--tracking-caps); color:var(--text-tertiary); font-weight:var(--fw-semibold); display:flex; justify-content:space-between; gap:var(--sp-4); }
.eeat-sig { display:flex; gap:var(--sp-4); align-items:flex-start; padding:var(--sp-3) 0; border-top:1px solid var(--border-subtle); font-size:var(--fs-sm); line-height:var(--lh-sm); } .eeat-sig:first-of-type { border-top:0; }
.eeat-sig-mark { flex:0 0 auto; width:14px; text-align:center; font-family:var(--font-mono); font-weight:var(--fw-bold); }
.eeat-sig-mark.is-yes { color:var(--ok); } .eeat-sig-mark.is-partial { color:var(--warn); } .eeat-sig-mark.is-no { color:var(--danger); } .eeat-sig-mark.is-na { color:var(--text-disabled); }
.eeat-sig-body { min-width:0; } .eeat-sig-label { color:var(--text-primary); } .eeat-sig.is-na .eeat-sig-label, .eeat-sig.is-na .eeat-sig-note { color:var(--text-tertiary); }
.eeat-sig-note { display:block; color:var(--text-secondary); font-size:var(--fs-xs); line-height:var(--lh-xs); overflow-wrap:anywhere; }
.eeat-sig-w { flex:0 0 auto; margin-left:auto; font-family:var(--font-mono); font-size:var(--fs-2xs); color:var(--text-tertiary); }
/* controls, coverage, recommendations, method */
.eeat-seg { display:inline-flex; border:1px solid var(--border); border-radius:var(--r-md); overflow:hidden; }
.eeat-seg button { height:var(--ctl-sm); padding:0 var(--sp-5); border:0; border-left:1px solid var(--border); background:transparent; color:var(--text-secondary); font-family:inherit; font-size:var(--fs-xs); cursor:pointer; }
.eeat-seg button:first-child { border-left:0; } .eeat-seg button:hover { background:var(--bg-hover); color:var(--text-primary); } .eeat-seg button[aria-pressed="true"] { background:var(--bg-active); color:var(--text-primary); font-weight:var(--fw-medium); }
.eeat-seg button:focus-visible { outline:none; box-shadow:var(--focus-ring); } .eeat-cov { display:grid; grid-template-columns:minmax(200px,1.4fr) auto minmax(120px,1fr) 64px; gap:var(--sp-4) var(--sp-5); align-items:center; }
.eeat-cov-label { font-size:var(--fs-sm); line-height:var(--lh-sm); color:var(--text-primary); } .eeat-cov-label small { display:block; color:var(--text-tertiary); font-size:var(--fs-xs); line-height:var(--lh-xs); }
.eeat-cov-count { font-family:var(--font-mono); font-size:var(--fs-xs); color:var(--text-tertiary); text-align:right; white-space:nowrap; } .eeat-rec { display:flex; gap:var(--sp-5); padding:var(--sp-5) var(--sp-6); border-top:1px solid var(--border-subtle); }
.eeat-rec:first-child { border-top:0; } .eeat-rec-body { min-width:0; } .eeat-rec-title { font-size:var(--fs-md); line-height:var(--lh-md); font-weight:var(--fw-medium); color:var(--text-primary); }
.eeat-rec-desc { margin-top:var(--sp-2); font-size:var(--fs-sm); line-height:var(--lh-sm); color:var(--text-secondary); } .eeat-method summary { cursor:pointer; font-size:var(--fs-sm); color:var(--text-secondary); padding:var(--sp-3) 0; }
.eeat-method summary:hover { color:var(--text-primary); } .eeat-method[open] summary { margin-bottom:var(--sp-4); } .eeat-method table { width:100%; border-collapse:collapse; }
.eeat-method th, .eeat-method td { text-align:left; padding:var(--sp-3) var(--sp-5); border-bottom:1px solid var(--border-subtle); font-size:var(--fs-sm); line-height:var(--lh-sm); color:var(--text-secondary); vertical-align:top; }
.eeat-method th { color:var(--text-tertiary); font-size:var(--fs-2xs); text-transform:uppercase; letter-spacing:var(--tracking-caps); font-weight:var(--fw-semibold); } .eeat-method td:first-child { color:var(--text-primary); }
@media (max-width:900px) { .eeat { padding:var(--sp-5); } .eeat-cov { grid-template-columns:1fr auto; } .eeat-cov > .eeat-meter { grid-column:1 / -1; } }
`;

    const ARTICLE_TYPES = ['Article', 'BlogPosting', 'NewsArticle', 'TechArticle', 'ScholarlyArticle',
        'Report', 'HowTo', 'Recipe', 'Review', 'FAQPage', 'QAPage', 'Course', 'VideoObject',
        'Service', 'Product', 'SoftwareApplication'];
    const ORG_TYPES = ['Organization', 'Corporation', 'LocalBusiness', 'ProfessionalService',
        'OnlineBusiness', 'NGO', 'EducationalOrganization', 'GovernmentOrganization'];
    // Share widgets and social buttons live in main content but cite nothing.
    const NON_CITATION_HOSTS = ['facebook.com', 'twitter.com', 'x.com', 'linkedin.com', 'pinterest.com',
        'instagram.com', 'youtube.com', 'wa.me', 'whatsapp.com', 't.me', 'reddit.com', 'tiktok.com',
        'gravatar.com', 'googletagmanager.com', 'google-analytics.com', 'doubleclick.net'];
    const FIRST_HAND = [
        /\b(?:we|our team|our engineers|our developers)\s+(?:have\s+)?(?:built|tested|shipped|measured|ran|deployed|migrated|interviewed|audited|implemented|benchmarked|rebuilt|designed|delivered)\b/i,
        /\bin our (?:experience|testing|work|projects?)\b/i,
        /\bwe(?:'ve| have)\s+(?:seen|found|worked|spent|helped|delivered)\b/i,
        /\bi\s+(?:tested|built|ran|measured|tried|interviewed|spent)\b/i,
        /\b(?:case study|when we worked with|a client asked us|on this project)\b/i
    ];
    const MAX_PAGES = 5000;          // scoring cost guard on very large crawls
    // How much main_text is scanned for a byline / first-hand phrasing. Small
    // crawls get a deep read; big ones stay responsive on the top of the page.
    const TEXT_SCAN_CHARS = 20000, TEXT_SCAN_CHARS_LARGE = 6000, LARGE_CRAWL = 500;
    const STALE_MONTHS = 24;
    // ---------------------------------------------------------------- signals
    // weight = share of that pillar's 100 points. strength = how much the
    // signal actually proves; weak signals are capped low and say so.
    const PILLARS = [
        { key: 'experience', label: 'Experience', short: 'Experience', weight: 0.15,
          blurb: 'Evidence a human with first-hand involvement made the page. Hardest to see from markup, so it counts least.' },
        { key: 'expertise', label: 'Expertise', short: 'Expertise', weight: 0.25,
          blurb: 'Subject knowledge and sourced claims: credentials, citations, a structured argument.' },
        { key: 'authority', label: 'Authoritativeness', short: 'Authority', weight: 0.25,
          blurb: 'Whether the site and its publisher vouch for this page: editorial links in, a publisher identity, a place in the hierarchy.' },
        { key: 'trust', label: 'Trust', short: 'Trust', weight: 0.35,
          blurb: 'The pillar a crawler can measure almost completely, and the one Google weights highest.' }
    ];

    const SIGNALS = [
        // --- Experience -----
        { key: 'author_identified', pillar: 'experience', weight: 30, strength: 'strong', label: 'Author is named',
          why: 'A resolvable author in JSON-LD (author / creator / reviewedBy, incl. @graph refs), a Person node, an author meta tag, or a visible byline in the main text.', skip: ['utility', 'listing', 'home'],
          test: f => f.author ? { state: 'yes', note: f.author.name + ' - ' + f.author.source } : { state: 'no', note: 'No author in JSON-LD, meta tags or a byline in the main text' } },
        { key: 'author_bio', pillar: 'experience', weight: 25, strength: 'strong', label: 'Author has a bio or profile',
          why: 'The named author resolves to a Person carrying a description, url or sameAs - somebody you can check, not just a string.', skip: ['utility', 'listing', 'home'],
          test: f => {
              if (!f.author) return { state: 'no', note: 'No author to profile' };
              if (f.authorBioParts.length) return { state: 'yes', note: 'Person node carries ' + f.authorBioParts.join(', ') };
              return { state: 'no', note: 'Author is a bare name with no Person node, bio or profile URL' };
          } },
        { key: 'first_hand', pillar: 'experience', weight: 20, strength: 'weak', label: 'First-hand language in the main text',
          why: 'Phrases like "we built", "in our experience", "we tested". Weak evidence - phrasing is cheap - so it is capped at a fifth of the pillar.', skip: ['utility', 'listing'],
          test: f => f.firstHand >= 2 ? { state: 'yes', note: f.firstHand + ' first-hand phrases in the main content' }
              : f.firstHand === 1 ? { state: 'partial', note: 'One first-hand phrase only' } : { state: 'no', note: 'Main text never describes doing the thing first-hand' } },
        { key: 'own_media', pillar: 'experience', weight: 15, strength: 'weak', label: 'Original media on the page',
          why: 'Three or more images served from this site\'s own domain. Weak - self-hosting a stock photo looks the same from outside.', skip: ['utility', 'listing'],
          test: f => f.ownImages >= 3 ? { state: 'yes', note: f.ownImages + ' images on this domain (of ' + f.totalImages + ')' }
              : f.ownImages >= 1 ? { state: 'partial', note: f.ownImages + ' own image(s) only' }
              : { state: 'no', note: f.totalImages ? 'All ' + f.totalImages + ' images are third-party' : 'No images' } },
        { key: 'reviewed_by', pillar: 'experience', weight: 10, strength: 'moderate', label: 'Reviewed / fact-checked by a named person',
          why: 'A reviewedBy, reviewer or Review node naming a person - the clearest machine-readable claim of review.', skip: ['utility', 'listing', 'home'],
          test: f => f.reviewer ? { state: 'yes', note: 'Reviewed by ' + f.reviewer } : { state: 'no', note: 'No reviewedBy / reviewer in the structured data' } },
        // --- Expertise -----
        { key: 'credentials', pillar: 'expertise', weight: 25, strength: 'strong', label: 'Author credentials are stated',
          why: 'The author\'s Person node carries jobTitle, worksFor, knowsAbout, honorificSuffix or sameAs - a checkable claim of standing.', skip: ['utility', 'listing', 'home'],
          test: f => {
              if (!f.author) return { state: 'no', note: 'No author, so no credentials' };
              if (f.credentialParts.length) return { state: 'yes', note: f.credentialParts.join(', ') };
              if (f.authorBioParts.length) return { state: 'partial', note: 'A bio, but no jobTitle / worksFor / sameAs' };
              return { state: 'no', note: 'Nothing states who the author is or why they would know' };
          } },
        { key: 'citations', pillar: 'expertise', weight: 25, strength: 'moderate', label: 'Cites outside sources from the main content',
          why: 'Distinct external domains linked from the page body (link records with in_main). Navigation, footer and share buttons are excluded, so this counts citations rather than chrome.', skip: ['utility', 'listing'],
          test: f => f.citationDomains.length >= 2 ? { state: 'yes', note: f.citationDomains.length + ' domains cited: ' + f.citationDomains.slice(0, 4).join(', ') }
              : f.citationDomains.length === 1 ? { state: 'partial', note: 'One source only: ' + f.citationDomains[0] }
              : { state: 'no', note: f.mainExternalRaw ? 'Only share / social links in the body' : 'No outbound links from the main content' } },
        { key: 'structure', pillar: 'expertise', weight: 20, strength: 'moderate', label: 'Headings structure the argument',
          why: 'One visible H1, no skipped heading levels, and enough subheads for the length. A page that cannot be outlined rarely explains anything.', skip: ['utility'],
          test: f => {
              const problems = f.headingProblems;
              if (!f.headingCount) return { state: 'no', note: 'No headings at all' };
              if (!problems.length) return { state: 'yes', note: f.headingCount + ' headings, one H1, no level skips' };
              return { state: problems.length === 1 ? 'partial' : 'no', note: problems.join('; ') };
          } },
        { key: 'content_schema', pillar: 'expertise', weight: 15, strength: 'moderate', label: 'Structured data describes the content',
          why: 'A content-bearing type (Article, BlogPosting, HowTo, FAQPage, Service, Product...), not merely "some JSON-LD on the page".', skip: [],
          test: f => f.contentTypes.length ? { state: 'yes', note: f.contentTypes.join(', ') }
              : f.allTypes.length ? { state: 'partial', note: 'Only ' + f.allTypes.slice(0, 4).join(', ') + ' - nothing describing the content' } : { state: 'no', note: 'No JSON-LD or microdata' } },
        { key: 'depth', pillar: 'expertise', weight: 15, strength: 'weak', label: 'Enough main content to answer the question',
          why: 'main_word_count (body text, chrome excluded). Length is necessary, never sufficient - which is why it is the smallest slice of this pillar.', skip: ['utility', 'listing'],
          test: f => f.mainWords >= 900 ? { state: 'yes', note: f.mainWords + ' words of body text' }
              : f.mainWords >= 300 ? { state: 'partial', note: f.mainWords + ' words - thin for a primary page' } : { state: 'no', note: f.mainWords + ' words of body text' } },
        // --- Authoritativeness -----
        { key: 'editorial_inbound', pillar: 'authority', weight: 25, strength: 'moderate', label: 'Other pages cite it in their body text',
          why: 'Internal links pointing here from another page\'s main content. Template links in a menu say nothing about a page; an editorial link is a vote.', skip: [],
          test: f => f.inboundMain >= 2 ? { state: 'yes', note: f.inboundMain + ' editorial links from ' + f.inboundMainSources + ' page(s)' }
              : f.inboundMain === 1 ? { state: 'partial', note: 'One editorial link in' }
              : { state: 'no', note: f.inboundAny ? 'Only template / navigation links in' : 'Nothing on the site links here' } },
        { key: 'publisher', pillar: 'authority', weight: 20, strength: 'moderate', label: 'A publisher is bound to the page',
          why: 'The page\'s structured data names a publisher, or an Organization node is part of its graph.', skip: [],
          test: f => f.publisher ? { state: 'yes', note: 'Published by ' + f.publisher } : { state: 'no', note: 'No publisher / Organization in the page graph' } },
        { key: 'org_identity', pillar: 'authority', weight: 15, strength: 'strong', siteLevel: true,
          label: 'The organisation is identifiable site-wide',
          why: 'Somewhere on the site, an Organization node carries sameAs profiles (and ideally a logo and contact point). Site-level: it lifts or limits every page equally.', skip: [],
          test: (f, site) => site.org && site.org.sameAs.length ? { state: 'yes', note: site.org.name + ' with ' + site.org.sameAs.length + ' sameAs profile(s)' + (site.org.extras.length ? ', ' + site.org.extras.join(', ') : '') }
              : site.org ? { state: 'partial', note: site.org.name + ' is declared but has no sameAs profiles' } : { state: 'no', note: 'No Organization node anywhere in the crawl' } },
        { key: 'author_page', pillar: 'authority', weight: 15, strength: 'moderate', label: 'The author has a profile page on this site',
          why: 'The author\'s URL resolves to a page we actually crawled, or the site has author/team pages. A byline that goes nowhere is not attribution.', skip: ['utility', 'listing', 'home'],
          test: (f, site) => {
              if (f.authorPageOnSite) return { state: 'yes', note: 'Profile: ' + f.authorPageOnSite };
              if (!f.author) return { state: 'no', note: 'No author to link to' };
              if (site.authorPages.length) return { state: 'partial', note: 'The site has author pages, but this byline does not link to one' };
              return { state: 'no', note: 'No author or team page found in the crawl' };
          } },
        { key: 'breadcrumbs', pillar: 'authority', weight: 15, strength: 'weak', label: 'Placed in the site hierarchy',
          why: 'A BreadcrumbList (or a crawl depth of 1) showing where the page sits. Weak on its own - it is context, not endorsement.', skip: [],
          test: f => f.hasBreadcrumb ? { state: 'yes', note: 'BreadcrumbList with ' + f.breadcrumbLength + ' step(s)' } : { state: 'no', note: 'No BreadcrumbList' } },
        { key: 'any_inbound', pillar: 'authority', weight: 10, strength: 'weak', label: 'Reachable from the rest of the site',
          why: 'Any internal link in at all, editorial or template. Weak, but an orphan page inherits nothing from the site.', skip: [],
          test: f => f.inboundAny >= 3 ? { state: 'yes', note: f.inboundAny + ' internal links in' }
              : f.inboundAny >= 1 ? { state: 'partial', note: f.inboundAny + ' internal link(s) in' } : { state: 'no', note: 'Orphan - no internal links point here' } },
        // --- Trust -----
        { key: 'https', pillar: 'trust', weight: 12, strength: 'strong', label: 'Served over HTTPS',
          why: 'A page asking to be trusted cannot be served in the clear.', skip: [],
          test: f => f.https ? { state: 'yes', note: 'https://' } : { state: 'no', note: 'Served over plain HTTP' } },
        { key: 'indexable', pillar: 'trust', weight: 15, strength: 'strong', label: 'Indexable',
          why: 'No noindex in the robots meta. A page excluded from the index cannot earn trust in search.', skip: [],
          test: f => f.noindex ? { state: 'no', note: 'robots: ' + f.robots } : { state: 'yes', note: f.robots ? 'robots: ' + f.robots : 'No robots directive (indexable by default)' } },
        { key: 'canonical', pillar: 'trust', weight: 15, strength: 'moderate', label: 'Canonical declared',
          why: 'A self-referencing canonical tells search engines which URL is the real one. A canonical pointing elsewhere is recorded, not rewarded.', skip: [],
          test: f => {
              if (!f.canonical) return { state: 'no', note: 'No canonical link' };
              if (f.canonicalSelf) return { state: 'yes', note: 'Self-referencing' };
              return { state: 'partial', note: 'Canonicalised to ' + f.canonical };
          } },
        { key: 'status_ok', pillar: 'trust', weight: 8, strength: 'strong', label: 'Responds 2xx',
          why: 'A page that errors or redirects is not the page users land on.', skip: [],
          test: f => f.status >= 200 && f.status < 300 ? { state: 'yes', note: 'HTTP ' + f.status } : { state: 'no', note: 'HTTP ' + (f.status || 'no response') } },
        { key: 'dates', pillar: 'trust', weight: 15, strength: 'moderate', label: 'Publish / update date available',
          why: 'datePublished or dateModified in JSON-LD, an article:* meta tag, or a Last-Modified header. Undated advice cannot be judged.', skip: ['utility'],
          test: f => f.dateBest ? { state: 'yes', note: f.dateLabel } : { state: 'no', note: 'No date in JSON-LD, meta tags or headers' } },
        { key: 'freshness', pillar: 'trust', weight: 15, strength: 'moderate', label: 'Kept up to date',
          why: 'Most recent date within ' + STALE_MONTHS + ' months (full credit under 12). Staleness is judged, not assumed: a page with no date scores nothing here because we cannot tell.', skip: ['utility'],
          test: f => {
              if (!f.dateBest) return { state: 'no', note: 'Undated - freshness unknown' };
              if (f.ageMonths <= 12) return { state: 'yes', note: 'Updated ' + f.ageLabel };
              if (f.ageMonths <= STALE_MONTHS) return { state: 'partial', note: 'Last touched ' + f.ageLabel };
              return { state: 'no', note: 'Stale: last touched ' + f.ageLabel };
          } },
        { key: 'trust_pages', pillar: 'trust', weight: 20, strength: 'strong', siteLevel: true,
          label: 'Site has contact, about and policy pages',
          why: 'Who is behind the site, how to reach them, and under what terms. Site-level: scored from the URLs in this crawl, so a crawl that never reached them will under-report.', skip: [],
          test: (f, site) => {
              const found = site.trustPages.found, missing = site.trustPages.missing;
              if (!missing.length) return { state: 'yes', note: found.join(', ') };
              if (found.length) return { state: 'partial', note: 'Found ' + found.join(', ') + '; missing ' + missing.join(', ') };
              return { state: 'no', note: 'No contact, about or policy page in this crawl' };
          } }
    ];

    const STATE_SCORE = { yes: 1, partial: 0.5, no: 0 };
    const STATE_MARK = { yes: '✓', partial: '~', no: '✗', na: '–' };
    // ------------------------------------------------------------- utilities
    function str(v) { return typeof v === 'string' ? v : (v == null ? '' : String(v)); }
    function arr(v) { return Array.isArray(v) ? v : (v == null || v === '' ? [] : [v]); }
    // List fields arrive parsed live and as JSON strings from a saved crawl.
    function listOf(v) { if (Array.isArray(v)) return v; const parsed = parseMaybeJson(v); return Array.isArray(parsed) ? parsed : (parsed ? [parsed] : []); }
    function num(v) { const n = Number(v); return isFinite(n) ? n : 0; }
    function truthy(v) { return v === true || v === 1 || v === '1' || v === 'true'; }
    function pct(a, b) { return b > 0 ? Math.round((a / b) * 100) : 0; }

    function parseMaybeJson(value) {
        if (typeof value === 'string') {
            const t = value.trim();
            if (!t) return null;
            try { return JSON.parse(t); } catch (e) { return null; }
        }
        return value && typeof value === 'object' ? value : null;
    }

    function hostOf(url) {
        try { return new URL(str(url)).hostname.toLowerCase().replace(/^www\./, ''); } catch (e) { return ''; }
    }
    // Registrable-ish domain: good enough to tell "another site" from "our CDN".
    function rootDomain(host) {
        const h = str(host).toLowerCase().replace(/^www\./, '');
        const parts = h.split('.').filter(Boolean);
        if (parts.length <= 2) return h;
        const twoLevel = /^(co|com|org|net|gov|ac|edu)\.[a-z]{2}$/.test(parts.slice(-2).join('.'));
        return parts.slice(twoLevel ? -3 : -2).join('.');
    }
    function normUrl(url) {
        const s = str(url).trim();
        if (!s) return '';
        try {
            const u = new URL(s);
            u.hash = '';
            u.hostname = u.hostname.toLowerCase().replace(/^www\./, '');
            let out = u.origin.replace(/^https?:/, '') + u.pathname.replace(/\/+$/, '') + (u.search || '');
            return out.toLowerCase();
        } catch (e) { return s.replace(/[#?].*$/, '').replace(/\/+$/, '').toLowerCase(); }
    }

    function typesOf(node) {
        if (!node || typeof node !== 'object') return [];
        return arr(node['@type']).map(t => str(t).split(/[/#]/).pop()).filter(Boolean);
    }
    function isType(node, list) { return typesOf(node).some(t => list.indexOf(t) !== -1); }

    function parseDate(value) {
        const s = str(value).trim();
        if (!s) return null;
        const d = new Date(s.length === 10 ? s + 'T00:00:00Z' : s);
        if (isNaN(d.getTime())) return null;
        const y = d.getUTCFullYear();
        if (y < 1995 || y > new Date().getUTCFullYear() + 1) return null;
        return d;
    }
    function ageLabel(months) {
        if (months < 1) return 'this month';
        if (months < 12) return months + ' month' + (months === 1 ? '' : 's') + ' ago';
        const years = Math.floor(months / 12);
        return years + ' year' + (years === 1 ? '' : 's') + ' ago';
    }

    ToadstonePlugin.register({
        id: 'e-e-a-t',
        name: 'E-E-A-T Analyzer',
        version: '2.0.0',
        author: 'Toadstone Community',
        description: 'Scores Experience, Expertise, Authoritativeness and Trust separately from the crawler\'s own data, and shows the evidence behind every score.',

        tab: { label: 'E-E-A-T', iconName: 'shield', position: 'end' },

        state: { sortBy: 'overall', showAll: false, expanded: null },

        onLoad() { this.injectStyles(); },

        onTabActivate(container, data) {
            this.injectStyles();
            this.render(container, data);
        },

        onDataUpdate(data) {
            if (!this.isActive || !this.container) return;
            // Live crawls fire this constantly; scoring every page each time
            // would make the tab stutter, so coalesce into one render a second.
            const now = Date.now();
            this._pending = data;
            if (this._timer) return;
            const wait = Math.max(0, 1000 - (now - (this._lastRender || 0)));
            this._timer = setTimeout(() => {
                this._timer = null;
                this._lastRender = Date.now();
                if (this.isActive && this.container) this.render(this.container, this._pending);
            }, wait);
        },

        onCrawlComplete(data) {
            if (this.isActive && this.container) this.render(this.container, data);
        },

        injectStyles() {
            if (document.getElementById('eeat-plugin-css')) return;
            const style = document.createElement('style');
            style.id = 'eeat-plugin-css';
            style.textContent = PLUGIN_CSS;
            document.head.appendChild(style);
        },
        // ------------------------------------------------------------ render
        render(container, data) {
            if (!container) return;
            try {
                const urls = (data && Array.isArray(data.urls)) ? data.urls : [];
                const links = (data && Array.isArray(data.links)) ? data.links : [];
                const analysis = this.analyse(urls, links);
                container.innerHTML = analysis.pages.length ? this.renderReport(analysis) : this.renderEmptyState(analysis);
                this.bindEvents(container);
            } catch (error) {
                console.error('E-E-A-T: analysis failed', error);
                container.innerHTML = '<div class="eeat"><div class="empty-state"><div class="empty-state-content">' + '<h3>E-E-A-T analysis failed</h3>'
                    + '<p>The crawl data could not be scored. The console has the details.</p>' + '<p class="eeat-note">' + this.esc(str(error && error.message)) + '</p>' + '</div></div></div>';
            }
        },

        esc(text) {
            try { return this.utils.escapeHtml(str(text)); }
            catch (e) { return str(text).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
        },

        quality(score) { return score >= 80 ? 'eeat-q-good' : score >= 55 ? 'eeat-q-ok' : 'eeat-q-bad'; },

        bindEvents(container) {
            if (container.dataset.eeatBound === '1') return;
            container.dataset.eeatBound = '1';
            container.addEventListener('click', (event) => {
                const seg = event.target.closest('[data-eeat-sort]');
                if (seg) { this.state.sortBy = seg.dataset.eeatSort; this.state.expanded = null; this.rerender(); return; }
                const more = event.target.closest('[data-eeat-more]');
                if (more) { this.state.showAll = !this.state.showAll; this.rerender(); return; }
                const row = event.target.closest('.eeat-row');
                if (row) this.toggleRow(row);
            });
            container.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                const row = event.target.closest('.eeat-row');
                if (!row) return;
                event.preventDefault();
                this.toggleRow(row);
            });
        },

        toggleRow(row) {
            const detail = row.nextElementSibling;
            if (!detail || !detail.classList.contains('eeat-detail')) return;
            const open = row.getAttribute('aria-expanded') === 'true';
            row.setAttribute('aria-expanded', open ? 'false' : 'true');
            detail.hidden = open;
            this.state.expanded = open ? null : row.dataset.eeatKey || null;
        },

        rerender() {
            if (this.container && this._lastData) this.render(this.container, this._lastData);
        },
        // ----------------------------------------------------------- analysis
        analyse(urls, links) {
            this._lastData = { urls: urls, links: links };

            const considered = [];
            let skippedAsset = 0, skippedError = 0;
            for (let i = 0; i < urls.length && considered.length < MAX_PAGES; i++) {
                const page = urls[i];
                if (!page || typeof page !== 'object' || !str(page.url)) continue;
                const ct = str(page.content_type).toLowerCase();
                if (ct && ct.indexOf('html') === -1) { skippedAsset++; continue; }
                const status = num(page.status_code);
                if (status && (status < 200 || status >= 400)) { skippedError++; continue; }
                considered.push(page);
            }
            const truncated = urls.length > MAX_PAGES;

            const linkFacts = this.indexLinks(links);
            const site = this.analyseSite(considered, linkFacts);
            const pages = considered.map(page => this.scorePage(page, site, linkFacts));

            const totals = { overall: 0 };
            PILLARS.forEach(p => { totals[p.key] = 0; });
            pages.forEach(p => {
                totals.overall += p.overall;
                PILLARS.forEach(pl => { totals[pl.key] += p.pillars[pl.key].score; });
            });
            const site_scores = { overall: pages.length ? Math.round(totals.overall / pages.length) : 0 };
            PILLARS.forEach(p => { site_scores[p.key] = pages.length ? Math.round(totals[p.key] / pages.length) : 0; });

            // Signal coverage across the pages where the signal applies.
            const coverage = SIGNALS.map(sig => {
                let yes = 0, partial = 0, no = 0, na = 0;
                pages.forEach(p => {
                    const r = p.results[sig.key];
                    if (!r || r.state === 'na') { na++; return; }
                    if (r.state === 'yes') yes++; else if (r.state === 'partial') partial++; else no++;
                });
                const applicable = yes + partial + no;
                return {
                    signal: sig, yes, partial, no, na, applicable,
                    coverage: applicable ? Math.round(((yes + partial * 0.5) / applicable) * 100) : null,
                    // Points the site-wide average would gain if this one signal were fixed everywhere.
                    gain: pages.length ? pages.reduce((sum, p) => sum + (p.gains[sig.key] || 0), 0) / pages.length : 0
                };
            });

            return {
                pages, site, site_scores, coverage,
                totalUrls: urls.length, skippedAsset, skippedError, truncated,
                linkRecords: links.length
            };
        },

        indexLinks(links) {
            const inboundMain = Object.create(null);   // target -> count
            const inboundMainSrc = Object.create(null);// target -> Set(source)
            const inboundAny = Object.create(null);
            const mainExtDomains = Object.create(null);// source -> Set(domain)
            const mainExtRaw = Object.create(null);    // source -> count incl. social
            for (let i = 0; i < links.length; i++) {
                const link = links[i];
                if (!link || typeof link !== 'object') continue;
                const target = normUrl(link.target_url);
                const source = normUrl(link.source_url);
                const internal = truthy(link.is_internal);
                const inMain = truthy(link.in_main);
                if (internal && target) {
                    inboundAny[target] = (inboundAny[target] || 0) + 1;
                    if (inMain && source && source !== target) {
                        inboundMain[target] = (inboundMain[target] || 0) + 1;
                        (inboundMainSrc[target] || (inboundMainSrc[target] = new Set())).add(source);
                    }
                } else if (!internal && source && inMain) {
                    mainExtRaw[source] = (mainExtRaw[source] || 0) + 1;
                    const domain = rootDomain(str(link.target_domain) || hostOf(link.target_url));
                    if (domain && NON_CITATION_HOSTS.indexOf(domain) === -1) {
                        (mainExtDomains[source] || (mainExtDomains[source] = new Set())).add(domain);
                    }
                }
            }
            return { inboundMain, inboundMainSrc, inboundAny, mainExtDomains, mainExtRaw };
        },

        // Facts that belong to the publisher, not to a single URL.
        analyseSite(pages, linkFacts) {
            const crawled = new Set();
            const hosts = Object.create(null);
            pages.forEach(p => {
                crawled.add(normUrl(p.url));
                const root = rootDomain(hostOf(p.url));
                if (root) hosts[root] = (hosts[root] || 0) + 1;
            });
            const primaryDomain = Object.keys(hosts).sort((a, b) => hosts[b] - hosts[a])[0] || '';

            let org = null;
            const authorPages = [];
            const found = { contact: null, about: null, policy: null };

            pages.forEach(page => {
                const path = str(page.url).replace(/^https?:\/\/[^/]+/i, '').toLowerCase();
                const nodes = this.nodesOf(page);
                nodes.forEach(node => {
                    if (isType(node, ORG_TYPES) && str(node.name)) {
                        const sameAs = arr(node.sameAs).map(str).filter(Boolean);
                        const extras = [];
                        if (node.logo) extras.push('logo');
                        if (node.contactPoint || node.telephone || node.email) extras.push('contact point');
                        if (node.address) extras.push('address');
                        // One organisation described across several pages: union
                        // what each copy declares rather than trusting one node.
                        if (!org) org = { name: str(node.name), sameAs: [], extras: [] };
                        if (org.name === str(node.name)) {
                            sameAs.forEach(u => { if (org.sameAs.indexOf(u) === -1) org.sameAs.push(u); });
                            extras.forEach(x => { if (org.extras.indexOf(x) === -1) org.extras.push(x); });
                        }
                    }
                    if (isType(node, ['ProfilePage'])) authorPages.push(page.url);
                });
                if (/\/author\/|\/team\/|\/our-team|\/people\//.test(path)) authorPages.push(page.url);
                if (!found.contact && (/contact/.test(path) || nodes.some(n => isType(n, ['ContactPage'])))) found.contact = page.url;
                if (!found.about && (/\/about|\/who-we-are|\/our-story/.test(path) || nodes.some(n => isType(n, ['AboutPage'])))) found.about = page.url;
                if (!found.policy && /privacy|terms|disclaimer|editorial-polic|cookie-polic/.test(path)) found.policy = page.url;
            });

            const foundList = [], missingList = [];
            [['contact', 'contact'], ['about', 'about'], ['policy', 'privacy / terms']].forEach(([key, label]) => {
                (found[key] ? foundList : missingList).push(label);
            });

            return {
                crawled, primaryDomain, org,
                scanChars: pages.length > LARGE_CRAWL ? TEXT_SCAN_CHARS_LARGE : TEXT_SCAN_CHARS,
                authorPages: Array.from(new Set(authorPages)),
                authorPageSet: new Set(Array.from(new Set(authorPages)).map(normUrl)),
                trustPages: { found: foundList, missing: missingList, urls: found },
                pageCount: pages.length
            };
        },

        nodesOf(page) {
            const out = [];
            const seen = new Set();
            const push = (node, depth) => {
                if (!node || depth > 6) return;
                if (Array.isArray(node)) { node.forEach(n => push(n, depth + 1)); return; }
                if (typeof node !== 'object' || seen.has(node)) return;
                seen.add(node);
                out.push(node);
                if (node['@graph']) push(node['@graph'], depth + 1);
            };
            const raw = Array.isArray(page.json_ld) ? page.json_ld : parseMaybeJson(page.json_ld);
            push(raw, 0);
            // Microdata gets a shallow shim so itemtype-only sites are not invisible.
            const micro = Array.isArray(page.schema_org) ? page.schema_org : parseMaybeJson(page.schema_org);
            arr(micro).forEach(item => {
                if (!item || typeof item !== 'object') return;
                const props = (item.properties && typeof item.properties === 'object') ? item.properties : {};
                out.push(Object.assign({ '@type': str(item.type).split(/[/#]/).pop() }, props));
            });
            return out;
        },
        // ------------------------------------------------------ page scoring
        pageFacts(page, site, linkFacts) {
            const nodes = this.nodesOf(page);
            const byId = Object.create(null);
            nodes.forEach(n => { const id = str(n['@id']); if (id) byId[id] = n; });
            const resolve = (value) => {
                if (!value) return null;
                if (Array.isArray(value)) { for (const v of value) { const r = resolve(v); if (r) return r; } return null; }
                if (typeof value === 'string') return { name: value, node: null };
                if (typeof value !== 'object') return null;
                const ref = str(value['@id']) && byId[str(value['@id'])];
                const node = (ref && ref !== value) ? Object.assign({}, ref, value) : value;
                const name = str(node.name) || str(node.givenName ? node.givenName + ' ' + str(node.familyName) : '') || str(value.name);
                return name.trim() ? { name: name.trim(), node } : (node.url || node.sameAs ? { name: str(node.url) || 'unnamed', node } : null);
            };

            const allTypes = [];
            nodes.forEach(n => typesOf(n).forEach(t => { if (allTypes.indexOf(t) === -1) allTypes.push(t); }));
            const contentTypes = allTypes.filter(t => ARTICLE_TYPES.indexOf(t) !== -1);
            // --- author
            let author = null;
            for (const key of ['author', 'creator']) {
                for (const node of nodes) {
                    if (!node[key]) continue;
                    const r = resolve(node[key]);
                    if (r) { author = { name: r.name, node: r.node, source: 'JSON-LD ' + key }; break; }
                }
                if (author) break;
            }
            if (!author) {
                const person = nodes.find(n => isType(n, ['Person']) && str(n.name));
                if (person) author = { name: str(person.name), node: person, source: 'Person node' };
            }
            const metaTags = (page.meta_tags && typeof page.meta_tags === 'object') ? page.meta_tags : {};
            const ogTags = (page.og_tags && typeof page.og_tags === 'object') ? page.og_tags : {};
            if (!author) {
                const metaAuthor = str(metaTags.author || metaTags['article:author'] || metaTags['twitter:creator'] || ogTags.author);
                if (metaAuthor && !/^https?:/i.test(metaAuthor)) author = { name: metaAuthor, node: null, source: 'author meta tag' };
            }
            const mainText = str(page.main_text).slice(0, site.scanChars || TEXT_SCAN_CHARS);
            if (!author) {
                const byline = /(?:^|[\s|·•])(?:By|Written by|Authored by)\s+([A-Z][\p{L}'’\-]+(?:\s+[A-Z][\p{L}'’.\-]+){1,2})/u.exec(mainText.slice(0, 1200));
                if (byline) author = { name: byline[1], node: null, source: 'visible byline' };
            }

            const authorNode = author && author.node ? author.node : null;
            const authorBioParts = [];
            const credentialParts = [];
            if (authorNode) {
                if (str(authorNode.description)) authorBioParts.push('a bio');
                if (str(authorNode.url)) authorBioParts.push('a profile URL');
                if (arr(authorNode.sameAs).length) { authorBioParts.push('sameAs profiles'); credentialParts.push(arr(authorNode.sameAs).length + ' sameAs profile(s)'); }
                if (str(authorNode.jobTitle)) credentialParts.push('jobTitle "' + str(authorNode.jobTitle) + '"');
                if (authorNode.worksFor) { const w = resolve(authorNode.worksFor); credentialParts.push('worksFor ' + (w ? w.name : 'an organisation')); }
                if (arr(authorNode.knowsAbout).length) credentialParts.push('knowsAbout');
                if (str(authorNode.honorificSuffix)) credentialParts.push(str(authorNode.honorificSuffix));
            }

            let authorPageOnSite = '';
            const authorUrl = authorNode ? (str(authorNode.url) || str(authorNode['@id'])) : '';
            if (authorUrl && /^https?:/i.test(authorUrl)) {
                const n = normUrl(authorUrl);
                if (site.crawled.has(n) || site.authorPageSet.has(n)) authorPageOnSite = authorUrl;
            }
            // --- reviewer
            let reviewer = null;
            for (const node of nodes) {
                const candidate = node.reviewedBy || node.reviewer || (isType(node, ['Review']) ? node.author : null);
                if (!candidate) continue;
                const r = resolve(candidate);
                // A reviewer who is also the author is recorded, and says so.
                if (r && r.name) { reviewer = r.name + (author && r.name === author.name ? ' (the author)' : ''); break; }
            }
            // --- publisher
            let publisher = null;
            for (const node of nodes) {
                if (!node.publisher) continue;
                const r = resolve(node.publisher);
                if (r) { publisher = r.name; break; }
            }
            if (!publisher) {
                const orgNode = nodes.find(n => isType(n, ORG_TYPES) && str(n.name));
                if (orgNode) publisher = str(orgNode.name);
            }
            // --- dates
            let published = null, modified = null;
            nodes.forEach(node => {
                const p = parseDate(node.datePublished || node.dateCreated);
                const m = parseDate(node.dateModified || node.dateUpdated);
                if (p && (!published || p < published)) published = p;
                if (m && (!modified || m > modified)) modified = m;
            });
            if (!published) published = parseDate(metaTags['article:published_time'] || ogTags['article:published_time'] || ogTags.published_time);
            if (!modified) modified = parseDate(metaTags['article:modified_time'] || ogTags['article:modified_time'] || ogTags.updated_time || page.last_modified);
            const dateBest = modified || published;
            const ageMonths = dateBest ? Math.max(0, Math.round((Date.now() - dateBest.getTime()) / (1000 * 60 * 60 * 24 * 30.44))) : null;
            const dateLabel = dateBest ? (modified ? 'modified ' + modified.toISOString().slice(0, 10) : '') +
                  (modified && published ? ', ' : '') +
                  (published ? 'published ' + published.toISOString().slice(0, 10) : '') : '';
            // --- headings
            const headings = listOf(page.headings).filter(h => h && typeof h === 'object');
            const visible = headings.filter(h => h.is_visible !== false);
            const h1s = visible.filter(h => num(h.level) === 1);
            const headingProblems = [];
            if (visible.length) {
                if (h1s.length === 0) headingProblems.push('no visible H1');
                else if (h1s.length > 1) headingProblems.push(h1s.length + ' H1s');
                let prev = 0, skipped = 0;
                visible.slice().sort((a, b) => num(a.dom_order) - num(b.dom_order)).forEach(h => {
                    const lvl = num(h.level);
                    if (prev && lvl > prev + 1) skipped++;
                    prev = lvl;
                });
                if (skipped) headingProblems.push(skipped + ' skipped heading level(s)');
                const subheads = visible.filter(h => num(h.level) >= 2 && num(h.level) <= 3).length;
                if (num(page.main_word_count) > 600 && subheads < 2) headingProblems.push('only ' + subheads + ' subhead(s) for ' + num(page.main_word_count) + ' words');
            }
            // --- images / first-hand language
            const images = listOf(page.images).filter(i => i && typeof i === 'object');
            const ownImages = images.filter(i => rootDomain(hostOf(i.src)) === site.primaryDomain).length;
            let firstHand = 0;
            FIRST_HAND.forEach(re => { if (re.test(mainText)) firstHand++; });
            // --- links
            const key = normUrl(page.url);
            const citationDomains = linkFacts.mainExtDomains[key] ? Array.from(linkFacts.mainExtDomains[key]).sort() : [];
            const robots = str(page.robots);

            const canonical = str(page.canonical_url);
            return {
                url: str(page.url),
                title: str(page.title) || str(page.h1),
                kind: this.pageKind(page, contentTypes, allTypes, published),
                allTypes, contentTypes,
                author, authorBioParts, credentialParts, authorPageOnSite, reviewer, publisher,
                published, modified, dateBest, ageMonths, ageLabel: ageMonths == null ? '' : ageLabel(ageMonths), dateLabel,
                headingCount: visible.length, headingProblems,
                mainWords: num(page.main_word_count) || num(page.word_count),
                ownImages, totalImages: images.length, firstHand,
                citationDomains, mainExternalRaw: num(linkFacts.mainExtRaw[key]),
                inboundMain: num(linkFacts.inboundMain[key]),
                inboundMainSources: linkFacts.inboundMainSrc[key] ? linkFacts.inboundMainSrc[key].size : 0,
                inboundAny: num(linkFacts.inboundAny[key]) || listOf(page.linked_from).length,
                hasBreadcrumb: nodes.some(n => isType(n, ['BreadcrumbList'])),
                breadcrumbLength: (nodes.find(n => isType(n, ['BreadcrumbList'])) || {}).itemListElement ? arr((nodes.find(n => isType(n, ['BreadcrumbList'])) || {}).itemListElement).length : 0,
                https: /^https:/i.test(str(page.url)),
                robots, noindex: /noindex/i.test(robots),
                canonical, canonicalSelf: !canonical || normUrl(canonical) === key,
                status: num(page.status_code)
            };
        },

        pageKind(page, contentTypes, allTypes, published) {
            const path = str(page.url).replace(/^https?:\/\/[^/]+/i, '').toLowerCase();
            // The front page is nobody's article: no byline expected of it.
            if (path === '' || path === '/') return 'home';
            if (allTypes.indexOf('ProfilePage') !== -1 || /\/author\/|\/team\/|\/people\//.test(path)) return 'profile';
            if (/privacy|terms|disclaimer|cookie-polic|sitemap|\/thank-you|\/404/.test(path)) return 'utility';
            if (allTypes.indexOf('ContactPage') !== -1 || /\/contact/.test(path)) return 'utility';
            if (allTypes.indexOf('CollectionPage') !== -1 || /\/(category|tag|blog|archive)\/?$/.test(path)) return 'listing';
            if (contentTypes.some(t => ['Article', 'BlogPosting', 'NewsArticle', 'TechArticle', 'ScholarlyArticle', 'HowTo', 'Report', 'Recipe'].indexOf(t) !== -1)) return 'article';
            if (published) return 'article';
            return 'page';
        },

        scorePage(page, site, linkFacts) {
            const facts = this.pageFacts(page, site, linkFacts);
            const results = Object.create(null);
            const pillars = Object.create(null);
            PILLARS.forEach(p => { pillars[p.key] = { earned: 0, applicable: 0, score: 0, gaps: [] }; });

            SIGNALS.forEach(sig => {
                let result;
                if (sig.skip && sig.skip.indexOf(facts.kind) !== -1) {
                    result = { state: 'na', note: 'Not scored for a ' + facts.kind + ' page' };
                } else {
                    try { result = sig.test(facts, site) || { state: 'no', note: '' }; }
                    catch (e) { result = { state: 'na', note: 'Signal could not be evaluated' }; }
                }
                results[sig.key] = result;
                const bucket = pillars[sig.pillar];
                if (!bucket || result.state === 'na') return;
                bucket.applicable += sig.weight;
                bucket.earned += sig.weight * (STATE_SCORE[result.state] || 0);
                if (result.state !== 'yes') bucket.gaps.push({ signal: sig, result: result, lost: sig.weight * (1 - (STATE_SCORE[result.state] || 0)) });
            });

            let overall = 0, weightSum = 0;
            PILLARS.forEach(p => {
                const b = pillars[p.key];
                b.score = b.applicable ? Math.round((b.earned / b.applicable) * 100) : 0;
                b.gaps.sort((a, b2) => b2.lost - a.lost);
                if (b.applicable) { overall += b.score * p.weight; weightSum += p.weight; }
            });
            // What this page's overall score would gain if each missing signal were
            // fixed - the unit the "what to change" list is ranked in.
            const gains = Object.create(null);
            if (weightSum) PILLARS.forEach(p => { const b = pillars[p.key]; if (b.applicable) b.gaps.forEach(g => { gains[g.signal.key] = (g.lost / b.applicable) * 100 * (p.weight / weightSum); }); });

            return {
                url: facts.url, key: normUrl(facts.url), title: facts.title, kind: facts.kind,
                facts, results, pillars, gains,
                overall: weightSum ? Math.round(overall / weightSum) : 0
            };
        },
        // ------------------------------------------------------------- views
        renderReport(a) {
            return '<div class="eeat">' + this.renderHeader(a) + this.renderScores(a) + this.renderSiteSignals(a) + this.renderWeakest(a) + this.renderCoverage(a)
                + this.renderRecommendations(a) + '</div>';
        },

        renderHeader(a) {
            const skipped = [];
            if (a.skippedAsset) skipped.push(a.skippedAsset + ' non-HTML');
            if (a.skippedError) skipped.push(a.skippedError + ' error/redirect');
            return '<div class="eeat-head">' + '<h2>E-E-A-T analysis</h2>' + '<p>' + a.pages.length + ' HTML page' + (a.pages.length === 1 ? '' : 's') + ' scored from '
                + a.totalUrls + ' crawled URL' + (a.totalUrls === 1 ? '' : 's') + ', using ' + a.linkRecords.toLocaleString() + ' link records.'
                + (skipped.length ? ' Skipped ' + skipped.join(' and ') + '.' : '') + (a.truncated ? ' Only the first ' + MAX_PAGES + ' pages are scored.' : '')
                + '</p>' + this.renderMethod() + '</div>';
        },

        renderMethod() {
            const rows = PILLARS.map(p => {
                const sigs = SIGNALS.filter(s => s.pillar === p.key);
                return '<tr><td>' + this.esc(p.label) + ' <span class="eeat-note">' + Math.round(p.weight * 100) + '% of overall</span>'
                    + '<div class="eeat-note">' + this.esc(p.blurb) + '</div></td>' + '<td>' + sigs.map(s =>
                        '<div class="eeat-sig"><div class="eeat-sig-body"><span class="eeat-sig-label">' + this.esc(s.label) + '</span>'
                        + '<span class="eeat-sig-note">' + this.esc(s.why) + '</span></div>'
                        + '<span class="eeat-strength is-' + s.strength + '">' + s.strength + '</span>' + '<span class="eeat-sig-w">' + s.weight + '</span></div>'
                      ).join('') + '</td></tr>';
            }).join('');
            return '<details class="eeat-method">' + '<summary>How this is scored - pillars, signals, weights, and what is deliberately not rewarded</summary>'
                + '<p class="eeat-note">Each signal scores full, half or nothing. Signals that cannot apply to a page (an author on a privacy policy, citations on a category listing) are dropped from both sides of the fraction rather than scored zero, and the page kind is shown next to every score. Weak signals are capped low on purpose: length is not expertise, and first-person phrasing is not experience.</p>'
                + '<table><thead><tr><th>Pillar</th><th>Signals and weights (out of 100 for that pillar)</th></tr></thead><tbody>' + rows + '</tbody></table>'
                + '<p class="eeat-note">Overall = Trust 35% + Expertise 25% + Authoritativeness 25% + Experience 15%, over the pillars that apply to the page. Trust leads because it is both the pillar Google weights highest and the one a crawler can measure most completely; Experience trails because markup rarely proves it.</p>'
                + '</details>';
        },

        renderScores(a) {
            const overallQ = this.quality(a.site_scores.overall);
            const tiles = PILLARS.map(p => {
                const score = a.site_scores[p.key];
                const q = this.quality(score);
                const worst = this.worstSignalFor(a, p.key);
                return '<div class="stat-tile eeat-tile">' + '<div class="stat-tile-label">' + this.esc(p.label) + '</div>'
                    + '<div class="stat-tile-value eeat-tile-value ' + q + '">' + score + '</div>' + '<div class="eeat-meter ' + q + '"><span style="width:' + score + '%"></span></div>'
                    + '<div class="eeat-tile-sub">' + Math.round(p.weight * 100) + '% of overall'
                    + (worst ? ' &middot; weakest: ' + this.esc(worst.signal.label.toLowerCase()) : '') + '</div>' + '</div>';
            }).join('');
            return '<div class="eeat-section">' + '<div class="stat-grid">' + '<div class="stat-tile eeat-tile eeat-kpi">' + '<div class="stat-tile-label">Overall E-E-A-T</div>'
                + '<div class="stat-tile-value eeat-tile-value ' + overallQ + '">' + a.site_scores.overall + '</div>'
                + '<div class="eeat-meter ' + overallQ + '"><span style="width:' + a.site_scores.overall + '%"></span></div>'
                + '<div class="eeat-tile-sub">mean of ' + a.pages.length + ' page score' + (a.pages.length === 1 ? '' : 's') + '</div>' + '</div>' + tiles + '</div></div>';
        },

        worstSignalFor(a, pillarKey) {
            return a.coverage
                .filter(c => c.signal.pillar === pillarKey && c.applicable) .sort((x, y) => x.coverage - y.coverage)[0] || null;
        },

        renderSiteSignals(a) {
            const site = a.site;
            const org = site.org;
            const chips = [];
            chips.push(org && org.sameAs.length
                ? this.chip('success', 'Organisation: ' + org.name + ' (' + org.sameAs.length + ' profiles' + (org.extras.length ? ', ' + org.extras.join(', ') : '') + ')')
                : org ? this.chip('warning', 'Organisation: ' + org.name + ' - no sameAs profiles') : this.chip('error', 'No Organization node anywhere in the crawl'));
            site.trustPages.found.forEach(f => chips.push(this.chip('success', 'Has a ' + f + ' page')));
            site.trustPages.missing.forEach(m => chips.push(this.chip('error', 'No ' + m + ' page found')));
            chips.push(site.authorPages.length ? this.chip('success', site.authorPages.length + ' author/team page' + (site.authorPages.length === 1 ? '' : 's'))
                : this.chip('warning', 'No author or team page in the crawl'));
            const dated = a.pages.filter(p => p.facts.dateBest).length;
            chips.push(this.chip(dated === a.pages.length ? 'success' : dated ? 'warning' : 'error',
                dated + ' of ' + a.pages.length + ' pages carry a date'));
            const authored = a.pages.filter(p => p.facts.author).length;
            chips.push(this.chip(authored > a.pages.length * 0.6 ? 'success' : authored ? 'warning' : 'error',
                authored + ' of ' + a.pages.length + ' pages name an author'));

            return '<div class="eeat-section">' + '<div class="eeat-section-head"><h3>Site-level signals</h3>'
                + '<span class="eeat-note">Properties of the publisher. They apply to every page equally, and are read from the URLs in this crawl - a partial crawl under-reports them.</span></div>'
                + '<div class="eeat-panel eeat-panel-pad"><div class="eeat-chips">' + chips.join('') + '</div></div>' + '</div>';
        },

        chip(kind, text) {
            return '<span class="badge badge-' + kind + '">' + this.esc(text) + '</span>';
        },

        renderWeakest(a) {
            const sortBy = this.state.sortBy;
            const pages = a.pages.slice().sort((x, y) => {
                const xv = sortBy === 'overall' ? x.overall : x.pillars[sortBy].score;
                const yv = sortBy === 'overall' ? y.overall : y.pillars[sortBy].score;
                return xv - yv || x.url.localeCompare(y.url);
            });
            const limit = this.state.showAll ? pages.length : Math.min(20, pages.length);
            const shown = pages.slice(0, limit);
            const best = a.pages.slice().sort((x, y) => y.overall - x.overall).slice(0, 5);

            const seg = ['overall'].concat(PILLARS.map(p => p.key)).map(key => {
                const label = key === 'overall' ? 'Overall' : PILLARS.find(p => p.key === key).label;
                return '<button type="button" data-eeat-sort="' + key + '" aria-pressed="' + (sortBy === key ? 'true' : 'false') + '">' + label + '</button>';
            }).join('');

            return '<div class="eeat-section">' + '<div class="eeat-section-head"><h3>Fix these first</h3><span class="eeat-seg">' + seg + '</span>'
                + '<span class="eeat-note">Open a row for the signals a page has and the ones it is missing.</span></div>' + this.renderPageTable(shown)
                + (pages.length > 20 ? '<div><button type="button" class="btn btn-secondary btn-sm" data-eeat-more="1">'
                    + (this.state.showAll ? 'Show the 20 weakest only' : 'Show all ' + pages.length + ' pages') + '</button></div>' : '')
                + '<div class="eeat-section-head"><h3>Strongest pages</h3><span class="eeat-note">Use these as the in-house pattern to copy.</span></div>' + this.renderPageTable(best) + '</div>';
        },

        renderPageTable(pages) {
            return '<div class="eeat-table-wrap"><table class="eeat-table"><thead><tr><th>Page</th><th>Kind</th><th class="eeat-num">Overall</th>'
                + PILLARS.map(p => '<th class="eeat-num" title="' + this.esc(p.label) + '">' + this.esc(p.short) + '</th>').join('')
                + '<th>Biggest gaps</th></tr></thead><tbody>' + pages.map(p => this.renderPageRow(p)).join('') + '</tbody></table></div>';
        },

        renderPageRow(page) {
            const gaps = [];
            PILLARS.forEach(p => page.pillars[p.key].gaps.forEach(g => gaps.push(g)));
            gaps.sort((a, b) => b.lost - a.lost);
            const gapText = gaps.slice(0, 3).map(g => g.signal.label.toLowerCase()).join(', ') || 'none';
            const path = (() => { try { const u = new URL(page.url); return u.pathname + u.search; } catch (e) { return page.url; } })();
            const open = this.state.expanded === page.key;
            const cells = PILLARS.map(p => {
                const s = page.pillars[p.key];
                if (!s.applicable) return '<td class="eeat-num eeat-note">n/a</td>';
                return '<td class="eeat-num ' + this.quality(s.score) + '">' + s.score + '</td>';
            }).join('');
            return '<tr class="eeat-row" tabindex="0" role="button" aria-expanded="' + (open ? 'true' : 'false') + '" data-eeat-key="' + this.esc(page.key) + '">'
                + '<td class="eeat-url" title="' + this.esc(page.url) + '"><span class="eeat-caret">›</span> ' + this.esc(path) + '</td>'
                + '<td><span class="badge badge-neutral badge-no-dot">' + this.esc(page.kind) + '</span></td>'
                + '<td class="eeat-num ' + this.quality(page.overall) + '">' + page.overall + '</td>' + cells + '<td class="eeat-note eeat-gaps" title="' + this.esc(gapText) + '">' + this.esc(gapText) + '</td>'
                + '</tr>' + '<tr class="eeat-detail"' + (open ? '' : ' hidden') + '><td colspan="' + (4 + PILLARS.length) + '">' + this.renderPageDetail(page) + '</td></tr>';
        },

        renderPageDetail(page) {
            const cols = PILLARS.map(p => {
                const bucket = page.pillars[p.key];
                const sigs = SIGNALS.filter(s => s.pillar === p.key).map(s => {
                    const r = page.results[s.key] || { state: 'na', note: '' };
                    return '<div class="eeat-sig is-' + r.state + '">' + '<span class="eeat-sig-mark is-' + r.state + '">' + STATE_MARK[r.state] + '</span>'
                        + '<span class="eeat-sig-body"><span class="eeat-sig-label">' + this.esc(s.label) + '</span>' + '<span class="eeat-sig-note">' + this.esc(r.note) + '</span></span>'
                        + '<span class="eeat-sig-w">' + (r.state === 'na' ? '&mdash;' : s.weight) + '</span></div>';
                }).join('');
                return '<div class="eeat-detail-col"><h4><span>' + this.esc(p.label) + '</span><span>' + (bucket.applicable ? bucket.score + '/100' : 'n/a') + '</span></h4>' + sigs + '</div>';
            }).join('');
            return '<div class="eeat-note eeat-detail-url">' + this.esc(page.url) + (page.title ? ' &mdash; ' + this.esc(page.title) : '') + '</div>'
                + '<div class="eeat-detail-grid">' + cols + '</div>';
        },

        renderCoverage(a) {
            const rows = a.coverage.map(c => {
                if (!c.applicable) {
                    return '<div class="eeat-cov-label">' + this.esc(c.signal.label)
                        + '<small>' + this.esc(PILLARS.find(p => p.key === c.signal.pillar).label) + ' &middot; not applicable to any page in this crawl</small></div>'
                        + '<span class="eeat-strength is-' + c.signal.strength + '">' + c.signal.strength + '</span>'
                        + '<div class="eeat-meter"><span style="width:0%"></span></div>' + '<div class="eeat-cov-count">n/a</div>';
                }
                const q = this.quality(c.coverage);
                return '<div class="eeat-cov-label">' + this.esc(c.signal.label) + '<small>' + this.esc(PILLARS.find(p => p.key === c.signal.pillar).label) + ' &middot; weight ' + c.signal.weight
                    + (c.na ? ' &middot; n/a on ' + c.na + ' page' + (c.na === 1 ? '' : 's') : '') + '</small></div>'
                    + '<span class="eeat-strength is-' + c.signal.strength + '">' + c.signal.strength + '</span>'
                    + '<div class="eeat-meter ' + q + '"><span style="width:' + c.coverage + '%"></span></div>'
                    + '<div class="eeat-cov-count">' + c.yes + (c.partial ? '+' + c.partial : '') + '/' + c.applicable + '</div>';
            }).join('');
            return '<div class="eeat-section">' + '<div class="eeat-section-head"><h3>Signal coverage</h3>'
                + '<span class="eeat-note">How many of the pages where a signal applies actually carry it. "12+3/40" = 12 full, 3 partial, out of 40 applicable pages.</span></div>'
                + '<div class="eeat-panel eeat-panel-pad"><div class="eeat-cov">' + rows + '</div></div>' + '</div>';
        },

        renderRecommendations(a) {
            const ranked = a.coverage
                .filter(c => c.applicable && c.coverage < 100) .sort((x, y) => y.gain - x.gain) .slice(0, 6);
            if (!ranked.length) {
                return '<div class="eeat-section"><div class="eeat-section-head"><h3>What to change</h3></div>' + '<div class="eeat-panel"><div class="eeat-rec"><div class="eeat-rec-body">'
                    + '<div class="eeat-rec-title">Every signal this plugin can see is present on every applicable page.</div>'
                    + '<div class="eeat-rec-desc">That is the ceiling of what a crawl can tell you. The rest of E-E-A-T &mdash; whether the advice is any good, whether the author really did the work &mdash; is not visible in markup.</div>'
                    + '</div></div></div></div>';
            }
            const items = ranked.map(c => {
                const missing = c.no + c.partial;
                const severity = c.gain >= 3 ? 'error' : c.gain >= 1 ? 'warning' : 'info';
                const examples = a.pages.filter(p => {
                    const r = p.results[c.signal.key];
                    return r && (r.state === 'no' || r.state === 'partial');
                }).sort((x, y) => (y.gains[c.signal.key] || 0) - (x.gains[c.signal.key] || 0)).slice(0, 3) .map(p => { try { return new URL(p.url).pathname; } catch (e) { return p.url; } });
                return '<div class="eeat-rec">' + '<span class="badge badge-' + severity + '">+' + (Math.round(c.gain * 10) / 10) + ' pts</span>' + '<div class="eeat-rec-body">'
                    + '<div class="eeat-rec-title">' + this.esc(c.signal.label) + ' &mdash; missing or partial on ' + missing + ' of ' + c.applicable + ' applicable pages</div>'
                    + '<div class="eeat-rec-desc">' + this.esc(c.signal.why) + (c.signal.strength === 'weak' ? ' <em>This is weak evidence; fix the stronger signals above it first.</em>' : '')
                    + (examples.length ? '<br>Start with: ' + examples.map(e => this.esc(e)).join(', ') : '') + '</div></div></div>';
            }).join('');
            return '<div class="eeat-section">' + '<div class="eeat-section-head"><h3>What to change</h3>'
                + '<span class="eeat-note">Ranked by what the site\'s average overall score would gain if the signal were fixed on every page that lacks it.</span></div>'
                + '<div class="eeat-panel">' + items + '</div>' + '</div>';
        },

        renderEmptyState(a) {
            const reason = !a || !a.totalUrls ? 'Start a crawl, or load a saved one, and this tab will score every HTML page it finds.'
                : 'The crawl has ' + a.totalUrls + ' URL' + (a.totalUrls === 1 ? '' : 's') + ', but none of them are HTML pages that returned a 2xx response.';
            return '<div class="eeat"><div class="empty-state"><div class="empty-state-content">' + '<h3>Nothing to score yet</h3><p>' + this.esc(reason) + '</p>'
                + '<p class="eeat-note">E-E-A-T is scored per page from structured data, headings, body text, and the link graph &mdash; so it needs crawled HTML.</p>' + '</div></div></div>';
        }
    });

    console.log('✅ E-E-A-T Analyzer 2.0 registered');
}());
