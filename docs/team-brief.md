# Auris — team brief (shared context for all planning agents)

## Who we are

You are one of four AI teammates jointly planning one product. Act like it:
build on, challenge, and improve each other's drafts — do not produce a
standalone generic essay.

- **Muse (muse-wonder-full-1.0)** — coordinator. Owns the repo, the
  fact-check, competitive GitHub analysis, synthesis, and final plan edits.
- **Claude via Claude Code CLI (`claude-fable-5-2`)** — planning teammate.
- **Codex via Muse (`gpt-6.1-sol`, reasoning = max)** — planning teammate.
- **Gemini (logged-in Chrome tab, human-relayed)** — planning teammate.
- **Exa (logged-in Chrome, human-relayed)** — research support for current facts.
- **Chandra Vikram** — human owner. Locks the final plan. No code is written
  until he has read and approved the final plan ("go" gate).

Repo: https://github.com/supermax01/auris (public, docs only so far).
Local: ~/Developer/auris on `main`.

## Mission

Build **Auris**: ambient clinician–patient audio → validated FHIR R4
resources (Observation, Condition, MedicationRequest, AllergyIntolerance),
with the SOAP note rendered *from* the FHIR resources. Fully open-source
core: open-weight models, self-hostable, no proprietary APIs in the core path.

**Ambition (owner's words):** one of the best OSS tools people can use for
work, research, and deployment. We must analyze existing GitHub repos in this
space, state what each has actually accomplished, and define how Auris beats
and one-ups them — on structured FHIR output, validation, evaluation honesty,
privacy/local deployment, and developer experience — not on marketing claims.

## Process

1. Round 1: each teammate drafts a plan from this brief + the shared prompt.
2. Coordinator adds competitive GitHub analysis (verified via `gh`).
3. Round 2: each teammate critiques the merged draft + competitive analysis.
4. Coordinator locks ONE final plan (docs/PLAN.md) → owner reads → "go" gate.
5. No application code before the gate. Docs/commits of docs are fine.

## Binding constraints (from completed primary-source fact-check)

- Diarisation gives anonymous speakers, not clinician/patient roles.
  Role assignment is its own component.
- JSON-Schema-valid ≠ US Core profile-conformant. A real validator stage
  exists and can fail. Never write "guarantee" for conformance.
- Extraction uses hand-reduced schemas, then maps to full FHIR resources.
- No same-model synthetic self-evaluation as headline evidence; TTS metrics
  are not real-clinic evidence. ACI-Bench (207) and PriMock57 have note
  targets, not FHIR gold — say so wherever used.
- The original manuscript's targets (F1 ≥ 0.80 etc.) are targets, not results.
- Diariser licences are traps: pyannote 3.x models are gated on Hugging Face;
  NVIDIA diarisation weights vary per model (CC BY 4.0 / NVIDIA Open Model
  License / OpenMDW). Behind an interface, chosen deliberately.
- Dev: Apple Silicon Mac, Python via mise/uv. Deploy: Linux, GPU optional.

## What "best OSS" must concretely mean in the plan

- One-command try-out (demo audio → FHIR Bundle + note) on a laptop.
- Swappable stages (ASR, diariser, extractor LLM, validator) via interfaces.
- Ships an evaluation harness others can run; publishes honest baselines.
- Deployable: offline/air-gapped capable, no audio leaves the machine,
  clear model-download and licence story, Docker path.
- Research-ready: gold-FHIR annotation protocol, IAA, dataset cards.
