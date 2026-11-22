#!/usr/bin/env python3
"""Run one extractor over one document and write a normalised extraction.

Normalised shape, so the scorer does not know which stack produced it:
    {"text": "<prose, tables excluded>", "tables": [[[cell, ...], ...], ...]}

Engines:
    docling[:ocr]   Docling, optionally forcing an OCR engine (rapidocr|easyocr)
    pdfium          pypdfium2 raw text — the control: no layout model, no table
                    model, which is what a text-only extraction of a PDF gives
"""
import argparse, json, os, resource, sys, time
from pathlib import Path

os.environ.setdefault("OMP_NUM_THREADS", "8")


def extract_pdfium(path, _):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(path))
    return {"text": "\n".join(p.get_textpage().get_text_bounded() for p in doc),
            "tables": []}


def extract_docling(path, ocr):
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions, RapidOcrOptions, EasyOcrOptions, TableFormerMode)

    opts = PdfPipelineOptions()
    opts.artifacts_path = os.environ["DOCLING_ARTIFACTS_PATH"]
    opts.do_table_structure = True
    opts.table_structure_options.do_cell_matching = True
    mode = os.environ.get("DOCLING_TABLE_MODE", "accurate")
    opts.table_structure_options.mode = TableFormerMode(mode)
    layout = os.environ.get("DOCLING_LAYOUT", "")
    if layout:
        from docling.datamodel.pipeline_options import LayoutObjectDetectionOptions
        opts.layout_options = LayoutObjectDetectionOptions.from_preset(layout)
    thr = os.environ.get("DOCLING_LAYOUT_THRESHOLD", "")
    if thr:
        opts.layout_options.engine_options.score_threshold = float(thr)
    engine = os.environ.get("DOCLING_LAYOUT_ENGINE", "")
    if engine:
        from docling.models.inference_engines.object_detection.base import (
            ObjectDetectionEngineType)
        opts.layout_options.engine_options.engine_type = ObjectDetectionEngineType(engine)
    opts.do_code_enrichment = False
    opts.do_formula_enrichment = False
    opts.do_picture_classification = False
    opts.do_picture_description = False

    if ocr in (None, "", "off"):
        opts.do_ocr = False
    else:
        opts.do_ocr = True
        if ocr == "rapidocr":
            opts.ocr_options = RapidOcrOptions(
                lang=["en"], force_full_page_ocr=True,
                model_size=os.environ.get("RAPIDOCR_SIZE", "small"))
        elif ocr == "easyocr":
            opts.ocr_options = EasyOcrOptions(
                lang=["en"], force_full_page_ocr=True,
                model_storage_directory=os.environ.get("EASYOCR_MODELS"),
                download_enabled=True)
        else:
            raise SystemExit(f"unknown ocr engine {ocr!r}")

    conv = DocumentConverter(format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        InputFormat.IMAGE: PdfFormatOption(pipeline_options=opts),
    })
    doc = conv.convert(str(path)).document

    tables = []
    for t in doc.tables:
        grid = getattr(t.data, "grid", None) or []
        tables.append([[c.text for c in row] for row in grid])

    # Prose with the tables taken out: a table read as prose must not be able to
    # earn text credit for its cells and table credit for them as well.
    table_refs = {t.self_ref for t in doc.tables}
    prose = []
    for item, _ in doc.iterate_items():
        if item.self_ref in table_refs:
            continue
        txt = getattr(item, "text", None)
        if txt:
            prose.append(txt)
    return {"text": "\n".join(prose), "tables": tables}


ENGINES = {"pdfium": extract_pdfium, "docling": extract_docling}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("engine")          # docling | docling:rapidocr | pdfium
    ap.add_argument("document")
    ap.add_argument("out")
    a = ap.parse_args()
    name, _, ocr = a.engine.partition(":")
    t0 = time.perf_counter()
    try:
        ex = ENGINES[name](Path(a.document), ocr or None)
        err = None
    except Exception as e:                        # a stack that cannot read a
        ex = {"text": "", "tables": []}           # format is a result, not a crash
        err = f"{type(e).__name__}: {e}"
    wall = time.perf_counter() - t0
    peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    ex["_run"] = {"engine": a.engine, "document": os.path.basename(a.document),
                  "wall_s": round(wall, 2), "peak_rss_mib": round(peak_mib, 1),
                  "error": err}
    Path(a.out).write_text(json.dumps(ex, indent=1), encoding="utf-8")
    print(json.dumps(ex["_run"]))
