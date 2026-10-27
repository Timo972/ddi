# iOS Developer Disk Images in GHCR

The weekly workflow publishes Apple's **iOS 17+ personalized DDI** as an OCI
artifact at `ghcr.io/timo972/ddi:<ProductBuildVersion>`.
`latest` points to the newest Xcode release the workflow has processed. The
artifact holds `Restore/BuildManifest.plist`, both personalized and Cryptex
disk images, their trust caches and Cryptex metadata, `Restore.plist`, and
`version.plist`. It preserves Apple's paths and manifest bytes.

Pull it with ORAS; it writes Apple's layout into the current directory:

```sh
oras pull ghcr.io/timo972/ddi:latest
```

The manifest still needs Apple's device-specific personalization ticket when
mounting; this artifact does not contain a ticket.

## Layout

Personalized, `artifactType: application/vnd.idevice.personalized-ddi.v2`:
one uncompressed layer per file, its `org.opencontainers.image.title` the
path relative to the mounted DDI (`Restore/BuildManifest.plist`,
`Restore/022-22446-057.dmg`, `Restore/Firmware/….trustcache`,
`version.plist`). The build manifest's layer has media type
`application/vnd.idevice.personalized-ddi.buildmanifest.v2`, every other
file `application/vnd.idevice.personalized-ddi.file.v2`. A path in the build
manifest (`Info.Path`) is the title without its `Restore/` prefix, so a
client reads the manifest layer, then fetches only the layers it needs (the
PersonalizedDMG and LoadableTrustCache, about 17 MB) and checks each against
its OCI digest and Apple's manifest digest. The manifest annotation
`org.opencontainers.image.version` is the DDI build; the manifest layer's
digest is the SHA-256 of `BuildManifest.plist`.

Classic, `artifactType: application/vnd.idevice.classic-ddi.v1`, tagged
`classic-<major.minor>`: two layers, `DeveloperDiskImage.dmg`
(`application/vnd.idevice.classic-ddi.image.v1`) and
`DeveloperDiskImage.dmg.signature`
(`application/vnd.idevice.classic-ddi.signature.v1`).
`org.opencontainers.image.version` is the version; `io.idevice.ddi.source`
names the doronz88 commit and directory, and each layer's
`io.idevice.ddi.source.git-blob` its git blob SHA-1 there. The device checks
Apple's signature when it mounts a classic image; no Apple digest exists to
check on the host.

Layout v1 (`application/vnd.idevice.personalized-ddi.v1`, one
`DeveloperDiskImage.tar.gz` layer) is retired: the `prune` job deletes its
package versions once `latest` names a v2 artifact.

## Classic images

Classic images no longer change: Xcode 15 replaced them with the
personalized image, and they ship only in Xcode 14.3.1 and older, which no
hosted runner carries. They were therefore uploaded once, on 2026-10-27, and
no workflow publishes them. The 40 versions (11.4 to 16.7) come from
`DeveloperDiskImages/<major.minor>/` of doronz88/DeveloperDiskImage at
commit `6eae353ae694bda1c421d4a3eee5459ae59c99a1`; each file was checked
against that tree's git blob SHA-1 before the push, and the annotations
record the source. The upload trusts doronz88's copies; the device's
signature check at mount is the end-to-end check. The `prune` job never
deletes them.

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
The Linux job reads GitHub's published runner-image list and chooses the newest
`xcode-<major>` runner no newer than the latest Xcode release. When GitHub adds
an `xcode-28` runner, the workflow can switch to it without an edit. The macOS
job selects the newest installed Xcode app whose build appears in the release
list, then runs Apple's documented
`xcodebuild -runFirstLaunch -checkForNewerComponents` to fetch device support.
If the runner has not caught up, the job records that fact and retries on the
next schedule. This can delay or prevent publication of releases that never
appear on a hosted runner.

The macOS job attaches `iOS_DDI.dmg` read-only with `hdiutil`. It refuses a
DDI whose build differs from the selected Xcode build. Before publication it
checks the SHA-1 or SHA-384 digest of every referenced payload against Apple's
`BuildManifest.plist`. It tags the verified artifact with its DDI build,
`latest`, and an `xcode-<build>` receipt tag.
If a v2 build tag already exists, it compares its manifest layer digest with
the local `BuildManifest.plist` before reusing it; a same-build content
change fails instead of silently retaining stale bytes. A build or receipt
tag in a retired layout counts as unpublished, so that build is pushed again
in the current one.
No device identifiers or personalization tickets are published.

The workflow runs on the default branch by schedule or manual dispatch. GitHub
must start both the Ubuntu and macOS jobs before publication can be verified.
A local check is:

```sh
python3 -B -m unittest discover -s scripts -p 'test_ddi_ghcr.py' -v
actionlint .github/workflows/developer-disk-image.yml
```
