# Step 6c — public adapters, split manifests and lexical coverage

This step implements PLAN §10 and A9 without running extraction, scoring a model,
creating clinical gold, changing the active prompt, or changing any FHIR rule.
There is no disagreement with `docs/fhir-reference.md`. No dependency was added.
Auris code remains Apache-2.0; the two attributed dataset extracts are CC BY 4.0.

## Offline setup and trust anchor

```sh
.venv/bin/auris setup datasets
.venv/bin/python -m auris.datasets.audit
```

Both commands require Claude's local sparse checkouts and perform no downloads or
Git subprocess calls. `setup datasets` accepts `--config`, `--cache` (the datasets
parent), and `--manifests`. Defaults respect `AURIS_CACHE` and the repository's
`eval/datasets` directory. Pins in `config/frozen.toml` include repository URL,
commit, licence and the SHA-256 of each committed source-file manifest. Those
manifests are the first-fetch trust anchor from the owner-supplied checkouts;
HEAD alone would not detect modified working files. They hash all 117 PriMock57
and 29 ACI files consumed by the adapters and provenance audit, including CSV
identity maps and licence/readme files. Audio, notes and consultation checklists
are unused. ACI JSON files also contain reference notes; their bytes are hashed,
but their `tgt` values are never used for adaptation, counting or splitting.

Setup resolves detached HEAD, loose refs, packed refs and worktree/common Git
directories by reading files. It refuses mismatched commits, source inventories,
source bytes and source-manifest hashes. Adapter reads recheck hashes before
UTF-8 decoding. Resolved source paths must stay inside their checkout. Missing
sources fail closed. It verifies committed split membership and transcript hashes
against a deterministic reconstruction; it never regenerates different splits.

Prepared transcripts and copies of the SHA manifests go only into
`cache/datasets/prepared/<dataset>/`, at 0600 inside 0700 directories. Logs contain
counts and identifiers, never transcript text. Setup writes both prompt audits
and prepares annotation inputs before reporting any leakage refusal. Existing
identical preparation is repeatable. No public manifest contains transcript text,
patient details, reference notes or purported annotations.

## PriMock57 representation

The parser accepts the supplied long-form `ooTextFile` / `TextGrid`, exactly one
`IntervalTier`, either a role-named tier or generic `Speaker` (112 of 114 files).
It checks interval numbering, counts, syntax, finite nonnegative bounds and
within-tier ordering. Doubled Praat quotes decode to one literal quote; multiline
text is supported. Filename suffix is the source of doctor/patient roles.

Doctor becomes `clinician`, patient becomes `patient`. Nonempty source intervals
are merged by start time, then doctor before patient on exact ties, then original
interval number. Overlaps are retained, without changing bounds. Segment IDs
retain role and original interval number. The 6,460 empty/whitespace-only source
intervals are dropped as silence.

Extraction text replaces each syntactic angle-bracket tag matching
`</?[A-Za-z][A-Za-z0-9_]*(?:\s*/)?\s*>` with a space, then collapses whitespace.
It never deletes enclosed words, repetitions, fillers or punctuation, guesses an
unintelligible word, or repairs an uncertain transcription. Exact decoded raw
text is retained on every segment for future WER work. Markup-only utterances
remain represented with empty extraction text (381 segments); no audio or words
are fabricated. Counted removals across all 57 visits:

| Tag | Tokens removed |
|---|---:|
| `<UNSURE>` | 1,132 |
| `</UNSURE>` | 1,132 |
| `<UNIN/>` | 1,359 |
| `<INAUDIBLE_SPEECH/>` | 814 |

Total removed tag characters: 44,223. Whitespace normalization is not counted as
markup removal. These counts are also stored in `primock57-adaptation.json` and
per-transcript source metadata. Raw text remains the reference; cleaned text is
not itself a claimed WER normalization standard.

Timing source is `human_utterance_bounds`, never ASR word timing. Aligned evidence
uses exact character offsets into cleaned text and the human utterance's full
audio bounds, marked approximate for a quote that may occupy only part of it.
Every transcript records source repository, commit, licence, encounter, variant,
source file paths and unadjudicated role provenance.

## ACI canonical text and roles

The source experiment JSONs use subset-local IDs such as `0-aci`, not D2N IDs.
Each JSON row is joined through its metadata CSV position and source identity
(`dataset`, `id`) to the challenge metadata's D2N encounter. Counts, IDs and
cross-split identity uniqueness are checked. Neither array position in the
challenge file nor a lexical resemblance is used to infer encounter identity.

The canonical policy is fixed before sampling:

- `virtscribe_humantrans` for valid/test1/test2/test3: named human transcription.
- `aci_asrcorr` for all five upstream cohorts: corrected ASR, with
  **speaker tags unverified**. No raw `aci_asr` or `virtscribe_asr` text is used.
- `virtassist` challenge `*_full.json` source dialogue: the scripted/reference
  text track; no alternative human/ASR experiment file exists for that subset.
- The 12 train `virtscribe` encounters are ineligible: no named train human
  variant was supplied. This is a conservative selection exception, pending
  upstream provenance clarification, not an assertion that the challenge
  transcript is raw ASR. The remaining train pool has 55 encounters; valid has
  20 and each test cohort has 40. Total canonical pool: 195, not 207.

The resulting 83 human/scripted references and 112 corrected-ASR transcripts are
reported separately in coverage. ACI's human/scripted subset is not described as
perfect clinical gold. Every speaker role still needs A9 adjudication. In
particular, corrected text does not establish that ASR speaker swaps were fixed.

Supported tags are `[doctor]`, `[patient]`, `[patient_guest]`, mapped to clinician,
patient and companion. Case is normalized; consecutive turns are never merged.
Unknown or malformed bracket tags refuse parsing. The supplied inline
`[ inaudible HH:MM:SS ]` annotation is preserved as source text and is **not** an
audio time. Two source empty turns remain as empty turns. One untagged opening
utterance is preserved as an `unknown` role; no speaker is guessed.

Transcript schema 2 introduces explicit `untimed` mode, absent start/end times,
no word timings, and consecutive one-based turn numbers. Chunked prompt rendering
retains the original turn numbers. Evidence has exact character offsets and turn
number, `audio_source=untimed`, and null audio offsets/approximation. SOAP receipts
cite `[turn N · role]`, including when a quote fails alignment. No synthetic audio
time is inferred. Schema-1 serialization and content hashes stay byte-compatible
with the existing fictional harness and already-frozen evaluations. Schema 2 uses
its own domain-separated hash and includes raw/source/timing metadata.

## Frozen membership and pending gold

All manifests declare `none (awaiting clinician annotation)` and have no intake
anchors or gold assertions. The evaluation runner explicitly refuses to treat
these membership manifests as scored datasets. Clinician gold and supplied,
fictional evaluation intake anchors must be prepared independently under the
existing evaluation schema; no patient or clinical facts are fabricated here.

Selection uses seed 1729, word-count rank terciles within each upstream cohort
(ties by encounter ID), proportional largest-remainder quotas, then Python 3.12
`Random.sample` over sorted IDs inside each stratum. Equal fractional remainders
favor short, then medium, then long. PriMock57 dev is 17 and the complement test
is 40. ACI dev includes all 20 valid plus 3 train (one per length tercile); test
selects 20 per upstream test1/test2/test3 using seeds 1730/1731/1732. Unselected
ACI encounters remain unused. Coverage never affects selection. Full-pool counts
are computed before selection, and coverage artifacts are written before split
files. No model output was used or viewed.

Each entry records D2N/source encounter ID, canonical transcript hash, a separate
text-only hash, variant, role warning, upstream cohort, word count and stratum.
Both within-split and dev/test duplicate text are refused. A changed seed, source,
variant, transcript or membership makes verification fail. `auris.datasets.audit`
creates absent manifests on first execution and otherwise compares bytes; it
refuses to overwrite changed artifacts. Do not regenerate frozen membership to
respond to test results.

## Extraction lock and leakage guard

The **first** test extraction now requires `--locked-test-run` (or the existing
explicit `--confirm-locked-test-rerun`). Merely invoking `eval run` cannot expose
test predictions. Every authorized test run reserves the split before server
startup. Refused unflagged attempts and refused repeat runs/scores are appended
to the shared 0600 test ledger; refused attempts do not consume a reservation.
A failed extraction still consumes its reservation. Reusing a split or overlapping
transcript hashes requires `--confirm-locked-test-rerun`; the first score for an
accepted run is permitted and later scores require confirmation as before.
Dev and harness-check runs do not lock. The ledger is an accidental-use guard,
not tamper-resistant access control; keep it across cache migrations.

Public evaluation scans the active prompt against all eligible canonical corpora,
including unused ACI encounters, as well as the supplied evaluation transcripts.
The step-4b exact-sentence and Jaccard >= 0.6 near-duplicate rules are unchanged.
Only the clinical watchword policy changes for public corpora: a shared term
(e.g. asthma) is a countable warning, since common clinical vocabulary is expected.
Authored fixtures retain the original refusal policy. Findings recorded in public
audit files contain only hashes/counts/settings, never quotes. Each corpus text is
one encounter joined by newlines, so sentence boundaries remain separated and
clinical-warning counts count affected encounters per term. This is a heuristic,
not proof against pretraining contamination.

At active prompt SHA-256
`a66c105f84af129589e5047c1eb3b4457a13b527cebee495030cb7f05cffa93e`:

| Dataset | Exact hits | Near-duplicate hits | Shared watchword hits | Result |
|---|---:|---:|---:|---|
| PriMock57 (57) | 0 | 1 | 0 | REFUSE |
| ACI canonical pool (195) | 0 | 0 | 0 | PASS |

Therefore `auris setup datasets` currently exits 1 **as required**, after writing
private prepared inputs, source manifests and both audit results. Public extraction
also refuses this prompt; no hit was waived and no prompt was changed to improve
results. The model-free coverage/split audit is allowed independently and does not
reserve or run test extraction. Follow-up: Claude/owner must review the prompt hit
under the existing prompt-change process. Do not weaken the guard or tune against
held-out model outputs to bypass it.

## Coverage and verification

The full tables, count units, lexicon hash and limitations are in
[`coverage-counts.md`](../../eval/datasets/coverage-counts.md), with machine-readable
aggregates alongside. PriMock57 has 5 vital candidates in 3 encounters, 7 new-order
candidates in 6, and zero dosed-order candidates under these shallow patterns.
Allergy-word counts include questions and repeated mentions and do not establish
affirmed allergies. The private-set targets in PLAN §10 remain necessary. Public
mock data can overlap model pretraining; no clinical accuracy is claimed.

Offline tests use authored fictional consultations and two small attributed
CC BY extracts. They cover parsing/refusals, overlap merge, retained words/raw
text, metadata identity joins, absent timings, turn receipts, legacy hashes,
deterministic stratification, pin and content mismatches, ledger refusal before
inference, public leakage warning/refusal, and the lexical counter. They need no
dataset cache, model or network. Existing validator regressions use the seeded
local cache. Real-model tests are not run in this step.

Follow-ups: review the prompt hit; confirm the excluded training-variant
provenance; create clinician annotations/adjudicated roles and supplied intakes;
obtain the independent FHIR subset; fill the commissioned recording gaps; run
locked test inference only in Slice 4. No dependency/setup install, network call,
model invocation, commit or push was performed.

Final verification (direct project venv; pytest networking disabled):

```text
.venv/bin/ruff check
All checks passed!

.venv/bin/ruff format --check
116 files already formatted

.venv/bin/pytest tests/datasets tests/test_transcript.py tests/test_evidence.py tests/test_fhir_mapper.py -q --disable-socket --tb=short
397 passed in 0.91s

.venv/bin/pytest -m 'not llm' -q --disable-socket --tb=short -rs
Validator wall-clock time — goldens + anchors: 6.355s
Validator wall-clock time — demo + display variants: 7.001s
Validator wall-clock time — broken resources: 7.874s
1252 passed, 2 deselected, 1 warning in 28.21s

.venv/bin/python -m auris.datasets.audit
primock57: all=57, dev=17, test=40
aci-bench: all=195, dev=23, test=60
coverage and splits frozen/verified; no extraction or scoring performed

.venv/bin/auris setup datasets
error: prompt/corpus leakage detected; evaluation refused
(exit 1: expected active-prompt guard refusal)

git diff --check
(no output; exit 0)
```

The warning is the existing deliberate socket-blocking test. Both real-model tests
were deselected. Setup's 57/195 prepared transcripts and source manifests were
verified at 0600; the private reports recorded PriMock57 fail and ACI pass.
Development also used read-only `rg`, `cat`, `sed`, `head`, `pwd`, git status,
branch and diff, `apply_patch`, direct `.venv/bin/python` inspection/assembly,
`ruff format`, `ruff check --fix`, and targeted pytest runs. Early failures exposed
legacy serialization compatibility (null turn fields) and were corrected without
rewriting FHIR goldens or transcript hashes. The final source scan found no
personal paths or email addresses in newly authored files.
