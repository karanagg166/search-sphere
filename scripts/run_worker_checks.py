"""Run preserved deterministic worker tests against the single canonical runtime."""

import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=["apps/worker/tests/test_"+name+".py" for name in ["line_cleaner","hyphenation_cleaner","text_cleaner",
    "whitespace_cleaner","noise_cleaner","chunking","document_extractor"]]
FILES += ["scripts/tests/test_worker_runtime_source.py","scripts/tests/test_hosted_runtime.py","scripts/tests/test_hosted_token_limits.py"]


def main():
    environment={**os.environ,"PYTHONPATH":str(ROOT/"apps/api"),"ENVIRONMENT":"test"}
    return subprocess.call([sys.executable,"-m","pytest","-c","apps/api/pyproject.toml","--rootdir",".",
        "-o","pythonpath=apps/api","--import-mode=importlib",*FILES,"-v"],cwd=ROOT,env=environment)


if __name__=="__main__":
    raise SystemExit(main())
