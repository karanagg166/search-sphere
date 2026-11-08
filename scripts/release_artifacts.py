"""Bounded trusted-repository artifact reads; never extract archives to disk."""
import io
import json
import stat
import subprocess
import threading
import zipfile

from release_gate import REPOSITORY, ReleaseError, request, require

MAX_ARCHIVE = 524288
MAX_PAYLOAD = 262144


def github(path):
    return request("https://api.github.com", "/repos/" + REPOSITORY + path, require("GH_TOKEN"))


def workflow_runs(workflow):
    """Fail closed on pagination bounds, changing history or incomplete metadata."""
    if workflow not in {"release.yml", "post-deploy-smoke.yml", "native-fixture-reconciliation.yml"}:
        raise ReleaseError("Unknown native release workflow.")
    runs, expected, seen = [], None, set()
    for page in range(1, 11):
        data = github(f"/actions/workflows/{workflow}/runs?branch=main&event=workflow_dispatch&per_page=100&page={page}")
        if not isinstance(data, dict):
            raise ReleaseError("Native history metadata is unavailable.")
        count, rows = data.get("total_count"), data.get("workflow_runs")
        if (type(count) is not int or not 0 <= count < 1000 or not isinstance(rows, list)
            or len(rows) > 100 or (expected is not None and expected != count)):
            raise ReleaseError("Native history is incomplete or exceeds its reconciliation bound.")
        expected = count
        for row in rows:
            if (not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0
                or row["id"] in seen or row.get("path") != ".github/workflows/" + workflow
                or row.get("event") != "workflow_dispatch" or row.get("head_branch") != "main"
                or not isinstance(row.get("repository"), dict)
                or row["repository"].get("full_name") != REPOSITORY
                or not isinstance(row.get("head_repository"), dict)
                or row["head_repository"].get("full_name") != REPOSITORY):
                raise ReleaseError("Untrusted or changing native workflow history.")
            seen.add(row["id"])
            runs.append(row)
        if len(runs) == count:
            return runs
        if len(rows) != 100 or len(runs) > count:
            break
    raise ReleaseError("Native history enumeration did not complete.")


def artifact_for(run, name):
    data = github(f"/actions/runs/{run['id']}/artifacts?per_page=100")
    if not isinstance(data, dict):
        raise ReleaseError("Artifact enumeration is unavailable.")
    rows, count = data.get("artifacts"), data.get("total_count")
    if (type(count) is not int or not 0 <= count < 100 or not isinstance(rows, list)
        or len(rows) != count):
        raise ReleaseError("Artifact enumeration is unavailable or exceeds its bound.")
    matches = [row for row in rows if isinstance(row, dict) and row.get("name") == name]
    if not matches:
        return None
    if len(matches) != 1:
        raise ReleaseError("Ambiguous native cleanup artifact.")
    value = matches[0]
    source = value.get("workflow_run", {})
    if (type(value.get("id")) is not int or value["id"] <= 0 or value.get("expired") is not False
        or type(value.get("size_in_bytes")) is not int or not 0 < value["size_in_bytes"] <= MAX_ARCHIVE
        or not isinstance(source, dict) or source.get("id") != run["id"]
        or source.get("head_sha") != run.get("head_sha") or source.get("head_branch") != "main"):
        raise ReleaseError("Unusable or foreign native cleanup artifact.")
    return value


def archive_bytes(artifact):
    """gh handles its authenticated download redirect; bound bytes and wall time."""
    if type(artifact.get("id")) is not int or artifact["id"] <= 0:
        raise ReleaseError("Invalid native cleanup artifact identity.")
    process = subprocess.Popen(
        ["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{artifact['id']}/zip"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )

    def stop():
        try:
            process.kill()
        except ProcessLookupError:
            pass

    timer = threading.Timer(45, stop)
    timer.daemon = True
    timer.start()
    try:
        payload = process.stdout.read(MAX_ARCHIVE + 1)
        if len(payload) > MAX_ARCHIVE:
            raise ReleaseError("Native cleanup archive exceeds its byte bound.")
        if process.wait(timeout=5) != 0:
            raise ReleaseError("Native cleanup archive download failed.")
        return payload
    except (OSError, subprocess.TimeoutExpired):
        raise ReleaseError("Native cleanup archive download failed.") from None
    finally:
        timer.cancel()
        stop()
        process.stdout.close()
        process.wait()


def archive_json(payload, filename):
    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("duplicate JSON field")
            result[name] = value
        return result

    try:
        if len(payload) > MAX_ARCHIVE:
            raise ValueError("archive bound")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            files = archive.infolist()
            if len(files) != 1:
                raise ValueError("ambiguous archive")
            info = files[0]
            mode = stat.S_IFMT(info.external_attr >> 16)
            if (info.filename != filename or info.is_dir() or mode not in (0, stat.S_IFREG)
                or info.flag_bits & 1 or not 0 < info.file_size <= MAX_PAYLOAD):
                raise ValueError("unsafe archive")
            with archive.open(info) as handle:
                data = handle.read(MAX_PAYLOAD + 1)
            if len(data) != info.file_size or len(data) > MAX_PAYLOAD:
                raise ValueError("payload bound")
            return json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile):
        raise ReleaseError("Native cleanup archive is invalid or unsafe.") from None
