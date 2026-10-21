"""Tests for independent prerelease gate, without downloads or paid services."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.release_preflight import validate_candidate, validate_tag


class CandidateReleaseTests(unittest.TestCase):
    def test_tag_rejects_different_versions_and_unstable_names(self):
        for tag in ("v5.2.1-rc.1", "v5.2.0", "v5.2.0-rc.0", "v05.2.0-rc.1", "v5.2.0-rc.1/evil"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                validate_tag(tag, "5.2.0")
        validate_tag("v5.2.0-rc.1", "5.2.0")

    def make_archive(self, base):
        from tools.release_preflight import sha256
        root = base / "root"
        (root / "core").mkdir(parents=True)
        (root / "core/config.py").write_text("APP_VERSION = '5.2.0'\n", encoding="utf-8")
        (root / "VERSION_REGISTER.json").write_text(json.dumps({"assigned_release_version": "5.2.0"}), encoding="utf-8")
        archive = base / "SeedanceStudio_Candidate.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.write(root / "core/config.py", "SeedanceStudio_Candidate/Studio/core/config.py")
            z.write(root / "VERSION_REGISTER.json", "SeedanceStudio_Candidate/Studio/VERSION_REGISTER.json")
            z.writestr("SeedanceStudio_Candidate/Studio/docs/REPOSITORY_BUILD.json",
                       json.dumps({"candidate_only": True, "release_passed": False}))
        receipt = {"archive": archive.name, "sha256": sha256(archive), "bytes": archive.stat().st_size,
                   "candidate_only": True, "release_passed": False, "fresh_windows_passed": False,
                   "every_source_hash_verified": True}
        archive.with_suffix(".receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        return root, archive

    def test_clean_candidate_passes(self):
        with tempfile.TemporaryDirectory() as t:
            root, archive = self.make_archive(Path(t))
            result = validate_candidate("v5.2.0-rc.1", archive, root)
            self.assertFalse(result["stable_release"])

    def test_archive_tampering_fails(self):
        with tempfile.TemporaryDirectory() as t:
            root, archive = self.make_archive(Path(t))
            with archive.open("ab") as f:
                f.write(b"tampered")
            with self.assertRaises(ValueError):
                validate_candidate("v5.2.0-rc.1", archive, root)

    def test_false_stable_receipt_fails(self):
        with tempfile.TemporaryDirectory() as t:
            root, archive = self.make_archive(Path(t))
            receipt_file = archive.with_suffix(".receipt.json")
            data = json.loads(receipt_file.read_text())
            data["release_passed"] = True
            receipt_file.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                validate_candidate("v5.2.0-rc.1", archive, root)


if __name__ == "__main__":
    unittest.main()
