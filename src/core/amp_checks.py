"""AMP validation: the rules that actually stop a page being served as AMP.

Google's own validator applies several hundred rules. Reimplementing it would
be a project in itself and would drift from the spec within a release, so this
does not try. It checks the mandatory markup and the forbidden constructs -
the rules that are stable, that account for nearly every real AMP failure,
and that a crawler can judge from the HTML it already has.

Everything here is decided from markup this crawler already parses. No
network call, no second fetch.

Two boundaries worth stating.

A page is only judged when it **claims** to be AMP, by the `amp` or `⚡`
attribute on `<html>`. Judging an ordinary page against AMP rules would
report every site on the internet as broken AMP.

A non-AMP page that *links* to an AMP version is judged on the link, not the
markup: `amphtml_missing_canonical` is about the pair being wired up in both
directions, which is the mistake that actually loses the AMP page from search.
"""

import re

from src.utils.issue_factory import make_issue

CATEGORY = 'AMP'

# The AMP runtime, which every AMP page must load.
RUNTIME = 'https://cdn.ampproject.org/v0.js'

# Tags AMP replaces with its own component, and what to use instead.
FORBIDDEN_TAGS = {
    'img': 'amp-img or amp-anim',
    'video': 'amp-video',
    'audio': 'amp-audio',
    'iframe': 'amp-iframe',
    'frame': 'nothing - frames are not valid AMP',
    'frameset': 'nothing - frames are not valid AMP',
    'object': 'nothing - plugins are not valid AMP',
    'embed': 'nothing - plugins are not valid AMP',
    'applet': 'nothing - applets are not valid AMP',
    'form': 'amp-form, which must be loaded as an extension',
}

DEFAULTS = {
    'max_examples': 5,
}

_AMP_HTML = re.compile(r'<html[^>]*\s(amp|⚡)(?=[\s>=])', re.I)
_TAG = re.compile(r'<\s*(%s)\b' % '|'.join(FORBIDDEN_TAGS), re.I)
_STYLE_TAG = re.compile(r'<style([^>]*)>', re.I)
_STYLESHEET = re.compile(r'<link[^>]+rel=["\']?stylesheet["\']?[^>]*>', re.I)
_INLINE_STYLE = re.compile(r'<[a-z][^>]*\sstyle\s*=', re.I)
_SCRIPT = re.compile(r'<script([^>]*)>', re.I)
_BOILERPLATE = re.compile(r'amp-boilerplate', re.I)
_AMPHTML_LINK = re.compile(r'<link[^>]+rel=["\']?amphtml["\']?[^>]*href=["\']([^"\']+)', re.I)
_CANONICAL = re.compile(r'<link[^>]+rel=["\']?canonical["\']?[^>]*href=["\']([^"\']+)', re.I)
_CHARSET = re.compile(r'<meta[^>]+charset=["\']?utf-8', re.I)
_VIEWPORT = re.compile(r'<meta[^>]+name=["\']?viewport["\']?', re.I)


def is_amp(html):
    """Does this page claim to be AMP?"""
    return bool(html) and bool(_AMP_HTML.search(html[:4000]))


def amphtml_link(html):
    """The AMP version this page points at, if any."""
    found = _AMPHTML_LINK.search(html or '')
    return found.group(1) if found else ''


def _config(config=None):
    cfg = dict(DEFAULTS)
    cfg.update(config or {})
    return cfg


def _issue(url, check, severity, name, details, offending, evidence, fix):
    return make_issue(url=url, check=check, severity=severity, category=CATEGORY,
                      issue=name, details=details, offending_value=offending,
                      evidence=evidence, how_to_fix=fix)


def page_issues(page, config=None):
    """Validate one page's AMP markup. Returns [] for a page that is not AMP."""
    cfg = _config(config)
    html = page.get('html') or ''
    url = page.get('url') or ''
    if not is_amp(html):
        return []

    issues = []

    if RUNTIME not in html:
        issues.append(_issue(
            url, 'amp_runtime_missing', 'error', 'AMP Runtime Not Loaded',
            'This page declares itself as AMP but does not load the AMP runtime, '
            'so it will not validate and will not be served as AMP.',
            RUNTIME, {'expected': f'<script async src="{RUNTIME}"></script>'},
            f'Add <script async src="{RUNTIME}"></script> to the head.'))

    if not _BOILERPLATE.search(html):
        issues.append(_issue(
            url, 'amp_boilerplate_missing', 'error', 'AMP Boilerplate Missing',
            'The required amp-boilerplate style block is absent, which fails '
            'validation on its own.',
            'amp-boilerplate', {'expected': 'the amp-boilerplate style and noscript block'},
            'Paste the boilerplate from the AMP specification into the head, unchanged.'))

    if not _CHARSET.search(html[:2000]):
        issues.append(_issue(
            url, 'amp_charset_missing', 'error', 'AMP Charset Missing',
            'AMP requires <meta charset="utf-8"> as the first child of the head.',
            'charset', {'expected': '<meta charset="utf-8">'},
            'Make <meta charset="utf-8"> the first element in the head.'))

    if not _VIEWPORT.search(html):
        issues.append(_issue(
            url, 'amp_viewport_missing', 'error', 'AMP Viewport Missing',
            'AMP requires a viewport meta tag naming at least width=device-width.',
            'viewport', {'expected': '<meta name="viewport" content="width=device-width">'},
            'Add <meta name="viewport" content="width=device-width"> to the head.'))

    found_tags = {}
    for match in _TAG.finditer(html):
        tag = match.group(1).lower()
        found_tags.setdefault(tag, 0)
        found_tags[tag] += 1
    if found_tags:
        listed = ', '.join(f'<{tag}> x{count}' for tag, count in sorted(found_tags.items()))
        issues.append(_issue(
            url, 'amp_forbidden_tag', 'error', 'Tag Not Allowed in AMP',
            f'AMP replaces these with its own components: {listed}.',
            ','.join(sorted(found_tags)),
            {'found': listed,
             'expected': '; '.join(f'<{t}> -> {FORBIDDEN_TAGS[t]}' for t in sorted(found_tags))},
            'Swap each tag for its AMP component and load that component\'s extension script.'))

    if _STYLESHEET.search(html):
        issues.append(_issue(
            url, 'amp_external_stylesheet', 'error', 'External Stylesheet in AMP',
            'AMP allows no external stylesheets except its own fonts; all CSS must '
            'be inline in a single <style amp-custom> block.',
            'stylesheet', {'found': _STYLESHEET.search(html).group(0)[:200]},
            'Inline the CSS into <style amp-custom> and remove the stylesheet link.'))

    custom = [m for m in _STYLE_TAG.finditer(html) if 'amp-custom' in (m.group(1) or '')]
    if len(custom) > 1:
        issues.append(_issue(
            url, 'amp_multiple_custom_styles', 'error', 'More Than One amp-custom Block',
            f'AMP allows exactly one <style amp-custom> block; this page has {len(custom)}.',
            str(len(custom)), {'found': f'{len(custom)} blocks', 'expected': 'exactly one'},
            'Merge every custom style into one <style amp-custom> block.'))

    inline = len(_INLINE_STYLE.findall(html))
    if inline:
        issues.append(_issue(
            url, 'amp_inline_style', 'error', 'Inline style Attribute in AMP',
            f'AMP forbids the style attribute; {inline} element(s) carry one.',
            str(inline), {'found': f'{inline} element(s)'},
            'Move those declarations into the <style amp-custom> block as classes.'))

    bad_scripts = []
    for match in _SCRIPT.finditer(html):
        attrs = match.group(1) or ''
        if 'application/ld+json' in attrs or 'application/json' in attrs:
            continue
        if 'cdn.ampproject.org' in attrs:
            continue
        if 'custom-element' in attrs or 'custom-template' in attrs:
            continue
        bad_scripts.append(match.group(0)[:120])
    if bad_scripts:
        issues.append(_issue(
            url, 'amp_custom_javascript', 'error', 'Author JavaScript in AMP',
            f'AMP runs no author JavaScript; {len(bad_scripts)} script tag(s) are '
            f'neither the AMP runtime, an AMP extension, nor JSON.',
            str(len(bad_scripts)),
            {'found': ' | '.join(bad_scripts[:cfg['max_examples']])},
            'Remove the script, or express the behaviour with an AMP component.'))

    canonical = _CANONICAL.search(html)
    if not canonical:
        issues.append(_issue(
            url, 'amp_canonical_missing', 'error', 'AMP Page Has No Canonical',
            'An AMP page must name its canonical version, or itself when it is '
            'the only version.',
            'canonical', {'expected': 'a rel=canonical link'},
            'Add a rel=canonical link pointing at the non-AMP page, or at this page.'))

    return issues


def pairing_issues(pages, config=None):
    """Whether AMP pages and their canonical pages point at each other.

    This is the mistake that actually loses an AMP page from search: the
    markup validates, but the pair is only wired in one direction, so the AMP
    version is never discovered or never associated with the page it serves.
    """
    _config(config)
    issues = []
    by_url = {}
    for page in pages or []:
        if isinstance(page, dict) and page.get('url'):
            by_url[page['url'].rstrip('/')] = page

    for page in pages or []:
        if not isinstance(page, dict):
            continue
        html = page.get('html') or ''
        url = page.get('url') or ''
        if not url or not html:
            continue

        pointed_at = amphtml_link(html)
        if pointed_at and not is_amp(html):
            target = by_url.get(pointed_at.rstrip('/'))
            if target is None:
                issues.append(_issue(
                    url, 'amphtml_target_not_crawled', 'info',
                    'AMP Version Was Not Crawled',
                    f'This page points at an AMP version at {pointed_at}, which this '
                    f'crawl never fetched, so its markup was not checked.',
                    pointed_at, {'found': pointed_at},
                    'Include the AMP URL in the crawl, or check it separately.'))
            elif not is_amp(target.get('html') or ''):
                issues.append(_issue(
                    url, 'amphtml_target_not_amp', 'error',
                    'AMP Link Points at a Page That Is Not AMP',
                    f'{pointed_at} is linked as the AMP version but does not declare '
                    f'itself as AMP.',
                    pointed_at, {'found': pointed_at, 'expected': 'a page with <html amp>'},
                    'Point rel=amphtml at the real AMP page, or remove the link.'))

        if is_amp(html):
            canonical = _CANONICAL.search(html)
            if not canonical:
                continue  # already reported by page_issues
            target = by_url.get(canonical.group(1).rstrip('/'))
            if target is None or target['url'].rstrip('/') == url.rstrip('/'):
                continue
            if not amphtml_link(target.get('html') or ''):
                issues.append(_issue(
                    url, 'amphtml_missing_canonical', 'warning',
                    'Canonical Page Does Not Link Back to the AMP Version',
                    f'This AMP page names {target["url"]} as canonical, but that page '
                    f'has no rel=amphtml link back, so the pair is only wired one way.',
                    target['url'], {'found': 'no rel=amphtml on the canonical page'},
                    'Add <link rel="amphtml" href="..."> to the canonical page.'))
    return issues


def amp_issues(pages, config=None):
    """Every AMP issue for a crawl."""
    issues = []
    for page in pages or []:
        if isinstance(page, dict):
            issues.extend(page_issues(page, config))
    issues.extend(pairing_issues(pages, config))
    return issues
