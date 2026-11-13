# Auris

Auris listens to a recorded doctor–patient visit and turns it into structured
medical records that hospital software can read — the vital signs taken, the
problems diagnosed, the medicines ordered and the allergies mentioned. Each
record is linked to the exact moment in the recording it came from, and from
those records Auris writes a short visit summary in the familiar SOAP layout
(Subjective, Objective, Assessment, Plan). It is open source, it runs on
your own laptop or server, and the audio never leaves that machine.

**Status:** pre-alpha, Slice 1 in progress.

**Scope:** research software, not cleared or approved for clinical use;
clinician review mandatory; regulatory classification is deployment-specific.

## Development quickstart

```sh
mise install
uv sync
uv run pytest
```

For the full product description and the binding technical contract, see
[docs/PLAN.md](docs/PLAN.md).

## Text demo (Slice 1)

Provision the runtimes with the existing mise configuration and make
`llama-server` available on PATH (or set `AURIS_LLAMA_SERVER`). Setup is the
only stage that downloads artifacts:

```sh
auris setup fhir
auris setup terminology --fetch
auris setup validator --fetch
auris setup model --fetch
auris demo
```

The fictional text visit writes its outputs into a new or empty
`./out/demo/`: `extraction.json`, `validation.json`, `bundle.json` and
`note.md`, plus `review-inputs.json` so review can reopen the visit locally.
For another transcript:

```sh
auris run --transcript examples/transcript.json --intake examples/intake.json --out out/visit
```

Runs are offline after setup. Missing validator setup leaves its lanes
NOT_CHECKED and produces an anchors-only collection. Unresolved material
assertions also block clinical export; review items stay visible in the note.
Only the supplied fictional intake enables example identifier URLs in the
validator. See [Step 5](docs/engineering/step-5.md) for validation scope,
the real-model smoke command and the frozen extraction output-cap limitation.

## Clinician review

For a completed run created with the current version:

```sh
auris review serve --run out/demo
```

Open the private loopback URL printed by the command and enter your name and
role once for the session. Each record shows its quote and receipt; expand
"Transcript context" to see the surrounding lines with the cited one
highlighted. Text-only visits show times or turn numbers, without an audio
player. **Approve** confirms your review of this exact item version.
**Dismiss** requires a reason and removes the item from export and the note's
record sections, retaining it in a dismissed list. **Undo** revokes the latest
action. Every action rewrites the note, bundle and validation report locally.

**Correct fact** opens the fields for that assertion kind. You can change the
kind, clear an unstated field, or correct a field, then choose **Save correction
and re-check**. Beside each quote, you can correct its segment's speaker role
to clinician, patient or other. The transcript and evidence quotes stay
unchanged; correcting transcript words needs audio and is deferred to Slice 2.

Every correction makes earlier approval stale and runs the same evidence,
consistency, mapping and FHIR checks as extracted output. While **Re-checking**
is visible, final export is blocked; you can still read the page and review
unaffected items. A failed or timed-out check stays blocked and offers **Retry
checks**. **Undo fact correction** and **Undo speaker correction** append a
revocation and restore the previous version, including applicable receipts.

A number absent from the quote fails even when a clinician enters it. You
cannot approve that failure: dismiss the item and document the correct value
in your own system. Review cannot supply missing codes, activate an order,
or make unchecked coding “validated.” Reported medicines
remain outside the order bundle; allergy denials remain ledger entries at the
default tier. **Ready to export** also counts items needing review that are
absent from the bundle and items kept only in the ledger; it does not mean
every discussed item is exported. Names and roles are attribution, not
authentication. See [step 12b](docs/engineering/step-12b.md) for correction
storage, re-check behavior and the fictional walkthrough.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
