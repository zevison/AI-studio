"""Offline update channel/adversarial checks. No HTTPS, no real signature, no fees."""
import tempfile
import unittest
from pathlib import Path

from tools.update_client import (
    UpdateError, checked_metadata, hash_file, validate_https, version
)

PIN = "a" * 64
POLICY = {"enabled": True, "signer_sha256": PIN,
          "manifest_url": "https://updates.example.com/stable.json"}


def candidate(**overrides):
    meta = {
        "app_id": "seedance-studio", "channel": "stable", "version": "5.2.1",
        "platform": "windows-x64", "format": "seedance.signed-exe@1",
        "size": 100, "sha256": "b" * 64, "signer_sha256": PIN,
        "download_url": "https://downloads.example.com/SeedanceStudio-Setup.exe",
    }
    meta.update(overrides)
    return meta


class UpdateAdversarialTests(unittest.TestCase):
    def test_valid_candidate(self):
        self.assertEqual(checked_metadata(candidate(), POLICY, "5.2.0")["version"], "5.2.1")

    def test_already_current_and_downgrade(self):
        self.assertIsNone(checked_metadata(candidate(version="5.2.0"), POLICY, "5.2.0"))
        self.assertIsNone(checked_metadata(candidate(version="5.1.1"), POLICY, "5.2.0"))

    def test_untrusted_and_foreign_rejected(self):
        for params in (
            {"signer_sha256": "c" * 64},
            {"channel": "beta"},
            {"app_id": "product-trend-assistant"},
            {"download_url": "http://evil.example/installer.exe"},
            {"download_url": "https://user:secret@updates.example/file.exe"},
            {"version": "5.2.1-dev"},
            {"size": -1},
            {"size": True},
            {"size": 3 * 1024**3},
            {"mandatory": True},
        ):
            with self.subTest(params=params), self.assertRaises(UpdateError):
                checked_metadata(candidate(**params), POLICY, "5.2.0")

    def test_tls_strict(self):
        self.assertEqual(validate_https("https://downloads.example/installer.exe"),
                         "https://downloads.example/installer.exe")
        for invalid in ("ftp://downloads.example/x", "http://downloads.example/x",
                        "https://downloads.example:8443/x", "https://x/#fragment"):
            with self.subTest(url=invalid), self.assertRaises(UpdateError):
                validate_https(invalid)

    def test_version_input(self):
        self.assertEqual(version("5.2.0"), (5, 2, 0))
        for invalid in ("05.2.0", "5.2", "5.2.0-rc.1", "5.2.0.0", "5.2.0/../x"):
            with self.assertRaises(UpdateError):
                version(invalid)

    def test_modified_installer_changes_hash(self):
        with tempfile.TemporaryDirectory() as t:
            path = Path(t) / "installer.exe"
            path.write_bytes(b"MZ" + b"a" * 20)
            original = hash_file(path)
            path.write_bytes(path.read_bytes() + b"evil")
            self.assertNotEqual(original, hash_file(path))


if __name__ == "__main__":
    unittest.main()
