# Step 7a — human annotation kit and timed development pilot

This step implements the first half of the annotation kit in PLAN §10, §12 and
A9, using the A3 reduced schema and existing harness gold types. PLAN and the
verified FHIR reference were read before implementation; there is no disagreement
with `docs/fhir-reference.md`. No prompt, frozen configuration, mapper, export
rule, existing dataset split, dependency or licence was changed. No model was
invoked and no clinical gold or agreement result was generated.

## Launch and workflow

Prerequisites: the coordinator has prepared the pinned public transcripts under
`cache/datasets/prepared/<dataset>/` and the read-only terminology index at
`cache/terminology/index.sqlite`. These commands do not download or run setup.
Defaults respect `AURIS_CACHE`; `--cache` names the cache root, rather than the
`datasets` subdirectory. Run pilots from development membership only:

```sh
.venv/bin/auris annotate pilot \
  --dataset eval/datasets/primock57-dev.json --n 8 --seed 1729 \
  --out out/annotation/primock-pilot

.venv/bin/auris annotate pilot \
  --dataset eval/datasets/aci-bench-dev.json --n 8 --seed 1729 \
  --out out/annotation/aci-pilot

.venv/bin/auris annotate serve \
  --assignment out/annotation/primock-pilot/assignment-A.json \
  --workdir out/annotation/primock-work-A
```

Use `assignment-B.json` and a separate workdir for B. A and B are pseudonyms,
not personal identities. Choose new/empty pilot output directories; existing
annotation workdirs can be resumed. `serve` also accepts `--manifests` for the
coordinator's trusted frozen manifests, and `--guidelines` for the issued guide.
The guide must exactly match the assignment hash. Keep returned annotation files
outside public version control.

The server prints a fresh `http://127.0.0.1:<random-port>/#token=...` URL. Open that
URL locally and keep it private. The token remains in the browser fragment and is
sent in the `X-Auris-Token` header on every API call; it is never logged or stored
in browser local storage. Stop with Ctrl-C, which saves current timing.

Screenshot-free workflow: the left pane shows one encounter's transcript, each
turn's source role and audio bounds, or its original turn number for untimed ACI.
The annotator explicitly confirms/corrects turn roles. Read-only transcript text
areas support mouse selection and keyboard selection with Shift + arrows.
Select words within a turn, then Add fact. The right pane opens a blank human
form with common dropdowns and fields for the chosen kind. Add selected evidence
can append another independently verified span from any turn. No fact labels,
values, acceptable wordings or codes are generated from the transcript.

The local lookup displays RxNorm ingredient/clinical-drug or billable ICD-10-CM
rows for a human query. Clicking a row fills only its code/concept ID; the human
must decide that it fits. Otherwise enter an `auris:` slug. Coded concept IDs
use the scorer's exact `system-URI:code` convention, rather than a shortened
system label. Fill acceptable
surface wordings and critical-error fields, then Apply fact and save. Existing
facts have Edit/Delete controls. Clinically important facts outside the nine
kinds have their own category/reason/evidence/notes form and list. Encounter
notes record ambiguity, breaks and off-tool work. Ctrl/Command-S explicitly
saves. Valid form edits autosave after a short pause without closing the form;
invalid edits stay open with inline errors and are not written. Complete or
cancel an open form before switching encounters. Encounter done requires all
turn roles to be explicitly reviewed; editing can reopen a completed encounter.

## Storage, evidence and blind access

`assignment-1` embeds the frozen public membership and its source hash, encounter
order, annotator pseudonym and guideline SHA-256. The server compares it with the
coordinator's frozen manifest before loading assigned transcripts, and verifies
prepared content hashes and source identities. The pilot validates development
transcripts only. It samples proportionally within the manifest's existing short,
medium and long strata, using largest-remainder quotas and a recorded seed.
Independent seeded A/B shuffles preserve the same encounter set. `pilot.json`
records selection/seed; `pilot-sheet.md` is the printable instruction sheet.

The pilot also generates deterministic, clearly fictional intake anchors for
**every encounter filename in that dataset's prepared cache**, including visits
outside the pilot. It enumerates non-development filenames without opening their
transcripts. All synthetic identifiers/names and encounter types are labelled
fictional; gender is unknown. Dates lie within FY2027 (2026-10-01–2027-09-30).
These are evaluation context, not recovered source demographics or clinical
facts. Each anchor validates against the existing A1 Intake model. Existing
intakes are never modified. No standard diagnosis or drug code is invented.

`annotation-1` stores one annotator's original work separately from adjudicated
gold: pseudonym, encounter/transcript hash, guideline hash, UTC start/update
timestamps, completion state, timing, human role decisions, fact/encounter notes,
reduced-schema assertions, acceptable wordings, concept IDs/codes, critical
attributes, and important out-of-scope facts. Evidence is segment ID plus
zero-based Unicode-character start/end (end exclusive) and SHA-256 of the exact
UTF-8 substring. Browser UTF-16 selection positions are converted to character
indexes. `/api/span` checks submitted text, offsets and hash against the server's
transcript; save rechecks every evidence hash. Saved evidence contains no quote
copies. Schemas reject inappropriate kind fields and inconsistent critical
attributes. Codes must identify eligible rows actually present in the local
read-only index. Lookup searches literal official rows and never calls the
production mapper's candidate/alias machinery; acceptable wordings are human
decisions. A regression verifies code-only concept matching in the existing
scorer after annotation conversion. Canonical values, when supplied, must be
finite decimals.

Documents are `<workdir>/<pseudonym>/<encounter>.annotation.json`, under 0700
directories with 0600 atomic writes through the existing private storage helper.
Symlink destinations and shared directory/file permissions are rejected. Client
requests cannot set identities, timestamps, guideline hashes or elapsed totals.
Only exact assigned filenames are loaded on resume. An interrupted atomic replace
preserves the previous file and removes the temporary file.

The stdlib HTTP server binds only to `127.0.0.1`, port 0; there is no configurable
external bind. Its allowlisted routes are `/api/session`, `/api/encounter`,
`/api/span`, `/api/lookup`, `/api/save` and `/api/timing`. It serves one packaged
inline HTML/CSS/JS page, with native light/dark appearance and labelled keyboard
controls. No directory listing, arbitrary-file handler, reference note, model
output, generated FHIR, prediction or other-annotator route exists. Host/Origin
checks and a restrictive content policy limit requests to this local origin.
HTTP logs are disabled; error responses go only to the local page. Nothing loads
an LLM, performs pre-annotation, accesses run outputs or makes external requests.

Test assignments use the existing shared `eval/test-ledger.jsonl`. The first
session requires `--locked-test-run` (or explicit rerun confirmation); later
sessions with overlapping test content require `--confirm-locked-test-rerun`.
Refusals are audited before transcript loading. Successful annotation sessions
reserve test use through the same ledger's `run` action, conservatively consuming
that reservation even if interrupted. Thus a second annotator or later inference
also needs explicit rerun confirmation. This is an accidental-exposure guard,
not tamper-proof authentication. Preserve the ledger across cache migrations.
Pilots always reject test manifests, even if other flags would authorize serving.

`auris.eval.gold.convert_annotation(path, transcript)` verifies a completed
annotation and converts its assertions/important facts into the existing gold
fact types, without manufacturing adjudication provenance or a scored Dataset.
The original file retains timing, notes, attribution and role decisions. A third
clinician must still adjudicate A/B and supply truthful provenance and the final
role-corrected transcript when assembling clinical gold. The regression manifest
that exercises `load_dataset` is explicitly fictional, harness-authored and
non-independent.

## Timing and human protocol

The browser reports focus/visibility and age of the last pointer, key, input or
scroll interaction every 10 seconds, and on focus/visibility changes. A monotonic
server clock counts active intervals only after a foreground report and caps
credit at the last reported interaction plus **60 seconds**. A delayed/background
tab cannot credit its entire gap. This is sampled research timing, not an
attention detector; long quiet reading needs a fresh interaction. Wall time
counts while that encounter is open and the server is running. Switching
encounters and completion stop its clock. Editing resumes a completed encounter;
restarting resumes saved totals without counting server downtime. Abrupt browser
closure/crash can lose the final unsaved interval or add up to the idle cutoff;
annotators must record such events and off-tool work in notes. Explicit save is
recommended before stopping.

The guide and one-page desk reference cover all nine kinds, evidence, spoken
values/medication details, person versus speaker, past versus resolved, pending
versus excluded diagnoses, NKA versus denial versus unobtained history, coding,
critical attributes, skipped chatter/questions and important out-of-scope facts.
Fourteen fictional teaching utterances are isolated from all extraction prompt
versions, demo, JSON transcript fixtures and harness-check encounters by the
unchanged step-4b leakage guard, including its original clinical watchwords.
They are teaching examples, never candidate gold for assigned encounters.

Two clinically trained humans annotate independently and blind; the third
clinician adjudicates afterward. The guide specifies timed 5–10 encounter dev
pilots, per-kind pre-adjudication agreement, detection F1 and Cohen's kappa with
explicit denominators/missing cases, and revision between rounds. Freeze only
at detection F1 >= 0.80 and kappa >= 0.70; after three unsuccessful rounds narrow
the schema and repeat, never lower the thresholds. No agreement, clinician work
or frozen guideline is claimed by these setup tools. Re-price PLAN §10's budget
from actual annotation/training/adjudication time.

## Verification and follow-ups

All Python checks use the existing project venv with networking disabled. No
mise, uv setup/add/sync/run, language/dependency installation, network call, model
inference, commit or push was performed. Dependencies added: **none**. New Auris
code and authored fictional test material are Apache-2.0; existing terminology
licences and CC-BY-4.0 dataset provenance are unchanged. The inline script was
syntax-checked using the already installed Node runtime, invoked from project
Python; the tool itself needs only stdlib and existing pydantic.

The offline tests call service and HTTP handlers directly, without localhost
sockets. They cover token checks, bind address, Host/Origin, Unicode/tampered
spans, kind/critical validation, local index rows and immutable lookup, allowlisted
access, frozen membership and ledger refusal, server-owned idle/focus timing,
completion/reopen timing, private atomic save/resume, conversion into harness gold,
deterministic stratified pilots for both datasets, fictional FY2027 intakes and
example leakage. Temporary real-cache development pilot smoke checks also verify
prepared input hashes and synthetic anchor construction; no held-out transcript
or model output is opened.

Follow-ups for Claude/owner: click through the actual UI outside the socket-blocked
sandbox, including keyboard selection, Unicode, edit/delete and background/idle
behaviour; review/commit the diff; name the two clinical annotators and third
adjudicator; run the human pilot and collect time/IAA; revise and freeze the guide
only after the stated gates. Later annotation/adjudication work must assemble
clinical gold and reconcile human role corrections. Pending diagnostic
possibilities are recorded out of scope because A3 has no differential kind;
this pilot policy needs clinical review before guideline freeze. The separate
30-encounter independent-FHIR subset and IAA/adjudication automation remain
outside Step 7a. No FHIR/export policy deviation was introduced.

Final verification (direct project venv; networking disabled):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
131 files already formatted

.venv/bin/pytest tests/annotation -q --disable-socket --tb=short
38 passed in 2.24s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 6.586s
Validator wall-clock time — demo + display variants: 7.217s
Validator wall-clock time — broken resources: 7.392s
1414 passed, 2 deselected, 1 warning in 32.10s

git diff --check
(no output; exit 0)

Inline JavaScript syntax and Unicode selection: passed

Real-cache pilot smoke checks (temporary private output):
primock57: 8 selected; short=3, medium=3, long=2; 57 valid fictional intake anchors
aci-bench: 8 selected; short=3, medium=3, long=2; 195 valid fictional intake anchors
Temporary pilot outputs removed; no annotations or held-out transcripts opened.

New-file personal-path and email scans: passed
```

The warning is the existing deliberate socket-blocking test. Both real-model
checks were deselected; the official validator regressions ran offline. The
focused annotation tests were rerun after the final literal-index lookup change.
Additional commands were read-only `rg`, `cat`, `sed`, `head`, `wc`, `pwd`, git
status/branch/diff; file creation/patches and project-Python editing/smoke scripts;
`ruff format`; and `auris annotate`, `serve`, and `pilot --help` (exit 0). Earlier
lint findings were formatting/long-line issues corrected before these final
checks. The JavaScript syntax/Unicode probe used existing Node through a
project-Python subprocess, without installing packages or opening a socket.

