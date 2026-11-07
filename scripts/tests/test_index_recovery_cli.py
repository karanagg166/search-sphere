"""Recovery receipts remain private, bounded and explicit before mutation."""

import importlib.util
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("index_recovery_cli", Path(__file__).parents[1]/"recover_missing_vectors.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_dry_run_requires_exact_operator_scope_and_output():
    with pytest.raises(SystemExit):
        cli.arguments([])
    args = cli.arguments(["--kind","standalone","--client","search_sphere","--tenant","synthetic-owner","--output","private.json"])
    assert args.apply is None
    with pytest.raises(SystemExit):
        cli.arguments(["--apply","private.json"])
    args = cli.arguments(["--apply","private.json","--digest","a"*64])
    assert args.apply == Path("private.json")


def test_receipt_write_is_private_nonoverwriting_and_does_not_follow_symlinks(tmp_path):
    path = tmp_path/"receipt.json"
    cli.write_private_plan(path, {"synthetic":True})
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert cli.read_private_plan(path) == {"synthetic":True}
    with pytest.raises(FileExistsError):
        cli.write_private_plan(path, {"overwrite":True})
    link = tmp_path/"symlink.json"
    link.symlink_to(path)
    with pytest.raises(OSError):
        cli.read_private_plan(link)
    with pytest.raises(OSError):
        cli.write_private_plan(link, {"overwrite":True})
    assert cli.read_private_plan(path) == {"synthetic":True}


def test_public_or_oversized_receipt_cannot_be_applied(tmp_path):
    path = tmp_path/"receipt.json"
    path.write_text('{}')
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private regular file"):
        cli.read_private_plan(path)
    path.chmod(0o600)
    path.write_text(' '*65537)
    with pytest.raises(ValueError, match="64 KiB"):
        cli.read_private_plan(path)
