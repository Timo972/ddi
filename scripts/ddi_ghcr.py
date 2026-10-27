#!/usr/bin/env python3
"""Discover an Xcode release, validate Apple's installed personalized iOS DDI,
and describe it as OCI layers for oras."""

import argparse
import hashlib
import json
import pathlib
import plistlib
import re
import urllib.parse
import urllib.request


RELEASES = "https://xcodereleases.com/data.json"
RUNNER_IMAGES = "https://raw.githubusercontent.com/actions/runner-images/main/README.md"
BUILD = re.compile(r"[0-9]{2}[A-Z][0-9]+[a-z]?")
RUNNER_ROW = re.compile(r"^\| Xcode ([0-9]+)\b.*\|\s*arm64\s*\|\s*`xcode-([0-9]+)`")
# OCI layout of a personalized DDI: one layer per file, titled by its path
# relative to the mounted image, so `oras pull` recreates Apple's layout and
# a client fetches only the files it needs. Version 1 was one tar.gz layer.
PERSONALIZED_TYPE = "application/vnd.idevice.personalized-ddi.v2"
MANIFEST_MEDIA = "application/vnd.idevice.personalized-ddi.buildmanifest.v2"
FILE_MEDIA = "application/vnd.idevice.personalized-ddi.file.v2"
MANIFEST_FILE = "Restore/BuildManifest.plist"
RETIRED_TYPES = {"application/vnd.idevice.personalized-ddi.v1"}
# The classic-<major.minor> tags (application/vnd.idevice.classic-ddi.v1) were
# uploaded once and are never pruned: classic images no longer change.
PAYLOADS = {
    "PersonalizedDMG",
    "LoadableTrustCache",
    "Cryptex1,GenericDmg",
    "Cryptex1,GenericTrustCache",
    "Cryptex1,CryptexInfoPlist",
    "Cryptex1,GenericVolume",
}


def fetch(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def discover(releases_bytes):
    releases = json.loads(releases_bytes)
    candidates = []
    for release in releases:
        version = release.get("version", {})
        build = version.get("build", "")
        url = release.get("links", {}).get("download", {}).get("url", "")
        checksum = release.get("checksums", {}).get("sha1", "")
        if not BUILD.fullmatch(build) or not re.fullmatch(r"[a-fA-F0-9]{40}", checksum):
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "download.developer.apple.com" or not parsed.path.endswith(".xip"):
            continue
        if "iOS" not in release.get("sdks", {}):
            continue
        candidates.append((release.get("_dateOrder", 0), release.get("_versionOrder", 0), release))
    if not candidates:
        raise ValueError("no Xcode release with an Apple XIP and checksum")
    ordered = sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)
    builds = []
    seen = set()
    for _, _, release in ordered:
        build = release["version"]["build"]
        if build in seen:
            continue
        seen.add(build)
        builds.append({
            "xcode_build": build,
            "xcode_version": release["version"]["number"],
            "xip_url": release["links"]["download"]["url"],
            "xip_sha1": release["checksums"]["sha1"].lower(),
        })
        if len(builds) == 12:
            break
    release = builds[0]
    return {
        **release,
        "candidates": builds,
    }


def select_runner(candidates, readme):
    newest_major = int(candidates[0]["xcode_build"][:2])
    available = set()
    for line in readme.splitlines():
        if match := RUNNER_ROW.match(line):
            if match.group(1) == match.group(2):
                available.add(int(match.group(1)))
    eligible = [major for major in available if major <= newest_major]
    if not eligible:
        raise ValueError("GitHub advertises no Xcode runner for the recent releases")
    return f"xcode-{max(eligible)}"


def select_xcode(candidates, app_root):
    installed = {}
    for app in pathlib.Path(app_root).glob("Xcode*.app"):
        version_file = app / "Contents/version.plist"
        if not version_file.is_file():
            continue
        version = plistlib.loads(version_file.read_bytes())
        build = version.get("ProductBuildVersion")
        if isinstance(build, str) and BUILD.fullmatch(build):
            installed[build] = app
    for candidate in candidates:
        build = candidate["xcode_build"]
        if build in installed:
            return {"available": True, "build": build, "path": str(installed[build])}
    return {"available": False}


def package(source):
    """Validate the DDI mounted at source and list its files as OCI layers."""
    source = pathlib.Path(source)
    restore = source / "Restore"
    version = plistlib.loads((source / "version.plist").read_bytes())
    manifest = plistlib.loads((restore / "BuildManifest.plist").read_bytes())
    build = manifest.get("ProductBuildVersion")
    if not isinstance(build, str) or not BUILD.fullmatch(build):
        raise ValueError("invalid DDI build")
    if version.get("Platform") != "iOS" or version.get("ProductBuildVersion") != build:
        raise ValueError("DDI version and manifest do not match")
    identities = manifest.get("BuildIdentities", [])
    if not identities:
        raise ValueError("DDI has no build identities")
    files = {"Restore/BuildManifest.plist", "Restore/Restore.plist", "version.plist"}
    found = set()
    digests = {}
    for identity in identities:
        for kind, entry in identity.get("Manifest", {}).items():
            if kind not in PAYLOADS:
                raise ValueError(f"unexpected DDI payload: {kind}")
            relative = pathlib.PurePosixPath(entry["Info"]["Path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe payload path: {relative}")
            path = restore / relative
            if not path.resolve().is_relative_to(restore.resolve()):
                raise ValueError(f"payload escapes Restore: {relative}")
            expected = entry.get("Digest")
            if not isinstance(expected, bytes) or len(expected) not in (20, 48) or not path.is_file():
                raise ValueError(f"missing payload or supported digest: {relative}")
            method = entry["Info"].get("HashMethod")
            if method not in (None, "sha1", "sha2-384") or (method == "sha1" and len(expected) != 20) or (method == "sha2-384" and len(expected) != 48):
                raise ValueError(f"unsupported digest method: {relative}")
            digests.setdefault(relative, set()).add(expected)
            files.add("Restore/" + str(relative))
            found.add(kind)
    if found != PAYLOADS:
        raise ValueError(f"incomplete personalized DDI: {sorted(PAYLOADS - found)}")
    for relative, expected_digests in digests.items():
        sha1, sha384 = hashlib.sha1(), hashlib.sha384()
        with (restore / relative).open("rb") as payload:
            for chunk in iter(lambda: payload.read(1024 * 1024), b""):
                sha1.update(chunk)
                sha384.update(chunk)
        for expected in expected_digests:
            actual = sha1.digest() if len(expected) == 20 else sha384.digest()
            if actual != expected:
                raise ValueError(f"digest mismatch: {relative}")
    for name in files:
        # oras push splits "path:mediatype" at the last colon.
        if ":" in name:
            raise ValueError(f"file name holds a colon: {name}")
    return {
        "ddi_build": build,
        "files": sorted(files),
        "layers": [f"{name}:{MANIFEST_MEDIA if name == MANIFEST_FILE else FILE_MEDIA}" for name in sorted(files)],
        "manifest_sha256": hashlib.sha256((restore / "BuildManifest.plist").read_bytes()).hexdigest(),
    }


def published_state(manifest, manifest_sha256):
    """Compare a published OCI manifest with a local BuildManifest.plist.

    "current": a v2 artifact whose BuildManifest layer has the same digest.
    "stale": any other layout, to be published again.
    "mismatch": a v2 artifact of the same build with other manifest bytes.
    """
    if manifest.get("artifactType") != PERSONALIZED_TYPE:
        return "stale"
    for layer in manifest.get("layers", []):
        if layer.get("mediaType") == MANIFEST_MEDIA:
            return "current" if layer.get("digest") == "sha256:" + manifest_sha256 else "mismatch"
    return "mismatch"


def is_current(manifest):
    """Report whether a published OCI manifest uses the current personalized layout."""
    return manifest.get("artifactType") == PERSONALIZED_TYPE


def prunable(versions):
    """Return the ids of package versions in a retired layout.

    versions holds {"id": ..., "artifactType": ...} entries. Only retired
    types are deleted, so an unknown or unreadable version stays.
    """
    return [v["id"] for v in versions if v.get("artifactType") in RETIRED_TYPES]



def load_json(path):
    return json.loads(pathlib.Path(path).read_text())


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover")
    select = sub.add_parser("select-xcode")
    select.add_argument("candidate")
    select.add_argument("app_root")
    sub.add_parser("package").add_argument("source")
    state = sub.add_parser("published-state")
    state.add_argument("manifest")
    state.add_argument("manifest_sha256")
    sub.add_parser("is-current").add_argument("manifest")
    sub.add_parser("prunable").add_argument("versions")
    args = parser.parse_args()
    if args.command == "discover":
        result = discover(fetch(RELEASES))
        result["macos_runner"] = select_runner(result["candidates"], fetch(RUNNER_IMAGES).decode())
    elif args.command == "select-xcode":
        result = select_xcode(load_json(args.candidate)["candidates"], args.app_root)
    elif args.command == "package":
        result = package(args.source)
    elif args.command == "published-state":
        result = published_state(load_json(args.manifest), args.manifest_sha256)
    elif args.command == "is-current":
        result = is_current(load_json(args.manifest))
    elif args.command == "prunable":
        result = prunable(load_json(args.versions))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
