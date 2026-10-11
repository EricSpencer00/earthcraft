# Unfinished archive repair proposals

This is a source checkpoint for two prepared Earthcraft repairs. Neither patch
is integrated or installed by this public checkpoint. The transport candidate
had a recorded task-owned local installation on 8 October; the contract
candidate remained isolated at that review. This checkpoint does not change
hosted CI, Pages deployment, the archive controller, or world generation.

The public base is commit `92e21255d2281aa521d36ca8aa485226e118ab3a`.
The exporter, uploader, and archive-controller baseline files targeted by
these patches are not present on that public commit. Integrate the authorized
baseline first; neither patch is directly applicable to public `main` as-is.
Other owners' unpublished files are not bundled to fill that gap.

## Archive command timeout proposal

`archive-transport-repair.patch` makes archive subprocess promises settle after
a timeout even when an exited process leaves an inherited pipe open. It adds
process-group cleanup, explicit timeout/output-limit classifications, and
foreground archive SSH options. Ordinary transport settings remain separate.
Transport retries retain their finite attempt/backoff limits and reject
authentication failures, uploader timeouts, and output-limit failures.

The prepared candidate passed 22 local synthetic subprocess/retry tests and
had a recorded local installation on 8 October 2026. That result is historical
candidate qualification, not a fresh production run or proof that an external
source can be read. The original
300-second export timeout and controller ownership prerequisites remain
unresolved; this checkpoint authorizes no retry or resume.

## Point-only archive contract proposal

`generated-tile-contract-v2.patch` adds typed point-only evidence to a versioned
shard/index contract, validates its source/frame/count/hash declarations, and
protects incomparable point-only and paired-roof representations from replacing
one another. Legacy version-one exports remain supported.

The prepared candidate passed 47 offline tests on 8 October 2026, using
synthetic point arrays, NBT fixtures, and a fake Hub client. A previously
archived version-one sample also passed offline uploader validation. The tests
do not establish positive production point-only measurements, independent
geometry accuracy, datum agreement, measured facade color, or game acceptance.
Maximum-density production memory and I/O performance still need measurement.
The legacy comparison test also depends on the historical sibling `prior/`
directory. Rebase must relocate and pin that frozen-prior fixture before the
tests can qualify a normal integrated repository.

## Provenance and remaining work

`qualification.json` records exact patch, prior-file, and candidate-file hashes
with the scope of the historical checks. No world, source archive, credential,
machine-local configuration, private runtime log, or third-party game asset is
included. Existing raw diagnostic evidence stays private.

Next, establish the intended baseline, rebase the repairs, rerun their focused
and complete offline suites, and qualify a frozen source-backed point-only
sample. Controller installation or resume additionally requires reliable
source receipt access and separately permitted process/lock ownership checks.
The working headless renderer and hosted CI/CD already published on `main`
validate archived evidence; they do not bypass these generation requirements.
