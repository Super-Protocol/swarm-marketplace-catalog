#!/usr/bin/env python3
"""Score an extractor's output against the corpus ground truth.

Two numbers per document, because they fail independently:

  text CER   — per ground-truth paragraph, the best-matching window of the
               extracted prose, Levenshtein distance over the paragraph length.
               Averaged over paragraphs, weighted by paragraph length.
  table F1   — over (row, column, cell text) triples. A table that was read as
               prose scores 0 here however good its text is, which is the
               distinction the acceptance criterion is about.
"""
import json, re, sys
from pathlib import Path
from rapidfuzz.distance import Levenshtein
from rapidfuzz import fuzz

WS = re.compile(r"\s+")


def norm(s):
    return WS.sub(" ", (s or "").replace("­", "")).strip()


def norm_cell(s):
    return WS.sub(" ", (s or "")).strip().lower()


def text_cer(gt_paras, extracted_text):
    """Weighted CER: each GT paragraph against its best window in the extraction."""
    hay = norm(extracted_text)
    num = den = 0
    per = []
    for p in gt_paras:
        needle = norm(p)
        den += len(needle)
        if not hay:
            num += len(needle)
            per.append(1.0)
            continue
        al = fuzz.partial_ratio_alignment(needle, hay)
        window = hay[al.dest_start:al.dest_end] if al else ""
        d = Levenshtein.distance(needle, window)
        d = min(d, len(needle))          # a miss costs the paragraph, not more
        num += d
        per.append(d / len(needle))
    return ((num / den) if den else None), per


def grid_triples(rows):
    out = set()
    for i, row in enumerate(rows):
        for j, c in enumerate(row):
            t = norm_cell(c)
            if t:
                out.add((i, j, t))
    return out


def cell_bag(rows):
    """Multiset of cell texts, positions ignored. A table whose grid is shifted by
    a row still scores here, so this separates 'read the values' from 'read the
    geometry' — and two stacks that disagree only about how a spanning header is
    represented are not scored apart on a convention."""
    from collections import Counter
    return Counter(norm_cell(c) for row in rows for c in row if norm_cell(c))


def bag_f1(a, b):
    inter = sum((a & b).values())
    p = inter / sum(b.values()) if sum(b.values()) else 0.0
    r = inter / sum(a.values()) if sum(a.values()) else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def best_match(gt_rows, cand_tables):
    gt = grid_triples(gt_rows)
    best, best_f1, best_idx = None, -1.0, None
    for k, cand in enumerate(cand_tables):
        ct = grid_triples(cand)
        if not ct and not gt:
            continue
        inter = len(gt & ct)
        p = inter / len(ct) if ct else 0.0
        r = inter / len(gt) if gt else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        if f1 > best_f1:
            best, best_f1, best_idx = (p, r, f1), f1, k
    return (best or (0.0, 0.0, 0.0)), best_idx


def score(gt, extraction):
    """extraction = {'text': str, 'tables': [[[cell,...],...],...]}"""
    cer, per_para = text_cer(gt["paragraphs"], extraction.get("text", ""))
    tables = extraction.get("tables", []) or []
    used, per_table = set(), []
    for gt_rows in gt["tables"]:
        pool = [t for k, t in enumerate(tables) if k not in used]
        remap = [k for k in range(len(tables)) if k not in used]
        (p, r, f1), idx = best_match(gt_rows, pool)
        if idx is not None and f1 > 0:
            used.add(remap[idx])
            shape = (len(pool[idx]), max((len(x) for x in pool[idx]), default=0))
        else:
            shape = (0, 0)
        cell_f1 = bag_f1(cell_bag(gt_rows), cell_bag(pool[idx])) if idx is not None else 0.0
        per_table.append({"precision": round(p, 4), "recall": round(r, 4),
                          "f1": round(f1, 4), "cell_set_f1": round(cell_f1, 4),
                          "gt_shape": [len(gt_rows), len(gt_rows[0])],
                          "got_shape": list(shape)})
    f1s = [t["f1"] for t in per_table]
    cfs = [t["cell_set_f1"] for t in per_table]
    return {
        "text_cer": round(cer, 4) if cer is not None else None,
        "text_cer_per_paragraph": [round(x, 4) for x in per_para],
        "tables_expected": len(gt["tables"]),
        "tables_emitted": len(tables),
        "table_f1_mean": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "cell_set_f1_mean": round(sum(cfs) / len(cfs), 4) if cfs else 0.0,
        "tables": per_table,
    }


if __name__ == "__main__":
    gt = json.loads(Path(sys.argv[1]).read_text())
    ex = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(score(gt, ex), indent=2))
