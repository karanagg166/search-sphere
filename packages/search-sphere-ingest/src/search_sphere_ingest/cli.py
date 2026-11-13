"""Explicit output paths protect document content from accidental console logging."""

import argparse
import json
import os
from pathlib import Path

from .errors import IngestError
from .models import IngestResult
from .pipeline import DocumentPipeline


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract and chunk PDFs without a web app or database"
    )
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument(
        "--memory-mb",
        type=int,
        default=384,
        help="POSIX address space ceiling; use Linux for enforceable isolation",
    )
    args = parser.parse_args()
    if args.memory_mb < 128 or args.memory_mb > 2048:
        parser.error("memory-mb must be 128..2048")
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        # macOS shared-library virtual mappings make RLIMIT_AS unsuitable there.
        import sys

        if sys.platform == "linux":
            resource.setrlimit(resource.RLIMIT_AS, (args.memory_mb * 1024 * 1024,) * 2)
    try:
        pipeline = DocumentPipeline()
        result = pipeline.extract(args.pdf) if args.extract_only else pipeline.ingest(args.pdf)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(result.model_dump_json(indent=2))
        status = result.document.status if isinstance(result, IngestResult) else result.status
        print(json.dumps({"status": status, "output_written": True}))
        return 0 if status == "COMPLETED" else 2
    except IngestError as error:
        print(json.dumps({"error": error.code, "retryable": error.retryable}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
