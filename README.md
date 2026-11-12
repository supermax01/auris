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

## Licence

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
