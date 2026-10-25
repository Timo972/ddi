import hashlib
import json
import pathlib
import plistlib
import tarfile
import tempfile
import unittest

import ddi_ghcr


class DiscoverTest(unittest.TestCase):
    def test_selects_newest_apple_xip(self):
        releases = [
            {
                "_dateOrder": date,
                "_versionOrder": date,
                "version": {"build": build, "number": "27.1"},
                "links": {"download": {"url": url}},
                "checksums": {"sha1": "a" * 40},
                "sdks": {"iOS": [{}]},
            }
            for date, build, url in [
                (20261015, "27A9275", "https://download.developer.apple.com/Xcode_27.1.xip"),
                (20261019, "27B48", "https://download.developer.apple.com/Xcode_27.2.xip"),
                (20261020, "27B99", "https://attacker.example/Xcode_27.2.xip"),
            ]
        ]
        result = ddi_ghcr.discover(json.dumps(releases).encode())
        self.assertEqual(result["xcode_build"], "27B48")
        self.assertEqual(len(result["candidates"]), 2)


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        restore = self.root / "Restore"
        restore.mkdir()
        (restore / "Restore.plist").write_bytes(plistlib.dumps({}))
        (self.root / "version.plist").write_bytes(plistlib.dumps({"Platform": "iOS", "ProductBuildVersion": "27A9275"}))
        manifest = {}
        for kind in ddi_ghcr.PAYLOADS:
            path = kind.replace(",", "-") + ".bin"
            content = kind.encode()
            (restore / path).write_bytes(content)
            manifest[kind] = {"Info": {"Path": path}, "Digest": hashlib.sha384(content).digest()}
        # Real manifests can carry both SHA-1 and SHA-384 for the same file.
        second = {kind: dict(value) for kind, value in manifest.items()}
        for kind in ("PersonalizedDMG", "LoadableTrustCache"):
            value = manifest[kind]
            second[kind] = {"Info": dict(value["Info"]), "Digest": hashlib.sha1(kind.encode()).digest()}
        self.manifest = {"ProductBuildVersion": "27A9275", "BuildIdentities": [{"Manifest": manifest}, {"Manifest": second}]}
        self.save_manifest()

    def save_manifest(self):
        (self.root / "Restore/BuildManifest.plist").write_bytes(plistlib.dumps(self.manifest))

    def test_packages_both_personalized_variants(self):
        archive = self.root / "ddi.tar.gz"
        result = ddi_ghcr.package(self.root, archive)
        self.assertEqual(result["ddi_build"], "27A9275")
        self.assertEqual(result["manifest_sha256"], hashlib.sha256((self.root / "Restore/BuildManifest.plist").read_bytes()).hexdigest())
        with tarfile.open(archive) as tar:
            self.assertIn("Restore/BuildManifest.plist", tar.getnames())
            self.assertEqual(len(tar.getnames()), 9)

    def test_rejects_modified_payload(self):
        path = self.root / "Restore/PersonalizedDMG.bin"
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            ddi_ghcr.package(self.root, self.root / "ddi.tar.gz")

    def test_rejects_missing_cryptex_asset(self):
        del self.manifest["BuildIdentities"][0]["Manifest"]["Cryptex1,GenericVolume"]
        del self.manifest["BuildIdentities"][1]["Manifest"]["Cryptex1,GenericVolume"]
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "incomplete personalized DDI"):
            ddi_ghcr.package(self.root, self.root / "ddi.tar.gz")


if __name__ == "__main__":
    unittest.main()
