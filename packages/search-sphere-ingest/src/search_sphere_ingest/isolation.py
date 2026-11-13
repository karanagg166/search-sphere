"""Native extraction in a disposable child, with no provider credentials inherited."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from .config import PipelineConfig
from .errors import IngestError, ResourceLimit
from .models import NormalizedDocument


def extract_isolated(content: bytes, config: PipelineConfig | None = None) -> NormalizedDocument:
    config = config or PipelineConfig()
    if len(content) > config.max_file_bytes:
        raise ResourceLimit("file_too_large")
    with TemporaryDirectory(prefix="ss-native-") as directory:
        root = Path(directory)
        source, settings, output = (
            root / name for name in ("source.pdf", "config.json", "result.json")
        )
        for path, data in ((source, content), (settings, config.model_dump_json().encode())):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
        try:
            process = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "search_sphere_ingest.isolation",
                    str(source),
                    str(settings),
                    str(output),
                ],
                timeout=config.timeout_seconds + 5,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"},
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise IngestError("processing_timeout", retryable=True) from None
        if process.returncode or not output.exists():
            raise ResourceLimit("native_worker_resource_failure")
        if output.stat().st_size > config.max_content_bytes * 2:
            raise ResourceLimit("worker_response_budget")
        try:
            result = json.loads(output.read_bytes())
            if "error" in result:
                raise IngestError(
                    str(result["error"]), retryable=bool(result.get("retryable", False))
                )
            return NormalizedDocument.model_validate(result["document"])
        except (ValueError, KeyError, TypeError):
            raise IngestError("invalid_worker_response") from None


def _worker() -> int:
    # Linux production isolation; macOS does not support meaningful RLIMIT_AS.
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024,) * 2)
    source, settings, output = (Path(value) for value in sys.argv[1:])
    config = PipelineConfig.model_validate_json(settings.read_bytes())
    try:
        from .pipeline import DocumentPipeline

        result: dict[str, object] = {
            "document": DocumentPipeline(config).extract(source).model_dump(mode="json")
        }
    except IngestError as error:
        result = {"error": error.code, "retryable": error.retryable}
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(result, handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker())
