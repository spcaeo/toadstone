## What this changes

<!-- One or two sentences. What is different after this is merged? -->

## How you convinced yourself it works

<!--
"The suite passes" is necessary and is not an answer. What did you run, and
what did it print? If you added a check, say which fixture page makes it fire.
If you added a safeguard, say how you broke the thing it guards and watched it
catch it.
-->

## Checklist

- [ ] A test fails without this change and passes with it
- [ ] The full suite passes (`for t in tests/*_tests.py; do python "$t"; done`)
- [ ] A new check fires in `tests/coverage_site.py`, or the PR says why it cannot
- [ ] No file I added or grew is over 1,000 lines (see CONTRIBUTING for the four already over)
- [ ] `python tools/verify_build_log.py` passes if the log was touched

## Anything you did not fix

<!-- Write it down here rather than leaving it to be rediscovered. -->
