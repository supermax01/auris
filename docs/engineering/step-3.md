# Step 3: official offline validator lanes

The reference is `validator_cli-7.0.5.jar`, SHA-256
`9dfe9f305b0f9389eceade2a4a1c9a2740d7b0e614066fdf07a78b571c76d92f`,
204,856,882 bytes, Apache-2.0. No Python dependency or warm wrapper was added.
The six additional validator-closure pins in `config/frozen.toml` are
CC0-1.0. Existing US Core dependencies retain their recorded licences;
packages and JAR remain in the ignored cache, not in the repository.

`auris setup validator` verifies the JAR, all 17 tarball hashes, declared
package identities/licences and supplied byte sizes, then stages a fresh
dedicated Java package cache. `--fetch` permits a missing JAR download only;
FHIR tarballs must already exist from `auris setup fhir`. Download tests use
fake fetchers. A mismatching cached artifact is refused, never overwritten
by a fetch. Unsafe tar paths and links are rejected. Setup and validation
must not run concurrently against the same cache home.

The seeded layout is
`cache/validator/home/.fhir/packages/<id>#<version>/package/`, with the
tarball's package contents and indexes. `packages.ini` is byte-identical
to the observed online cache: `[cache]`, `version = 4`, CRLF line endings.
An additional `auris-pins.json` receipt lets the runner reject a missing or
stale seed before Java starts. Filesystem layout was compared against all
17 folders from the online probe; no probe folders were copied. The pinned
R4 tarballs retain three StructureDefinitions each that the online-installed
core/examples folders omit. Root OpenAPI/image assets are not seeded, and
Java may regenerate `.index.json`/`.index.db` files. These differences do
not prevent offline loading or change the 26 probe results.

Two versions each of extensions.r4 and terminology.r4 are intentional.
Duplicate `(id, version)` pins remain forbidden; `FhirConfig.package(id,
version)` selects the exact version. The package manifests actually declare
R5 core 5.0.0 in **three** packages: hl7.fhir.uv.extensions 5.4.0,
hl7.terminology 7.5.0 and hl7.terminology.r5 7.5.0. It was not needed by
the observed validator closure. Setup reports these declarations and does
not fetch R5 core or fail because it is absent.

## Running and consuming validation

`ValidatorRunner(config, cache, timeout_seconds=180)` resolves Java using
`AURIS_JAVA`, then `JAVA_HOME/bin/java`, then PATH. A broken explicit override
is reported instead of silently selecting another runtime. The heap is
the frozen config value (2 GB maximum). Every resource's base copy and real
profile-bearing copy are passed together to one JVM. Input-file extensions
on the returned OperationOutcomes identify results; entry order is ignored.

The CLI flags are `-version 4.0.1 -ig hl7.fhir.us.core#6.1.0 -tx n/a
-txCache n/a -no-http-access -disable-default-resource-fetcher -locale en-US`.
No package or terminology download is possible through the validator's HTTP
layer. JVM environment option overrides are removed. Inputs and the output
file are 0600 in a disposable 0700 workdir. Raw output, stdout/stderr,
the command, elapsed seconds and failure reason are retained in `ValidationRun`;
resource content is not sent to application logs. Timeout, process failure,
missing dependencies and malformed/incomplete outcomes produce NOT_CHECKED.
An unresolved profile produces profile NOT_CHECKED even if its message is
only a warning.

`validate_mappings(runner, intake, outcomes)` checks the unchanged intake
anchors along with every built resource, returning updated outcomes and the
run. `apply_validation` can apply a result separately, but verifies the
resource hash. `write_validation(path, intake, outcomes, run)` writes all five
lanes, issues, claimed profiles, review reasons, export states and labels,
plus the raw validator run, with the existing private-storage helper.
Intake anchors honestly report evidence NOT_CHECKED because they are supplied,
not transcript-extracted. Ledger rows also remain visible in the report.

`MappingOutcome.export_state` is now computed. A built resource waits in
`pending_validation` until schema, evidence, R4 and profile pass. Any FAIL
becomes `validation_failed`, including after review. Material/review-flagged
resources require a review digest matching their current resource content.
Edits invalidate validation and approval by changing the digest. The review
workflow/log remains a later step; this step supplies the gate only.
`build_collection(..., validation=run)` also requires checked, unchanged
intake anchors and checks every selected resource against this run. Preview
still includes built review/failing resources. No caller can assign a
`final_exportable` decision independently of the checks.

Tier zero always reports terminology NOT_CHECKED. Terminology diagnostics
are informational in that lane, preserving their original severity and
the raw OperationOutcome. Structural warnings/information stay visible but
do not fail a lane. A passing record says exactly
`profile-checked, terminology NOT_CHECKED`; failed/incomplete records say so.
The label `validated` requires all five lanes to pass, impossible through
this tier-zero runner.

## Offline proof and observed oracle behavior

The commands below ran from the repository root in the network-blocked
sandbox. `AURIS_JAVA` denotes the supplied local Temurin 21 executable;
no installation or network setup was run. The help was read using:

```sh
"$AURIS_JAVA" -Duser.home="$PWD/cache/validator/home" \
  -jar cache/validator/validator_cli-7.0.5.jar -help
.venv/bin/python -m auris.cli setup fhir
.venv/bin/python -m auris.cli setup validator
```

Exact offline probe command (26 pre-existing fictional probe files):

```sh
"$AURIS_JAVA" -Xmx2g -Duser.home="$PWD/cache/validator/home" \
  -jar cache/validator/validator_cli-7.0.5.jar cache/validator/probe/*.json \
  -version 4.0.1 -ig hl7.fhir.us.core#6.1.0 -tx n/a \
  -no-http-access -disable-default-resource-fetcher \
  -output cache/validator/probe-offline-outcome.json \
  > cache/validator/probe-offline.log 2>&1
```

It exited 0 with zero error/fatal issues across all 26 inputs. The official
timing line was `Loading: 00:04.983, validation: 00:00.376 (#26)`.
After the final reseed the same command again exited 0 with 26 outcomes
and zero error/fatal issues: `Loading: 00:04.165, validation: 00:00.413 (#26)`.
This matches the online probe's zero errors. Recorded fixture runs using
the Python runner took 5.546 s for 16 resources/32 inputs (including anchors)
and 5.079 s for six broken resources/12 inputs. The full test-suite run
reported 6.236 s and 5.252 s respectively. Times include JVM startup and
preflight, and vary with index/cache warmth.

The integration tests enable `allow_example_urls=True`, which adds
`-allow-example-urls true`. The official validator otherwise rejects the
fictional anchors' example.org identifier systems; this is an explicit
fixture option, not a mapper change or a suppressed clinical constraint.
No golden clinical resource required a mapper fix. Golden outcome metadata
changed to reflect pending validation; clinical golden resources are unchanged.
The demo intake encounter alone moved to 2026-11-02 09:00–09:20 at -05:00,
so the pinned FY2027 ICD-10-CM release applies. Independent golden visits
remain on their existing fictional November 12 date.

| Broken input | Base R4 | US Core profile |
|---|---|---|
| Allergy without clinicalStatus | FAIL (`ait-1`) | PASS (no additional error) |
| BP root value, no components | FAIL | FAIL |
| BP missing systolic slice | FAIL | FAIL |
| MedicationRequest without intent | FAIL | FAIL (inherited cardinality repeated in US Core context) |
| Condition without category | PASS | FAIL |
| Vital without effectiveDateTime | FAIL | FAIL |

**Scope caveat:** removing `meta.profile` does not suppress the official
validator's automatic R4 vital-profile checks. Its recorded
`VALIDATION_VAL_PROFILE_SIGNPOST_OBS` message explains that the LOINC code
requires the corresponding R4 vital profile. Hence the BP/time mutations
also fail pass 1. The lane classifier preserves that real checker scope;
it does not hide base errors to produce profile-only failures. Pass 2
subtracts matching pass-1 diagnostics as a multiset, ignoring source
line/column extensions; different profile-context diagnostic text remains
visible. In particular, inherited medication intent cardinality is reported
again with a US Core source URL. These observations qualify the brief's
profile-only examples; they do not change the binding FHIR reference.

## Checks and follow-ups

```sh
.venv/bin/ruff check
.venv/bin/ruff format --check
.venv/bin/pytest -m 'not validator' -q
.venv/bin/pytest -m validator -q
.venv/bin/pytest -q
```

Pytest uses the existing `--disable-socket` default. The validator marker is
registered. Integration tests skip with an actionable reason only when Java
or the JAR is absent; corrupt artifacts or stale seeds fail. The one warning
is the existing test that intentionally proves sockets are blocked.

Final summaries:

```text
ruff check: All checks passed!
ruff format --check: 58 files already formatted
fast tests: 712 passed, 2 deselected, 1 warning in 0.73s
integration-only run: 2 passed, 710 deselected in 10.49s
full suite: 714 passed, 1 warning in 12.24s
git diff --check: exit 0, no output
```

The integration-only run preceded two additional fast failure-handling tests;
the full suite includes them. Its validator timings were 6.236 s for goldens
and anchors and 5.252 s for broken resources.

Follow-ups: the review action/log and run-wide orchestration, transaction
server acceptance, the warm-wrapper contract test and CI artifact provisioning
remain in their planned later steps. No unresolved dependency gate remains.
