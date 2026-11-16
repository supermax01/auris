# Source and method for `top_drugs.tsv`

## Source

- Survey: AHRQ Medical Expenditure Panel Survey, Household Component (MEPS HC)
- File: **HC-254A, 2024 Prescribed Medicines** public use file (data year **2024**, released July 2026). This was the newest Prescribed Medicines file on meps.ahrq.gov when checked on 2026-11-13 (HC-248A is 2023, HC-239A is 2022).
- Release page: https://meps.ahrq.gov/mepsweb/data_stats/download_data_files_detail.jsp?cboPufNumber=HC-254A

Files downloaded, all from the official host meps.ahrq.gov, on 2026-11-13:

| Role | URL | Local file | Bytes | SHA-256 |
|---|---|---|---|---|
| Data (ASCII, fixed width, zipped) | https://meps.ahrq.gov/mepsweb/data_files/pufs/h254a/h254adat.zip | `data/h254adat/h254adat.zip` (member `h254a.dat`, 120,275,400 bytes, 204,550 records of 586 chars + CRLF) | 5,814,620 | `c15398e09a5fae4d8cc9f6a11d92ad53f29266eefbae2377a3a32afc2b81acae` |
| Record layout (SAS programming statements) | https://meps.ahrq.gov/mepsweb/data_stats/download_data/pufs/h254a/h254asu.txt | `data/h254a_sas/h254asu.txt` | 60,868 | `8abef5706995bfea01e689bbe50f3823c2c77c67dc260c740b3aaf1b8082789b` |
| Documentation (includes the therapeutic class code table) | https://meps.ahrq.gov/mepsweb/data_stats/download_data/pufs/h254a/h254adoc.shtml | `data/h254a_doc/h254adoc.shtml` | 247,437 | `049445b93ff21b3dbb64c132ee7fad62105634c17f060118a05356db08573c4b` |

(The documentation is a web page, so AHRQ may change it later. The data zip is the input that matters.)

## Licence and terms

- MEPS material is a US federal government work in the **public domain**. From https://meps.ahrq.gov/mepsweb/copyright.jsp: it "is in the public domain, except for previously copyrighted photographs and illustrations" and is free to use without specific permission. AHRQ asks that users cite it as the source.
- The Data Use Agreement in the PUF documentation (Section A) limits use of the microdata to statistical reporting and analysis and forbids any attempt to re-identify people. An aggregate ranking of drug names by national fill volume is statistical reporting and contains no person-level data.
- The drug names come from the Multum Lexicon (Oracle Health). The documentation asks users to cite Multum Lexicon as the source of the Multum variables. Generic drug names are factual identifiers, not creative content.
- Suggested citation: Agency for Healthcare Research and Quality, Medical Expenditure Panel Survey, HC-254A: 2024 Prescribed Medicines file. Drug names from Multum Lexicon (Oracle Health).

## Fields used

Column positions are read from the `INPUT` statement in `h254asu.txt`. No offsets are hard-coded in the script.

- `RXDRGNAM` (cols 129-188): "MULTUM MEDICINE NAME (IMPUTED)", the generic name most used by prescribers. This is the grouping key, stripped and lower-cased.
- `PERWT24F` (cols 570-581): the 2024 full-year person weight. Every record is one purchase or fill of a prescribed medicine, so a drug's weighted fills are the sum of `PERWT24F` over its records.
- `RXNDC` (cols 189-199): used only to detect masked records (see below).
- The therapeutic class table ("Definitions of Therapeutic Class Code", Appendix 5 of the documentation; 492 labels with positive codes): used only to detect masked records.

## Computation (`build_top_drugs.py`)

```
python3 -I build_top_drugs.py data/h254adat/h254adat.zip data/h254a_sas/h254asu.txt \
    data/h254a_doc/h254adoc.shtml top_drugs.tsv \
    --all all_drugs_full.tsv --masked masked_classes.tsv
```

The script uses only the standard library and gives the same output on every run. Ties are broken by name. Output SHA-256 of `top_drugs.tsv`: `d3af22a7e15b1d0bee53748d8979b962166688d3f1ff34304b6eca8edbd4d324`.

Counts reported by the script:

| Item | Records | Weighted fills |
|---|---|---|
| All records | 204,550 | |
| Weight = 0 (excluded; these contribute nothing to weighted totals) | 1,648 | 0 |
| Positive weight | 202,902 | 3,029,450,238 (about 3.03 billion fills in the US in 2024) |
| Excluded: `RXDRGNAM` blank or a reserved code (`-15`) | 7,180 | 108,423,420 (3.6%) |
| Excluded: masked records, where the drug name was replaced by a class name | 14,097 | 226,542,733 (7.5%) |
| Kept: 436 distinct generic names | | |
| Top 150 | | 77.5% of all positive-weight fills |

Columns in `top_drugs.tsv`:
- `rank`
- `generic_name`: `RXDRGNAM`, lower-cased
- `weighted_fills`: sum of `PERWT24F`, rounded to the nearest whole fill
- `unweighted_records`: the number of positive-weight records

## Cleaning and caveats

1. **Reserved or invalid names dropped.** If `RXDRGNAM` starts with `-` (MEPS reserved codes; only `-15`, "cannot be computed", occurs here), the record is dropped. Looking at `RXNAME` for these records shows they are mostly diabetic supplies and devices (glucose meters and strips such as OneTouch, FreeStyle Libre and Dexcom CGMs, BD pen needles, lancets, Omnipod) plus 2,128 records where every name is suppressed. They are not drug ingredients.
2. **Masked low-use drugs dropped.** For confidentiality, AHRQ masks orphan drugs and drugs estimated to be used by fewer than 400,000 people. It sets `RXNDC` to `-15` and replaces `RXDRGNAM` with the Multum therapeutic class name (for example "central nervous system agents", "cgrp inhibitors", "insulin"). A record counts as masked when **`RXNDC` starts with `-` and `RXDRGNAM` matches a class label** from the documentation table. Matching compares letters and digits only, and a prefix of at least 20 characters is accepted because the file truncates some labels, e.g. "miscellaneous anxiolytics, sedatives and hypnot". 117 class names were caught. They are listed in `masked_classes.tsv`. No kept name matches a class label. **Effect:** some real drugs never appear under their own name, and the masked volume (7.5%) is spread across classes. Newer brand-only or low-use molecules (some CGRP drugs, biologics, inhaler combinations) are probably under-ranked or missing.
3. **Salt forms: no merging was needed.** `RXDRGNAM` is already salt-free. "metoprolol" covers succinate and tartrate, and "amphetamine-dextroamphetamine" and "potassium chloride" appear as Multum names them. Nothing was merged.
4. **Route-qualified names were kept as separate entries.** Multum adds a route to some names. In the top 150 these are: fluticasone nasal, latanoprost ophthalmic, triamcinolone topical, ketoconazole topical, azelastine nasal, prednisolone ophthalmic, diclofenac topical and clobetasol topical. "fluticasone nasal" (rank 32) and "fluticasone" (rank 143) are separate entries, as are "diclofenac" (rank 111) and "diclofenac topical" (rank 139). For a speech hint list, drop the route word and deduplicate.
5. **Combination products are one entry each**, with Multum's separators and ingredients in Multum's order. Two ingredients are joined with `-` (mostly alphabetical, e.g. "acetaminophen-hydrocodone", "hydrochlorothiazide-lisinopril"). Three ingredients are joined with `/` ("fluticasone/umeclidinium/vilanterol"). The 13 combinations in the top 150 are: amphetamine-dextroamphetamine, acetaminophen-hydrocodone, ethinyl estradiol-norethindrone, amoxicillin-clavulanate, hydrochlorothiazide-lisinopril, fluticasone-salmeterol, acetaminophen-oxycodone, hydrochlorothiazide-losartan, budesonide-formoterol, ethinyl estradiol-norgestimate, fluticasone/umeclidinium/vilanterol, sulfamethoxazole-trimethoprim and hydrochlorothiazide-triamterene.
6. **What MEPS covers.** Purchases reported by households of the US civilian non-institutionalized population. Nursing-home and inpatient use is excluded. Some prescribed OTC products appear (aspirin, ibuprofen, cetirizine, vitamins D2 and D3, folic acid, ferrous sulfate). Each fill counts once, whatever its days' supply, so 30-day and 90-day fills weigh the same. These are survey estimates with sampling error, and close ranks (within a few percent) are not statistically distinct.
7. **Independence.** The list comes only from the federal MEPS file above. No evaluation dataset and no commercial ranking (e.g. ClinCalc) was used.

## Other files in this folder

- `all_drugs_full.tsv`: all 436 kept names, same columns.
- `masked_classes.tsv`: the 117 excluded class names with their weighted fills.
