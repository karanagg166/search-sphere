"""Fail if pytest's legacy path setting accidentally chooses old worker sources."""

import importlib.util
from pathlib import Path

import src
from src.processing.chunking import semantic_splitter
from src.processing.extraction import ocr_processor


def test_worker_regressions_exercise_canonical_hosted_modules():
    expected=Path(__file__).resolve().parents[2]/"apps/api/src"
    assert Path(src.__file__).resolve()==expected/"__init__.py"
    assert Path(semantic_splitter.__file__).resolve().is_relative_to(expected)
    assert Path(ocr_processor.__file__).resolve().is_relative_to(expected)
    for package in ["torch","fastembed","transformers","sentence_transformers","pytesseract","docling"]:
        assert importlib.util.find_spec(package) is None
