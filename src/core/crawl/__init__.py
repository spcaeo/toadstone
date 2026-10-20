"""Parts of the crawler, split out to keep any one file readable.

`src/crawler.py` had grown past 2,600 lines, well over this project's
1,000-line limit. These are groups of methods lifted out of that class
unchanged and mixed back into it. They are mixins rather than free functions
because each one reaches for crawler state - the config, the running flag,
the issue detector, the results list - and threading all of that through
arguments would be a far larger change than moving the code.
"""
