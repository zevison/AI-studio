"""Offline gate for a GitHub prerelease of the existing full Windows candidate ZIP.

This does NOT certify a stable release, fresh-PC installation, or EXE packaging.
No paid API or production credential is required.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-rc\.([1-9][0-9]*)$")


def declared_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for item in tree.body:
        if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
            if any(isinstance(name, ast.Name) and name.id == "APP_VERSION" for name in item.targets):
                if isinstance(item.value.value, str):
                    return item.value.value
    raise ValueError("Missing literal APP_VERSION")


def validate_tag(tag: str, version: str) -> None:
    match = TAG.fullmatch(tag)
    if not match or ".".join(match.groups()[:3]) != version:
        raise ValueError("Candidate tag must match source version: v" + version + "-rc.N")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_candidate(tag: str, archive: Path, root: Path = ROOT) -> dict:
    version = declared_version(root / "core/config.py")
    validate_tag(tag, version)
    register = json.loads((root / "VERSION_REGISTER.json").read_text(encoding="utf-8-sig"))
    if register.get("assigned_release_version") != version:
        raise ValueError("VERSION_REGISTER disagrees with APP_VERSION")
    if not archive.is_file():
        raise FileNotFoundError(archive)
    receipt_file = archive.with_suffix(".receipt.json")
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    if not (
        receipt.get("candidate_only") is True
        and receipt.get("release_passed") is False
        and receipt.get("fresh_windows_passed") is False
        and receipt.get("every_source_hash_verified") is True
    ):
        raise ValueError("Candidate receipt has invalid release classification")
    if receipt.get("archive") != archive.name or receipt.get("bytes") != archive.stat().st_size:
        raise ValueError("Candidate receipt does not match archive identity")
    if receipt.get("sha256") != sha256(archive):
        raise ValueError("Archive SHA-256 mismatch")
    prefix = archive.stem + "/Studio/"
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None:
            raise ValueError("ZIP CRC failed")
        source = bundle.read(prefix + "core/config.py")
        if source != (root / "core/config.py").read_bytes():
            raise ValueError("Packaged source bytes differ from current commit")
        included_register = json.loads(bundle.read(prefix + "VERSION_REGISTER.json"))
        if included_register.get("assigned_release_version") != version:
            raise ValueError("Packaged release registry mismatch")
        proof = json.loads(bundle.read(prefix + "docs/REPOSITORY_BUILD.json"))
        if proof.get("candidate_only") is not True or proof.get("release_passed") is not False:
            raise ValueError("Packaged proof falsely asserts stable acceptance")
    return {"tag": tag, "version": version, "sha256": receipt["sha256"],
            "archive": archive.name, "candidate_only": True, "stable_release": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    result = validate_candidate(args.tag, args.archive.resolve())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
