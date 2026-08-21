import os
import io
import re
from PIL import Image

_OCR_ENGINE = None

def get_ocr_engine():
    """Lazy-load singleton instance of RapidOCR."""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
        except Exception:
            _OCR_ENGINE = False
    return _OCR_ENGINE if _OCR_ENGINE is not False else None


def extract_ocr_text(image_input) -> str:
    """
    Extract visible text from a screenshot file path or PIL Image / bytes.
    Returns clean extracted string with all words separated by space.
    """
    if not image_input:
        return ""

    engine = get_ocr_engine()
    if not engine:
        return ""

    try:
        if isinstance(image_input, str):
            if not os.path.exists(image_input):
                return ""
            result, _ = engine(image_input)
        elif isinstance(image_input, (bytes, bytearray)):
            img = Image.open(io.BytesIO(image_input))
            result, _ = engine(img)
        elif isinstance(image_input, Image.Image):
            result, _ = engine(image_input)
        else:
            return ""

        if not result:
            return ""

        text_lines = [item[1] for item in result if len(item) > 1 and item[1]]
        clean_text = " ".join(text_lines)
        return clean_text.strip()
    except Exception:
        return ""
