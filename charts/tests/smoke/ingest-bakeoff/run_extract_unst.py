#!/usr/bin/env python3
"""The same normalised extraction, produced by Unstructured instead of Docling.

Runs in its own virtualenv (`.venv-unst`): Unstructured and Docling cannot share
one, and the comparison is only fair if each stack brings its own dependency tree.
"""
import argparse, json, os, re, resource, time
from html.parser import HTMLParser
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "8")


class TableHTML(HTMLParser):
    """Unstructured reports a table as `metadata.text_as_html`; turn it back into a
    cell grid, expanding colspan/rowspan the way a grid represents a span."""

    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None
        self.pending = {}          # column -> (text, rows_left)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = {"text": "", "cols": int(a.get("colspan", 1)),
                         "rows": int(a.get("rowspan", 1))}

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr":
            out, col = [], 0
            while col in self.pending:
                text, left = self.pending[col]
                out.append(text)
                self.pending[col] = (text, left - 1) if left > 1 else None
                if self.pending[col] is None:
                    del self.pending[col]
                col += 1
            for c in self.row:
                for _ in range(c["cols"]):
                    out.append(c["text"].strip())
                    if c["rows"] > 1:
                        self.pending[col] = (c["text"].strip(), c["rows"] - 1)
                    col += 1
                    while col in self.pending:
                        text, left = self.pending[col]
                        out.append(text)
                        col += 1
            self.rows.append(out)
            self.row = []


def grid(html):
    p = TableHTML()
    p.feed(html or "")
    return p.rows


def extract(path, strategy):
    from unstructured.partition.auto import partition
    kwargs = {"filename": str(path), "infer_table_structure": True}
    if path.suffix.lower() in (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        kwargs["strategy"] = strategy
    els = partition(**kwargs)
    prose, tables = [], []
    for e in els:
        if type(e).__name__ == "Table":
            html = getattr(e.metadata, "text_as_html", None)
            g = grid(html) if html else []
            tables.append(g if g else [[str(e)]])
        else:
            t = str(e).strip()
            if t:
                prose.append(t)
    return {"text": "\n".join(prose), "tables": tables}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("strategy")            # hi_res | fast
    ap.add_argument("document")
    ap.add_argument("out")
    a = ap.parse_args()
    t0 = time.perf_counter()
    try:
        ex = extract(Path(a.document), a.strategy)
        err = None
    except Exception as e:
        ex = {"text": "", "tables": []}
        err = f"{type(e).__name__}: {e}"
    ex["_run"] = {"engine": f"unstructured:{a.strategy}",
                  "document": os.path.basename(a.document),
                  "wall_s": round(time.perf_counter() - t0, 2),
                  "peak_rss_mib": round(
                      resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                  "error": err}
    Path(a.out).write_text(json.dumps(ex, indent=1), encoding="utf-8")
    print(json.dumps(ex["_run"]))
