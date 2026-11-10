"""Bounded native-PDF text matching. No inference, OCR, network or application config."""

import json
import math
import re
import sys

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 100
MAX_CHARACTERS = 2_000_000
STOP_WORDS = frozenset("a an and are as at be by can do does for from how i in is it me my of on or please tell that the their this to was what when where which who why with you your".split())


class NativeTextError(ValueError):
    pass


def terms_for(query):
    terms = set(re.findall(r"[\w]+(?:[-./:][\w]+)*", query.casefold(), re.UNICODE)) - STOP_WORDS
    terms = {word for word in terms if len(word) > 1 or word.isnumeric()}
    if not terms or len(terms) > 32:
        raise NativeTextError("specific_search_terms_required")
    return terms


def search_native_pdf(content, query, limit=5):
    import pymupdf

    if not content.startswith(b"%PDF-") or len(content) > MAX_BYTES:
        raise NativeTextError("invalid_pdf")
    terms = terms_for(query)
    matches, characters = [], 0
    try:
        with pymupdf.open(stream=content, filetype="pdf") as pdf:
            if pdf.needs_pass:
                raise NativeTextError("encrypted_pdf")
            if not 1 <= pdf.page_count <= MAX_PAGES:
                raise NativeTextError("pdf_page_limit")
            pages = pdf.page_count
            for page_number, page in enumerate(pdf, 1):
                for block in page.get_text("blocks", sort=True):
                    if block[6] != 0:
                        continue
                    text = block[4].strip()
                    characters += len(text)
                    if characters > MAX_CHARACTERS:
                        raise NativeTextError("pdf_text_limit")
                    folded = text.casefold()
                    tokens = set(re.findall(r"[\w]+(?:[-./:][\w]+)*", folded, re.UNICODE))
                    found = terms & tokens
                    if not found:
                        continue
                    # Deterministic relevance only. Return verbatim source text.
                    position = min(folded.find(term) for term in found)
                    start = max(0, position - 500) if len(text) > 2000 else 0
                    excerpt = text[start:start + 2000]
                    bbox = [round(float(value), 2) for value in block[:4]]
                    if not all(math.isfinite(value) for value in bbox):
                        raise NativeTextError("invalid_pdf")
                    matches.append({"page_number": page_number, "bbox": bbox,
                                    "text": excerpt, "matched_terms": len(found),
                                    "_score": len(found) / len(terms)})
                    # Bound retained output while scanning the complete allowed PDF.
                    matches.sort(key=lambda row: (-row["_score"], row["page_number"]))
                    del matches[limit:]
    except NativeTextError:
        raise
    except Exception:
        raise NativeTextError("invalid_pdf") from None
    for match in matches:
        match.pop("_score")
    return {"results": matches, "pages_checked": pages, "native_text_present": characters > 0}


def main():
    try:
        # Hard limits are applied only in the child process, before loading MuPDF.
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
        header = sys.stdin.buffer.readline(8193)
        if len(header) > 8192:
            raise NativeTextError("invalid_request")
        request = json.loads(header)
        query, limit = request["query"], request["limit"]
        if not isinstance(query, str) or not 1 <= len(query) <= 1000 or type(limit) is not int or not 1 <= limit <= 10:
            raise NativeTextError("invalid_request")
        content = sys.stdin.buffer.read(MAX_BYTES + 1)
        result = search_native_pdf(content, query, limit)
    except NativeTextError as error:
        result = {"error_code": str(error)}
    except BaseException:
        result = {"error_code": "pdf_processing_failed"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
