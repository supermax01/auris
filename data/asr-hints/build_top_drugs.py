#!/usr/bin/env python3
"""Rank US outpatient prescription drugs by survey-weighted fills (MEPS HC Prescribed Medicines PUF).

Standard library only. Deterministic (ties broken by name).

Usage:
  python3 -I build_top_drugs.py <dat.zip> <sas_statements.txt> <doc.shtml> <out_top.tsv>
                                [--top N] [--all <out_all.tsv>] [--masked <out_masked.tsv>]

Inputs (all from meps.ahrq.gov, same PUF, e.g. HC-254A):
  <dat.zip>             ASCII data archive (one fixed-width .dat member), e.g. h254adat.zip
  <sas_statements.txt>  SAS programming statements, e.g. h254asu.txt; its INPUT statement is parsed
                        for column positions (no hard-coded offsets)
  <doc.shtml>           PUF documentation, e.g. h254adoc.shtml; its "Definitions of Therapeutic
                        Class Code" table supplies the Multum class labels used to spot masked records

Method:
  Each record is one household-reported purchase (fill) of a prescribed medicine.
  weighted fills(drug) = sum of PERWTyyF over the drug's records with PERWTyyF > 0
  unweighted records(drug) = count of those records
  Grouping key = RXDRGNAM (Multum generic name, imputed), stripped and lower-cased.
  Salt forms and combinations are NOT merged (RXDRGNAM is already salt-free, e.g. "metoprolol").

Exclusions (counted and reported on stderr):
  - RXDRGNAM blank or a MEPS reserved code (starts with '-', e.g. -15 "cannot be computed").
  - Masked records: AHRQ replaces the name of low-use drugs with their Multum therapeutic class
    and sets RXNDC to -15. A record is treated as masked when RXNDC starts with '-' AND RXDRGNAM
    equals a therapeutic class label from the documentation (compared on letters/digits only;
    prefix match allowed for labels the file truncates, minimum 20 chars).
"""
import html
import re
import sys
import zipfile
from collections import defaultdict

NAME_FIELD = "RXDRGNAM"
NDC_FIELD = "RXNDC"
WEIGHT_RE = re.compile(r"^PERWT\d\dF$")


def parse_layout(sas_path):
    """Return {field: (start0, width)} from the SAS INPUT statement."""
    text = open(sas_path, encoding="latin-1").read()
    m = re.search(r"^\s*INPUT\s+(@.*?);", text, re.S | re.M)
    if not m:
        sys.exit("INPUT statement not found in SAS file")
    return {name: (int(pos) - 1, int(width))
            for pos, name, width in re.findall(r"@(\d+)\s+(\w+)\s+\$?(\d+)\.\d*", m.group(1))}


def parse_class_labels(doc_path):
    """Return the set of Multum therapeutic class labels (lower case) from the doc's appendix table."""
    text = open(doc_path, encoding="latin-1").read()
    m = re.search(r'summary="Listing of definitions of therapeutic class code\.".*?</table>', text, re.S | re.I)
    if not m:
        sys.exit("therapeutic class table not found in documentation")
    labels = set()
    for code, label in re.findall(r"<td>\s*(-?\d+)\s*</td>\s*<td>(.*?)</td>", m.group(0), re.S):
        if int(code) > 0:
            labels.add(" ".join(html.unescape(re.sub(r"<[^>]+>", " ", label)).split()).lower())
    if len(labels) < 100:
        sys.exit(f"too few class labels parsed: {len(labels)}")
    return labels


def _key(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def is_class_label(name, labels):
    """Exact match, or prefix match (>= 20 chars) for labels the data file truncates.
    Compared on letters/digits only (the doc and data differ in commas, e.g. 'sedatives, and')."""
    if name not in _CACHE:
        k = _key(name)
        keys = {_key(lab) for lab in labels}
        _CACHE[name] = k in keys or (len(k) >= 20 and any(lab.startswith(k) for lab in keys))
    return _CACHE[name]


_CACHE = {}


def main(argv):
    args = list(argv)
    opts = {"--top": "150", "--all": None, "--masked": None}
    for flag in list(opts):
        if flag in args:
            i = args.index(flag)
            opts[flag] = args[i + 1]
            del args[i:i + 2]
    if len(args) != 4:
        sys.exit(__doc__)
    zip_path, sas_path, doc_path, out_path = args
    top_n = int(opts["--top"])

    layout = parse_layout(sas_path)
    labels = parse_class_labels(doc_path)
    weight_fields = sorted(f for f in layout if WEIGHT_RE.match(f))
    if len(weight_fields) != 1 or NAME_FIELD not in layout or NDC_FIELD not in layout:
        sys.exit(f"unexpected layout: weights={weight_fields}")
    wfield = weight_fields[0]
    (ns, nw), (ds, dw), (ws, ww) = layout[NAME_FIELD], layout[NDC_FIELD], layout[wfield]

    wsum, count = defaultdict(float), defaultdict(int)
    mwsum, mcount = defaultdict(float), defaultdict(int)
    stats = defaultdict(int)
    wtot = defaultdict(float)
    with zipfile.ZipFile(zip_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".dat") and "__macosx" not in n.lower()]
        if len(members) != 1:
            sys.exit(f"expected exactly one .dat member, got {members}")
        with z.open(members[0]) as fh:
            for raw in fh:
                line = raw.decode("latin-1").rstrip("\r\n")
                if not line.strip():
                    continue
                stats["records"] += 1
                w = float(line[ws:ws + ww])
                if w <= 0:
                    stats["zero_weight_records"] += 1
                    continue
                stats["positive_weight_records"] += 1
                wtot["all"] += w
                name = line[ns:ns + nw].strip().lower()
                ndc = line[ds:ds + dw].strip()
                if not name or name.startswith("-"):
                    stats["invalid_name_records"] += 1
                    wtot["invalid_name"] += w
                elif ndc.startswith("-") and is_class_label(name, labels):
                    stats["masked_class_records"] += 1
                    wtot["masked_class"] += w
                    mwsum[name] += w
                    mcount[name] += 1
                else:
                    wsum[name] += w
                    count[name] += 1

    def write(path, table, cnt):
        ranked = sorted(table, key=lambda k: (-table[k], k))
        with open(path, "w", encoding="utf-8", newline="\n") as out:
            out.write("rank\tgeneric_name\tweighted_fills\tunweighted_records\n")
            for r, k in enumerate(ranked, 1):
                out.write(f"{r}\t{k}\t{round(table[k])}\t{cnt[k]}\n")
        return ranked

    ranked = sorted(wsum, key=lambda k: (-wsum[k], k))
    write(out_path, {k: wsum[k] for k in ranked[:top_n]}, count)
    if opts["--all"]:
        write(opts["--all"], wsum, count)
    if opts["--masked"]:
        write(opts["--masked"], mwsum, mcount)

    # Class-label names that also occur with a valid NDC (kept, but worth eyeballing).
    suspicious = sorted(k for k in wsum if is_class_label(k, labels))

    err = sys.stderr
    print(f"data member: {members[0]}; weight: {wfield}; name: {NAME_FIELD}; class labels parsed: {len(labels)}", file=err)
    for k in ("records", "zero_weight_records", "positive_weight_records",
              "invalid_name_records", "masked_class_records"):
        print(f"{k}: {stats[k]}", file=err)
    print(f"distinct kept names: {len(wsum)}; distinct masked class names: {len(mwsum)}", file=err)
    print(f"weighted fills, all positive-weight records: {round(wtot['all'])}", file=err)
    print(f"  excluded invalid name: {round(wtot['invalid_name'])} ({wtot['invalid_name'] / wtot['all']:.4f})", file=err)
    print(f"  excluded masked class: {round(wtot['masked_class'])} ({wtot['masked_class'] / wtot['all']:.4f})", file=err)
    top_w = sum(wsum[k] for k in ranked[:top_n])
    print(f"top {top_n} share of all weighted fills: {top_w / wtot['all']:.4f}", file=err)
    print(f"kept names that equal a class label (valid NDC): {suspicious}", file=err)


if __name__ == "__main__":
    main(sys.argv[1:])
