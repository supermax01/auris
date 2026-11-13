# Attributed public mock-consultation extracts

These are public **mock/fictional consultations**, not clinical gold or real
patient data. Used only to check source syntax; never prompt examples.

- `primock-extract.TextGrid`: first two doctor intervals from PriMock57
  `transcripts/day1_consultation01_doctor.TextGrid`, Babylon Health,
  commit `cd2ac707ad03cb4d2531f4ec6b90c659bf4357c5`. The interval count was
  reduced to two; the source bounds and text are retained.
  Source: https://github.com/babylonhealth/primock57
- `aci-extract.txt`: one patient turn from ACI-Bench
  `data/challenge_data_json/train_full.json`, encounter `D2N001-virtassist`,
  commit `b909b2bb9cf1d19de08df15cddde7bd0179665e4`. Extracted unchanged,
  with a trailing newline. Yim et al., ACI-BENCH, Scientific Data (2023).
  Source: https://github.com/wyim/aci-bench

Both extracts: Creative Commons Attribution 4.0 International,
https://creativecommons.org/licenses/by/4.0/ (see repository NOTICE).
All other test consultations are authored fictional fixtures under Apache-2.0.
