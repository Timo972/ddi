# iOS Developer Disk Images

This public repository publishes Apple's iOS developer disk images (DDIs) to
`ghcr.io/timo972/ddi` as OCI artifacts, one layer per file:

- **Personalized** (iOS 17 and later), from the DDI Xcode installs, checked
  against Apple's `BuildManifest.plist` digests. Tags: the DDI build
  (`27B48`), `xcode-<build>`, and `latest` for the newest.
- **Classic** (iOS 11.4 to 16.7), uploaded once from
  [doronz88/DeveloperDiskImage](https://github.com/doronz88/DeveloperDiskImage)
  at a pinned commit; they no longer change. Tags: `classic-<major.minor>`
  (`classic-16.7`).

Pull the newest personalized image, in Apple's `Restore` layout:

```sh
oras pull ghcr.io/timo972/ddi:latest
```

Or a classic one:

```sh
oras pull ghcr.io/timo972/ddi:classic-16.7
```

Device-specific personalization still requires Apple's signing service.

See [layout, source, validation, and workflow limits](docs/developer-disk-image-ghcr.md).
