# Contributing to Earthcraft

Thanks for taking a look. Earthcraft is early enough that a clear issue, a
small test, or a careful correction to the documentation can be as useful as a
new feature.

## Before changing code

Read the short [project plan](docs/PLAN.md) and the relevant source notes. In
particular, keep the one-block-per-metre coordinate contract intact, preserve
source provenance, and keep unknown geography marked as unknown. A plausible
render is not evidence of a correct reconstruction.

For a new idea, open an issue first if it changes the data model, source
policy, coordinate system, or public claims. For a small fix, a pull request
with a short explanation is fine. Please use a public landmark or a synthetic
fixture in examples; do not upload a private address, personal photograph, or
unlicensed map extract.

## Local setup

The supported development setup is Python 3.11 with `uv` on macOS. The public
test environment is intentionally ordinary Python and does not require a model
download, a hosted service, or a Minecraft client.

```sh
uv venv .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
PYTHONPATH=scripts .venv/bin/python -m unittest discover -s tests -v
```

Run the test suite from the repository root. If you are working on an
optional source adapter, also run its focused tests and explain any live-data
or game-client checks that you could not run.

## Branches, commits, and pull requests

Use a short branch name such as `fix/kml-height`, `docs/contributing`, or
`research/source-coverage`. Keep commits understandable on their own. A
useful sequence might be a fixture and test, the implementation, then the
documentation; it does not need to be one enormous commit. Write commit
messages in the present tense, for example:

```text
Fix KML height rounding at the world floor
Add an offline fixture for incomplete building metadata
Document the source terms for the new adapter
```

Earthcraft's history is written and reviewed by people. Do not add
`Co-authored-by` trailers for AI systems, bots, or other tools. Do not paste
generated release notes or generic marketing copy into a commit or pull
request. If a tool helped you work, inspect every line and submit it under
your own authorship.

A pull request should say:

- what changed and why;
- how it was tested, including the exact command;
- whether it changes generated geometry, coordinate handling, source rights,
  resource use, or public claims;
- what remains unverified.

Keep unrelated cleanup out of the same pull request. Screenshots are welcome
for a visual change, but they do not replace a geometry or file-format check.

## Data and source adapters

Do not commit raw downloads, generated worlds, model weights, run logs, or
credentials. Use a synthetic fixture for CI and keep larger inputs in an
ignored local workspace. Every real source adapter should retain its source
identifier, capture or retrieval date, license, checksum, coordinate system,
transformation, and distribution decision.

The default path is deterministic and uses no AI. Optional local computer
vision experiments must remain explicit, bounded, reproducible, and separate
from the default build. No hosted inference or silent fallback is acceptable,
and no model may invent geometry that the admitted sources do not support.

## Review standard

Reviewers should be able to reproduce the claimed result from the pull
request. Check the tests, inspect the diff for personal paths and generated
files, and ask for a narrower claim when the evidence is narrower than the
language. It is completely acceptable for a change to end with a documented
unknown or a failed experiment.
