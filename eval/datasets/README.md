# Frozen public dataset manifests — no redistributed corpus

- `*-sources.json`: SHA-256 of every source file read; manifest hashes and commits
  are pinned in `config/frozen.toml`.
- `*-dev.json`, `*-test.json`: encounter IDs, hashes, provenance and length strata;
  seed 1729, no clinical gold. Never substitute these for annotated evaluation
  manifests or treat absent annotations as zero facts.
- `*-adaptation.json`: aggregate markup/empty-turn/role counts, no transcript text.
- `*-leakage.json`: active-prompt overlap counts/hashes, no quoted data.
- `coverage-counts.md` and `.json`: model-free lexical candidate estimates and
  their limits, with separate human/scripted and corrected-ASR-inclusive views.

See [the step-6c engineering note](../../docs/engineering/step-6c.md) for exact
variant selection, the 12 ineligible ACI training encounters, attribution,
reproduction, locking and the current PriMock57 prompt-overlap refusal.

Raw and adapted data remain in the gitignored cache. Clinical annotation is
pending for every entry. Public mock corpora may have appeared in pretraining.
