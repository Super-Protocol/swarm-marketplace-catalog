# The ingestion bake-off

The measurement behind the pipeline choice in SUP-874: which open stack replaces
`nv-ingest` and the four extraction NIMs, decided on numbers rather than on preference.

Run it when a candidate changes — a new Docling release, a new layout preset, a new OCR
engine — before the component's version moves.

## What it measures, and why these two numbers

An extractor can lose a document in two independent ways, and one number hides the
other:

- **text CER** — per ground-truth paragraph, the best-matching window of the extracted
  prose, Levenshtein distance over the paragraph's length, averaged weighted by length.
  This is what OCR gets wrong.
- **table F1** — over `(row, column, cell text)` triples. **A table read as prose scores
  zero here however perfect its text is**, which is the distinction SUP-874's acceptance
  criterion is about: "tables extracted rather than flattened into prose".

`cell_set_f1` is the same cells as a multiset with positions ignored. It separates "read
the values" from "read the geometry", so two stacks that disagree only about how a
spanning header is represented in a grid are not scored apart on a convention.

The control is `pdfium` — raw PDF text, no layout model and no table model. It scores a
text CER of 0.0000 on born-digital documents and finds **0 tables out of 19** across the
seven PDFs here. That is the whole argument for a layout and table model rather than a
text extractor, in one row.

## The two corpora

`make_corpus.py` generates `corpus/` from the structures declared inside it, so the
ground truth is exact rather than annotated, and it is ours to commit and to publish:

| File | What it is for |
|---|---|
| `born_digital.pdf` | ruled tables with a text layer |
| `hard_tables.pdf` | **no ruling lines**, and a two-level spanning header — where layout models disagree |
| `report.docx`, `page.html` | the same content through the non-PDF backends |
| `scanned.pdf`, `scanned_hard.pdf`, `scanned_page1.png` | the above rasterised at 200 dpi, rotated 0.6°, blurred and speckled: no text layer at all |

`real/*.json` is ground truth **transcribed** from the upstream blueprint's own sample
documents (`data/multimodal` in `NVIDIA-AI-Blueprints/rag` 2.6.2): `table_test.pdf`
(one 21×6 table), `multimodal_test.pdf` (two tables, two charts, three bullet points —
the document states its own expected answer in its conclusion) and `embedded_table.pdf`
(eight dependency tables from the pandas manual).

**Those documents are not in this repository and must never be.** They are under the
NVIDIA Asset License Agreement: evaluation is permitted, redistribution is not, and
`docs/ported-listings.md` §2 forbids them in an image, a dataset, a demo or a
screenshot. Only the transcribed cell grids and the scores live here. Point
`UPSTREAM_MULTIMODAL` at a local checkout to reproduce that half.

## Running it

```bash
# Docling, every layout preset × TableFormer mode × OCR engine, both corpora
python -m venv .venv && . .venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision
pip install docling rapidocr-onnxruntime reportlab python-docx pypdfium2 pillow rapidfuzz
docling-tools models download -o ./models layout tableformer
docling-tools models download rapidocr --rapidocr-backend-lang onnxruntime:en -o ./models

python make_corpus.py
DOCLING_ARTIFACTS_PATH=$PWD/models \
UPSTREAM_MULTIMODAL=/path/to/rag/data/multimodal \
  python bench.py                       # writes results.json and a Markdown table

# Unstructured, the rejected alternative, same documents and same scorer
python -m venv .venv-unst && . .venv-unst/bin/activate
pip install 'unstructured[pdf,docx]'    # plus the tesseract-ocr system package
UPSTREAM_MULTIMODAL=/path/to/rag/data/multimodal ./bench_unst.sh
```

Install torch from the CPU index. The default wheel pulls sixteen NVIDIA proprietary
CUDA packages into a component whose entire purpose is not needing any, and takes the
environment from 1.8 GiB to 6.3 GiB.

`results.json` is the recorded run on this tree: 59 rows, 8 vCPU, no GPU.
