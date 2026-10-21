"""Fail-closed updater for signed Windows EXE releases.

Downloads only; does not interrupt the browser, backend, billing or running jobs.
The verified installer is applied on the next safe launch, while no backend runs.
Signing certificate pin MUST be provisioned before the updater can be enabled.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

APP_ID = "seedance-studio"
ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "tools" / "update_policy.json"
STORE = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache") / "SeedanceStudio" / "updates"
VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
MAX_SIZE = 2 * 1024**3
MAX_MANIFEST_BYTES = 65536
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class UpdateError(RuntimeError):
    pass


def version(text: str) -> tuple[int, int, int]:
    matched = VERSION.fullmatch(text) if isinstance(text, str) else None
    if not matched:
        raise UpdateError("Invalid 3-component release version")
    return tuple(map(int, matched.groups()))


def current_version() -> str:
    tree = ast.parse((ROOT / "core/config.py").read_text(encoding="utf-8-sig"))
    for assignment in tree.body:
        if isinstance(assignment, ast.Assign) and isinstance(assignment.value, ast.Constant):
            if any(isinstance(k, ast.Name) and k.id == "APP_VERSION" for k in assignment.targets):
                return str(assignment.value.value)
    raise UpdateError("Cannot read installed application version")


def validate_https(value: str) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise UpdateError("Invalid URL")
    url = urllib.parse.urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise UpdateError("Only credential-free HTTPS URLs are allowed")
    if url.port not in (None, 443):
        raise UpdateError("Unexpected HTTPS port")
    return value


def read_policy() -> dict:
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"enabled": False}
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return {"enabled": False}
    signer = policy.get("signer_sha256")
    if not isinstance(signer, str) or not SHA256.fullmatch(signer):
        raise UpdateError("Updater disabled: trusted signing certificate pin not provisioned")
    validate_https(policy.get("manifest_url"))
    return policy


def checked_metadata(data: dict, policy: dict, installed: str) -> dict | None:
    if not isinstance(data, dict) or data.get("app_id") != APP_ID or data.get("channel") != "stable":
        raise UpdateError("Unexpected application or update channel")
    target = data.get("version")
    if version(target) <= version(installed):
        return None
    if data.get("platform") != "windows-x64":
        raise UpdateError("Wrong target platform")
    if data.get("format") != "seedance.signed-exe@1":
        raise UpdateError("Unexpected update manifest format")
    length = data.get("size")
    if type(length) is not int or not 0 < length <= MAX_SIZE:
        raise UpdateError("Invalid installer size")
    digest = data.get("sha256")
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise UpdateError("Invalid installer hash")
    signer = data.get("signer_sha256")
    if not isinstance(signer, str) or signer.lower() != policy["signer_sha256"].lower():
        raise UpdateError("Installer publisher does not match pinned certificate")
    validate_https(data.get("download_url"))
    if data.get("mandatory") is True:
        # Enforced silently forced installs are intentionally unsupported.
        raise UpdateError("Forced installation policy not supported")
    return {k: data[k] for k in ("app_id", "channel", "version", "platform", "format",
                                "size", "sha256", "signer_sha256", "download_url")}


def fetch_manifest(policy: dict) -> dict:
    request = urllib.request.Request(validate_https(policy["manifest_url"]),
                                     headers={"User-Agent": "SeedanceStudio-WindowsUpdater/1"})
    with urllib.request.urlopen(request, timeout=12) as response:
        validate_https(response.geturl())
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("Manifest too large")
    doc = json.loads(raw.decode("utf-8"))
    if not isinstance(doc, dict):
        raise UpdateError("Manifest must be an object")
    return doc


def hash_file(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_signature(file: Path, expected: str) -> None:
    if os.name != "nt":
        raise UpdateError("Windows Authenticode verification requires Windows")
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "if($s.Status -ne 'Valid' -or $null -eq $s.SignerCertificate){exit 31};"
        "$h=$s.SignerCertificate.GetCertHash([Security.Cryptography.HashAlgorithmName]::SHA256);"
        "$actual=[BitConverter]::ToString($h).Replace('-','');"
        "if($actual -ne $args[1]){exit 32}"
    )
    env = os.environ.copy()
    env["STUDIO_VERIFY_FILE"] = str(file)
    env["STUDIO_VERIFY_PIN"] = expected.upper()
    script = script.replace("$args[0]", "$env:STUDIO_VERIFY_FILE").replace("$args[1]", "$env:STUDIO_VERIFY_PIN")
    import base64
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW, env=env,
    )
    if proc.returncode:
        raise UpdateError("Authenticode validation failed (or signer is not trusted)")


def atomic_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(dir=path.parent, prefix=".update-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(doc, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(filename, path)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)


def download(meta: dict, dest: Path) -> None:
    partial = dest.with_name(dest.stem + ".partial.exe")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            existing = partial.stat().st_size if partial.exists() else 0
            if existing > meta["size"]:
                partial.unlink()
                existing = 0
            request = urllib.request.Request(meta["download_url"], headers={
                "User-Agent": "SeedanceStudio-WindowsUpdater/1",
                **({"Range": f"bytes={existing}-"} if existing else {}),
            })
            with urllib.request.urlopen(request, timeout=30) as response:
                validate_https(response.geturl())
                status = response.status
                append = bool(existing and status == 206)
                if status not in (200, 206) or (not existing and status != 200):
                    raise UpdateError("Unexpected HTTP download status")
                if status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {existing}-"):
                        raise UpdateError("Range response does not match partial bytes")
                with partial.open("ab" if append else "wb") as output:
                    total = existing if append else 0
                    started = time.monotonic()
                    while True:
                        if time.monotonic() - started > 1800:
                            raise UpdateError("Download time limit reached")
                        block = response.read(1024 * 1024)
                        if not block:
                            break
                        total += len(block)
                        if total > meta["size"]:
                            raise UpdateError("Download exceeded declared size")
                        output.write(block)
            if partial.stat().st_size != meta["size"]:
                raise UpdateError("Incomplete installer download")
            if hash_file(partial).lower() != meta["sha256"].lower():
                partial.unlink(missing_ok=True)
                raise UpdateError("Installer SHA-256 mismatch")
            verify_signature(partial, meta["signer_sha256"])
            os.replace(partial, dest)
            return
        except (OSError, urllib.error.URLError, UpdateError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(1 + attempt * 2)


def log(message: str) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    with (STORE / "update.log").open("a", encoding="utf-8") as file:
        file.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + message[:450] + "\n")


def check() -> None:
    policy = read_policy()
    if not policy.get("enabled") or os.name != "nt":
        return
    metadata = checked_metadata(fetch_manifest(policy), policy, current_version())
    if metadata is None:
        return
    dest = STORE / ("SeedanceStudio-" + metadata["version"] + "-Setup.exe")
    if dest.exists():
        if dest.stat().st_size != metadata["size"] or hash_file(dest).lower() != metadata["sha256"].lower():
            dest.unlink()
    if not dest.exists():
        download(metadata, dest)
    verify_signature(dest, metadata["signer_sha256"])
    atomic_json(STORE / "pending.json", {"metadata": metadata, "installer": dest.name})
    log("Verified new version " + metadata["version"] + "; queued for next safe launch")


def apply_ready() -> bool:
    if os.name != "nt" or not (STORE / "pending.json").is_file():
        return False
    policy = read_policy()
    if not policy.get("enabled"):
        return False
    try:
        ready = json.loads((STORE / "pending.json").read_text(encoding="utf-8"))
        meta = checked_metadata(ready["metadata"], policy, current_version())
        if meta is None:
            (STORE / "pending.json").unlink(missing_ok=True)
            return False
        if ready.get("installer") != "SeedanceStudio-" + meta["version"] + "-Setup.exe":
            raise UpdateError("Pending file name mismatch")
        installer = STORE / ready["installer"]
        if installer.stat().st_size != meta["size"] or hash_file(installer).lower() != meta["sha256"].lower():
            raise UpdateError("Pending installer hash mismatch")
        verify_signature(installer, policy["signer_sha256"])
        from workspace_install_guard import any_workspace_running
        if any_workspace_running(ROOT):
            log("App process still running; update deferred")
            return False
        # Launch a separate process; NSIS installer performs its own stopped-task and
        # data migration checks and refuses to switch registration on failure.
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--run-installer",
                          str(installer)], cwd=str(ROOT), close_fds=True,
                         creationflags=NO_WINDOW, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (OSError, ValueError, KeyError, TypeError, UpdateError) as exc:
        log("Pending update refused: " + type(exc).__name__)
        return False


def run_installer(path: Path) -> None:
    # Validate again in child (the old launcher has already exited).
    ready = json.loads((STORE / "pending.json").read_text(encoding="utf-8"))
    policy = read_policy()
    meta = checked_metadata(ready["metadata"], policy, current_version())
    if meta is None or path.resolve() != (STORE / ready["installer"]).resolve():
        raise UpdateError("Pending update changed before installation")
    if path.stat().st_size != meta["size"] or hash_file(path).lower() != meta["sha256"].lower():
        raise UpdateError("Installer changed")
    verify_signature(path, policy["signer_sha256"])
    result = subprocess.run([str(path), "/S"], timeout=3600, creationflags=NO_WINDOW)
    if result.returncode:
        raise UpdateError("Installer failed with code " + str(result.returncode))
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\SeedanceStudio") as key:
        new_root = Path(winreg.QueryValueEx(key, "CurrentAppRoot")[0])
    if new_root.resolve() == ROOT.resolve():
        raise UpdateError("Installer returned success without activating new application")
    (STORE / "pending.json").unlink(missing_ok=True)
    log("Updated to " + meta["version"])
    subprocess.Popen(["cmd.exe", "/c", str(new_root / "00_START.cmd")],
                     cwd=str(new_root), creationflags=NO_WINDOW)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply-ready", action="store_true")
    group.add_argument("--run-installer", type=Path)
    args = parser.parse_args()
    try:
        if args.check:
            check()
        elif args.apply_ready:
            return 10 if apply_ready() else 0
        else:
            run_installer(args.run_installer)
        return 0
    except Exception as exc:
        log("Update error: " + type(exc).__name__ + ": " + str(exc)[:160])
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
