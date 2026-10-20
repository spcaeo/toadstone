"""Run every fixture mode and print how many distinct checks fired.

    python tools/coverage_sweep.py

This is the number the README quotes, and it is the only claim in this project
that matters: not how many checks exist, but how many have been *watched* to
fire against a site built to be wrong.

Why a tool rather than a loop in the README: `tests/coverage_site.py` truncates
its slug log at the start of every run, so six runs pointed at one file leave
you with the slugs from the sixth and a confidently wrong total. (That mistake
was made while writing this; it reported 30 instead of 180.) Each mode gets its
own file here, and the union is taken at the end.

Modes, and why each needs a crawl of its own:

    default              the whole broken site
    SITEMAP_LIMITS=1     a sitemap of 50,001 URLs and an index of 51 children,
                         which would otherwise queue tens of thousands of URLs
                         and crowd out every other check
    SITEMAP_LIMITS=2     a site where no sitemap lists a single URL, which
                         cannot share a run with one that works
    SITEMAP_LIMITS=3     52MB of sitemap, for the size limit
    SILENT_IMAGE=1       a handler that accepts the connection and answers
                         nothing, which doubles the runtime
    https                the checks that only exist over TLS
"""

import argparse
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MODES = [
    ('default',          {},                                'tests/coverage_site.py'),
    ('sitemap-limits-1', {'COVERAGE_SITEMAP_LIMITS': '1'},  'tests/coverage_site.py'),
    ('sitemap-limits-2', {'COVERAGE_SITEMAP_LIMITS': '2'},  'tests/coverage_site.py'),
    ('sitemap-limits-3', {'COVERAGE_SITEMAP_LIMITS': '3'},  'tests/coverage_site.py'),
    ('silent-image',     {'COVERAGE_SILENT_IMAGE': '1'},    'tests/coverage_site.py'),
    ('https',            {},                                'tests/coverage_site_https.py'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', help='write the union of slugs here')
    ap.add_argument('--expect', type=int, default=0,
                    help='exit non-zero if fewer than this many distinct checks fire')
    ap.add_argument('--only', help='run one mode by name')
    args = ap.parse_args()

    modes = [m for m in MODES if not args.only or m[0] == args.only]
    if not modes:
        print(f'no such mode: {args.only}. Try: {", ".join(m[0] for m in MODES)}')
        return 2

    work = tempfile.mkdtemp(prefix='toadstone-sweep-')
    union, failed = set(), []

    for name, extra, script in modes:
        log = os.path.join(work, name + '.txt')
        env = dict(os.environ, TOADSTONE_SLUG_LOG=log, **extra)
        print(f'--- {name}', flush=True)
        result = subprocess.run([sys.executable, script], cwd=REPO, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        for line in result.stdout.splitlines():
            if line.startswith(('pages crawled', 'checks fired')) or line.strip() == 'OK':
                print('   ' + line.strip(), flush=True)
        if result.returncode != 0:
            failed.append(name)
            print(f'   FAILED (exit {result.returncode})', flush=True)
        slugs = set()
        if os.path.exists(log):
            with open(log) as fh:
                slugs = {l.strip() for l in fh if l.strip()}
        union |= slugs
        print(f'   {len(slugs)} distinct checks in this mode', flush=True)

    print(f'\n{len(union)} distinct checks fired across {len(modes)} mode(s)')
    if args.out:
        with open(args.out, 'w') as fh:
            fh.write('\n'.join(sorted(union)) + '\n')
        print(f'written to {args.out}')
    if failed:
        print('modes that failed: ' + ', '.join(failed))
        return 1
    if args.expect and len(union) < args.expect:
        print(f'FAIL: expected at least {args.expect}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
