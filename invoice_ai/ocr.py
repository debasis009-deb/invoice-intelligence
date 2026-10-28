"""Turn an image or PDF into text boxes (with positions and confidence) and group them into lines.

Engines: `tesseract` (pytesseract, needs the Tesseract binary), `rapidocr`
(pip-only ONNX models) and `pdf-text` (text layer of a digital PDF, no OCR).
`auto` uses the PDF text layer when a PDF has one, otherwise RapidOCR.
"""
import io
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

import pdfplumber
import pypdfium2
import pytesseract
from PIL import Image

if os.environ.get("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.environ["TESSERACT_CMD"]

ENGINES = ("rapidocr", "tesseract", "pdf-text")
RENDER_DPI = 150


@dataclass
class Word:
    """One text box from an engine. Rapidocr boxes can hold several words."""
    text: str
    x0: float
    x1: float
    y: float  # vertical centre
    height: float
    conf: float  # 0..1


@dataclass
class Token:
    """One whitespace-separated piece of text with its horizontal extent."""
    text: str
    x0: float
    x1: float
    conf: float


@dataclass
class Line:
    text: str
    conf: float
    tokens: list[Token] = field(default_factory=list)
    y: float = 0.0
    height: float = 0.0


def split_word(word: Word) -> list[Token]:
    """Split a box into tokens; positions inside a multi-word box are estimated from character counts."""
    width = (word.x1 - word.x0) / max(len(word.text), 1)
    return [Token(m.group(), word.x0 + m.start() * width, word.x0 + m.end() * width, word.conf)
            for m in re.finditer(r"\S+", word.text)]


def group_words(words: list[Word]) -> list[Line]:
    """Words whose vertical centres are within 0.6 of a text height share a line."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.y):
        if rows and abs(w.y - rows[-1][-1].y) <= w.height * 0.6:
            rows[-1].append(w)
        else:
            rows.append([w])
    lines = []
    for row in rows:
        row.sort(key=lambda w: w.x0)
        tokens = [t for w in row for t in split_word(w)]
        lines.append(Line(" ".join(w.text for w in row), sum(w.conf for w in row) / len(row), tokens,
                          sum(w.y for w in row) / len(row), max(w.height for w in row)))
    return lines


def load_pages(data: bytes, filename: str) -> list[Image.Image]:
    if filename.lower().endswith(".pdf"):
        pdf = pypdfium2.PdfDocument(data)
        return [page.render(scale=RENDER_DPI / 72).to_pil() for page in pdf]
    return [Image.open(io.BytesIO(data))]


def _tesseract(img: Image.Image) -> list[Word]:
    # Tried 1.5x upscaling with --psm 6 on the dev split: no net gain, so defaults stay.
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    return [Word(t, x, x + w, y + h / 2, h, float(c) / 100)
            for t, x, w, y, h, c in zip(d["text"], d["left"], d["width"], d["top"], d["height"], d["conf"])
            if t.strip() and float(c) >= 0]




@lru_cache(maxsize=1)
def _rapid():
    from rapidocr_onnxruntime import RapidOCR  # slow import, load on first use
    return RapidOCR()


def _rapidocr(img: Image.Image) -> list[Word]:
    import numpy as np
    result, _ = _rapid()(np.array(img.convert("RGB")))
    words = []
    for box, text, conf in result or []:
        ys = [p[1] for p in box]
        # one entry per text segment; its left edge orders segments on a line
        xs = [p[0] for p in box]
        words.append(Word(text, min(xs), max(xs), sum(ys) / 4, max(ys) - min(ys), float(conf)))
    return words


def _pdf_text(data: bytes) -> list[Word]:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return [Word(w["text"], w["x0"], w["x1"], (w["top"] + w["bottom"]) / 2, w["bottom"] - w["top"], 1.0)
                for w in pdf.pages[0].extract_words()]


def read_words(data: bytes, filename: str, engine: str = "auto") -> tuple[list[Word], str]:
    """Return the text boxes of the first page and the engine actually used."""
    is_pdf = filename.lower().endswith(".pdf")
    if engine == "auto":
        engine = "pdf-text" if is_pdf and _pdf_text(data) else "rapidocr"
    if engine == "pdf-text":
        return _pdf_text(data), engine
    if engine not in ENGINES:
        raise ValueError(f"unknown engine {engine!r}, choose from {ENGINES} or auto")
    page = load_pages(data, filename)[0]
    return (_tesseract(page) if engine == "tesseract" else _rapidocr(page)), engine
