# Personalized iOS Developer Disk Images

This public repository tests automatic publication of Apple's iOS 17+
personalized Developer Disk Image to `ghcr.io/timo972/ddi`. Once a workflow run
publishes an image, pull the newest verified build with:

```sh
oras pull ghcr.io/timo972/ddi:latest
tar -xzf DeveloperDiskImage.tar.gz
```

The archive contains the original `Restore` layout, including the personalized
and Cryptex images, trust caches, and build manifest. Device-specific
personalization still requires Apple's signing service.

See [source, validation, and workflow limits](docs/developer-disk-image-ghcr.md).
