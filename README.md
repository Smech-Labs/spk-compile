# spk-compile

The build system for **SmechOS** — an independent Linux distribution built
from scratch by Smech Labs.

SmechOS is **not** a fork or derivative of ChromiumOS, Ubuntu, Fedora, Debian
or any other distribution, and is not affiliated with, endorsed by or
sponsored by any of those projects or their vendors. `spk-compile.py`
downloads upstream sources and compiles them into a bootable image: a
systemd/glibc base, a custom kernel, KDE Plasma as the desktop, and `spk` as
the package manager.

Bug reports live in **[smechos-issues](https://github.com/Smech-Labs/smechos-issues)**.

---

## Contents

| File | Purpose |
|---|---|
| `spk-compile.py` | The whole build system: phases, package lists, patches |
| `build-one.sh` | Build a **single** package against an existing rootfs |
| `Dockerfile`, `build.sh` | Containerised full build |

---

## Start here if you're picking up an issue

Most open issues are of the form *"package X was never added to the component
list"*. The code change is genuinely one line. The expensive part is
**verifying** it, and you do not need a full distribution build for that.

```bash
./build-one.sh spectacle 6.7.2 /path/to/rootfs
```

That compiles just the one package against a rootfs you already have and
installs it. Minutes, not hours.

Requirements: `podman`, `sudo`, ~6 GB free, and an extracted SmechOS rootfs
(see below).

### KDE Gear vs Plasma — the trap

KDE ships on two independent release tracks with **different version
numbers**, and guessing wrong gives a confusing 404:

| Track | Examples | Version style | Flag |
|---|---|---|---|
| Plasma | `kwin`, `spectacle`, `kinfocenter` | `6.7.2` | *(default)* |
| KDE Gear | `konsole`, `dolphin`, `ark` | `25.08.3` | `--gear` |

```bash
./build-one.sh konsole 25.08.3 /path/to/rootfs --gear
```

---

## Getting a rootfs

Either extract one from an existing ISO:

```bash
mkdir -p /tmp/iso && sudo mount -o loop,ro SmechOS-1.0-RC2.iso /tmp/iso
unsquashfs -d ./rootfs /tmp/iso/live/filesystem.squashfs
```

…or build one from scratch (long — several hours):

```bash
sudo python3 spk-compile.py smechos-plasma-live
```

---

## Why builds happen in a container

This is the single most important thing to understand before touching the
build, and it is not obvious.

**The SmechOS rootfs ships no libc headers.** There is no
`/usr/include/bits`, and the multiarch include directory contains only
`qt6`. It is a pure *runtime* image, so it **cannot be used as a build
sysroot** — `--sysroot` against it fails immediately.

Building on a modern host instead runs into a second wall. The rootfs is
**Ubuntu glibc 2.39**; a current host is much newer. The rootfs's own build
tools (`qtpaths`, `moc`, `msgfmt`) then cannot run at all:

```
qtpaths: symbol lookup error: .../libc.so.6:
         undefined symbol: __nptl_change_stack_perm, version GLIBC_PRIVATE
```

That is the target's `libc.so.6` being loaded by a newer host `ld.so`. You
can work around it with explicit `ld.so --library-path` invocations, but
build systems call these tools by absolute path, so you end up bind-mounting
a directory of wrapper scripts over the rootfs's `bin` — at which point mount
propagation bites too.

`ubuntu:24.04` **is** glibc 2.39. Inside it every one of those problems
disappears at once: the rootfs's tools run natively, headers and libraries
agree, and output is ABI-correct by construction. `build-one.sh` does exactly
this and nothing clever.

### RUNPATH leaks

CMake bakes the staging path into `RUNPATH` even with
`CMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE`:

```
RUNPATH: [/usr/lib/x86_64-linux-gnu:/home/you/rootfs/usr/lib/...]
```

Binaries still work (the real path is first, dead entries are skipped) but it
ships your home directory inside the ISO. `build-one.sh` strips this with
`patchelf` automatically. If you install packages by hand, check it:

```bash
readelf -d <binary> | grep -i runpath
```

---

## Repacking and testing

```bash
sudo mksquashfs ./rootfs iso-build/live/filesystem.squashfs -comp xz -noappend
grub2-mkrescue -o smechos.iso iso-build -- -volid SMECHOS_LIVE
```

Boot it. **The virtio flags matter:**

```bash
qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -machine q35 \
    -cdrom smechos.iso \
    -device virtio-vga-gl -display egl-headless,gl=on -vnc :1
```

- `virtio-vga-gl` (not `virtio-vga`) is required for virgl. Without it the
  guest reports `[drm] features: -virgl` and falls back to llvmpipe, which
  currently segfaults in a loop and gives you a black screen that looks like
  a total failure but is not (see issue #2).
- `-vnc :1` then `vncviewer localhost:5901` to interact. QEMU's own
  `screendump` returns `Error: no surface` under virgl, because the scanout
  is a host GL dmabuf with no CPU-side copy — capture over VNC instead.
- Add `-serial file:boot.log -append "console=ttyS0,115200n8"` (with
  `-kernel`/`-initrd`) to capture boot output as text.

Check the result:

```bash
journalctl -b | grep -iE 'segfault|error|failed'
```

---

## Structure of `spk-compile.py`

Phases run in order and are stamped, so completed work is skipped on re-run.
Run one phase with `--phase <name>`, which bypasses the stamp check.

```bash
sudo python3 spk-compile.py smechos-plasma-live --phase mesa
sudo python3 spk-compile.py --list smechos-plasma-live
```

Component lists live near the bottom: `kf6` (Frameworks), `plasma` (Plasma),
and the phase list `SMECHOS_PLASMA_LIVE_PHASES`. **Most package-missing bugs
are fixed by adding one string to one of these lists.**

---

## Contributing

See [CONTRIBUTING.md](https://github.com/Smech-Labs/.github/blob/main/CONTRIBUTING.md).
Issues: [smechos-issues](https://github.com/Smech-Labs/smechos-issues) —
several are labelled `good first issue`.

## License

MIT — see [LICENSE](LICENSE).
