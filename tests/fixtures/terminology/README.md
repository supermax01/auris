These are tiny public terminology extracts, not patient data. Tests wrap
these text files into zip archives offline and use the production readers.
Archive source URLs, releases and checksums are pinned in config/frozen.toml.

- RXNCONSO.RRF: RXNORM unsuppressed rows for selected ingredients, a PIN,
  a brand, a combination ingredient, and two fully specified drug products
  from RxNorm Current Prescribable Content, 2026-11-02 (no licence required).
  SY/PSN/TMSY rows deliberately exercise the TTY allowlist.
- RXNREL.RRF: the six original relationships between selected terms from
  that same CPC release. The archive's SQL schema documents the columns.
- icd10cm_order_2027.txt: original CMS FY2027 order rows for A00 (header),
  A00.0, E11.9, J20.9 and J45.909 (billable). Public domain.
- ValueSet-ucum-*.json: id, URL, version and compose extracts from the
  pinned hl7.fhir.r4.core#4.0.1 package (CC0-1.0). The UCUM notice in NOTICE
  applies to the individual unit codes. Full FHIR packages are not bundled.

Fictional visits and their independently specified mapper expectations
are in ../fhir/a3_golden.json. Test mutations are parser/refusal probes,
not additional clinical terminology or assertions about real patients.
