# The check reference

> For the complete documentation index, see [llms.txt](https://spcaeo.github.io/toadstone/llms.txt). Every page here is also served as Markdown at the same address with `.md` instead of `.html`.

Every check Toadstone can report, with its slug, its severity and what to
do about it. **This file is generated** from a running install's own
catalogue (`tools/generate_checks_doc.py`), so it cannot drift from the
wording the code actually uses.

The slug is the stable part. It is what the API filters on, what a finding
carries in its `check` field, and what `/checks#<slug>` links to in the
running app. Names and wording may be improved; slugs are not renamed
without a reason.

**180 of these have been observed firing in a real crawl** of the
deliberately broken fixture site in `tests/coverage_site.py`, across its
several modes. Those are marked ✅. A check with no mark is not a check
that does not work - it is one this fixture has no page for yet, and
that is a gap in the fixture, recorded rather than hidden.

---

## AMP  <sub>13 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **AMP Boilerplate Missing**<br><code>amp_boilerplate_missing</code> | 🔴 error | Paste the boilerplate from the AMP specification into the head, unchanged. |
| ✅ | **AMP Page Has No Canonical**<br><code>amp_canonical_missing</code> | 🔴 error | Add a rel=canonical link pointing at the non-AMP page, or at this page. |
| ✅ | **AMP Charset Missing**<br><code>amp_charset_missing</code> | 🔴 error | Make <meta charset="utf-8"> the first element in the head. |
| ✅ | **Author JavaScript in AMP**<br><code>amp_custom_javascript</code> | 🔴 error | Remove the script, or express the behaviour with an AMP component. |
| ✅ | **External Stylesheet in AMP**<br><code>amp_external_stylesheet</code> | 🔴 error | Inline the CSS into <style amp-custom> and remove the stylesheet link. |
| ✅ | **Tag Not Allowed in AMP**<br><code>amp_forbidden_tag</code> | 🔴 error | Swap each tag for its AMP component and load that component's extension script. |
| ✅ | **Inline style Attribute in AMP**<br><code>amp_inline_style</code> | 🔴 error | Move those declarations into the <style amp-custom> block as classes. |
| ✅ | **More Than One amp-custom Block**<br><code>amp_multiple_custom_styles</code> | 🔴 error | Merge every custom style into one <style amp-custom> block. |
| ✅ | **AMP Runtime Not Loaded**<br><code>amp_runtime_missing</code> | 🔴 error | Add <script async src="https://cdn.ampproject.org/v0.js"></script> to the head. |
| ✅ | **AMP Viewport Missing**<br><code>amp_viewport_missing</code> | 🔴 error | Add <meta name="viewport" content="width=device-width"> to the head. |
| ✅ | **AMP Link Points at a Page That Is Not AMP**<br><code>amphtml_target_not_amp</code> | 🔴 error | Point rel=amphtml at the real AMP page, or remove the link. |
| ✅ | **Canonical Page Does Not Link Back to the AMP Version**<br><code>amphtml_missing_canonical</code> | 🟠 warning | Add <link rel="amphtml" href="..."> to the canonical page. |
|  | **AMP Version Was Not Crawled**<br><code>amphtml_target_not_crawled</code> | 🔵 info | Include the AMP URL in the crawl, or check it separately. |

## Accessibility  <sub>11 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Buttons must have discernible text**<br><code>a11y_button-name</code> | 🔴 error | Buttons must have discernible text |
| ✅ | **Elements must meet minimum color contrast ratio thresholds**<br><code>a11y_color-contrast</code> | 🔴 error | Elements must meet minimum color contrast ratio thresholds |
| ✅ | **Documents must have <title> element to aid in navigation**<br><code>a11y_document-title</code> | 🔴 error | Documents must have <title> element to aid in navigation |
| ✅ | **<html> element must have a lang attribute**<br><code>a11y_html-has-lang</code> | 🔴 error | <html> element must have a lang attribute |
| ✅ | **Images must have alternative text**<br><code>a11y_image-alt</code> | 🔴 error | Images must have alternative text |
| ✅ | **Form elements must have labels**<br><code>a11y_label</code> | 🔴 error | Form elements must have labels |
| ✅ | **Links must have discernible text**<br><code>a11y_link-name</code> | 🔴 error | Links must have discernible text |
|  | **Scrollable region must have keyboard access**<br><code>a11y_scrollable-region-focusable</code> | 🔴 error | Scrollable region must have keyboard access |
| ✅ | **Images Without Alt Text**<br><code>images_missing_alt</code> | 🟠 warning | — |
|  | **Missing Language Attribute**<br><code>missing_lang</code> | 🟠 warning | — |
|  | **Accessibility Check Did Not Run**<br><code>a11y_check_failed</code> | 🔵 info | Report this as a LibreCrawl bug if it happens on every page. |

## CMS  <sub>7 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Placeholder Value In A CMS Field**<br><code>cms_field_placeholder</code> | 🔴 error | Fill the field in with real content, or clear it so the template falls back instead of printing the placeholder. |
| ✅ | **Structured Data Stripped By The Front End**<br><code>cms_structured_data_stripped</code> | 🔴 error | Render the JSON-LD block from the CMS content in the page template, or remove it from the CMS so the two agree. |
| ✅ | **Published Item Never Crawled**<br><code>cms_not_crawled</code> | 🟠 warning | Link the published URL from the site so it can be reached, and check that robots.txt and the crawl scope do not exclude it. |
| ✅ | **Published Item Missing From The Sitemap**<br><code>cms_published_not_in_sitemap</code> | 🟠 warning | Include the published URL in the XML sitemap, or unpublish it. |
| ✅ | **CMS SEO Description Too Long**<br><code>cms_seo_description_too_long</code> | 🟠 warning | Shorten the SEO description in the CMS to the configured limit. |
| ✅ | **CMS SEO Title Too Long**<br><code>cms_seo_title_too_long</code> | 🟠 warning | Shorten the SEO title in the CMS to the configured limit. |
| ✅ | **Stray Whitespace In A CMS Field**<br><code>cms_stray_whitespace</code> | 🟠 warning | Trim the leading, trailing and doubled spaces out of the field. |

## Content  <sub>4 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Broken Image (404)**<br><code>broken_image_404</code> | 🔴 error | — |
| ✅ | **Broken Image (503)**<br><code>broken_image_503</code> | 🔴 error | — |
|  | **Content Removed**<br><code>content_removed</code> | 🟠 warning | Restore the missing copy, or confirm the deletion was intended - compare this page against the previous crawl before publishing. |
| ✅ | **Thin Content**<br><code>thin_content</code> | 🟠 warning | — |

## Duplication  <sub>1 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Duplicate Content Detected**<br><code>duplicate_content</code> | 🟠 warning | Keep one page as the canonical version of this content and either rewrite the others or point their rel=canonical at it. |

## Freshness  <sub>8 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Future Date**<br><code>future_date</code> | 🔴 error | Correct the JSON-LD datePublished value to the real publication or modification time; a future date suppresses indexing and misreports freshness. |
| ✅ | **Modified Before Published**<br><code>modified_before_published</code> | 🔴 error | Set the modified date to the real last-edit time, or to the published date when the page has never been edited. |
|  | **Published Dates Come From One Machine Run**<br><code>dates_from_one_machine_run</code> | 🟠 warning | Store a publication date per page when it first goes live and never rewrite it. If these pages really were published in one batch, give them all the same timestamp and accept this issue. |
| ✅ | **Dates Taken From the Clock, Not the Content**<br><code>dates_read_at_render</code> | 🟠 warning | Take the published date from the content's own metadata and the modified date from its last real edit, rather than calling the clock while the page renders. |
| ✅ | **Invalid Date Format**<br><code>invalid_date_format</code> | 🟠 warning | Append the timezone offset (for example +00:00 or Z) to the JSON-LD dateModified value. |
| ✅ | **Missing Article Dates**<br><code>missing_dates</code> | 🟠 warning | Add datePublished and dateModified to the Article JSON-LD block as ISO-8601 values with a timezone offset. |
|  | **Modified Date Mismatch**<br><code>modified_date_mismatch</code> | 🟠 warning | Make one source authoritative - usually the CMS modified timestamp - and derive the JSON-LD, meta tag, sitemap lastmod and visible date from it. |
| ✅ | **Stale Content**<br><code>stale_content</code> | 🔵 info | Review and refresh the content, then bump dateModified, article:modified_time and the sitemap lastmod together. |

## Indexability  <sub>2 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
|  | **Nofollow Tag Present**<br><code>nofollow</code> | 🔴 error | — |
| ✅ | **Noindex Tag Present**<br><code>noindex</code> | 🔴 error | — |

## Links  <sub>12 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Ambiguous Anchor Text**<br><code>ambiguous_anchor</code> | 🟠 warning | Give each target its own anchor, so one phrase means one page. |
| ✅ | **Page Too Deep**<br><code>deep_page</code> | 🟠 warning | Link to the page from a shallower hub so it sits within a few clicks. |
|  | **Internal Link Removed**<br><code>internal_link_removed</code> | 🟠 warning | Restore the links, or confirm they were removed on purpose - check whether a template or menu change dropped them site-wide. |
|  | **Orphan Page**<br><code>orphan_page</code> | 🟠 warning | Add a link from the body copy of a related page. |
| ✅ | **Self-Cannibalising Anchor Text**<br><code>self_cannibalising_anchor</code> | 🟠 warning | Point the keyword anchor at the page that targets it, or reword it. |
| ✅ | **Page Reachable Only From the Sitemap**<br><code>sitemap_only_page</code> | 🟠 warning | Link to the page from a relevant page, or drop it from the sitemap. |
| ✅ | **Generic Anchor Text**<br><code>generic_anchor</code> | 🔵 info | Replace the anchor with words describing the target page. |
|  | **Inconsistent Anchor Text**<br><code>inconsistent_anchors</code> | 🔵 info | Settle on one or two anchors per target and use them consistently. |
| ✅ | **Section Hub Not Linked**<br><code>missing_parent_link</code> | 🔵 info | Link to the section hub from the page, usually in a breadcrumb. |
| ✅ | **One-Way Internal Link**<br><code>one_way_pair</code> | 🔵 info | Add a link back where the two pages are genuinely related. |
| ✅ | **Internal Links Carry Tracking Parameters**<br><code>tracking_parameters_on_internal_links</code> | 🔵 info | — |
| ✅ | **Weakly Linked Page**<br><code>weak_page</code> | 🔵 info | Add content links from related pages so the page earns its place. |

## Mobile  <sub>1 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
|  | **Missing Viewport Meta Tag**<br><code>missing_viewport</code> | 🔴 error | — |

## Performance  <sub>4 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Large Page Size**<br><code>large_page_size</code> | 🔴 error | — |
|  | **Slow Response Time**<br><code>slow_response_time</code> | 🔴 error | — |
| ✅ | **Moderate Page Size**<br><code>moderate_page_size</code> | 🟠 warning | — |
|  | **Moderate Response Time**<br><code>moderate_response_time</code> | 🟠 warning | — |

## Rendered  <sub>9 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
|  | **Image Rendered Larger Than Its Source**<br><code>rendered_image_oversized</code> | 🔴 error | Serve the image at the size it is displayed, or cap its CSS width to the source width. |
| ✅ | **Page Chrome Not Visible**<br><code>rendered_chrome_hidden</code> | 🟠 warning | Remove the CSS rule hiding the nav, or the layout the rule belongs to. |
| ✅ | **JavaScript Console Error**<br><code>rendered_console_error</code> | 🟠 warning | Fix the script that throws, or remove it from the page. |
| ✅ | **Heading Styled But Not Marked Up**<br><code>rendered_fake_heading</code> | 🟠 warning | Replace the element with the heading level it looks like, or drop the heading styling. |
|  | **Text Hidden By CSS**<br><code>rendered_hidden_text</code> | 🟠 warning | Delete the text, or remove the rule that hides it, so the markup matches the page. |
| ✅ | **Structured Data Removed By JavaScript**<br><code>rendered_jsonld_removed</code> | 🟠 warning | Stop the script that rewrites the page from discarding the JSON-LD block. |
| ✅ | **Horizontal Overflow**<br><code>rendered_layout_overflow</code> | 🟠 warning | Constrain the overflowing element with max-width:100% or overflow-x handling at this breakpoint. |
| ✅ | **Structured Data Added By JavaScript**<br><code>rendered_jsonld_injected</code> | 🔵 info | Emit this JSON-LD in the server response so every parser sees it. |
|  | **Visible Text Missing From The HTML**<br><code>rendered_text_js_injected</code> | 🔵 info | Render this copy server-side so it is in the HTML response. |

## Rules  <sub>1 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Forbidden phrase (rule "coverage-banned-claim")**<br><code>rule:coverage-banned-claim</code> | 🟠 warning | Remove or reword the text matching "guaranteed results" on this page. |

## SEO  <sub>7 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
|  | **Missing H1 Tag**<br><code>missing_h1</code> | 🔴 error | — |
|  | **Missing Meta Description**<br><code>missing_meta_description</code> | 🔴 error | — |
|  | **Missing Title Tag**<br><code>missing_title</code> | 🔴 error | — |
|  | **Meta Description Too Long**<br><code>meta_description_too_long</code> | 🟠 warning | — |
| ✅ | **Meta Description Too Short**<br><code>meta_description_too_short</code> | 🟠 warning | — |
|  | **Title Too Long**<br><code>title_too_long</code> | 🟠 warning | — |
| ✅ | **Title Too Short**<br><code>title_too_short</code> | 🟠 warning | — |

## Security  <sub>10 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
|  | **Cookie Readable by Script**<br><code>cookie_without_httponly</code> | 🟠 warning | Add HttpOnly to every cookie that no script needs to read. |
| ✅ | **CSP Allows 'unsafe-eval'**<br><code>csp_unsafe_eval</code> | 🟠 warning | Remove 'unsafe-eval' and replace the code that needs it. |
| ✅ | **CSP Allows 'unsafe-inline'**<br><code>csp_unsafe_inline</code> | 🟠 warning | Replace 'unsafe-inline' with a per-response nonce or a hash for each inline script. |
| ✅ | **Referrer-Policy Leaks Full URLs**<br><code>insecure_referrer_policy</code> | 🟠 warning | Replace unsafe-url with strict-origin-when-cross-origin. |
| ✅ | **No X-Content-Type-Options**<br><code>missing_x_content_type_options</code> | 🟠 warning | Send X-Content-Type-Options: nosniff on every response. |
| ✅ | **Page Can Be Framed**<br><code>missing_x_frame_options</code> | 🟠 warning | Send X-Frame-Options: SAMEORIGIN, or a CSP frame-ancestors 'self' directive. |
| ✅ | **No Content-Security-Policy**<br><code>missing_csp</code> | 🔵 info | Send a Content-Security-Policy header, starting with a report-only policy if the site has never had one. |
| ✅ | **No Permissions-Policy**<br><code>missing_permissions_policy</code> | 🔵 info | Send a Permissions-Policy header denying the browser features the page does not use. |
| ✅ | **No Referrer-Policy**<br><code>missing_referrer_policy</code> | 🔵 info | Send Referrer-Policy: strict-origin-when-cross-origin. |
| ✅ | **Server Version Disclosed**<br><code>server_version_disclosed</code> | 🔵 info | Drop the version from the Server header and remove X-Powered-By entirely. |

## Sitemap  <sub>20 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Sitemap File Error**<br><code>sitemap_file_error</code> | 🔴 error | Serve every sitemap file listed in robots.txt and in the index with HTTP 200 and valid XML. |
| ✅ | **Sitemap URL Blocked by robots.txt**<br><code>sitemap_url_blocked_by_robots</code> | 🔴 error | Allow these URLs in robots.txt, or remove them from the sitemap. |
| ✅ | **Sitemap URL Canonicalises Elsewhere**<br><code>sitemap_url_canonicalised_away</code> | 🔴 error | List the canonical URL in the sitemap instead of this one. |
| ✅ | **Sitemap URL Is noindex**<br><code>sitemap_url_noindex</code> | 🔴 error | Either remove the noindex tag or drop the URL from the sitemap. |
| ✅ | **Sitemap URL Returns 4xx**<br><code>sitemap_url_not_found</code> | 🔴 error | Remove dead URLs from the sitemap, or restore the pages they point at. |
| ✅ | **Sitemap URL Returns 5xx**<br><code>sitemap_url_server_error</code> | 🔴 error | Fix the server errors, or remove those URLs from the sitemap. |
|  | **Sitemap/Canonical Mismatch**<br><code>sitemap_canonical_mismatch</code> | 🟠 warning | Emit the exact canonical URL in the sitemap, character for character. |
| ✅ | **Duplicate URL in Sitemaps**<br><code>sitemap_duplicate_urls</code> | 🟠 warning | List each URL in exactly one sitemap file, once. |
| ✅ | **Inconsistent Trailing Slashes**<br><code>sitemap_inconsistent_trailing_slash</code> | 🟠 warning | Pick one trailing-slash convention and list only canonical URLs in that form. |
| ✅ | **Most Sitemap Entries Have No lastmod**<br><code>sitemap_lastmod_coverage_low</code> | 🟠 warning | Emit an accurate <lastmod> for every URL from the CMS's modified date. |
| ✅ | **lastmod in the Future**<br><code>sitemap_lastmod_future</code> | 🟠 warning | Emit the real modified date; a future date makes the signal worthless. |
| ✅ | **Malformed lastmod**<br><code>sitemap_lastmod_malformed</code> | 🟠 warning | Write <lastmod> as YYYY-MM-DD or a full W3C datetime with a timezone offset. |
|  | **lastmod Not Updated**<br><code>sitemap_lastmod_not_bumped</code> | 🟠 warning | Make the CMS write the real modified date into the sitemap when content is edited. |
|  | **No Readable Sitemap**<br><code>sitemap_missing</code> | 🟠 warning | Publish an XML sitemap and declare it with a Sitemap: line in robots.txt. |
| ✅ | **Mixed Hostnames in Sitemap**<br><code>sitemap_mixed_hosts</code> | 🟠 warning | List every URL on the canonical hostname only. |
| ✅ | **Sitemap Index Nested Too Deep**<br><code>sitemap_nested_index</code> | 🟠 warning | Flatten the sitemap tree: an index must point directly at URL sitemaps. |
| ✅ | **Sitemap URL Redirects**<br><code>sitemap_url_redirects</code> | 🟠 warning | List the final destination URL in the sitemap instead of the redirecting one. |
| ✅ | **Missing lastmod**<br><code>sitemap_lastmod_missing</code> | 🔵 info | Emit <lastmod> from the CMS modified date for every entry. |
| ✅ | **Crawled Page Missing From Sitemap**<br><code>sitemap_missing_crawled_url</code> | 🔵 info | Add these URLs to the sitemap, or mark them noindex if they should not be indexed. |
| ✅ | **Sitemap URL Not Crawled**<br><code>sitemap_url_not_crawled</code> | 🔵 info | Check that these URLs are linked from the site, or raise the crawl limit. |

## Social  <sub>2 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **Missing OpenGraph Tags**<br><code>missing_opengraph</code> | 🟠 warning | — |
| ✅ | **Missing Twitter Card Tags**<br><code>missing_twitter_card</code> | 🟠 warning | — |

## Structured Data  <sub>1 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **No Structured Data**<br><code>no_structured_data</code> | 🔴 error | — |

## Technical  <sub>50 checks</sub>

| | Check | Severity | What to do |
|---|---|---|---|
| ✅ | **404 Client Error**<br><code>404_client_error</code> | 🔴 error | — |
| ✅ | **Canonical Blocked by robots.txt**<br><code>canonical_blocked_by_robots</code> | 🔴 error | Allow the canonical URL in robots.txt; a blocked canonical cannot be read. |
|  | **Canonical Points to Another Domain**<br><code>canonical_cross_domain</code> | 🔴 error | Point the canonical at this site, or remove the page if the other domain owns it. |
|  | **Canonical Points to a Noindex Page**<br><code>canonical_to_noindex</code> | 🔴 error | Canonicalise to an indexable page, or drop the noindex from the target. |
| ✅ | **Connection Error**<br><code>connection_error</code> | 🔴 error | — |
|  | **Connection Refused**<br><code>connection_refused</code> | 🔴 error | — |
|  | **DNS Not Found**<br><code>dns_not_found</code> | 🔴 error | — |
|  | **FAQ Markup Not in the Visible Text**<br><code>faq_answer_not_visible</code> | 🔴 error | Make the marked-up question the exact string the page shows, numbering prefixes included. |
| ✅ | **Invalid hreflang Code**<br><code>hreflang_invalid_code</code> | 🔴 error | Use a language code, optionally with a region: en, en-GB, es-US. |
| ✅ | **hreflang Return Link Missing**<br><code>hreflang_no_return_link</code> | 🔴 error | Every hreflang must be mutual: if A names B, B must name A. |
| ✅ | **JSON-LD Fails to Parse**<br><code>jsonld_parse_error</code> | 🔴 error | Fix the JSON syntax; a block that does not parse is ignored entirely by search engines. |
| ✅ | **Page Links to Broken URLs**<br><code>links_to_broken_urls</code> | 🔴 error | Update or remove the link; a visitor following it hits an error. |
| ✅ | **Noindex Page in the Sitemap**<br><code>noindex_in_sitemap</code> | 🔴 error | Remove the URL from the sitemap, or remove its noindex. |
|  | **Redirect Loop**<br><code>redirect_loop</code> | 🔴 error | Break the loop: a redirect must end at a URL that serves content. |
| ✅ | **Request Timeout**<br><code>request_timeout</code> | 🔴 error | — |
| ✅ | **SSL/TLS Error**<br><code>ssl_tls_error</code> | 🔴 error | — |
| ✅ | **Canonical Chain**<br><code>canonical_chain</code> | 🟠 warning | Point every page at the final canonical URL directly. |
| ✅ | **Canonical URL Different**<br><code>canonical_different</code> | 🟠 warning | — |
| ✅ | **Relative Canonical URL**<br><code>canonical_relative</code> | 🟠 warning | Write the canonical as an absolute URL; a relative one is resolved inconsistently. |
| ✅ | **Canonical Points to a Redirect**<br><code>canonical_to_redirect</code> | 🟠 warning | Canonicalise to the destination of the redirect. |
| ✅ | **Duplicate Meta Description Across Pages**<br><code>duplicate_meta_description</code> | 🟠 warning | Write a description per page; a shared one is usually rewritten by Google. |
| ✅ | **Duplicate Title Across Pages**<br><code>duplicate_title</code> | 🟠 warning | Give every page a title that describes that page alone. |
|  | **Empty Alt on an Image That Means Something**<br><code>empty_alt_on_content_image</code> | 🟠 warning | Describe the image in its alt, or give the link a text label; keep alt="" for decoration only. |
| ✅ | **Empty Heading**<br><code>empty_heading</code> | 🟠 warning | Remove the empty heading, or give it text. |
|  | **hreflang Set Has No Self-Reference**<br><code>hreflang_missing_self</code> | 🟠 warning | Add an hreflang entry pointing at the page itself. |
|  | **Lazy-Loaded Image Above the Fold**<br><code>image_lazy_above_fold</code> | 🟠 warning | Load the first images eagerly; lazy-loading them delays the largest paint. |
| ✅ | **Image Without Width and Height**<br><code>image_missing_dimensions</code> | 🟠 warning | Set width and height so the browser can reserve the space and the layout stops jumping. |
|  | **Oversized Image**<br><code>image_too_heavy</code> | 🟠 warning | Compress the image and serve a modern format at the size it is displayed. |
| ✅ | **Nofollow on Internal Links**<br><code>internal_link_nofollow</code> | 🟠 warning | Remove rel="nofollow" from internal links; it only wastes the link. |
| ✅ | **Internal Links Point at Redirects**<br><code>internal_link_to_redirect</code> | 🟠 warning | Link straight to the destination; every redirect costs a round trip. |
|  | **JSON-LD @id Resolves to Nothing**<br><code>jsonld_dangling_id</code> | 🟠 warning | Define the referenced node on the page, or inline the object instead of referencing it. |
| ✅ | **Missing Canonical URL**<br><code>missing_canonical</code> | 🟠 warning | — |
| ✅ | **Internal Links Mix www and non-www**<br><code>mixed_host_links</code> | 🟠 warning | Pick one hostname and link to it consistently. |
| ✅ | **Internal Links Mix http and https**<br><code>mixed_protocol_links</code> | 🟠 warning | Link internally over https only. |
| ✅ | **More Than One H1**<br><code>multiple_h1</code> | 🟠 warning | Keep one H1 per page and demote the others to H2. |
| ✅ | **Noindex Page Linked Internally**<br><code>noindex_page_linked</code> | 🟠 warning | Drop the links, or make the page indexable if it should rank. |
| ✅ | **Heavy Page**<br><code>page_too_heavy</code> | 🟠 warning | Trim the payload: images, embedded fonts and scripts dominate page weight. |
| ✅ | **Broken Pagination Chain**<br><code>pagination_broken_chain</code> | 🟠 warning | Point rel="next" at the next page that exists, or drop it from the last page. |
| ✅ | **Paginated Page Canonicalises to Page 1**<br><code>pagination_canonical_to_page_one</code> | 🟠 warning | Make each paginated page canonical to itself; Google reads a canonical to page 1 as a mistake. |
| ✅ | **Redirect Chain**<br><code>redirect_chain</code> | 🟠 warning | Point the first URL straight at the final destination. |
|  | **Slow Time to First Byte**<br><code>slow_ttfb</code> | 🟠 warning | Cache the response or speed up the server; nothing renders before the first byte. |
|  | **Templated Title Says Nothing**<br><code>templated_title</code> | 🟠 warning | Put what the page is about before the template, and make it different per page. |
|  | **Meta Description Too Wide**<br><code>description_too_wide</code> | 🔵 info | Put the point in the first 900px; the rest is cut off. |
| ✅ | **Duplicate H1 Across Pages**<br><code>duplicate_h1</code> | 🔵 info | Make each H1 name what is on that page. |
| ✅ | **Stray Whitespace in Heading Text**<br><code>heading_stray_whitespace</code> | 🔵 info | Trim the heading text; the spacing shows up in search results and anchors. |
| ✅ | **Pagination Does Not Link Back**<br><code>pagination_no_return_link</code> | 🔵 info | Add rel="prev" pointing back at the page that names this one as rel="next". |
| ✅ | **Paginated Page Has No Inbound Links**<br><code>pagination_orphaned_page</code> | 🔵 info | Link the page from a listing, a sitemap or navigation so it does not depend on the pagination chain. |
| ✅ | **Heading Level Skipped**<br><code>skipped_heading_level</code> | 🔵 info | Step heading levels one at a time so the outline reads correctly. |
|  | **Title Too Wide for Search Results**<br><code>title_too_wide</code> | 🔵 info | Shorten the title, or move the wide part to the end where it can be cut. |
|  | **Content Page Cannot Be Cached**<br><code>uncacheable_content</code> | 🔵 info | Allow caching for pages that do not change per visitor. |

## Observed firing, not described above  <sub>59 checks</sub>

These fired in the coverage sweep but have never been reported through
the install this reference was generated from, so there is no wording
to quote for them yet. They are listed rather than dropped, because a
reference that silently omits a third of the checks is how a tool ends
up with checks nobody knows exist.

- `agent_unavailable` · `agent_weak_experience` · `authority_page_buried`
- `bot_spoofing` · `broken_image` · `casing_mistake`
- `cls_needs_improvement` · `cls_poor` · `cms_author_unexpected`
- `cookie_without_samesite` · `cookie_without_secure_flag` · `crawl_budget_wasted`
- `field_cls_poor` · `field_first_contentful_paint_poor` · `field_inp_poor`
- `field_lcp_poor` · `first_contentful_paint_poor` · `ga_high_bounce`
- `ga_no_sessions` · `ga_page_not_in_crawl` · `grammar_mistake`
- `gsc_canonical_overridden` · `gsc_low_ctr` · `gsc_no_impressions`
- `gsc_not_indexed` · `gsc_page_not_in_crawl` · `gsc_query_cannibalisation`
- `high_spam_score` · `hsts_max_age_too_short` · `inp_needs_improvement`
- `inp_poor` · `insecure_form_action` · `lcp_needs_improvement`
- `lcp_poor` · `link_metrics_unavailable` · `log_format_unrecognised`
- `missing_hsts` · `mixed_content_active` · `mixed_content_passive`
- `mobile_desktop_divergence` · `never_requested_by_search` · `no_external_interest`
- `pdf_damaged` · `pdf_encrypted` · `pdf_missing_title`
- `pdf_no_text_layer` · `pdf_too_heavy` · `performance_score_low`
- `punctuation_mistake` · `requested_but_not_crawled` · `sitemap_empty`
- `sitemap_file_limit` · `sitemap_mixed_protocols` · `sitemap_too_large`
- `sitemap_too_many_urls` · `speed_index_poor` · `spelling_mistake`
- `style_suggestion` · `time_to_interactive_poor`

---

*163 checks described, 59 more observed firing, 222 in total. Generated by
`tools/generate_checks_doc.py`. Per-install counts and example text are
deliberately omitted: both are facts about one machine's crawls rather
than about the check.*

---

Last updated: 2026-10-20. Source: [docs/checks.md on GitHub](https://github.com/spcaeo/toadstone/blob/main/docs/checks.md).
