import hashlib
import json
import pathlib
import plistlib
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

    def test_selects_newest_installed_xcode(self):
        with tempfile.TemporaryDirectory() as tmp:
            apps = pathlib.Path(tmp)
            for name, build in [("Xcode_27.1.app", "27A9275"), ("Xcode_27.2_RC.app", "27B48")]:
                contents = apps / name / "Contents"
                contents.mkdir(parents=True)
                (contents / "version.plist").write_bytes(plistlib.dumps({"ProductBuildVersion": build}))
            selected = ddi_ghcr.select_xcode(
                [{"xcode_build": "27B48"}, {"xcode_build": "27A9275"}], apps
            )
            self.assertEqual(selected["build"], "27B48")
            self.assertEqual(selected["path"], str(apps / "Xcode_27.2_RC.app"))

    def test_selects_newest_advertised_runner_without_exceeding_release(self):
        readme = "\n".join([
            "| Xcode 27 | arm64 | `xcode-27` or `xcode-27-xlarge` | [xcode-27] |",
            "| Xcode 28 | arm64 | `xcode-28` or `xcode-28-xlarge` | [xcode-28] |",
        ])
        self.assertEqual(ddi_ghcr.select_runner([{"xcode_build": "28A100"}], readme), "xcode-28")
        self.assertEqual(ddi_ghcr.select_runner([{"xcode_build": "27B48"}], readme), "xcode-27")
        self.assertEqual(ddi_ghcr.select_runner([{"xcode_build": "28A100"}], readme.splitlines()[0]), "xcode-27")


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

    def test_lists_one_layer_per_file(self):
        result = ddi_ghcr.package(self.root)
        self.assertEqual(result["ddi_build"], "27A9275")
        self.assertEqual(result["manifest_sha256"], hashlib.sha256((self.root / "Restore/BuildManifest.plist").read_bytes()).hexdigest())
        self.assertEqual(len(result["files"]), 9)
        self.assertIn("version.plist", result["files"])
        self.assertIn("Restore/BuildManifest.plist:" + ddi_ghcr.MANIFEST_MEDIA, result["layers"])
        self.assertIn("Restore/PersonalizedDMG.bin:" + ddi_ghcr.FILE_MEDIA, result["layers"])
        self.assertEqual(sum(layer.endswith(ddi_ghcr.MANIFEST_MEDIA) for layer in result["layers"]), 1)

    def test_rejects_modified_payload(self):
        path = self.root / "Restore/PersonalizedDMG.bin"
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            ddi_ghcr.package(self.root)

    def test_rejects_missing_cryptex_asset(self):
        del self.manifest["BuildIdentities"][0]["Manifest"]["Cryptex1,GenericVolume"]
        del self.manifest["BuildIdentities"][1]["Manifest"]["Cryptex1,GenericVolume"]
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "incomplete personalized DDI"):
            ddi_ghcr.package(self.root)


class PublishedStateTest(unittest.TestCase):
    def manifest(self, artifact_type, digest):
        return {
            "artifactType": artifact_type,
            "layers": [
                {"mediaType": ddi_ghcr.FILE_MEDIA, "digest": "sha256:" + "1" * 64},
                {"mediaType": ddi_ghcr.MANIFEST_MEDIA, "digest": "sha256:" + digest},
            ],
        }

    def test_states(self):
        sha = "a" * 64
        self.assertEqual(ddi_ghcr.published_state(self.manifest(ddi_ghcr.PERSONALIZED_TYPE, sha), sha), "current")
        self.assertEqual(ddi_ghcr.published_state(self.manifest(ddi_ghcr.PERSONALIZED_TYPE, "b" * 64), sha), "mismatch")
        v1 = {"artifactType": "application/vnd.idevice.personalized-ddi.v1", "layers": [{"mediaType": "application/vnd.idevice.personalized-ddi.v1+tar+gzip"}]}
        self.assertEqual(ddi_ghcr.published_state(v1, sha), "stale")
        self.assertFalse(ddi_ghcr.is_current(v1))
        self.assertTrue(ddi_ghcr.is_current(self.manifest(ddi_ghcr.PERSONALIZED_TYPE, sha)))

    def test_prunes_only_retired_layouts(self):
        versions = [
            {"id": 1, "artifactType": "application/vnd.idevice.personalized-ddi.v1"},
            {"id": 2, "artifactType": ddi_ghcr.PERSONALIZED_TYPE},
            {"id": 3, "artifactType": ddi_ghcr.CLASSIC_TYPE},
            {"id": 4, "artifactType": ""},
            {"id": 5},
        ]
        self.assertEqual(ddi_ghcr.prunable(versions), [1])


class ClassicTest(unittest.TestCase):
    def tree(self, entries, truncated=False):
        return {"truncated": truncated, "tree": [
            {"type": "blob", "path": path, "sha": sha, "size": 1} for path, sha in entries
        ] + [{"type": "tree", "path": "DeveloperDiskImages/16.7"}]}

    def test_plans_sorted_versions(self):
        tree = self.tree([
            ("DeveloperDiskImages/16.7/DeveloperDiskImage.dmg", "a"),
            ("DeveloperDiskImages/16.7/DeveloperDiskImage.dmg.signature", "b"),
            ("DeveloperDiskImages/9.3/DeveloperDiskImage.dmg", "c"),
            ("DeveloperDiskImages/9.3/DeveloperDiskImage.dmg.signature", "d"),
            ("PersonalizedImages/Xcode_iOS_DDI_Personalized/Image.dmg", "e"),
            ("DeveloperDiskImages/16.7/README.md", "f"),
        ])
        plan = ddi_ghcr.classic_plan(tree)
        self.assertEqual([p["version"] for p in plan], ["9.3", "16.7"])
        self.assertEqual([(f["name"], f["sha"]) for f in plan[1]["files"]],
                         [("DeveloperDiskImage.dmg", "a"), ("DeveloperDiskImage.dmg.signature", "b")])

    def test_rejects_incomplete_or_odd_trees(self):
        with self.assertRaisesRegex(ValueError, "lacks"):
            ddi_ghcr.classic_plan(self.tree([("DeveloperDiskImages/16.7/DeveloperDiskImage.dmg", "a")]))
        with self.assertRaisesRegex(ValueError, "unexpected"):
            ddi_ghcr.classic_plan(self.tree([("DeveloperDiskImages/16.7 beta/DeveloperDiskImage.dmg", "a")]))
        with self.assertRaisesRegex(ValueError, "truncated"):
            ddi_ghcr.classic_plan(self.tree([], truncated=True))
        with self.assertRaisesRegex(ValueError, "no classic"):
            ddi_ghcr.classic_plan(self.tree([]))

    def test_classic_push_round_trips_through_state(self):
        plan = ddi_ghcr.classic_plan(self.tree([
            ("DeveloperDiskImages/16.7/DeveloperDiskImage.dmg", "a"),
            ("DeveloperDiskImages/16.7/DeveloperDiskImage.dmg.signature", "b"),
        ]))
        push = ddi_ghcr.classic_push(plan[0], "c" * 40)
        self.assertEqual(push["layers"], [
            "DeveloperDiskImage.dmg:application/vnd.idevice.classic-ddi.image.v1",
            "DeveloperDiskImage.dmg.signature:application/vnd.idevice.classic-ddi.signature.v1",
        ])
        self.assertEqual(push["annotations"]["$manifest"]["org.opencontainers.image.version"], "16.7")
        self.assertIn("c" * 40, push["annotations"]["$manifest"]["io.idevice.ddi.source"])
        # What oras records for the push reads back as current.
        layers = [
            {"annotations": {"org.opencontainers.image.title": name, **push["annotations"][name]}}
            for name in ddi_ghcr.CLASSIC_FILES
        ]
        self.assertEqual(ddi_ghcr.classic_state({"artifactType": ddi_ghcr.CLASSIC_TYPE, "layers": layers}, plan[0]), "current")

    def test_git_blob_sha_matches_git(self):
        # git hash-object of "hello\n"
        self.assertEqual(ddi_ghcr.git_blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_classic_state(self):
        entry = {"files": [{"name": "DeveloperDiskImage.dmg", "sha": "a"}, {"name": "DeveloperDiskImage.dmg.signature", "sha": "b"}]}
        layers = [
            {"annotations": {"org.opencontainers.image.title": "DeveloperDiskImage.dmg", ddi_ghcr.GIT_BLOB_ANNOTATION: "a"}},
            {"annotations": {"org.opencontainers.image.title": "DeveloperDiskImage.dmg.signature", ddi_ghcr.GIT_BLOB_ANNOTATION: "b"}},
        ]
        self.assertEqual(ddi_ghcr.classic_state({"artifactType": ddi_ghcr.CLASSIC_TYPE, "layers": layers}, entry), "current")
        self.assertEqual(ddi_ghcr.classic_state({"artifactType": ddi_ghcr.CLASSIC_TYPE, "layers": layers[:1]}, entry), "stale")
        self.assertEqual(ddi_ghcr.classic_state({"artifactType": "other", "layers": layers}, entry), "stale")


if __name__ == "__main__":
    unittest.main()
