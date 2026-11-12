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

The fictional text visit writes four private files into a new or empty
`./out/demo/`: `extraction.json`, `validation.json`, `bundle.json` and
`note.md`. For another transcript:

```sh
auris run --transcript examples/transcript.json --intake examples/intake.json --out out/visit
```

Runs are offline after setup. Missing validator setup leaves its lanes
NOT_CHECKED and produces an anchors-only collection. Unresolved material
assertions also block clinical export; review items stay visible in the note.
Only the supplied fictional intake enables example identifier URLs in the
validator. See [Step 5](docs/engineering/step-5.md) for validation scope,
the real-model smoke command and the frozen extraction output-cap limitation.

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
