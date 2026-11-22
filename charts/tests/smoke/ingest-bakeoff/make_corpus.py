#!/usr/bin/env python3
"""Build a ground-truth document corpus for the ingestion bake-off.

Everything here is generated from the DOC structure below, so the ground truth is
exact rather than annotated: the same prose and the same cell matrices are written
into a born-digital PDF, a DOCX, an HTML page and a 200-dpi scan of the PDF.
"""
import json, os, subprocess, sys
from pathlib import Path

OUT = Path(__file__).parent / "corpus"
OUT.mkdir(exist_ok=True)

TITLE = "Quarterly Reliability Review"

PARAS = [
 "This review covers the reliability of the ingestion tier for the quarter ending 30 September. "
 "Three incidents exceeded the thirty-minute threshold and each is recorded below with its root cause.",
 "The first incident was a storage quota exhausted during a bulk import of scanned contracts. "
 "The quota covered the steady state but not the surge created by a parallel re-index, and the "
 "heaviest pod was evicted rather than throttled.",
 "The second incident was a model server returning fluent but incorrect output after an engine "
 "upgrade. Both the readiness probe and the render test passed, which is why it was found by a "
 "reader rather than by the pipeline.",
 "Remediation work is tracked separately. The quota arithmetic is now derived from the rendered "
 "manifests instead of being written by hand, and the engine digest is pinned behind a smoke test "
 "on real hardware.",
]

TABLES = [
 {
  "caption": "Table 1. Incidents by severity and quarter.",
  "rows": [
    ["Severity", "Q1", "Q2", "Q3", "Trend"],
    ["Critical", "2", "1", "3", "up"],
    ["Major", "7", "9", "6", "down"],
    ["Minor", "31", "28", "34", "up"],
    ["Total", "40", "38", "43", "up"],
  ],
 },
 {
  "caption": "Table 2. Mean time to recovery, minutes.",
  "rows": [
    ["Component", "Median", "p90", "Worst"],
    ["Ingestion", "12", "41", "96"],
    ["Retrieval", "4", "11", "22"],
    ["Model server", "8", "27", "58"],
  ],
 },
]

# A second document whose tables are the ones open stacks are supposed to lose:
# no ruling lines at all, and a two-level header with spanning cells. The grid is
# written the way a cell grid represents a span — the spanning text repeated across
# every cell it covers — which is also how Docling's table grid reports it.
HARD_PARAS = [
 "The figures below are drawn from the same quarter and are reproduced without ruling "
 "lines, as they appear in the finance pack.",
 "Where a heading spans two columns it is a group heading, and the column beneath it "
 "carries the unit named in the second row.",
]

HARD_TABLES = [
 {
  "caption": "Table 3. Spend by region, no ruling lines.",
  "spans": True,
  "rows": [
    ["Region", "Compute", "Compute", "Storage", "Storage"],
    ["Region", "USD", "share", "USD", "share"],
    ["EMEA", "412,900", "38%", "91,200", "31%"],
    ["Americas", "503,100", "46%", "126,400", "43%"],
    ["APAC", "174,600", "16%", "76,100", "26%"],
  ],
 },
 {
  "caption": "Table 4. Headcount, borderless.",
  "spans": False,
  "rows": [
    ["Team", "Opened", "Closed", "Net"],
    ["Platform", "6", "2", "+4"],
    ["Ingestion", "3", "4", "-1"],
    ["Security", "2", "0", "+2"],
  ],
 },
]

GT = {"title": TITLE, "paragraphs": PARAS, "tables": [t["rows"] for t in TABLES]}
GT_HARD = {"title": "Finance Pack Extract", "paragraphs": HARD_PARAS,
           "tables": [t["rows"] for t in HARD_TABLES]}


def hard_tables_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                    TableStyle)
    styles = getSampleStyleSheet()
    story = [Paragraph("Finance Pack Extract", styles["Title"]), Spacer(1, 6 * mm)]
    for p in HARD_PARAS:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 3 * mm)]
    for t in HARD_TABLES:
        story += [Spacer(1, 5 * mm), Paragraph(t["caption"], styles["Italic"]),
                  Spacer(1, 2 * mm)]
        if t["spans"]:
            # Collapse the repeated span text into one cell for rendering.
            head = ["Region", "Compute", "", "Storage", ""]
            rows = [head] + t["rows"][1:]
            style = [("SPAN", (1, 0), (2, 0)), ("SPAN", (3, 0), (4, 0)),
                     ("SPAN", (0, 0), (0, 1))]
        else:
            rows, style = [r[:] for r in t["rows"]], []
        tbl = Table(rows, hAlign="LEFT")
        tbl.setStyle(TableStyle(style + [
            ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ALIGN", (1, 2), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story += [tbl, Spacer(1, 4 * mm)]
    SimpleDocTemplate(str(path), pagesize=A4,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm).build(story)


def born_digital_pdf(path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    styles = getSampleStyleSheet()
    story = [Paragraph(TITLE, styles["Title"]), Spacer(1, 6 * mm)]
    for p in PARAS[:2]:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 3 * mm)]
    for t in TABLES:
        story += [Spacer(1, 4 * mm), Paragraph(t["caption"], styles["Italic"]), Spacer(1, 2 * mm)]
        tbl = Table(t["rows"], hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story += [tbl, Spacer(1, 4 * mm)]
    for p in PARAS[2:]:
        story += [Paragraph(p, styles["BodyText"]), Spacer(1, 3 * mm)]
    SimpleDocTemplate(str(path), pagesize=A4,
                      leftMargin=20 * mm, rightMargin=20 * mm,
                      topMargin=20 * mm, bottomMargin=20 * mm).build(story)


def docx_file(path):
    from docx import Document
    from docx.shared import Pt
    d = Document()
    d.add_heading(TITLE, level=1)
    for p in PARAS[:2]:
        d.add_paragraph(p)
    for t in TABLES:
        d.add_paragraph(t["caption"]).italic = True
        tbl = d.add_table(rows=0, cols=len(t["rows"][0]))
        tbl.style = "Table Grid"
        for r in t["rows"]:
            cells = tbl.add_row().cells
            for c, v in zip(cells, r):
                c.text = v
    for p in PARAS[2:]:
        d.add_paragraph(p)
    d.save(str(path))


def html_file(path):
    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = ["<!doctype html><html><head><meta charset='utf-8'>",
             f"<title>{esc(TITLE)}</title></head><body>", f"<h1>{esc(TITLE)}</h1>"]
    for p in PARAS[:2]:
        parts.append(f"<p>{esc(p)}</p>")
    for t in TABLES:
        parts.append(f"<p><em>{esc(t['caption'])}</em></p><table border='1'>")
        head, *body = t["rows"]
        parts.append("<thead><tr>" + "".join(f"<th>{esc(c)}</th>" for c in head) + "</tr></thead><tbody>")
        for r in body:
            parts.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>")
        parts.append("</tbody></table>")
    for p in PARAS[2:]:
        parts.append(f"<p>{esc(p)}</p>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def scanned_pdf(src_pdf, out_pdf, out_png, dpi=200, angle=0.6):
    """Rasterise the born-digital PDF, rotate and add sensor noise, re-wrap as an
    image-only PDF. This is the 'scanned document' input: no text layer at all."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter
    import random
    random.seed(874)
    doc = pdfium.PdfDocument(str(src_pdf))
    pages = []
    for i in range(len(doc)):
        img = doc[i].render(scale=dpi / 72).to_pil().convert("L")
        img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
        img = img.filter(ImageFilter.GaussianBlur(0.4))
        px = img.load()
        w, h = img.size
        for _ in range((w * h) // 400):          # ~0.25% speckle
            x, y = random.randrange(w), random.randrange(h)
            px[x, y] = max(0, min(255, px[x, y] + random.choice((-60, 60))))
        pages.append(img.convert("RGB"))
    pages[0].save(str(out_png), "PNG")
    pages[0].save(str(out_pdf), "PDF", save_all=True, append_images=pages[1:],
                  resolution=float(dpi))


if __name__ == "__main__":
    born_digital_pdf(OUT / "born_digital.pdf")
    hard_tables_pdf(OUT / "hard_tables.pdf")
    docx_file(OUT / "report.docx")
    html_file(OUT / "page.html")
    scanned_pdf(OUT / "born_digital.pdf", OUT / "scanned.pdf", OUT / "scanned_page1.png")
    scanned_pdf(OUT / "hard_tables.pdf", OUT / "scanned_hard.pdf",
                OUT / "scanned_hard_page1.png")
    (OUT / "ground_truth.json").write_text(json.dumps(GT, indent=2) + "\n", encoding="utf-8")
    (OUT / "ground_truth_hard.json").write_text(json.dumps(GT_HARD, indent=2) + "\n",
                                                encoding="utf-8")
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:24s} {f.stat().st_size:>9,d} bytes")
