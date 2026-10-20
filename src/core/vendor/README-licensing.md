# Vendored third-party code, and the licence that governs it

`axe.min.js` is [axe-core](https://github.com/dequelabs/axe-core) 4.10.2 by
Deque Systems, bundled unmodified, under the **Mozilla Public License 2.0** —
the full text of which is beside it in `LICENSE.MPL-2.0`.

## The carve-out, stated plainly

Toadstone as a whole is licensed under the Elastic License 2.0. **The Elastic
License 2.0 does not apply to this file.**

`axe.min.js` remains available to you under the MPL-2.0, on MPL-2.0's terms,
and nothing in Toadstone's licence restricts the rights MPL-2.0 grants you in
it. That is not generosity; MPL-2.0 section 3.3 permits distributing a Larger
Work under terms of your choosing *provided* the Covered Software stays under
the MPL and the recipients' rights in it are not restricted. Saying so here is
how that condition is met.

The same applies to any MPL-2.0 component installed as a dependency rather than
vendored here — at the time of writing, `certifi`, which arrives with
`requests` and ships in every environment and every Docker image built from
this repository. See `NOTICE`.

## What "unmodified" means here

The file is byte-for-byte as published:

    axe-core 4.10.2
    sha256  b511cd9d…  (verify with: shasum -a 256 axe.min.js)

Its header carries Deque's copyright and the MPL Exhibit A notice, intact, as
section 3.4 requires. **If you modify it, MPL-2.0 section 3.2 obliges you to
make the modified source available** under the MPL — which is the one
obligation in this repository that attaches to a file rather than to the
project, and the reason this note exists rather than a line in NOTICE.
