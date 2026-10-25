# Personalized iOS Developer Disk Image in GHCR

The weekly workflow publishes Apple's **iOS 17+ personalized DDI** as an OCI
artifact at `ghcr.io/timo972/ddi:<ProductBuildVersion>`.
`latest` points to the newest Xcode release the workflow has processed. The
archive contains `Restore/BuildManifest.plist`, both personalized and Cryptex
disk images, their trust caches and Cryptex metadata, `Restore.plist`, and
`version.plist`. It preserves Apple's paths and manifest bytes.

Pull it with ORAS:

```sh
oras pull ghcr.io/timo972/ddi:latest
tar -xzf DeveloperDiskImage.tar.gz
```

The manifest still needs Apple's device-specific personalization ticket when
mounting; this artifact does not contain a ticket or a classic pre-iOS-17
`DeveloperDiskImage.dmg.signature`.

## Source and publication

This public test repository uses `ubuntu-latest` for discovery, so it can test
without registering a self-hosted runner. The job polls the xcodereleases.com
JSON feed and considers
the twelve most recent distinct Xcode builds. That feed supplies Xcode builds,
Apple's XIP URLs, and SHA-1 checksums. Apple's
`index2.dvtdownloadableindex` lists simulator runtimes, Metal toolchains,
documentation, and device-support components, but **no personalized DDI**;
the workflow does not poll it because its changes cannot identify a new DDI.

The XIP URL redirects anonymous requests to Apple's unauthorized page. More
importantly, the current Xcode.app bundle does not contain the DDI: Xcode
installs it separately under `/Library/Developer/CoreDevice/CandidateDDIs`.
For these reasons the workflow does not download or expand the XIP on Ubuntu.
The macOS job uses GitHub's `xcode-27` runner. It selects the newest Xcode app
whose build appears in that release list, then runs Apple's documented
`xcodebuild -runFirstLaunch -checkForNewerComponents` to fetch device support.
If the runner has not caught up, the job records that fact and retries on the
next schedule. This can delay or prevent publication of releases that never
appear on a hosted runner.

The macOS job attaches `iOS_DDI.dmg` read-only with `hdiutil`. It refuses a
DDI whose build differs from the selected Xcode build. Before publication it
checks the SHA-1 or SHA-384 digest of every referenced payload against Apple's
`BuildManifest.plist`. It tags the verified artifact with its DDI build,
`latest`, and an `xcode-<build>` receipt tag.
If a build tag already exists, it checks the recorded manifest SHA-256 before
reusing it; a same-build content change fails instead of silently retaining
stale bytes.
No device identifiers or personalization tickets are published.

The workflow runs on the default branch by schedule or manual dispatch. GitHub
must start both the Ubuntu and macOS jobs before publication can be verified.
A local check is:

```sh
python3 -B -m unittest discover -s scripts -p 'test_ddi_ghcr.py' -v
actionlint .github/workflows/developer-disk-image.yml
```
