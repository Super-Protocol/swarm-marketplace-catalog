"""Deterministic PDF, DOCX and TXT writers, standard library only.

A clinical note has to arrive the way a clinic's notes actually arrive — a PDF out
of an endoscopy reporting system, a DOCX letter from a referring practice, a text
export from a scheduling tool. Rendering them needs no third-party package: a PDF
is a small object graph and a DOCX is a ZIP of four XML parts, so both are written
here directly. The gain is not fewer dependencies, it is byte-for-byte
reproducibility — reportlab and python-docx both stamp a creation time, and a file
whose bytes move every run cannot be checked into anything or compared across runs.

Everything below is pinned: a fixed PDF `/CreationDate`, a fixed ZIP member
timestamp, no hashing of unordered collections. Two runs of generate.py with the
same seed produce identical files.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import Iterable, Sequence

# A fixed point in time, so the rendered bytes do not depend on when they were
# rendered. 2026-11-17 is the generator's as-of date.
FIXED_PDF_DATE = "D:20261117000000Z"
FIXED_ZIP_TIME = (2026, 11, 17, 0, 0, 0)

PAGE_W, PAGE_H = 612, 792  # US Letter, in points: these are US clinic documents
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 54, 54, 54

# Courier at 9pt advances exactly 5.4pt per glyph, which is what makes wrapping a
# character count rather than a font-metrics problem.
BODY_FONT, BODY_SIZE, BODY_ADVANCE = "F1", 9.0, 5.4
BODY_LEADING = 11.5
HEAD_LEADING = 18.0

LINE_CHARS = int((PAGE_W - 2 * MARGIN_X) / BODY_ADVANCE)  # 93

# Line styles a note may use: a heading, a paragraph, a preformatted block, a
# vertical gap, a horizontal rule. `blank` and `rule` ignore their text.

@dataclass(frozen=True)
class Line:
    style: str
    text: str = ""


def h1(text: str) -> Line:
    return Line("h1", text)


def h2(text: str) -> Line:
    return Line("h2", text)


def body(text: str) -> Line:
    return Line("body", text)


def mono(text: str) -> Line:
    return Line("mono", text)


def blank() -> Line:
    return Line("blank")


def rule() -> Line:
    return Line("rule")


def wrap(text: str, width: int = LINE_CHARS) -> list[str]:
    """Greedy wrap. Returns at least one line, so an empty paragraph keeps its space.

    A word longer than the line is left over-long rather than hyphenated: in this
    corpus the only such words are identifiers, and breaking an MRN in half would
    be worse than a ragged margin.
    """
    words = text.split()
    if not words:
        return [""]
    out: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            out.append(current)
            current = word
    out.append(current)
    return out


def flow(lines: Sequence[Line]) -> list[Line]:
    """Wrap every `body` line to the page width; `mono` lines are left as written."""
    out: list[Line] = []
    for line in lines:
        if line.style == "body":
            # An indented paragraph keeps its indent on every line it wraps onto;
            # without this a wrapped list item loses its shape after the first line.
            stripped = line.text.lstrip(" ")
            indent = " " * (len(line.text) - len(stripped))
            for piece in wrap(stripped, width=LINE_CHARS - len(indent)):
                out.append(Line("body", indent + piece))
        elif line.style in ("h1", "h2"):
            # h1 renders in Helvetica-Bold 13pt, which is wider than Courier 9pt,
            # so it wraps earlier than the body does.
            width = 58 if line.style == "h1" else LINE_CHARS
            for piece in wrap(line.text, width=width):
                out.append(Line(line.style, piece))
        else:
            out.append(line)
    return out


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_PDF_FONTS = {
    "F1": "Courier",
    "F2": "Courier-Bold",
    "F3": "Helvetica-Bold",
}


#: WinAnsiEncoding is not Latin-1. The typographic punctuation this corpus uses
#: lives in the 0x80-0x9F range that Latin-1 leaves as control characters, so
#: encoding straight to Latin-1 turns every em dash into a question mark. Mapping
#: them explicitly keeps the dashes and quotes, and keeps `pdftotext` able to
#: recover them — which matters, because something downstream of this file is
#: going to extract its text rather than look at it.
_WINANSI = {
    "\u2014": "\x97", "\u2013": "\x96", "\u2018": "\x91", "\u2019": "\x92",
    "\u201c": "\x93", "\u201d": "\x94", "\u2026": "\x85", "\u2022": "\x95",
    "\u2122": "\x99", "\u20ac": "\x80",
}


def _pdf_escape(text: str) -> str:
    out = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    for source, replacement in _WINANSI.items():
        out = out.replace(source, replacement)
    return out.encode("latin-1", "replace").decode("latin-1")


def _paginate(lines: Sequence[Line]) -> list[list[Line]]:
    pages: list[list[Line]] = []
    current: list[Line] = []
    y = PAGE_H - MARGIN_TOP
    for line in lines:
        leading = HEAD_LEADING if line.style in ("h1", "h2") else BODY_LEADING
        if y - leading < MARGIN_BOTTOM:
            pages.append(current)
            current = []
            y = PAGE_H - MARGIN_TOP
        current.append(line)
        y -= leading
    pages.append(current)
    return pages


def _page_stream(lines: Sequence[Line]) -> bytes:
    parts: list[str] = []
    y = PAGE_H - MARGIN_TOP
    for line in lines:
        if line.style == "blank":
            y -= BODY_LEADING
            continue
        if line.style == "rule":
            y -= BODY_LEADING
            parts.append(
                f"0.5 w {MARGIN_X} {y + 3:.1f} m {PAGE_W - MARGIN_X} {y + 3:.1f} l S"
            )
            continue
        if line.style == "h1":
            font, size, leading = "F3", 13.0, HEAD_LEADING
        elif line.style == "h2":
            font, size, leading = "F2", 10.0, HEAD_LEADING
        else:
            font, size, leading = BODY_FONT, BODY_SIZE, BODY_LEADING
        y -= leading
        parts.append(
            f"BT /{font} {size:g} Tf 1 0 0 1 {MARGIN_X} {y:.1f} Tm "
            f"({_pdf_escape(line.text)}) Tj ET"
        )
    return "\n".join(parts).encode("latin-1")


def write_pdf(path: str, title: str, lines: Sequence[Line]) -> None:
    pages = _paginate(flow(lines))

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)  # object numbers are 1-based

    font_ids = {}
    for key, base in _PDF_FONTS.items():
        font_ids[key] = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>"
            % base.encode("ascii")
        )

    font_dict = " ".join(f"/{k} {v} 0 R" for k, v in font_ids.items())

    # The pages object has to name its kids, and the kids have to name it, so its
    # number is reserved before the kids exist.
    pages_id = add(b"PLACEHOLDER")
    page_ids: list[int] = []
    for page in pages:
        stream = _page_stream(page)
        content_id = add(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
        page_ids.append(
            add(
                (
                    f"<< /Type /Page /Parent {pages_id} 0 R "
                    f"/MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                    f"/Resources << /Font << {font_dict} >> >> "
                    f"/Contents {content_id} 0 R >>"
                ).encode("latin-1")
            )
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Count {len(page_ids)} /Kids [{kids}] >>".encode("latin-1")
    )

    info_id = add(
        (
            f"<< /Title ({_pdf_escape(title)}) /Producer (swarm-marketplace-catalog "
            f"synthetic data generator) /CreationDate ({FIXED_PDF_DATE}) "
            f"/ModDate ({FIXED_PDF_DATE}) >>"
        ).encode("latin-1")
    )
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number
        out += obj
        out += b"\nendobj\n"

    xref_at = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog_id,
        info_id,
        xref_at,
    )

    with open(path, "wb") as handle:
        handle.write(bytes(out))


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
</Types>
"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>
"""

_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr></w:pPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="240" w:after="120"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="200" w:after="80"/></w:pPr>
    <w:rPr><w:b/><w:sz w:val="26"/></w:rPr>
  </w:style>
</w:styles>
"""


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _docx_body(lines: Iterable[Line]) -> str:
    parts: list[str] = []
    for line in lines:
        text = _xml_escape(line.text)
        if line.style == "h1":
            parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
                f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
            )
        elif line.style == "h2":
            parts.append(
                f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr>'
                f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
            )
        elif line.style == "mono":
            parts.append(
                '<w:p><w:pPr><w:spacing w:after="0"/></w:pPr><w:r>'
                '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="18"/></w:rPr>'
                f'<w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
            )
        elif line.style in ("blank", "rule"):
            parts.append('<w:p><w:pPr><w:spacing w:after="0"/></w:pPr></w:p>')
        else:
            parts.append(
                f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
            )
    return "".join(parts)


def write_docx(path: str, title: str, lines: Sequence[Line]) -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{_docx_body(lines)}"
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"/></w:sectPr>'
        "</w:body></w:document>"
    )
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/"'
        ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{_xml_escape(title)}</dc:title>"
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-11-17T00:00:00Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-11-17T00:00:00Z</dcterms:modified>'
        "</cp:coreProperties>"
    )

    members = [
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("docProps/core.xml", core),
        ("word/_rels/document.xml.rels", _DOC_RELS),
        ("word/styles.xml", _STYLES),
        ("word/document.xml", document),
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in members:
            info = zipfile.ZipInfo(name, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, text)


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------


def write_txt(path: str, title: str, lines: Sequence[Line]) -> None:
    out: list[str] = []
    for line in flow(lines):
        if line.style == "blank":
            out.append("")
        elif line.style == "rule":
            out.append("-" * LINE_CHARS)
        elif line.style == "h1":
            out.append(line.text.upper())
        elif line.style == "h2":
            out.append(line.text)
        else:
            out.append(line.text)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(out).rstrip("\n") + "\n")


WRITERS = {"pdf": write_pdf, "docx": write_docx, "txt": write_txt}
