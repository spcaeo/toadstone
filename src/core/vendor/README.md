# Vendored third-party code

## axe.min.js

[axe-core](https://github.com/dequelabs/axe-core) 4.10.2, the accessibility
engine Deque publishes under the Mozilla Public License 2.0. The licence
header is kept intact at the top of the file.

It is vendored rather than fetched at crawl time for two reasons. A crawler
that pulled it from a CDN would break on an install with no outbound network,
and injecting a remote script into a page under audit is exactly the thing a
strict Content-Security-Policy on that page is there to stop. Playwright adds
the file's contents to the page directly, so neither applies.

Upgrading: replace the file, update the version here and in
`src/core/accessibility.py`, then run `python3 tests/accessibility_tests.py`.
