# Worker boot-disk sizing

Date: 2026-09-29. Scope: read-only canonical Docker and host inspection, plus Get of the
previously reviewed public Ubuntu base image. No image pull/build, workload, cleanup,
restart, cloud mutation or production sizing change was performed.

## Measured evidence

Canonical worker build: `454e46a03634fd5ca55672c41f1fd17ddc052376`.
Actual running image: `sha256:b8fc901e707924979399662d870b3d2f6f2f3768e4919390956a59c742552a81`.
Both bulk and selfie use this image. Docker Engine is `29.6.0`, with the containerd
`overlayfs` snapshotter. Measurements below are bytes converted using 2^30 for GiB.

| Component | Evidence | GiB |
| --- | --- | ---: |
| Compressed image content | `docker image inspect .Size`: 3,860,232,436 bytes | 3.595 |
| Unpacked image layers | container `SizeRootFs`: 5,191,462,912 bytes | 4.835 |
| Combined image accounting | `docker image ls` and `docker system df -v`: 9.05 decimal GB | 8.430 |
| Models visible inside the container | `du -sx -B1 /worker/models`: 1,726,410,752 bytes | 1.608 |
| Python packages visible inside the container | `du -sx -B1 /usr/local/lib/python3.12/site-packages`: 1,298,579,456 bytes | 1.209 |
| Current writable layer, per inspected bulk/selfie container | `docker inspect --size`: 4,096 bytes | negligible |

Models and packages are already included in the image accounting: do not add them again.
The combined value is image accounting, not a clean-VM `df` delta or a measured peak
during extraction. On a dedicated VM, allow for the whole image, not just the unique
7.419 decimal GB shown after sharing layers with unrelated canonical images.
Multiple containers on the same host share image layers; separate worker VMs do not.

[Docker documents](https://docs.docker.com/engine/storage/containerd/) that containerd
retains both compressed registry blobs and extracted layers. Therefore the compressed
3.595 GiB alone is not the boot-disk requirement.

The final `RUN chown -R worker:worker /worker` layer is reported as **1.77 decimal GB**
(approximately 1.65 GiB), while the visible `/worker` tree occupies 1,774,325,760 bytes.
This is concrete layer duplication worth addressing separately. It is not fixed by deleting
models at runtime; use correct ownership when introducing files to avoid the extra copy.
No Dockerfile changes were made as part of this diagnosis. A future rebuilt image must be
measured again; do not book estimated savings as completed.

## OS and runtime budget: estimate, not clean-image proof

The reviewed public base image `fd84a0ma316h9ddtvdoi`
(`ubuntu-24-04-lts-v20260928`) is READY and reports `min_disk_size=10 GiB`.
That is the provider's minimum boot-disk size, **not 10 GiB of occupied OS files**.
The final clean worker OS image with pinned Docker/Compose and telemetry dependencies
has not been built or booted.

As a cross-check only, canonical host directory sizes were `/usr` 3.419 GiB,
`/boot` 0.114 GiB, apt state 0.195 GiB, dpkg state 0.032 GiB and `/etc` 0.006 GiB.
These are a mature, different-purpose host, not a substitute for measuring the clean image.
Budget **4–5 GiB** for OS, Docker/Compose, Python and telemetry prerequisites until that
image is measured. Existing canonical DB volumes, application images and historical images
must not be copied or charged to the worker OS budget.

## Calculated dedicated-worker budget

| Item | GiB | Confidence |
| --- | ---: | --- |
| Current worker image, compressed plus unpacked | 8.43 | measured image accounting |
| Clean OS and host runtime | 4–5 | estimate; clean image outstanding |
| Bounded logs and temporary execution files | 1–2 | selected budget, not a measured worst case |
| Free headroom / filesystem overhead / startup extraction margin | 3–4 | selected reserve |
| Total | **16.43–19.43** | estimate conditional on one retained worker image |

The log allowance requires an explicit bounded journald policy in the reviewed worker OS
recipe; the remote Compose uses journald, but this sizing check does not prove that the
canonical log cap is inherited by the future worker image. Temporary media is one job at a
time, with bounded inputs; this is not a measured maximum of every processor's scratch use.

The existing release replaces VMs rather than pulling old and new worker images onto the
same worker VM. This budget assumes a clean OS-only base image and one worker digest per
VM. A base image with a preloaded old worker image or an in-place two-image update invalidates
the estimate. Building the Docker image on worker VMs is also excluded.

**20 GiB is a plausible smaller candidate**, pending a clean-image startup/extraction and
bounded representative scratch check. **24 GiB offers more initial headroom without image
optimization.** Neither is currently certified by a clean worker VM. The earlier 32 GiB
was conservative planning capacity, not a demonstrated minimum.

## Quota arithmetic, conditional on the previously inspected 100 GiB canonical SSD

| Worker disk size | Two worker disks + canonical | Four worker disks + canonical |
| --- | ---: | ---: |
| 20 GiB | 140 GiB | 180 GiB |
| 24 GiB | 148 GiB | 196 GiB |
| 32 GiB | 164 GiB | 228 GiB |

These figures are allocations, not occupied bytes, and are not a fresh quota snapshot.
Include every transitional/stopped/retained worker disk and any builder disk before applying.
Do not infer that group maximums alone bound provider replacement disk lifetimes.
Changing disk size or the approved scaling/release design remains a separate implementation
decision; this diagnosis does not resume the paused fixed-mode implementation or authorize
resource creation.

Commands: selected `docker ps/info/version/image inspect/history/image ls/system df`,
selected container `docker inspect --size`, directory-only `du -sx -B1`, `df -B1 /`,
and `yc compute image get --id fd84a0ma316h9ddtvdoi --folder-id b1g2qttgfhb4gdunvlge`.
No container environment, credentials, customer content or secret payloads were printed.
