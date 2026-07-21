#!/usr/bin/env python3
"""
spk-compile -- SmechOS/SmechVisor sovereign build orchestrator (Project SmechDeployV2)

Standalone. No pip, no venv, no external deps. Runs on any Linux host with Python 3.
All build phases are implemented inline -- no external scripts required.
SmechDeploy (the old script collection) is retired; this file IS the build system.

Usage:
    python3 spk-compile.py smechos                         # full SmechOS build (musl/OpenRC)
    python3 spk-compile.py smechvisor                      # full SmechVisor build
    python3 spk-compile.py smechos-plasma-live             # full SmechOS live build (glibc/systemd)
    python3 spk-compile.py smechos  --phase kde            # single phase
    python3 spk-compile.py smechvisor --phase kernel
    python3 spk-compile.py smechos  --iso install          # build install ISO
    python3 spk-compile.py smechvisor --iso install
    python3 spk-compile.py smechvisor --iso shim           # build deploy shim ISO
    python3 spk-compile.py smechos-plasma-live --iso live  # build KDE Plasma live ISO
    python3 spk-compile.py --list smechos                  # list phases
    python3 spk-compile.py --version
"""

# Before running, mount a disk image at /mnt/smechos_build_root:
#
#   dd if=/dev/zero of=smechos_build.img bs=1G count=80
#   mkfs.ext4 smechos_build.img
#   sudo mount -o loop smechos_build.img /mnt/smechos_build_root
#
# The image needs ~60-80 GB free. This keeps the build root off your host filesystem.

import argparse
import glob
import os
import sys
import subprocess
import shutil
import urllib.request
import time
import textwrap

# ── Version & constants ───────────────────────────────────────────────────────

VERSION = "2.2.39"
DEFAULT_TARGET = "/mnt/smechos_build_root"
BUILD_TMP  = "/tmp/smechos_build"
STAMP_DIR  = "/mnt/spk-compile-sources/.stamps"  # persistent across reboots

# Source versions
LINUX_VER      = "6.12.16"
GRUB_VER       = "2.12"
MUSL_VER       = "1.2.5"
QT6_VER        = "6.10.3"
PLASMA_VER     = "6.7.2"
KF6_VER        = "6.27.0"
MESA_VER       = "24.3.4"
OPENRC_VER     = "0.54"
APPSTREAM_VER     = "1.0.4"
PACKAGEKIT_VER    = "1.3.0"
PACKAGEKITQT_VER  = "1.1.4"
SYSTEMD_VER    = "256.7"
CALAMARES_VER  = "3.3.10"
BUSYBOX_VER    = "1.36.1"
WAYLAND_PROTO_VER = "1.48"
WAYLAND_VER       = "1.24.0"
LIBINPUT_VER      = "1.28.0"
LIBEIS_VER        = "1.4.0"

# Download URLs (KDE URLs are resolved dynamically at build time — see _resolve_kde_versions)
LINUX_URL    = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{LINUX_VER}.tar.xz"
GRUB_URL     = f"https://ftp.gnu.org/gnu/grub/grub-{GRUB_VER}.tar.xz"
MUSL_URL     = f"https://musl.libc.org/releases/musl-{MUSL_VER}.tar.gz"
QT6_MINOR    = ".".join(QT6_VER.split(".")[:2])
QT6_BASE_URL = f"https://download.qt.io/official_releases/qt/{QT6_MINOR}/{QT6_VER}/submodules"
MESA_URL     = f"https://mesa.freedesktop.org/archive/mesa-{MESA_VER}.tar.xz"
WAYLAND_PROTO_URL = f"https://gitlab.freedesktop.org/wayland/wayland-protocols/-/archive/{WAYLAND_PROTO_VER}/wayland-protocols-{WAYLAND_PROTO_VER}.tar.gz"
WAYLAND_URL       = f"https://gitlab.freedesktop.org/wayland/wayland/-/archive/{WAYLAND_VER}/wayland-{WAYLAND_VER}.tar.gz"
LIBINPUT_URL      = f"https://gitlab.freedesktop.org/libinput/libinput/-/archive/{LIBINPUT_VER}/libinput-{LIBINPUT_VER}.tar.gz"
LIBEIS_URL        = f"https://gitlab.freedesktop.org/libeis/libeis/-/releases/{LIBEIS_VER}/downloads/libeis-{LIBEIS_VER}.tar.xz"
OPENRC_URL   = f"https://github.com/OpenRC/openrc/archive/refs/tags/{OPENRC_VER}.tar.gz"
# Plasma + KF6 URLs are set by _resolve_kde_versions() before each build
PLASMA_URL   = f"https://download.kde.org/stable/plasma/{PLASMA_VER}"
KF6_URL      = f"https://download.kde.org/stable/frameworks/6.27"

# ANSI
R       = "\x1b[0m"
BOLD    = "\x1b[1m"
GREEN   = "\x1b[32m"
CYAN    = "\x1b[36m"
YELLOW  = "\x1b[33m"
RED     = "\x1b[31m"
MAGENTA = "\x1b[35m"

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg, color=CYAN):
    print(f"{color}{BOLD}[spk-compile]{R} {msg}", flush=True)

def log_phase(name, desc):
    print(f"\n{MAGENTA}{BOLD}{'='*64}{R}", flush=True)
    print(f"{MAGENTA}{BOLD}  PHASE: {name}  --  {desc}{R}", flush=True)
    print(f"{MAGENTA}{BOLD}{'='*64}{R}", flush=True)

def err(msg):
    print(f"{RED}{BOLD}[ERROR]{R} {msg}", file=sys.stderr, flush=True)
    sys.exit(1)

def _resolve_kde_versions():
    """Query download.kde.org and return (plasma_ver, kf6_minor, kf6_ver).
    Always resolves to the highest published stable release so builds never
    pin stale EoL versions."""
    import re
    log("Resolving latest stable KDE Plasma + Frameworks from download.kde.org...")

    def _fetch(url):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read().decode()
        except Exception as e:
            err(f"Could not reach {url}: {e}")

    # Plasma: directory listing gives x.y.z/ entries
    plasma_vers = re.findall(r'href="([0-9]+\.[0-9]+\.[0-9]+)/"',
                             _fetch("https://download.kde.org/stable/plasma/"))
    if not plasma_vers:
        err("Could not detect latest Plasma version from download.kde.org/stable/plasma/")
    plasma_ver = sorted(plasma_vers, key=lambda v: [int(x) for x in v.split(".")])[-1]

    # KF6 minor: directory listing gives x.y/ entries
    kf6_minors = re.findall(r'href="([0-9]+\.[0-9]+)/"',
                            _fetch("https://download.kde.org/stable/frameworks/"))
    if not kf6_minors:
        err("Could not detect latest KF6 version from download.kde.org/stable/frameworks/")
    kf6_minor = sorted(kf6_minors, key=lambda v: [int(x) for x in v.split(".")])[-1]

    # KF6 full version: parse from a known filename inside the minor directory
    kf6_listing = _fetch(f"https://download.kde.org/stable/frameworks/{kf6_minor}/")
    full = re.findall(rf'extra-cmake-modules-([0-9]+\.[0-9]+\.[0-9]+)\.tar', kf6_listing)
    kf6_ver = full[0] if full else f"{kf6_minor}.0"

    log(f"KDE Plasma {plasma_ver}  |  KDE Frameworks {kf6_ver}", color=GREEN)
    return plasma_ver, kf6_minor, kf6_ver

def nproc():
    return str(os.cpu_count() or 4)

def ensure(path):
    os.makedirs(path, exist_ok=True)

def run(cmd, cwd=None, env=None, sudo=False, check=True):
    if sudo and os.geteuid() != 0:
        cmd = ["sudo"] + list(cmd)
    log(f"$ {' '.join(str(c) for c in cmd)}", color=R)
    result = subprocess.run(list(cmd), cwd=cwd, env=env)
    if check and result.returncode != 0:
        err(f"Command failed (exit {result.returncode}): {' '.join(str(c) for c in cmd)}")
    return result

def download(url, dest):
    if os.path.exists(dest):
        log(f"Cached: {os.path.basename(dest)}")
        return
    ensure(os.path.dirname(dest))
    log(f"Downloading {os.path.basename(dest)}...")
    urllib.request.urlretrieve(url, dest + ".part")
    os.rename(dest + ".part", dest)
    log(f"Saved: {dest}", color=GREEN)

def extract(tarball, dest, strip=1):
    ensure(dest)
    log(f"Extracting {os.path.basename(tarball)} -> {dest}")
    run(["tar", "--strip-components", str(strip), "-xf", tarball, "-C", dest])

def sources(target):
    """Source cache directory adjacent to the build root."""
    d = os.path.join(os.path.dirname(target.rstrip("/")), "spk-compile-sources")
    ensure(d)
    return d

def _stamp_path(profile, name):
    return os.path.join(STAMP_DIR, f"{profile}-{name}.done")

def _phase_done(profile, name):
    return os.path.exists(_stamp_path(profile, name))

def _mark_done(profile, name):
    ensure(STAMP_DIR)
    with open(_stamp_path(profile, name), "w") as f:
        import datetime
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())

def build_env(target):
    e = dict(os.environ)
    e["SMECH_TARGET"] = target
    e.pop("TARGET", None)
    prefix = f"{target}/usr"
    e["PATH"] = f"{prefix}/bin:{e.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    e["PKG_CONFIG_PATH"] = (
        f"{prefix}/lib/x86_64-linux-musl/pkgconfig:"
        f"{prefix}/lib/pkgconfig:"
        f"{prefix}/share/pkgconfig:"
        "/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig:/usr/lib/pkgconfig"
    )
    e["CFLAGS"]          = f"-I{prefix}/include"
    e["CXXFLAGS"]        = f"-I{prefix}/include"
    e["LDFLAGS"]         = f"-L{prefix}/lib/x86_64-linux-musl -L{prefix}/lib"
    e["LD_LIBRARY_PATH"] = f"{prefix}/lib:{prefix}/lib/x86_64-linux-musl"
    e["CC"]  = "musl-gcc"
    e["CXX"] = "musl-g++"
    e["FORCE_UNSAFE_CONFIGURE"] = "1"
    return e

def build_env_glibc(target):
    """Like build_env() but uses system glibc/gcc instead of musl-gcc."""
    e = dict(os.environ)
    e["SMECH_TARGET"] = target
    e.pop("TARGET", None)
    # Use GCC 14 — required for std::ranges::to (C++23) in libkscreen and other Plasma 6 packages
    e["CC"]  = "gcc-14"
    e["CXX"] = "g++-14"
    prefix = f"{target}/usr"
    e["PATH"] = f"{prefix}/bin:{e.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    e["PKG_CONFIG_PATH"] = (
        f"{prefix}/lib/x86_64-linux-gnu/pkgconfig:{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig:"
        "/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig:/usr/lib/pkgconfig"
    )
    e["CFLAGS"]   = f"-I{prefix}/include"
    e["CXXFLAGS"] = f"-I{prefix}/include"
    e["LDFLAGS"]  = f"-L{prefix}/lib/x86_64-linux-gnu -L{prefix}/lib"
    e["LD_LIBRARY_PATH"] = f"{prefix}/lib/x86_64-linux-gnu:{prefix}/lib"
    e["FORCE_UNSAFE_CONFIGURE"] = "1"
    return e

# Set to True by cmd_build when the active profile uses glibc instead of musl.
_USE_GLIBC = False

def active_env(target):
    """Return the right build environment for the currently running profile."""
    return build_env_glibc(target) if _USE_GLIBC else build_env(target)

def _extract_deb(deb_path, dest):
    """Extract a .deb file's data.tar into dest."""
    work = deb_path + ".extract"
    shutil.rmtree(work, ignore_errors=True)
    ensure(work)
    run(["ar", "x", os.path.abspath(deb_path)], cwd=work)
    for ext in ("data.tar.xz", "data.tar.zst", "data.tar.gz", "data.tar"):
        data_tar = os.path.join(work, ext)
        if os.path.exists(data_tar):
            run(["tar", "-xf", data_tar, "-C", dest])
            break
    shutil.rmtree(work, ignore_errors=True)

def cmake_install(src_dir, prefix, extra_args=None, env=None, build_dir=None):
    bd = build_dir or os.path.join(src_dir, "build")
    ensure(bd)
    # Always resolve cmake/ninja from the host system PATH, never from the
    # build root — a partially-installed cmake there can't find its own modules.
    _host = "/usr/local/bin:/usr/bin:/bin"
    cmake_bin = shutil.which("cmake", path=_host) or "cmake"
    ninja_bin = shutil.which("ninja", path=_host) or "ninja"
    # Ensure build-time tools (generated executables like katehighlightingindexer,
    # kcmdesktopfilegenerator, etc.) find our Qt/KF6 libs at build time.
    # These tools have no RUNPATH so LD_LIBRARY_PATH must be set in the env.
    build_env = dict(env) if env else dict(os.environ)
    prefix_lib = f"{prefix}/lib"
    prefix_arch_lib = f"{prefix}/lib/x86_64-linux-gnu"
    # LD_LIBRARY_PATH: runtime loader path for build-time tools (no RUNPATH)
    existing_ldp = build_env.get("LD_LIBRARY_PATH", "")
    if prefix_lib not in existing_ldp:
        build_env["LD_LIBRARY_PATH"] = f"{prefix_lib}:{existing_ldp}" if existing_ldp else prefix_lib
    # LIBRARY_PATH: linker search path so arch-specific libs (libsystemd etc.) are found
    existing_lp = build_env.get("LIBRARY_PATH", "")
    if prefix_arch_lib not in existing_lp:
        build_env["LIBRARY_PATH"] = f"{prefix_arch_lib}:{existing_lp}" if existing_lp else prefix_arch_lib
    run([cmake_bin, src_dir,
         "-G", "Ninja",
         f"-DCMAKE_INSTALL_PREFIX={prefix}",
         "-DCMAKE_BUILD_TYPE=Release",
         ] + (extra_args or []), cwd=bd, env=build_env)
    run([ninja_bin, "-j", nproc(), "-k", "0"], cwd=bd, env=build_env, check=False)
    run([cmake_bin, "--install", bd], env=build_env, sudo=(os.geteuid() != 0))

def meson_install(src_dir, prefix, extra_args=None, env=None, build_dir=None):
    bd = build_dir or os.path.join(src_dir, "build")
    if os.path.exists(bd):
        shutil.rmtree(bd)
    run(["meson", "setup", bd, src_dir,
         f"--prefix={prefix}", "--buildtype=release",
         ] + (extra_args or []), env=env)
    run(["ninja", "-C", bd, "-j", nproc()], env=env)
    run(["ninja", "-C", bd, "install"], env=env, sudo=(os.geteuid() != 0))

# ── Phase implementations ─────────────────────────────────────────────────────

def phase_bootstrap_musl(target):
    log_phase("musl", "Bootstrap musl libc + musl-gcc wrapper")
    src = sources(target)
    tarball = os.path.join(src, f"musl-{MUSL_VER}.tar.gz")
    download(MUSL_URL, tarball)
    bd = os.path.join(BUILD_TMP, "musl")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    prefix = f"{target}/usr"
    env = dict(os.environ)
    env.pop("CC", None)
    env.pop("CXX", None)
    run(["./configure",
         f"--prefix={prefix}",
         "--syslibdir=/lib",
         "--enable-optimize=speed"],
        cwd=bd, env=env)
    run(["make", "-j", nproc()], cwd=bd, env=env)
    run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))

    # musl-gcc wrapper script
    specs   = os.path.join(prefix, "lib", "musl-gcc.specs")
    wrapper = os.path.join(prefix, "bin", "musl-gcc")
    ensure(os.path.dirname(wrapper))
    with open(wrapper, "w") as f:
        f.write(f"#!/bin/sh\nexec gcc \"$@\" -specs {specs}\n")
    os.chmod(wrapper, 0o755)
    log("musl installed.", color=GREEN)

def phase_bootstrap_userland(target):
    log_phase("userland", "Bootstrap GNU userland against musl")
    src  = sources(target)
    env  = build_env(target)
    pfix = f"{target}/usr"

    pkgs = [
        ("bash",      "5.2.37",
         "https://ftp.gnu.org/gnu/bash/bash-5.2.37.tar.gz",
         ["--without-bash-malloc", "--disable-nls"]),
        ("coreutils", "9.5",
         "https://ftp.gnu.org/gnu/coreutils/coreutils-9.5.tar.xz",
         ["--disable-nls"]),
        ("grep",      "3.11",
         "https://ftp.gnu.org/gnu/grep/grep-3.11.tar.xz", []),
        ("sed",       "4.9",
         "https://ftp.gnu.org/gnu/sed/sed-4.9.tar.xz", []),
        ("gawk",      "5.3.1",
         "https://ftp.gnu.org/gnu/gawk/gawk-5.3.1.tar.xz", []),
        ("findutils", "4.10.0",
         "https://ftp.gnu.org/gnu/findutils/findutils-4.10.0.tar.xz", []),
        ("tar",       "1.35",
         "https://ftp.gnu.org/gnu/tar/tar-1.35.tar.xz", []),
        ("gzip",      "1.13",
         "https://ftp.gnu.org/gnu/gzip/gzip-1.13.tar.xz", []),
        ("xz",        "5.6.3",
         "https://github.com/tukaani-project/xz/releases/download/v5.6.3/xz-5.6.3.tar.xz",
         ["--disable-xzdec", "--disable-lzmadec"]),
    ]
    for name, ver, url, flags in pkgs:
        tarball = os.path.join(src, os.path.basename(url))
        download(url, tarball)
        bd = os.path.join(BUILD_TMP, name)
        shutil.rmtree(bd, ignore_errors=True)
        extract(tarball, bd)
        run(["./configure", f"--prefix={pfix}",
             "--host=x86_64-linux-musl"] + flags,
            cwd=bd, env=env)
        run(["make", "-j", nproc()], cwd=bd, env=env)
        run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))
        log(f"{name} {ver} installed.", color=GREEN)

def phase_write_etc(target):
    log_phase("etc", "Write /etc skeleton")
    etc = os.path.join(target, "etc")
    ensure(etc)

    files = {
        "hostname":    "smechos\n",
        "hosts":       "127.0.0.1  localhost\n127.0.1.1  smechos\n::1  localhost\n",
        "resolv.conf": "nameserver 1.1.1.1\nnameserver 8.8.8.8\n",
        "fstab":       (
            "proc     /proc     proc    defaults  0 0\n"
            "sysfs    /sys      sysfs   defaults  0 0\n"
            "devtmpfs /dev      devtmpfs defaults 0 0\n"
        ),
        "shells":      "/bin/sh\n/bin/bash\n",
        "passwd":      (
            "root:x:0:0:root:/root:/bin/bash\n"
            "smech:x:1000:1000:SmechOS User:/home/smech:/bin/bash\n"
            "sddm:x:999:999:SDDM:/var/lib/sddm:/sbin/nologin\n"
        ),
        "group":       (
            "root:x:0:\nwheel:x:10:smech\nvideo:x:14:smech\n"
            "audio:x:29:smech\nsmech:x:1000:\nsddm:x:999:\n"
        ),
        "shadow":      "root:!:19900:0:99999:7:::\nsmech:!:19900:0:99999:7:::\n",
        "os-release":  (
            'NAME="SmechOS"\n'
            'PRETTY_NAME="SmechOS 1.0 (Sovereign)"\n'
            'ID=smechos\nVERSION_ID="1.0"\n'
            'HOME_URL="https://os.smech.xyz"\n'
            'ANSI_COLOR="1;31"\n'
        ),
        "locale.conf":  "LANG=en_US.UTF-8\n",
        "vconsole.conf":"KEYMAP=us\n",
    }
    for name, content in files.items():
        with open(os.path.join(etc, name), "w") as f:
            f.write(content)

    for d in ["init.d", "runlevels/sysinit", "runlevels/boot",
              "runlevels/default", "runlevels/shutdown", "conf.d"]:
        ensure(os.path.join(etc, d))

    for d in ["proc", "sys", "dev", "run", "tmp", "home/smech", "root",
              "boot/efi", "usr/bin", "usr/sbin", "usr/lib", "usr/share",
              "var/log", "var/run", "lib/modules", "lib/firmware"]:
        ensure(os.path.join(target, d))
    log("/etc skeleton written.", color=GREEN)

def phase_openrc(target):
    log_phase("openrc", f"Deploy OpenRC {OPENRC_VER}")
    src     = sources(target)
    tarball = os.path.join(src, f"openrc-{OPENRC_VER}.tar.gz")
    download(OPENRC_URL, tarball)
    bd = os.path.join(BUILD_TMP, "openrc")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    env = dict(os.environ)
    env.pop("CC", None)
    run(["make", f"DESTDIR={target}", "PREFIX=/usr",
         "MKNET=no", "-j", nproc()], cwd=bd, env=env)
    run(["make", f"DESTDIR={target}", "PREFIX=/usr", "install"],
        cwd=bd, env=env, sudo=(os.geteuid() != 0))

    runlevel_services = {
        "sysinit": ["devfs", "dmesg", "udev"],
        "boot":    ["modules", "localmount", "hostname", "networking"],
        "default": ["dbus", "sddm"],
        "shutdown":["mount-ro", "killprocs"],
    }
    etc = os.path.join(target, "etc")
    for level, svcs in runlevel_services.items():
        rl = os.path.join(etc, "runlevels", level)
        ensure(rl)
        for svc in svcs:
            dst = os.path.join(rl, svc)
            if not os.path.lexists(dst):
                try:
                    os.symlink(f"/etc/init.d/{svc}", dst)
                except FileExistsError:
                    pass
    log("OpenRC deployed.", color=GREEN)

def phase_inittab(target):
    log_phase("inittab", "Write /etc/inittab")
    with open(os.path.join(target, "etc", "inittab"), "w") as f:
        f.write(textwrap.dedent("""\
            ::sysinit:/sbin/openrc sysinit
            ::wait:/sbin/openrc boot
            ::wait:/sbin/openrc default
            tty1::respawn:/sbin/agetty --autologin smech tty1 linux
            tty2::respawn:/sbin/agetty tty2 linux
            ::ctrlaltdel:/sbin/reboot
            ::shutdown:/sbin/openrc shutdown
        """))
    log("inittab written.", color=GREEN)

def phase_kernel(target):
    log_phase("kernel", f"Compile Linux {LINUX_VER}")
    src     = sources(target)
    tarball = os.path.join(src, f"linux-{LINUX_VER}.tar.xz")
    download(LINUX_URL, tarball)
    bd = os.path.join(BUILD_TMP, f"linux-{LINUX_VER}")
    if not os.path.exists(bd):
        extract(tarball, bd)
    env = dict(os.environ)
    env.pop("CC", None)
    env.pop("CXX", None)

    run(["make", "defconfig"], cwd=bd, env=env)

    # Append sovereign feature set
    extras = textwrap.dedent("""\
        CONFIG_KVM=m
        CONFIG_KVM_INTEL=m
        CONFIG_KVM_AMD=m
        CONFIG_VFIO=m
        CONFIG_VFIO_PCI=m
        CONFIG_VHOST=m
        CONFIG_VHOST_NET=m
        CONFIG_INTEL_IOMMU=y
        CONFIG_AMD_IOMMU=y
        CONFIG_IOMMU_DEFAULT_PASSTHROUGH=y
        CONFIG_EFI=y
        CONFIG_EFI_STUB=y
        CONFIG_VIRTIO=m
        CONFIG_VIRTIO_PCI=m
        CONFIG_VIRTIO_NET=m
        CONFIG_VIRTIO_BLK=m
        CONFIG_DRM=m
        CONFIG_DRM_AMDGPU=m
        CONFIG_DRM_NOUVEAU=m
    """)
    with open(os.path.join(bd, ".config"), "a") as f:
        f.write(extras)
    run(["make", "olddefconfig"], cwd=bd, env=env)
    run(["make", "-j", nproc(), "bzImage", "modules"], cwd=bd, env=env)

    boot = os.path.join(target, "boot")
    ensure(boot)
    shutil.copy2(os.path.join(bd, "arch/x86/boot/bzImage"),
                 os.path.join(boot, "vmlinuz"))
    run(["make", f"INSTALL_MOD_PATH={target}", "modules_install"],
        cwd=bd, env=env, sudo=(os.geteuid() != 0))
    log(f"Linux {LINUX_VER} installed.", color=GREEN)

def phase_grub(target):
    log_phase("grub", f"Compile GRUB {GRUB_VER} EFI + BIOS")
    src     = sources(target)
    tarball = os.path.join(src, f"grub-{GRUB_VER}.tar.xz")
    download(GRUB_URL, tarball)
    prefix = f"{target}/usr"
    env = dict(os.environ)
    env.pop("CC", None)
    env.pop("CXX", None)

    for platform, tgt_arch in [("efi", "x86_64"), ("pc", "i386")]:
        bd = os.path.join(BUILD_TMP, f"grub-{platform}")
        shutil.rmtree(bd, ignore_errors=True)
        extract(tarball, bd)
        # GRUB 2.12 bug: extra_deps.lst is a required prerequisite but is
        # neither generated nor shipped in the tarball — create it empty.
        open(os.path.join(bd, "grub-core", "extra_deps.lst"), "w").close()
        run(["./configure",
             f"--prefix={prefix}",
             f"--with-platform={platform}",
             f"--target={tgt_arch}",
             "--disable-werror",
             "--disable-nls"],
            cwd=bd, env=env)
        run(["make", "-j", nproc()], cwd=bd, env=env)
        run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))
    log(f"GRUB {GRUB_VER} installed.", color=GREEN)

def phase_qt_deps(target):
    log_phase("qt-deps", f"Compile Qt6 {QT6_VER} modules")
    src = sources(target)
    env = active_env(target)
    prefix = f"{target}/usr"

    # Wipe old Qt installation so build tools don't load stale sonames
    for pattern in [f"{prefix}/lib/libQt6*.so*",
                    f"{prefix}/lib/libQt6*.a",
                    f"{prefix}/lib/libQt6*.prl"]:
        for f in glob.glob(pattern):
            try: os.remove(f)
            except OSError: pass
    for d in glob.glob(f"{prefix}/lib/cmake/Qt6*"):
        shutil.rmtree(d, ignore_errors=True)
    for d in glob.glob(f"{prefix}/include/Qt*"):
        shutil.rmtree(d, ignore_errors=True)

    modules = [
        ("qtbase",        ["-DFEATURE_testlib=OFF"]),
        ("qtshadertools", []),
        ("qtdeclarative", []),
        ("qtsvg",         []),
        ("qttools",       ["-DFEATURE_assistant=OFF", "-DFEATURE_designer=OFF",
                           "-DFEATURE_pixeltool=OFF", "-DFEATURE_qdoc=OFF"]),
        ("qtwayland",     []),
        ("qtmultimedia",  []),
        ("qt5compat",        []),
        ("qtspeech",         ["-DQT_FEATURE_speechd=OFF", "-DQT_FEATURE_flite=OFF"]),
        ("qtpositioning",    ["-DFEATURE_gypsy=OFF", "-DFEATURE_geoclue2=OFF"]),
    ]
    for name, extra in modules:
        fname   = f"{name}-everywhere-src-{QT6_VER}.tar.xz"
        url     = f"{QT6_BASE_URL}/{fname}"
        tarball = os.path.join(src, fname)
        download(url, tarball)
        bd = os.path.join(BUILD_TMP, f"qt6-{name}")
        shutil.rmtree(bd, ignore_errors=True)
        shutil.rmtree(os.path.join(BUILD_TMP, f"qt6-{name}-build"), ignore_errors=True)
        extract(tarball, bd)
        cmake_install(bd, prefix,
            extra_args=[f"-DCMAKE_PREFIX_PATH={prefix}",
                        "-DCMAKE_INSTALL_LIBDIR=lib",
                        "-DBUILD_TESTING=OFF",
                        "-DQT_BUILD_TESTS=OFF",
                        "-DQT_BUILD_EXAMPLES=OFF"] + extra,
            env=env,
            build_dir=os.path.join(BUILD_TMP, f"qt6-{name}-build"))
        log(f"Qt6/{name} done.", color=GREEN)

    # KF6 tools embed RUNPATH pointing to the arch-specific lib dir (e.g.
    # lib/x86_64-linux-gnu) but Qt is installed to lib/ directly.  Symlink
    # all Qt shared libs into the arch dir so dynamic linker finds them.
    arch_libdir = f"{prefix}/lib/x86_64-linux-gnu"
    ensure(arch_libdir)
    for sopath in glob.glob(f"{prefix}/lib/libQt6*.so*"):
        dest = os.path.join(arch_libdir, os.path.basename(sopath))
        if not os.path.lexists(dest):
            os.symlink(sopath, dest)
    log("Qt6 arch-dir symlinks created.", color=GREEN)

CMAKE_BOOTSTRAP_VER = "3.31.6"
CMAKE_BOOTSTRAP_URL = f"https://github.com/Kitware/CMake/releases/download/v{CMAKE_BOOTSTRAP_VER}/cmake-{CMAKE_BOOTSTRAP_VER}-linux-x86_64.tar.gz"

def phase_cmake_bootstrap(target):
    log_phase("cmake-bootstrap", f"Bootstrap CMake {CMAKE_BOOTSTRAP_VER} (KDE requires 3.29+)")
    src     = sources(target)
    tarball = os.path.join(src, f"cmake-{CMAKE_BOOTSTRAP_VER}-linux-x86_64.tar.gz")
    download(CMAKE_BOOTSTRAP_URL, tarball)
    extract_dir = os.path.join(BUILD_TMP, "cmake-bootstrap")
    shutil.rmtree(extract_dir, ignore_errors=True)
    ensure(extract_dir)
    run(["tar", "--strip-components", "1", "-xf", tarball, "-C", extract_dir])
    # Install cmake/cpack/ctest to /usr/local/bin so cmake_install() picks them up first
    for binary in ("cmake", "cpack", "ctest", "cmake-gui"):
        src_bin = os.path.join(extract_dir, "bin", binary)
        dst_bin = os.path.join("/usr/local/bin", binary)
        if os.path.exists(src_bin):
            run(["cp", "-f", src_bin, dst_bin], sudo=(os.geteuid() != 0))
    # Modules directory must be alongside the binary
    modules_dst = f"/usr/local/share/cmake-{CMAKE_BOOTSTRAP_VER[:4]}"
    run(["cp", "-r", os.path.join(extract_dir, "share", f"cmake-{CMAKE_BOOTSTRAP_VER[:4]}"),
         modules_dst], sudo=(os.geteuid() != 0))
    result = subprocess.run(["/usr/local/bin/cmake", "--version"], capture_output=True, text=True)
    log(f"cmake bootstrap: {result.stdout.strip().splitlines()[0]}", color=GREEN)

def phase_mesa(target):
    log_phase("mesa", f"Compile Mesa {MESA_VER}")
    src     = sources(target)
    tarball = os.path.join(src, f"mesa-{MESA_VER}.tar.xz")
    download(MESA_URL, tarball)
    bd = os.path.join(BUILD_TMP, "mesa")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    meson_install(bd, f"{target}/usr",
        extra_args=[
            "-Dgallium-drivers=radeonsi,nouveau,iris,crocus,swrast",
            "-Dvulkan-drivers=amd,intel",
            "-Dglx=dri", "-Degl=enabled", "-Dgbm=enabled",
            "-Dopengl=true", "-Dgles1=enabled", "-Dgles2=enabled",
            "-Dshared-glapi=enabled",
            "-Dplatforms=x11,wayland",
            "-Dglvnd=disabled", "-Db_lto=false",
        ],
        env=active_env(target),
        build_dir=os.path.join(BUILD_TMP, "mesa-build"))
    log(f"Mesa {MESA_VER} installed.", color=GREEN)

def _patch_kwin_vulkan(bd):
    """Patch kwin Vulkan files for GCC 14 C++23: ResultValue<T> structured bindings not supported.
    Also patches std::erase_if + std::ranges::any_of which fails due to std::ref wrapping in GCC 14.
    VULKAN_HPP_NO_EXCEPTIONS without VULKAN_HPP_EXPECTED → ResultValue<T> with .result/.value fields.
    """
    patches = {
        "src/core/renderdevice.cpp": [
            # erase_if + ranges::any_of: GCC 14 std::ref wrapping rejects implicit conversions
            (
                'std::erase_if(missingExtensions, [&extensionProps](std::string_view required) {\n            return std::ranges::any_of(extensionProps, [required](const auto &ext) {\n                return required == ext.extensionName;\n            });\n        });',
                'missingExtensions.erase(\n            std::remove_if(missingExtensions.begin(), missingExtensions.end(),\n                [&extensionProps](const char *required) {\n                    const std::string_view sv{required};\n                    for (const auto &ext : extensionProps) {\n                        if (sv == std::string_view{ext.extensionName.data()}) return true;\n                    }\n                    return false;\n                }),\n            missingExtensions.end());',
            ),
            # createInstance: structured binding → ResultValue .result/.value
            (
                'auto [result, instance] = context.createInstance(instanceInfo);\n    if (result != vk::Result::eSuccess && !validationLayers.empty()) {\n        // try again without the validation layer\n        validationLayers.clear();\n        instanceInfo.setPEnabledLayerNames(validationLayers);\n        auto [result, instance] = context.createInstance(instanceInfo);\n        if (result == vk::Result::eSuccess) {\n            qCWarning(KWIN_CORE, "Vulkan validation layer is not installed");\n            return std::move(instance);\n        }\n    }\n    return std::move(instance);',
                'auto instanceRV = context.createInstance(instanceInfo);\n    if (instanceRV.result != vk::Result::eSuccess && !validationLayers.empty()) {\n        validationLayers.clear();\n        instanceInfo.setPEnabledLayerNames(validationLayers);\n        auto instanceRV2 = context.createInstance(instanceInfo);\n        if (instanceRV2.result == vk::Result::eSuccess) {\n            qCWarning(KWIN_CORE, "Vulkan validation layer is not installed");\n            return std::move(instanceRV2.value);\n        }\n    }\n    if (instanceRV.result != vk::Result::eSuccess) return vk::raii::Instance{VK_NULL_HANDLE};\n    return std::move(instanceRV.value);',
            ),
            # enumeratePhysicalDevices structured binding
            (
                'const auto [enumerateResult, physicalDevices] = instance.enumeratePhysicalDevices();\n    if (enumerateResult != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "querying vulkan devices failed:" << vk::to_string(enumerateResult);\n        return nullptr;\n    }',
                'auto enumerateRV = instance.enumeratePhysicalDevices();\n    if (enumerateRV.result != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "querying vulkan devices failed:" << vk::to_string(enumerateRV.result);\n        return nullptr;\n    }\n    auto physicalDevices = std::move(enumerateRV.value);',
            ),
            # enumerateDeviceExtensionProperties structured binding
            (
                'const auto [extensionPropResult, extensionProps] = physicalDevice.enumerateDeviceExtensionProperties();\n        if (extensionPropResult != vk::Result::eSuccess) {\n            continue;\n        }',
                'auto extensionPropsRV = physicalDevice.enumerateDeviceExtensionProperties();\n        if (extensionPropsRV.result != vk::Result::eSuccess) {\n            continue;\n        }\n        const auto &extensionProps = extensionPropsRV.value;',
            ),
            # createDevice structured binding
            (
                'auto [result, logicalDevice] = physicalDevice.createDevice(deviceInfo);\n        if (result != vk::Result::eSuccess) {\n            qCWarning(KWIN_VULKAN, "vkCreateDevice for %s failed: %s", deviceName, vk::to_string(vk::Result(result)).c_str());\n            continue;\n        }\n\n        auto ret = std::make_unique<VulkanDevice>(\n            physicalDevice,\n            std::move(logicalDevice),',
                'auto createDeviceRV = physicalDevice.createDevice(deviceInfo);\n        if (createDeviceRV.result != vk::Result::eSuccess) {\n            qCWarning(KWIN_VULKAN, "vkCreateDevice for %s failed: %s", deviceName, vk::to_string(createDeviceRV.result).c_str());\n            continue;\n        }\n\n        auto ret = std::make_unique<VulkanDevice>(\n            physicalDevice,\n            std::move(createDeviceRV.value),',
            ),
        ],
        "src/vulkan/vulkan_device.cpp": [
            # createCommandPool
            (
                'auto [result, cmdPool] = m_logical.createCommandPool(vk::CommandPoolCreateInfo{\n        vk::CommandPoolCreateFlagBits::eResetCommandBuffer,\n        m_queueFamilyIndex,\n    });\n    if (result != vk::Result::eSuccess) {\n        qCCritical(KWIN_VULKAN) << "creating a command pool failed:" << vk::to_string(result);\n        return;\n    }\n    m_commandPool = std::move(cmdPool);',
                'auto cmdPoolRV = m_logical.createCommandPool(vk::CommandPoolCreateInfo{\n        vk::CommandPoolCreateFlagBits::eResetCommandBuffer,\n        m_queueFamilyIndex,\n    });\n    if (cmdPoolRV.result != vk::Result::eSuccess) {\n        qCCritical(KWIN_VULKAN) << "creating a command pool failed:" << vk::to_string(cmdPoolRV.result);\n        return;\n    }\n    m_commandPool = std::move(cmdPoolRV.value);',
            ),
            # createImage (importDmabuf)
            (
                'auto [imageResult, image] = m_logical.createImage(imageInfo);\n    if (imageResult != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "creating vulkan image failed!" << vk::to_string(imageResult);\n        return nullptr;\n    }',
                'auto imageRV = m_logical.createImage(imageInfo);\n    if (imageRV.result != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "creating vulkan image failed!" << vk::to_string(imageRV.result);\n        return nullptr;\n    }\n    auto image = std::move(imageRV.value);',
            ),
            # getMemoryFdPropertiesKHR
            (
                'const auto [memoryFdResult, memoryFdProperties] = m_logical.getMemoryFdPropertiesKHR(vk::ExternalMemoryHandleTypeFlagBits::eDmaBufEXT, duplicatedFds[i].get());\n        if (memoryFdResult != vk::Result::eSuccess) {\n            qCWarning(KWIN_VULKAN) << "failed to get memory fd properties!" << vk::to_string(memoryFdResult);\n            return nullptr;\n        }',
                'auto memFdRV = m_logical.getMemoryFdPropertiesKHR(vk::ExternalMemoryHandleTypeFlagBits::eDmaBufEXT, duplicatedFds[i].get());\n        if (memFdRV.result != vk::Result::eSuccess) {\n            qCWarning(KWIN_VULKAN) << "failed to get memory fd properties!" << vk::to_string(memFdRV.result);\n            return nullptr;\n        }\n        const auto &memoryFdProperties = memFdRV.value;',
            ),
            # allocateMemory (dmabuf)
            (
                'auto [allocateResult, memory] = m_logical.allocateMemory(memoryInfo);\n        if (allocateResult != vk::Result::eSuccess) {\n            qCWarning(KWIN_VULKAN, "\'Allocating\' memory for dmabuf failed: %s", vk::to_string(allocateResult).c_str());\n            return nullptr;\n        }\n\n        bindInfos[i] = vk::BindImageMemoryInfo{image, memory, 0};',
                'auto allocRV = m_logical.allocateMemory(memoryInfo);\n        if (allocRV.result != vk::Result::eSuccess) {\n            qCWarning(KWIN_VULKAN, "\'Allocating\' memory for dmabuf failed: %s", vk::to_string(allocRV.result).c_str());\n            return nullptr;\n        }\n        auto memory = std::move(allocRV.value);\n\n        bindInfos[i] = vk::BindImageMemoryInfo{image, memory, 0};',
            ),
            # allocateCommandBuffers
            (
                'auto [result, buffers] = m_logical.allocateCommandBuffers(vk::CommandBufferAllocateInfo{\n        m_commandPool,\n        vk::CommandBufferLevel::ePrimary,\n        1,\n    });\n    if (result != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "Failed to create a command buffer" << vk::to_string(result);\n        return nullptr;\n    }\n    return std::move(buffers.front());',
                'auto allocCmdRV = m_logical.allocateCommandBuffers(vk::CommandBufferAllocateInfo{\n        m_commandPool,\n        vk::CommandBufferLevel::ePrimary,\n        1,\n    });\n    if (allocCmdRV.result != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "Failed to create a command buffer" << vk::to_string(allocCmdRV.result);\n        return nullptr;\n    }\n    return std::move(allocCmdRV.value.front());',
            ),
            # createSemaphore + importSemaphoreFdKHR (result reused after binding)
            (
                'vk::SemaphoreCreateInfo semaphoreInfo{};\n    auto [result, semaphore] = m_logical.createSemaphore(semaphoreInfo);\n    if (result != vk::Result::eSuccess) {\n        return std::nullopt;\n    }\n    vk::ImportSemaphoreFdInfoKHR importInfo{\n        semaphore,\n        vk::SemaphoreImportFlagBits::eTemporary,\n        vk::ExternalSemaphoreHandleTypeFlagBits::eSyncFd,\n        syncFd.get(),\n    };\n    result = m_logical.importSemaphoreFdKHR(importInfo);\n    if (result != vk::Result::eSuccess) {\n        return std::nullopt;\n    }',
                'vk::SemaphoreCreateInfo semaphoreInfo{};\n    auto semRV = m_logical.createSemaphore(semaphoreInfo);\n    if (semRV.result != vk::Result::eSuccess) {\n        return std::nullopt;\n    }\n    auto semaphore = std::move(semRV.value);\n    vk::ImportSemaphoreFdInfoKHR importInfo{\n        semaphore,\n        vk::SemaphoreImportFlagBits::eTemporary,\n        vk::ExternalSemaphoreHandleTypeFlagBits::eSyncFd,\n        syncFd.get(),\n    };\n    vk::Result importResult = m_logical.importSemaphoreFdKHR(importInfo);\n    if (importResult != vk::Result::eSuccess) {\n        return std::nullopt;\n    }',
            ),
            # createFence
            (
                'auto [fenceResult, fence] = m_logical.createFence(vk::FenceCreateInfo{\n        vk::FenceCreateFlags{},\n        &exportInfo,\n    });\n    if (fenceResult != vk::Result::eSuccess) {\n        return std::nullopt;\n    }',
                'auto fenceRV = m_logical.createFence(vk::FenceCreateInfo{\n        vk::FenceCreateFlags{},\n        &exportInfo,\n    });\n    if (fenceRV.result != vk::Result::eSuccess) {\n        return std::nullopt;\n    }\n    auto fence = std::move(fenceRV.value);',
            ),
            # getFenceFdKHR
            (
                'const auto [fdResult, fd] = m_logical.getFenceFdKHR(vk::FenceGetFdInfoKHR{\n        fence,\n        vk::ExternalFenceHandleTypeFlagBits::eSyncFd,\n    });\n    if (fdResult != vk::Result::eSuccess) {\n        return std::nullopt;\n    }\n    FileDescriptor ret{fd};',
                'auto fdRV = m_logical.getFenceFdKHR(vk::FenceGetFdInfoKHR{\n        fence,\n        vk::ExternalFenceHandleTypeFlagBits::eSyncFd,\n    });\n    if (fdRV.result != vk::Result::eSuccess) {\n        return std::nullopt;\n    }\n    FileDescriptor ret{fdRV.value};',
            ),
            # allocateMemory image overload
            (
                '    if (const auto typeIndex = findMemoryType(requirements.memoryRequirements.memoryTypeBits, memoryProperties)) {\n        auto [result, ret] = m_logical.allocateMemory(vk::MemoryAllocateInfo{\n            requirements.memoryRequirements.size,\n            *typeIndex,\n        });\n        if (result == vk::Result::eSuccess) {\n            return std::move(ret);\n        } else {\n            qCWarning(KWIN_VULKAN) << "Allocating memory for an image failed:" << vk::to_string(result);\n            return nullptr;\n        }\n    } else {\n        qCWarning(KWIN_VULKAN) << "could not find a suitable memory index for an image";\n        return nullptr;\n    }\n}\n\nvk::raii::DeviceMemory VulkanDevice::allocateMemory(const vk::BufferCreateInfo &bufferInfo, vk::MemoryPropertyFlags memoryProperties)\n{\n    const auto requirements = m_logical.getBufferMemoryRequirements(vk::DeviceBufferMemoryRequirements{\n        &bufferInfo,\n    });\n    if (const auto typeIndex = findMemoryType(requirements.memoryRequirements.memoryTypeBits, memoryProperties)) {\n        auto [result, ret] = m_logical.allocateMemory(vk::MemoryAllocateInfo{\n            requirements.memoryRequirements.size,\n            *typeIndex,\n        });\n        if (result == vk::Result::eSuccess) {\n            return std::move(ret);\n        } else {\n            qCWarning(KWIN_VULKAN) << "Allocating memory for a buffer failed:" << vk::to_string(result);\n            return nullptr;\n        }\n    } else {\n        qCWarning(KWIN_VULKAN) << "could not find a suitable memory index for a buffer";\n        return nullptr;\n    }\n}',
                '    if (const auto typeIndex = findMemoryType(requirements.memoryRequirements.memoryTypeBits, memoryProperties)) {\n        auto allocRV = m_logical.allocateMemory(vk::MemoryAllocateInfo{\n            requirements.memoryRequirements.size,\n            *typeIndex,\n        });\n        if (allocRV.result == vk::Result::eSuccess) {\n            return std::move(allocRV.value);\n        } else {\n            qCWarning(KWIN_VULKAN) << "Allocating memory for an image failed:" << vk::to_string(allocRV.result);\n            return nullptr;\n        }\n    } else {\n        qCWarning(KWIN_VULKAN) << "could not find a suitable memory index for an image";\n        return nullptr;\n    }\n}\n\nvk::raii::DeviceMemory VulkanDevice::allocateMemory(const vk::BufferCreateInfo &bufferInfo, vk::MemoryPropertyFlags memoryProperties)\n{\n    const auto requirements = m_logical.getBufferMemoryRequirements(vk::DeviceBufferMemoryRequirements{\n        &bufferInfo,\n    });\n    if (const auto typeIndex = findMemoryType(requirements.memoryRequirements.memoryTypeBits, memoryProperties)) {\n        auto allocRV = m_logical.allocateMemory(vk::MemoryAllocateInfo{\n            requirements.memoryRequirements.size,\n            *typeIndex,\n        });\n        if (allocRV.result == vk::Result::eSuccess) {\n            return std::move(allocRV.value);\n        } else {\n            qCWarning(KWIN_VULKAN) << "Allocating memory for a buffer failed:" << vk::to_string(allocRV.result);\n            return nullptr;\n        }\n    } else {\n        qCWarning(KWIN_VULKAN) << "could not find a suitable memory index for a buffer";\n        return nullptr;\n    }\n}',
            ),
        ],
        "src/vulkan/vulkan_texture.cpp": [
            # createBuffer (download)
            (
                'auto [bufResult, stagingBuffer] = m_device->logicalDevice().createBuffer(bufferInfo);\n    if (bufResult != vk::Result::eSuccess) {\n        return {};\n    }\n    stagingBuffer.bindMemory(stagingMemory, 0);\n\n    auto commandBuffer = m_device->createCommandBuffer();\n    commandBuffer.begin(vk::CommandBufferBeginInfo{vk::CommandBufferUsageFlagBits::eOneTimeSubmit});\n    vk::BufferImageCopy2 copyRegion{',
                'auto stagingBufRV = m_device->logicalDevice().createBuffer(bufferInfo);\n    if (stagingBufRV.result != vk::Result::eSuccess) {\n        return {};\n    }\n    auto stagingBuffer = std::move(stagingBufRV.value);\n    stagingBuffer.bindMemory(stagingMemory, 0);\n\n    auto commandBuffer = m_device->createCommandBuffer();\n    commandBuffer.begin(vk::CommandBufferBeginInfo{vk::CommandBufferUsageFlagBits::eOneTimeSubmit});\n    vk::BufferImageCopy2 copyRegion{',
            ),
            # mapMemory (download)
            (
                '// use mapMemory/unmapMemory (Vulkan 1.0) instead of mapMemory2/unmapMemory2 (Vulkan 1.4)\n    // for compatibility with lavapipe and other drivers that don\'t support 1.4\n    auto [mapResult, dataPtr] = stagingMemory.mapMemory(0, bufferSize);\n    if (mapResult != vk::Result::eSuccess) {\n        return {};\n    }\n\n    std::memcpy(result.bits(), dataPtr, bufferSize);',
                '// use mapMemory/unmapMemory (Vulkan 1.0) instead of mapMemory2/unmapMemory2 (Vulkan 1.4)\n    // for compatibility with lavapipe and other drivers that don\'t support 1.4\n    auto mapRV = stagingMemory.mapMemory(0, bufferSize);\n    if (mapRV.result != vk::Result::eSuccess) {\n        return {};\n    }\n    void *dataPtr = mapRV.value;\n\n    std::memcpy(result.bits(), dataPtr, bufferSize);',
            ),
            # createBuffer + mapMemory (update)
            (
                'auto [result, stagingBuffer] = m_device->logicalDevice().createBuffer(bufferInfo);\n    if (result != vk::Result::eSuccess) {\n        return false;\n    }\n    stagingBuffer.bindMemory(stagingMemory, 0);\n    auto [mapResult, dataPtr] = stagingMemory.mapMemory(0, vk::DeviceSize(img.sizeInBytes()));\n    if (mapResult != vk::Result::eSuccess) {\n        return false;\n    }',
                'auto updateBufRV = m_device->logicalDevice().createBuffer(bufferInfo);\n    if (updateBufRV.result != vk::Result::eSuccess) {\n        return false;\n    }\n    auto stagingBuffer = std::move(updateBufRV.value);\n    stagingBuffer.bindMemory(stagingMemory, 0);\n    auto updateMapRV = stagingMemory.mapMemory(0, vk::DeviceSize(img.sizeInBytes()));\n    if (updateMapRV.result != vk::Result::eSuccess) {\n        return false;\n    }\n    void *dataPtr = updateMapRV.value;',
            ),
            # createImage (allocate)
            (
                'auto [result, image] = device->logicalDevice().createImage(info);\n    if (result != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "creating image failed!" << vk::to_string(result);\n        return nullptr;\n    }\n    image.bindMemory(memory, 0);',
                'auto imageRV = device->logicalDevice().createImage(info);\n    if (imageRV.result != vk::Result::eSuccess) {\n        qCWarning(KWIN_VULKAN) << "creating image failed!" << vk::to_string(imageRV.result);\n        return nullptr;\n    }\n    auto image = std::move(imageRV.value);\n    image.bindMemory(memory, 0);',
            ),
        ],
        "src/vulkan/vulkan_render_time_query.cpp": [
            # getResults structured binding
            (
                'auto [result, timestamps] = m_pool.getResults<uint64_t>(0, 2, 2 * sizeof(uint64_t), sizeof(uint64_t), vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWait);\n        if (result != vk::Result::eSuccess) {\n            reset();\n            return std::nullopt;\n        }',
                'auto tsRV = m_pool.getResults<uint64_t>(0, 2, 2 * sizeof(uint64_t), sizeof(uint64_t), vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWait);\n        if (tsRV.result != vk::Result::eSuccess) {\n            reset();\n            return std::nullopt;\n        }\n        const auto &timestamps = tsRV.value;',
            ),
            # createQueryPool
            (
                'auto [result, query] = device->logicalDevice().createQueryPool(vk::QueryPoolCreateInfo{\n        vk::QueryPoolCreateFlags{},\n        vk::QueryType::eTimestamp,\n        2,\n    });\n    if (result != vk::Result::eSuccess) {\n        return nullptr;\n    }\n    buffer.resetQueryPool(query, 0, 2);\n    buffer.writeTimestamp(vk::PipelineStageFlagBits::eTopOfPipe, query, 0);\n    return std::make_unique<VulkanRenderTimeQuery>(device, std::move(query));',
                'auto queryRV = device->logicalDevice().createQueryPool(vk::QueryPoolCreateInfo{\n        vk::QueryPoolCreateFlags{},\n        vk::QueryType::eTimestamp,\n        2,\n    });\n    if (queryRV.result != vk::Result::eSuccess) {\n        return nullptr;\n    }\n    auto query = std::move(queryRV.value);\n    buffer.resetQueryPool(query, 0, 2);\n    buffer.writeTimestamp(vk::PipelineStageFlagBits::eTopOfPipe, query, 0);\n    return std::make_unique<VulkanRenderTimeQuery>(device, std::move(query));',
            ),
        ],
        # PipeWire 1.2+ SyncTimeline API not in PipeWire 1.0.5
        "src/plugins/screencast/screencastbuffer.cpp": [
            (
                '    const void *syncTimelineMeta = spa_buffer_find_meta_data(pwBuffer->buffer, SPA_META_SyncTimeline, sizeof(spa_meta_sync_timeline));',
                '#if PW_CHECK_VERSION(1,2,0)\n    const void *syncTimelineMeta = spa_buffer_find_meta_data(pwBuffer->buffer, SPA_META_SyncTimeline, sizeof(spa_meta_sync_timeline));\n#else\n    const void *syncTimelineMeta = nullptr;\n#endif',
            ),
            (
                '    std::unique_ptr<SyncTimeline> synctimeline;\n    if (syncTimelineMeta) {\n        synctimeline = std::make_unique<SyncTimeline>(backend->drmDevice()->fileDescriptor());\n        const FileDescriptor &syncobjfd = synctimeline->fileDescriptor();\n        if (!syncobjfd.isValid()) {\n            buffer->drop();\n            return nullptr;\n        }\n\n        // Signal the first timeline point, so the very first recording can proceed.\n        synctimeline->signal(0);\n\n        spa_data &acquireData = spaData[attrs->planeCount];\n        acquireData.type = SPA_DATA_SyncObj;\n        acquireData.flags = SPA_DATA_FLAG_READABLE;\n        acquireData.fd = syncobjfd.get();\n\n        spa_data &releaseData = spaData[attrs->planeCount + 1];\n        releaseData.type = SPA_DATA_SyncObj;\n        releaseData.flags = SPA_DATA_FLAG_READABLE;\n        releaseData.fd = syncobjfd.get();\n    }',
                '    std::unique_ptr<SyncTimeline> synctimeline;\n#if PW_CHECK_VERSION(1,2,0)\n    if (syncTimelineMeta) {\n        synctimeline = std::make_unique<SyncTimeline>(backend->drmDevice()->fileDescriptor());\n        const FileDescriptor &syncobjfd = synctimeline->fileDescriptor();\n        if (!syncobjfd.isValid()) {\n            buffer->drop();\n            return nullptr;\n        }\n\n        // Signal the first timeline point, so the very first recording can proceed.\n        synctimeline->signal(0);\n\n        spa_data &acquireData = spaData[attrs->planeCount];\n        acquireData.type = SPA_DATA_SyncObj;\n        acquireData.flags = SPA_DATA_FLAG_READABLE;\n        acquireData.fd = syncobjfd.get();\n\n        spa_data &releaseData = spaData[attrs->planeCount + 1];\n        releaseData.type = SPA_DATA_SyncObj;\n        releaseData.flags = SPA_DATA_FLAG_READABLE;\n        releaseData.fd = syncobjfd.get();\n    }\n#endif',
            ),
        ],
        "src/plugins/screencast/screencaststream.cpp": [
            # Buffer params explicit sync block
            (
                '    // Buffer parameters for explicit sync. It requires two extra blocks to hold acquire and\n    // release syncobjs.\n    if (m_dmabufParams && m_dmabufParams->supportsSyncObj) {\n        spa_pod_builder_push_object(&pod_builder.b, &f, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers);\n        spa_pod_builder_add(&pod_builder.b,\n                            SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(3, 2, 4),\n                            SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(buffertypes),\n                            SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(m_dmabufParams->planeCount + 2), 0);\n        spa_pod_builder_prop(&pod_builder.b, SPA_PARAM_BUFFERS_metaType, SPA_POD_PROP_FLAG_MANDATORY);\n        spa_pod_builder_int(&pod_builder.b, 1 << SPA_META_SyncTimeline);\n        params.append((spa_pod *)spa_pod_builder_pop(&pod_builder.b, &f));\n    }',
                '    // Buffer parameters for explicit sync. It requires two extra blocks to hold acquire and\n    // release syncobjs.\n#if PW_CHECK_VERSION(1,2,0)\n    if (m_dmabufParams && m_dmabufParams->supportsSyncObj) {\n        spa_pod_builder_push_object(&pod_builder.b, &f, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers);\n        spa_pod_builder_add(&pod_builder.b,\n                            SPA_PARAM_BUFFERS_buffers, SPA_POD_CHOICE_RANGE_Int(3, 2, 4),\n                            SPA_PARAM_BUFFERS_dataType, SPA_POD_CHOICE_FLAGS_Int(buffertypes),\n                            SPA_PARAM_BUFFERS_blocks, SPA_POD_Int(m_dmabufParams->planeCount + 2), 0);\n        spa_pod_builder_prop(&pod_builder.b, SPA_PARAM_BUFFERS_metaType, SPA_POD_PROP_FLAG_MANDATORY);\n        spa_pod_builder_int(&pod_builder.b, 1 << SPA_META_SyncTimeline);\n        params.append((spa_pod *)spa_pod_builder_pop(&pod_builder.b, &f));\n    }\n#endif',
            ),
            # Meta params SyncTimeline block
            (
                '    if (m_dmabufParams && m_dmabufParams->supportsSyncObj) {\n        params.append(\n            (spa_pod *)spa_pod_builder_add_object(&pod_builder.b,\n                                                  SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,\n                                                  SPA_PARAM_META_type, SPA_POD_Id(SPA_META_SyncTimeline),\n                                                  SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_sync_timeline))));\n    }',
                '#if PW_CHECK_VERSION(1,2,0)\n    if (m_dmabufParams && m_dmabufParams->supportsSyncObj) {\n        params.append(\n            (spa_pod *)spa_pod_builder_add_object(&pod_builder.b,\n                                                  SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,\n                                                  SPA_PARAM_META_type, SPA_POD_Id(SPA_META_SyncTimeline),\n                                                  SPA_PARAM_META_size, SPA_POD_Int(sizeof(struct spa_meta_sync_timeline))));\n    }\n#endif',
            ),
            # dequeueBuffer synctimeline block
            (
                '        auto dmabuf = static_cast<DmaBufScreenCastBuffer *>(pwBuffer->user_data);\n        if (dmabuf && dmabuf->synctimeline) {\n            spa_meta_sync_timeline *synctmeta =\n                static_cast<spa_meta_sync_timeline *>(spa_buffer_find_meta_data(spaBuffer,\n                                                                                SPA_META_SyncTimeline,\n                                                                                sizeof(spa_meta_sync_timeline)));\n            return dmabuf->synctimeline->isMaterialized(synctmeta->release_point);\n        }',
                '#if PW_CHECK_VERSION(1,2,0)\n        auto dmabuf = static_cast<DmaBufScreenCastBuffer *>(pwBuffer->user_data);\n        if (dmabuf && dmabuf->synctimeline) {\n            spa_meta_sync_timeline *synctmeta =\n                static_cast<spa_meta_sync_timeline *>(spa_buffer_find_meta_data(spaBuffer,\n                                                                                SPA_META_SyncTimeline,\n                                                                                sizeof(spa_meta_sync_timeline)));\n            return dmabuf->synctimeline->isMaterialized(synctmeta->release_point);\n        }\n#endif',
            ),
            # render path: synctmeta declaration + dmabuf synctimeline block
            (
                '    spa_meta_sync_timeline *synctmeta = nullptr;\n\n    Region damage;\n    if (effectiveContents & Content::Video) {\n        if (auto memfd = dynamic_cast<MemFdScreenCastBuffer *>(buffer)) {\n            damage = m_source->render(memfd->view.image(), m_damageJournal.accumulate(memfd->m_age, Region::infinite()));\n            bumpBufferAge(memfd);\n        } else if (auto dmabuf = dynamic_cast<DmaBufScreenCastBuffer *>(buffer)) {\n            if (dmabuf->synctimeline) {\n                synctmeta = static_cast<spa_meta_sync_timeline *>(spa_buffer_find_meta_data(spa_buffer,\n                                                                                            SPA_META_SyncTimeline,\n                                                                                            sizeof(spa_meta_sync_timeline)));\n                FileDescriptor syncFileFd = dmabuf->synctimeline->exportSyncFile(synctmeta->release_point);\n                EGLNativeFence fence = EGLNativeFence::importFence(backend->eglDisplayObject(), std::move(syncFileFd));\n                if (fence.waitSync() != EGL_TRUE) {\n                    qCWarning(KWIN_SCREENCAST) << objectName() << "Failed to wait on a fence, recording may be corrupted";\n                }\n            }',
                '#if PW_CHECK_VERSION(1,2,0)\n    spa_meta_sync_timeline *synctmeta = nullptr;\n#endif\n\n    Region damage;\n    if (effectiveContents & Content::Video) {\n        if (auto memfd = dynamic_cast<MemFdScreenCastBuffer *>(buffer)) {\n            damage = m_source->render(memfd->view.image(), m_damageJournal.accumulate(memfd->m_age, Region::infinite()));\n            bumpBufferAge(memfd);\n        } else if (auto dmabuf = dynamic_cast<DmaBufScreenCastBuffer *>(buffer)) {\n#if PW_CHECK_VERSION(1,2,0)\n            if (dmabuf->synctimeline) {\n                synctmeta = static_cast<spa_meta_sync_timeline *>(spa_buffer_find_meta_data(spa_buffer,\n                                                                                            SPA_META_SyncTimeline,\n                                                                                            sizeof(spa_meta_sync_timeline)));\n                FileDescriptor syncFileFd = dmabuf->synctimeline->exportSyncFile(synctmeta->release_point);\n                EGLNativeFence fence = EGLNativeFence::importFence(backend->eglDisplayObject(), std::move(syncFileFd));\n                if (fence.waitSync() != EGL_TRUE) {\n                    qCWarning(KWIN_SCREENCAST) << objectName() << "Failed to wait on a fence, recording may be corrupted";\n                }\n            }\n#endif',
            ),
            # DmaBuf sync path if(synctmeta) block
            (
                '    if (spa_data[0].type == SPA_DATA_DmaBuf) {\n        if (synctmeta) {\n            EGLNativeFence fence(backend->eglDisplayObject());\n\n            synctmeta->acquire_point = synctmeta->release_point + 1;\n            synctmeta->release_point = synctmeta->acquire_point + 1;\n\n            auto dmabuf = static_cast<DmaBufScreenCastBuffer *>(buffer);\n            dmabuf->synctimeline->moveInto(synctmeta->acquire_point, fence.takeFileDescriptor());\n        } else {\n            // Implicit sync is broken on Nvidia and with llvmpipe\n            if (context->glPlatform()->isNvidia() || context->isSoftwareRenderer()) {\n                glFinish();\n            } else {\n                glFlush();\n            }\n        }\n    }',
                '    if (spa_data[0].type == SPA_DATA_DmaBuf) {\n#if PW_CHECK_VERSION(1,2,0)\n        if (synctmeta) {\n            EGLNativeFence fence(backend->eglDisplayObject());\n\n            synctmeta->acquire_point = synctmeta->release_point + 1;\n            synctmeta->release_point = synctmeta->acquire_point + 1;\n\n            auto dmabuf = static_cast<DmaBufScreenCastBuffer *>(buffer);\n            dmabuf->synctimeline->moveInto(synctmeta->acquire_point, fence.takeFileDescriptor());\n        } else {\n#endif\n            // Implicit sync is broken on Nvidia and with llvmpipe\n            if (context->glPlatform()->isNvidia() || context->isSoftwareRenderer()) {\n                glFinish();\n            } else {\n                glFlush();\n            }\n#if PW_CHECK_VERSION(1,2,0)\n        }\n#endif\n    }',
            ),
        ],
    }
    # Fix PipeWire 1.0.5 system header: spa/pod/dynamic.h mixes positional and designated
    # initializers which is illegal in C++23 (GCC 14 -std=gnu++23 rejects it)
    _fix_spa_dynamic_header()
    # Fix systemd _sd-common.h: __STDC_VERSION__ used without defined() guard,
    # rejected by GCC 14 -Werror=undef when included from C++ code
    _fix_sd_common_header()
    for rel_path, subs in patches.items():
        fpath = os.path.join(bd, rel_path)
        if not os.path.exists(fpath):
            continue
        with open(fpath) as f:
            txt = f.read()
        for old, new in subs:
            if old in txt:
                txt = txt.replace(old, new)
        with open(fpath, "w") as f:
            f.write(txt)

def _fix_sd_common_header():
    """Fix systemd _sd-common.h: __STDC_VERSION__ used without defined() guard,
    rejected by GCC 14 -Werror=undef when included from C++ translation units."""
    hdr = "/mnt/smechos_build_root/usr/include/systemd/_sd-common.h"
    if not os.path.exists(hdr):
        return
    with open(hdr) as f:
        txt = f.read()
    old = "#  if __STDC_VERSION__ >= 199901L && !defined(__cplusplus)"
    new = "#  if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 199901L && !defined(__cplusplus)"
    if old in txt:
        with open(hdr, "w") as f:
            f.write(txt.replace(old, new))

def _fix_spa_dynamic_header():
    """Fix PipeWire 1.0.5 spa/pod/dynamic.h: mixed positional/designated initializer
    rejected by GCC 14 with -std=gnu++23. Idempotent (only patches if not already patched)."""
    hdr = "/usr/include/spa-0.2/spa/pod/dynamic.h"
    if not os.path.exists(hdr):
        return
    with open(hdr) as f:
        txt = f.read()
    old = '\t\tSPA_VERSION_POD_BUILDER_CALLBACKS,\n\t\t.overflow = spa_pod_dynamic_builder_overflow'
    new = '\t\t.version = SPA_VERSION_POD_BUILDER_CALLBACKS,\n\t\t.overflow = spa_pod_dynamic_builder_overflow'
    if old in txt:
        with open(hdr, "w") as f:
            f.write(txt.replace(old, new))

def _patch_syntax_highlighting(bd):
    """Fix fish.xml: variable-length lookbehind rejected by PCRE2 at index validation time."""
    fpath = os.path.join(bd, "data/syntax/fish.xml")
    if not os.path.exists(fpath):
        return
    with open(fpath) as f:
        txt = f.read()
    old = '        <RegExpr String="(?&lt;=^|/[\'&quot;]?)&amp;simple_command;&amp;is_end_of_simple_cmd;" lookAhead="1" context="CommandPartCommand"/>'
    new = '        <RegExpr String="&amp;simple_command;&amp;is_end_of_simple_cmd;" lookAhead="1" context="CommandPartCommand"/>'
    if old in txt:
        with open(fpath, "w") as f:
            f.write(txt.replace(old, new))

def _patch_plasma_workspace(bd):
    """Patch plasma-workspace CMakeLists.txt: Qt6Location/Positioning not built, make optional."""
    cmake = os.path.join(bd, "CMakeLists.txt")
    if not os.path.exists(cmake):
        return
    with open(cmake) as f:
        txt = f.read()
    old = 'find_package(Qt6 ${QT_MIN_VERSION} CONFIG REQUIRED COMPONENTS\n                    Concurrent DBus Location Network Positioning Quick QuickWidgets\n                    ShaderTools Sql Svg Widgets)'
    new = 'find_package(Qt6 ${QT_MIN_VERSION} CONFIG REQUIRED COMPONENTS\n                    Concurrent DBus Network Quick QuickWidgets\n                    ShaderTools Sql Svg Widgets)\nfind_package(Qt6 ${QT_MIN_VERSION} CONFIG OPTIONAL_COMPONENTS Location Positioning)'
    if old in txt:
        with open(cmake, "w") as f:
            f.write(txt.replace(old, new))

def _kde_pkg(name, version, base_url, target, env, profile="smechos-plasma-live"):
    stamp = f"kde-pkg-{name}"
    if _phase_done(profile, stamp):
        log(f"kde/{name} already built — skipping", color=YELLOW)
        return
    src     = sources(target)
    fname   = f"{name}-{version}.tar.xz"
    tarball = os.path.join(src, fname)
    download(f"{base_url}/{fname}", tarball)
    bd      = os.path.join(BUILD_TMP, f"kde-{name}")
    bd_build = os.path.join(BUILD_TMP, f"kde-{name}-build")
    shutil.rmtree(bd,       ignore_errors=True)
    shutil.rmtree(bd_build, ignore_errors=True)
    extract(tarball, bd)
    # GCC 14 + Vulkan-HPP NO_EXCEPTIONS patches: std::expected structured bindings not supported
    if name == "kwin":
        _patch_kwin_vulkan(bd)
    if name == "plasma-workspace":
        _patch_plasma_workspace(bd)
    if name == "syntax-highlighting":
        _patch_syntax_highlighting(bd)
    # Per-package extra cmake args for packages with optional/missing system deps
    pkg_extra = {
        "prison":            ["-DWITH_ZXING=OFF"],
        "plasma-workspace":  ["-DWITH_X11=OFF"],
        "breeze":            ["-DBUILD_QT5=OFF"],
        "plasma-integration":["-DBUILD_QT5=OFF"],      # Qt5 not installed; Qt6-only build
        "plasma-desktop":    ["-DWITH_KACCOUNTS=OFF",
                              "-DBUILD_KCMS_JOYSTICK=OFF",
                              "-DBUILD_KCM_MOUSE_X11=OFF",
                              "-DBUILD_KCM_TOUCHPAD_X11=OFF",
                              "-DBUILD_KCM_KEYBOARD_X11=OFF"],
    }
    cmake_install(bd, f"{target}/usr",
        extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                    "-DBUILD_TESTING=OFF", "-DBUILD_QCH=OFF",
                    "-DBUILD_PYTHON_BINDINGS=OFF"] + pkg_extra.get(name, []),
        env=env,
        build_dir=bd_build)
    _mark_done(profile, stamp)
    log(f"{name} {version} done.", color=GREEN)

def phase_wayland(target):
    log_phase("wayland", f"Build wayland {WAYLAND_VER}")
    src     = sources(target)
    tarball = os.path.join(src, f"wayland-{WAYLAND_VER}.tar.gz")
    download(WAYLAND_URL, tarball)
    bd = os.path.join(BUILD_TMP, "wayland")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    # gitlab archive nests under wayland-<ver>/
    inner = os.path.join(bd, f"wayland-{WAYLAND_VER}")
    srcdir = inner if os.path.isdir(inner) else bd
    builddir = os.path.join(bd, "build")
    env = os.environ.copy()
    env["PKG_CONFIG_PATH"] = "/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig"
    run(["meson", "setup", builddir, srcdir,
         f"--prefix={target}/usr",
         "--buildtype=release",
         "-Ddocumentation=false",
         "-Dtests=false"], env=env)
    run(["ninja", "-C", builddir], env=env)
    run(["ninja", "-C", builddir, "install"], env=env)
    result = subprocess.run(["wayland-scanner", "--version"], capture_output=True, text=True)
    log(f"wayland-scanner: {result.stderr.strip() or result.stdout.strip()}", color=GREEN)

def phase_wayland_protocols(target):
    log_phase("wayland-protocols", f"Build wayland-protocols {WAYLAND_PROTO_VER}")
    src     = sources(target)
    tarball = os.path.join(src, f"wayland-protocols-{WAYLAND_PROTO_VER}.tar.gz")
    download(WAYLAND_PROTO_URL, tarball)
    bd = os.path.join(BUILD_TMP, "wayland-protocols")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    inner = os.path.join(bd, f"wayland-protocols-{WAYLAND_PROTO_VER}")
    srcdir = inner if os.path.isdir(inner) else bd
    builddir = os.path.join(bd, "build")
    env = active_env(target)
    extra = f"{target}/usr/lib/x86_64-linux-gnu/pkgconfig:{target}/usr/lib/pkgconfig:{target}/usr/share/pkgconfig"
    env["PKG_CONFIG_PATH"] = extra + ":" + env.get("PKG_CONFIG_PATH", "")
    run(["meson", "setup", builddir, srcdir,
         f"--prefix={target}/usr",
         "--buildtype=release"], env=env)
    run(["ninja", "-C", builddir], env=env)
    run(["ninja", "-C", builddir, "install"], env=env)
    log(f"wayland-protocols {WAYLAND_PROTO_VER} installed", color=GREEN)

def phase_libinput(target):
    log_phase("libinput", f"Build libinput {LIBINPUT_VER}")
    src     = sources(target)
    tarball = os.path.join(src, f"libinput-{LIBINPUT_VER}.tar.gz")
    download(LIBINPUT_URL, tarball)
    bd = os.path.join(BUILD_TMP, "libinput")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    inner = os.path.join(bd, f"libinput-{LIBINPUT_VER}")
    srcdir = inner if os.path.isdir(inner) else bd
    builddir = os.path.join(bd, "build")
    env = active_env(target)
    run(["meson", "setup", builddir, srcdir,
         f"--prefix={target}/usr",
         "--buildtype=release",
         "-Ddocumentation=false",
         "-Dtests=false",
         "-Dlibwacom=false",
         "-Ddebug-gui=false"], env=env)
    run(["ninja", "-C", builddir], env=env)
    run(["ninja", "-C", builddir, "install"], env=env)
    log(f"libinput {LIBINPUT_VER} installed", color=GREEN)

def phase_libeis(target):
    log_phase("libeis", f"Build libeis {LIBEIS_VER} (remote input emulation for kwin)")
    src     = sources(target)
    tarball = os.path.join(src, f"libeis-{LIBEIS_VER}.tar.xz")
    download(LIBEIS_URL, tarball)
    bd = os.path.join(BUILD_TMP, "libeis")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    inner = os.path.join(bd, f"libeis-{LIBEIS_VER}")
    srcdir = inner if os.path.isdir(inner) else bd
    builddir = os.path.join(bd, "build")
    env = active_env(target)
    run(["meson", "setup", builddir, srcdir,
         f"--prefix={target}/usr",
         "--buildtype=release",
         "-Dtests=disabled",
         "-Ddocumentation=disabled"], env=env)
    run(["ninja", "-C", builddir], env=env)
    run(["ninja", "-C", builddir, "install"], env=env)
    log(f"libeis {LIBEIS_VER} installed", color=GREEN)
    log(f"wayland-protocols {WAYLAND_PROTO_VER} installed to {target}/usr", color=GREEN)

def _symlink_arch_libs(target):
    """Create top-level lib/ symlinks for all arch-specific shared libs (.so and .so.*).
    The linker gets -L lib/ but DT_NEEDED entries reference SONAMEs like libKF6Service.so.6
    which live in lib/x86_64-linux-gnu/. Without these symlinks the linker can't resolve
    transitive deps and --no-undefined fails."""
    arch_dir = os.path.join(target, "usr/lib/x86_64-linux-gnu")
    top_dir  = os.path.join(target, "usr/lib")
    if not os.path.isdir(arch_dir):
        return
    for fname in os.listdir(arch_dir):
        if ".so" in fname and fname.startswith("lib"):
            dest = os.path.join(top_dir, fname)
            if not os.path.lexists(dest):
                os.symlink(os.path.join("x86_64-linux-gnu", fname), dest)

def _build_xkbregistry(target, profile="smechos-plasma-live"):
    """Rebuild libxkbcommon 1.6.0 with xkbregistry enabled (not in Ubuntu packages)."""
    stamp = "xkbcommon-with-registry"
    if _phase_done(profile, stamp):
        log("xkbcommon/xkbregistry already built — skipping", color=YELLOW)
        return
    src = sources(target)
    ver = "1.6.0"
    fname = f"libxkbcommon-{ver}.tar.xz"
    tarball = os.path.join(src, fname)
    download(f"https://xkbcommon.org/download/{fname}", tarball)
    bd = os.path.join(BUILD_TMP, "xkbcommon")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    prefix = os.path.join(target, "usr")
    meson_install(bd, prefix, extra_args=[
        "-Denable-docs=false",
        "-Denable-xkbregistry=true",
        "-Denable-x11=true",
        "-Denable-wayland=true",
    ])
    _mark_done(profile, stamp)
    log(f"xkbcommon {ver} (with xkbregistry) done.", color=GREEN)

def phase_kde(target):
    log_phase("kde", f"Compile KDE Frameworks {KF6_VER} + Plasma {PLASMA_VER}")
    _symlink_arch_libs(target)
    _build_xkbregistry(target)
    # Purge stale KF6/KDE cmake configs installed by Ubuntu packages into the
    # build root's multiarch cmake path — they carry wrong versions (e.g. 6.6.0)
    # that cmake prefers over our freshly built 6.27.0 ones.
    _profile = "smechos-plasma-live"
    if not _phase_done(_profile, "kde-cmake-purge"):
        multiarch_cmake = os.path.join(target, "usr/lib/x86_64-linux-gnu/cmake")
        if os.path.isdir(multiarch_cmake):
            for entry in os.listdir(multiarch_cmake):
                if any(entry.startswith(p) for p in ("KF6", "KDE", "KDecoration", "Plasma", "KWin")):
                    shutil.rmtree(os.path.join(multiarch_cmake, entry), ignore_errors=True)
            log("Purged stale KF6/KDE cmake configs from multiarch path", color=YELLOW)
        _mark_done(_profile, "kde-cmake-purge")
    env = active_env(target)

    kf6 = [
        # Tier 0 — no KF6 deps
        "extra-cmake-modules",
        "karchive", "kcodecs", "kcoreaddons", "kdbusaddons",
        "kguiaddons", "ki18n", "kitemmodels", "kitemviews",
        "kunitconversion",      # required by plasma5support
        "kwidgetsaddons", "kwindowsystem",
        # Tier 1 — depend on Tier 0
        "kconfig", "kdoctools",   # kdoctools: docbook/man page generator; required by plasma-desktop
        # Tier 2 — depend on kconfig
        "kcolorscheme", "kauth",
        # Tier 3 — depend on kauth/kcolorscheme
        "kconfigwidgets",
        # Tier 4 — knotifications must precede kjobwidgets
        "kcompletion", "kglobalaccel", "kcrash", "knotifications",
        "kjobwidgets", "sonnet", "kpackage", "kservice",
        # Tier 5 — breeze-icons required by kiconthemes
        "breeze-icons", "kiconthemes", "kxmlgui", "solid",
        # Tier 6
        "kbookmarks", "kio", "kfilemetadata",
        # Tier 7
        "ktextwidgets", "knotifyconfig", "kparts", "kwallet",
        # Tier 8 — kirigami must precede ksvg (KirigamiPlatform dep)
        "kirigami", "kdeclarative", "ksvg",
        "kstatusnotifieritem", "kidletime",
        "qqc2-desktop-style",  # QQC2 desktop style; required by plasma-desktop
        # Tier 9 — kcmutils required by kwin/plasma; kholidays required by knighttime; attica required by knewstuff
        "kcmutils", "kholidays", "attica", "knewstuff", "krunner",
        # Tier 10 — required by plasma-workspace / plasma-nm
        "prison",              # KF6Prison: barcode/QR generator (required by plasma-workspace)
        "syntax-highlighting",  # KF6SyntaxHighlighting: required by ktexteditor
        "ktexteditor",         # KF6TextEditor: text editor component (required by plasma-workspace)
        "kded",                # KF6KDED: KDE daemon infrastructure (required by plasma-workspace)
        "networkmanager-qt",   # KF6NetworkManagerQt: REQUIRED by plasma-workspace on Linux
        "modemmanager-qt",     # KF6ModemManagerQt: required by plasma-nm for mobile broadband
        "kquickcharts",        # KF6QuickCharts: required by plasma-pa (volume applet charts)
        "purpose",             # KF6Purpose: sharing/intent framework (required by Discover)
    ]
    for mod in kf6:
        _kde_pkg(mod, KF6_VER, KF6_URL, target, env)

    plasma = [
        # plasma-activities must precede libplasma
        "plasma-activities", "plasma-activities-stats",
        # libplasma (was plasma-framework in KF5) ships with Plasma release
        "libplasma",
        # kdecoration provides KDecoration3; kwayland requires wayland >= 1.24 (now built)
        # libkscreen provides KF6Screen required by kscreenlocker; must come first
        # knighttime and kscreenlocker are required by kwin
        "kdecoration", "kwayland", "libkscreen",
        # layer-shell-qt must be built from 6.7.2 source before kscreenlocker:
        # system package 6.6.5 is ABI-incompatible with Qt 6.10.3 private API
        "layer-shell-qt",
        "knighttime", "kscreenlocker",
        "libksysguard",        # KSysGuard libs: required by ksystemstats and optional in plasma-workspace
        "kwin", "plasma-workspace", "plasma5support", "plasma-desktop",
        "plasma-nm", "plasma-pa", "powerdevil", "breeze",
        "systemsettings", "plasma-integration", "kdeplasma-addons",
        "ksystemstats", "kscreen",
    ]
    # kirigami-addons has its own release schedule; tarballs sit flat in the dir
    _kde_pkg("kirigami-addons", "1.12.1",
             "https://download.kde.org/stable/kirigami-addons",
             target, env)

    # pulseaudio-qt — required by plasma-pa; own versioning, not part of KF6/Plasma tarballs
    _paq_stamp = "pulseaudio-qt-1.8.1"
    if not _phase_done("smechos-plasma-live", _paq_stamp):
        _paq_ver = "1.8.1"
        _paq_tb  = os.path.join(sources(target), f"pulseaudio-qt-{_paq_ver}.tar.xz")
        download(f"https://download.kde.org/stable/pulseaudio-qt/pulseaudio-qt-{_paq_ver}.tar.xz", _paq_tb)
        _paq_bd  = os.path.join(BUILD_TMP, "pulseaudio-qt")
        shutil.rmtree(_paq_bd, ignore_errors=True)
        extract(_paq_tb, _paq_bd)
        cmake_install(_paq_bd, f"{target}/usr",
            extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                        "-DBUILD_TESTING=OFF"],
            env=env)
        _mark_done("smechos-plasma-live", _paq_stamp)
        log(f"pulseaudio-qt {_paq_ver} done.", color=GREEN)
    else:
        log("pulseaudio-qt already built — skipping", color=YELLOW)

    # qtkeychain 0.16.0 — required by plasma-nm for secure credential storage
    _qtkeychain_stamp = "qtkeychain-0.16.0"
    if not _phase_done("smechos-plasma-live", _qtkeychain_stamp):
        _qk_ver = "0.16.0"
        _qk_url = f"https://github.com/frankosterfeld/qtkeychain/archive/refs/tags/{_qk_ver}.tar.gz"
        _qk_tb  = os.path.join(sources(target), f"qtkeychain-{_qk_ver}.tar.gz")
        download(_qk_url, _qk_tb)
        _qk_bd  = os.path.join(BUILD_TMP, "qtkeychain")
        shutil.rmtree(_qk_bd, ignore_errors=True)
        extract(_qk_tb, _qk_bd)
        cmake_install(_qk_bd, f"{target}/usr",
            extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                        "-DBUILD_TESTING=OFF",
                        "-DBUILD_WITH_QT6=ON",
                        "-DLIBSECRET_SUPPORT=OFF"],  # avoid libsecret dep
            env=env)
        _mark_done("smechos-plasma-live", _qtkeychain_stamp)
        log(f"qtkeychain {_qk_ver} done.", color=GREEN)
    else:
        log("qtkeychain already built — skipping", color=YELLOW)

    for mod in plasma:
        _kde_pkg(mod, PLASMA_VER, PLASMA_URL, target, env)

def phase_plasma_configure(target):
    log_phase("plasma-configure", "Configure Plasma/SDDM session")
    etc = os.path.join(target, "etc")

    sddm_dir = os.path.join(etc, "sddm.conf.d")
    ensure(sddm_dir)
    with open(os.path.join(sddm_dir, "autologin.conf"), "w") as f:
        f.write("[Autologin]\nUser=smech\nSession=plasma\n")

    with open(os.path.join(etc, "sddm.conf"), "w") as f:
        f.write("[Theme]\nCurrent=breeze\n\n[General]\nDisplayServer=wayland\n")

    for name, content in [
        ("sddm", textwrap.dedent("""\
            #!/sbin/openrc-run
            name="SDDM"
            command="/usr/bin/sddm"
            command_background=true
            pidfile="/run/sddm.pid"
            depend() { need localmount dbus udev; }
        """)),
        ("dbus", textwrap.dedent("""\
            #!/sbin/openrc-run
            name="D-Bus"
            command="/usr/bin/dbus-daemon"
            command_args="--system --fork --print-pid"
            pidfile="/run/dbus.pid"
            depend() { need localmount; }
        """)),
    ]:
        path = os.path.join(etc, "init.d", name)
        with open(path, "w") as f:
            f.write(content)
        os.chmod(path, 0o755)
    log("Plasma session configured.", color=GREEN)

def phase_kwin_deps(target):
    log_phase("kwin-deps", "Copy KWin compositor dependencies from host")
    libs = [
        "/usr/lib/x86_64-linux-gnu/libdrm.so.2",
        "/usr/lib/x86_64-linux-gnu/libxkbcommon.so.0",
        "/usr/lib/x86_64-linux-gnu/libinput.so.10",
        "/usr/lib/x86_64-linux-gnu/libevdev.so.2",
        "/usr/lib/x86_64-linux-gnu/libmtdev.so.1",
    ]
    dst = os.path.join(target, "usr", "lib")
    ensure(dst)
    for lib in libs:
        if os.path.exists(lib):
            dest = os.path.join(dst, os.path.basename(lib))
            if not os.path.exists(dest):
                shutil.copy2(lib, dest)
                log(f"Copied {os.path.basename(lib)}")
        else:
            log(f"Skipped (not on host): {os.path.basename(lib)}", color=YELLOW)
    log("KWin deps done.", color=GREEN)

def phase_qt6uitools(target):
    log_phase("qt6uitools", "Ensure Qt6UITools present")
    if os.path.exists(os.path.join(target, "usr", "lib", "libQt6UiTools.so")):
        log("Qt6UITools already present.", color=GREEN)
        return
    for candidate in ["/usr/lib/x86_64-linux-gnu/libQt6UiTools.so",
                      "/usr/lib/libQt6UiTools.so"]:
        if os.path.exists(candidate):
            shutil.copy2(candidate, os.path.join(target, "usr", "lib"))
            log("Qt6UITools copied from host.", color=GREEN)
            return
    log("Qt6UITools not found -- will arrive with qtbase.", color=YELLOW)

def phase_patch_metadata(target):
    log_phase("patch-metadata", "Patch KDE metadata for SmechOS branding")
    import fnmatch
    release_file = os.path.join(target, "etc", "os-release")
    if os.path.exists(release_file):
        overrides = {
            "NAME":         "SmechOS",
            "PRETTY_NAME":  "SmechOS 1.0 (Sovereign)",
            "HOME_URL":     "https://os.smech.xyz",
        }
        with open(release_file) as f:
            lines = f.readlines()
        done = set()
        new_lines = []
        for line in lines:
            key = line.split("=", 1)[0].strip()
            if key in overrides:
                new_lines.append(f'{key}="{overrides[key]}"\n')
                done.add(key)
            else:
                new_lines.append(line)
        for k, v in overrides.items():
            if k not in done:
                new_lines.append(f'{k}="{v}"\n')
        with open(release_file, "w") as f:
            f.writelines(new_lines)
    log("Metadata patched.", color=GREEN)

def phase_plasma_discover(target):
    log_phase("discover", "Compile Plasma Discover + PackageKit + SPK backend")
    src    = sources(target)
    env    = active_env(target)
    prefix = f"{target}/usr"

    # AppStream
    url     = f"https://www.freedesktop.org/software/appstream/releases/AppStream-{APPSTREAM_VER}.tar.xz"
    tarball = os.path.join(src, f"AppStream-{APPSTREAM_VER}.tar.xz")
    download(url, tarball)
    bd = os.path.join(BUILD_TMP, "appstream")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    # Tarball has leading ./ so strip-components 1 leaves AppStream-VER/ as subdir
    bd_src = os.path.join(bd, f"AppStream-{APPSTREAM_VER}")
    # Remove Qt test subdir — QtTest is not in our build root and the tests aren't needed
    qt_tests_dir = os.path.join(bd_src, "qt", "tests")
    if os.path.isdir(qt_tests_dir):
        qt_meson = os.path.join(bd_src, "qt", "meson.build")
        with open(qt_meson) as f: content = f.read()
        content = content.replace("subdir('tests/')", "# subdir('tests/')  # disabled: no QtTest")
        content = content.replace("subdir('tests')", "# subdir('tests')  # disabled: no QtTest")
        with open(qt_meson, "w") as f: f.write(content)
    meson_install(bd_src, prefix,
        extra_args=["-Ddocs=false", "-Dapidocs=false",
                    "-Dcompose=false", "-Dqt=true"],
        env=env)

    # PackageKit
    url     = f"https://www.freedesktop.org/software/PackageKit/releases/PackageKit-{PACKAGEKIT_VER}.tar.xz"
    tarball = os.path.join(src, f"PackageKit-{PACKAGEKIT_VER}.tar.xz")
    download(url, tarball)
    bd = os.path.join(BUILD_TMP, "packagekit")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    meson_install(bd, prefix,
        extra_args=["-Ddaemon_tests=false", "-Dlocal_checkout=false",
                    "-Dgstreamer_plugin=false", "-Dgtk_module=false"],
        env=env)

    # packagekit-qt (Qt6 bindings for PackageKit — required by Discover)
    _pf = "smechos-plasma-live"
    pkqt_stamp = f"packagekitqt-{PACKAGEKITQT_VER}"
    if not _phase_done(_pf, pkqt_stamp):
        pkqt_url     = f"https://github.com/PackageKit/packagekit-qt/archive/refs/tags/v{PACKAGEKITQT_VER}.tar.gz"
        pkqt_tarball = os.path.join(src, f"packagekit-qt-{PACKAGEKITQT_VER}.tar.gz")
        download(pkqt_url, pkqt_tarball)
        pkqt_bd = os.path.join(BUILD_TMP, "packagekit-qt")
        shutil.rmtree(pkqt_bd, ignore_errors=True)
        extract(pkqt_tarball, pkqt_bd)
        cmake_install(pkqt_bd, prefix,
            extra_args=[f"-DCMAKE_PREFIX_PATH={prefix}"],
            env=env)
        _mark_done(_pf, pkqt_stamp)

    # SPK PackageKit script backend
    backend_dir = os.path.join(target, "usr", "lib", "packagekit-backend")
    ensure(backend_dir)
    with open(os.path.join(backend_dir, "pk-backend-spk.py"), "w") as f:
        f.write(textwrap.dedent("""\
            #!/usr/bin/env python3
            import subprocess, sys
            proc = subprocess.Popen(["spk", "packagekit-backend"],
                stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
            sys.exit(proc.wait())
        """))
    os.chmod(os.path.join(backend_dir, "pk-backend-spk.py"), 0o755)

    pk_conf = os.path.join(target, "etc", "PackageKit")
    ensure(pk_conf)
    with open(os.path.join(pk_conf, "PackageKit.conf"), "w") as f:
        f.write("[Daemon]\nDefaultBackend=spk\n")

    # Plasma Discover
    url     = f"https://download.kde.org/stable/plasma/{PLASMA_VER}/discover-{PLASMA_VER}.tar.xz"
    tarball = os.path.join(src, f"discover-{PLASMA_VER}.tar.xz")
    download(url, tarball)
    bd = os.path.join(BUILD_TMP, "discover")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=["-DWITH_KCM=OFF", "-DWITH_SNAP=OFF",
                    "-DWITH_FLATPAK=ON", "-DWITH_FWUPD=ON",
                    "-DWITH_PACKAGEKIT=ON",
                    "-DCMAKE_INSTALL_LIBDIR=lib",
                    "-DBUILD_TESTING=OFF",
                    f"-DCMAKE_PREFIX_PATH={prefix}"],
        env=env, build_dir=os.path.join(BUILD_TMP, "discover-build"))
    log("Plasma Discover + PackageKit + Flatpak + fwupd installed.", color=GREEN)

# ── Package bundling ─────────────────────────────────────────────────────────

def phase_bundle_packages(target):
    """Bundle compiled output into .tar.xz packages consumable by spk install."""
    log_phase("bundle", "Bundle compiled output into spk-installable .tar.xz packages")
    out = "/tmp/smechos-packages"
    shutil.rmtree(out, ignore_errors=True)
    ensure(out)

    import hashlib

    def tar_paths(pkg_name, paths, prefix=None):
        """Create pkg_name.tar.xz from a list of (src_glob_or_dir, archive_path) tuples."""
        out_file = os.path.join(out, f"{pkg_name}.tar.xz")
        args = ["tar", "-cJf", out_file]
        # Build a list of existing paths relative to target
        includes = []
        for rel in paths:
            full = os.path.join(target, rel.lstrip("/"))
            if os.path.exists(full):
                includes.append(rel.lstrip("/"))
            else:
                log(f"  {pkg_name}: skipping missing path {rel}", color=YELLOW)
        if not includes:
            log(f"  {pkg_name}: no files found, skipping", color=YELLOW)
            return
        run(["tar", "-cJf", out_file, "-C", target] + includes)
        size  = os.path.getsize(out_file) // 1_048_576
        h     = hashlib.sha256()
        with open(out_file, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        log(f"  {pkg_name}.tar.xz  {size} MB  sha256:{h.hexdigest()[:16]}…", color=GREEN)

    tar_paths("base-system", [
        "usr/bin/bash", "usr/bin/sh", "bin",
        "usr/bin/coreutils", "usr/bin/grep", "usr/bin/sed", "usr/bin/gawk",
        "usr/bin/tar", "usr/bin/gzip", "usr/bin/xz", "usr/bin/find",
        "usr/lib/libc.so", "usr/lib/libm.so",
        "etc/passwd", "etc/group", "etc/shells", "etc/hostname",
        "etc/hosts", "etc/resolv.conf", "etc/fstab", "etc/os-release",
    ])

    tar_paths("kernel-modules", [
        "boot/vmlinuz", "boot/System.map", "lib/modules",
    ])

    tar_paths("firmware", [
        "lib/firmware",
    ])

    tar_paths("bootloader-grub", [
        "usr/lib/grub", "usr/bin/grub-install", "usr/bin/grub-mkconfig",
        "usr/bin/grub-mkimage", "usr/bin/grub-probe",
        "usr/share/grub", "boot/grub",
    ])

    tar_paths("qt6", [
        "usr/lib/libQt6Core.so.6", "usr/lib/libQt6Gui.so.6",
        "usr/lib/libQt6Widgets.so.6", "usr/lib/libQt6Network.so.6",
        "usr/lib/libQt6DBus.so.6", "usr/lib/libQt6Qml.so.6",
        "usr/lib/libQt6Quick.so.6", "usr/lib/libQt6Svg.so.6",
        "usr/lib/libQt6WaylandClient.so.6", "usr/lib/libQt6Multimedia.so.6",
        "usr/lib/qt6", "usr/plugins", "usr/qml",
    ])

    tar_paths("mesa-graphics", [
        "usr/lib/libGL.so.1", "usr/lib/libEGL.so.1",
        "usr/lib/libgbm.so.1", "usr/lib/libglapi.so.0",
        "usr/lib/libvulkan.so.1",
        "usr/lib/dri", "usr/lib/gallium-pipe",
        "usr/share/vulkan", "usr/share/glvnd",
    ])

    tar_paths("kde-frameworks", [
        "usr/lib/libKF6Core.so.6", "usr/lib/libKF6Config.so.6",
        "usr/lib/libKF6ConfigWidgets.so.6", "usr/lib/libKF6I18n.so.6",
        "usr/lib/libKF6IconThemes.so.6", "usr/lib/libKF6KIO.so.6",
        "usr/lib/libKF6Parts.so.6", "usr/lib/libKF6Service.so.6",
        "usr/lib/libKF6Solid.so.6", "usr/lib/libKF6WindowSystem.so.6",
        "usr/lib/libKF6XmlGui.so.6",
        "usr/share/kf6", "usr/share/locale",
    ])

    tar_paths("plasma", [
        "usr/bin/plasmashell", "usr/bin/kwin_wayland", "usr/bin/kwin_x11",
        "usr/bin/sddm", "usr/bin/startplasma-wayland",
        "usr/bin/krunner", "usr/bin/kscreen-doctor",
        "usr/lib/libplasma.so.6", "usr/lib/libplasmaquick.so.6",
        "usr/lib/plasma-desktop", "usr/lib/kwin",
        "usr/share/plasma", "usr/share/sddm",
        "usr/share/applications/org.kde.plasmashell.desktop",
        "etc/sddm.conf.d",
    ])

    tar_paths("plasma-discover", [
        "usr/bin/plasma-discover",
        "usr/lib/plasma-discover",
        "usr/share/applications/org.kde.discover.desktop",
    ])

    tar_paths("packagekit-spk", [
        "usr/bin/packagekitd",
        "usr/lib/packagekit-backend",
        "usr/share/dbus-1/system-services/org.freedesktop.PackageKit.service",
        "etc/PackageKit",
    ])

    log(f"All packages written to {out}", color=GREEN)
    log("Upload these to the GitHub Release and set RELEASE_BASE_URL in spk.", color=YELLOW)

# ── Plasma Live phases ────────────────────────────────────────────────────────

def phase_bootstrap_userland_glibc(target):
    """Bootstrap GNU userland against host glibc (used by the plasma-live profile)."""
    log_phase("userland-glibc", "Bootstrap GNU userland against host glibc")
    src  = sources(target)
    env  = build_env_glibc(target)
    pfix = f"{target}/usr"
    pkgs = [
        ("bash",      "5.2.37",
         "https://ftp.gnu.org/gnu/bash/bash-5.2.37.tar.gz",
         ["--without-bash-malloc", "--disable-nls"]),
        ("coreutils", "9.5",
         "https://ftp.gnu.org/gnu/coreutils/coreutils-9.5.tar.xz", ["--disable-nls"]),
        ("grep",      "3.11",
         "https://ftp.gnu.org/gnu/grep/grep-3.11.tar.xz", []),
        ("sed",       "4.9",
         "https://ftp.gnu.org/gnu/sed/sed-4.9.tar.xz", []),
        ("gawk",      "5.3.1",
         "https://ftp.gnu.org/gnu/gawk/gawk-5.3.1.tar.xz", []),
        ("findutils", "4.10.0",
         "https://ftp.gnu.org/gnu/findutils/findutils-4.10.0.tar.xz", []),
        ("tar",       "1.35",
         "https://ftp.gnu.org/gnu/tar/tar-1.35.tar.xz", []),
        ("gzip",      "1.13",
         "https://ftp.gnu.org/gnu/gzip/gzip-1.13.tar.xz", []),
        ("xz",        "5.6.3",
         "https://github.com/tukaani-project/xz/releases/download/v5.6.3/xz-5.6.3.tar.xz",
         ["--disable-xzdec", "--disable-lzmadec"]),
    ]
    for name, ver, url, flags in pkgs:
        tarball = os.path.join(src, os.path.basename(url))
        download(url, tarball)
        bd = os.path.join(BUILD_TMP, name)
        shutil.rmtree(bd, ignore_errors=True)
        extract(tarball, bd)
        run(["./configure", f"--prefix={pfix}"] + flags, cwd=bd, env=env)
        run(["make", "-j", nproc()], cwd=bd, env=env)
        run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))
        log(f"{name} {ver} installed.", color=GREEN)

def _resolve_systemd_version():
    """Resolve latest systemd release from GitHub."""
    import re
    log("Resolving latest systemd version from GitHub...")
    try:
        with urllib.request.urlopen(
                "https://api.github.com/repos/systemd/systemd/releases/latest",
                timeout=15) as r:
            data = r.read().decode()
        ver = re.search(r'"tag_name"\s*:\s*"v([0-9.]+)"', data)
        if ver:
            log(f"systemd {ver.group(1)}", color=GREEN)
            return ver.group(1)
    except Exception:
        pass
    log("Could not resolve systemd version, using fallback 261.1", color=YELLOW)
    return "261.1"

def phase_systemd(target):
    """Compile systemd from source (with libcap + util-linux deps)."""
    systemd_ver = _resolve_systemd_version()
    log_phase("systemd", f"Compile systemd {systemd_ver} from source")
    src    = sources(target)
    env    = build_env_glibc(target)
    prefix = f"{target}/usr"

    # ── gperf (needed for systemd hash table generation) ──────────────────────
    gperf_ver = "3.1"
    gperf_url = f"https://ftp.gnu.org/gnu/gperf/gperf-{gperf_ver}.tar.gz"
    tarball   = os.path.join(src, f"gperf-{gperf_ver}.tar.gz")
    download(gperf_url, tarball)
    bd = os.path.join(BUILD_TMP, "gperf")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    run(["./configure", f"--prefix={prefix}"], cwd=bd, env=env)
    run(["make", "-j", nproc()], cwd=bd, env=env)
    run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))
    log("gperf installed.", color=GREEN)

    # ── libcap (POSIX capabilities library) ───────────────────────────────────
    libcap_ver = "2.73"
    libcap_url = (f"https://mirrors.edge.kernel.org/pub/linux/libs/security/"
                  f"linux-privs/libcap2/libcap-{libcap_ver}.tar.xz")
    tarball = os.path.join(src, f"libcap-{libcap_ver}.tar.xz")
    download(libcap_url, tarball)
    bd = os.path.join(BUILD_TMP, "libcap")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cap_env = dict(env)
    cap_env["prefix"] = prefix
    run(["make", "-j", nproc(), f"prefix={prefix}", "lib=lib",
         "GOLANG=no", "PYTHON=no"], cwd=bd, env=cap_env)
    run(["make", "install", f"prefix={prefix}", "lib=lib",
         "GOLANG=no", "PYTHON=no"], cwd=bd, env=cap_env,
        sudo=(os.geteuid() != 0))
    log("libcap installed.", color=GREEN)

    # ── util-linux (provides libmount + libblkid required by systemd) ─────────
    ul_ver = "2.40.4"
    ul_url = (f"https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/"
              f"v2.40/util-linux-{ul_ver}.tar.xz")
    tarball = os.path.join(src, f"util-linux-{ul_ver}.tar.xz")
    download(ul_url, tarball)
    bd = os.path.join(BUILD_TMP, "util-linux")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    run(["./configure", f"--prefix={prefix}",
         "--disable-all-programs",
         "--enable-libmount", "--enable-libblkid",
         "--enable-libuuid",
         "--without-python", "--disable-nls"], cwd=bd, env=env)
    run(["make", "-j", nproc()], cwd=bd, env=env)
    run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))
    log("util-linux (libmount + libblkid) installed.", color=GREEN)

    # ── systemd ───────────────────────────────────────────────────────────────
    sd_url  = (f"https://github.com/systemd/systemd/archive/refs/tags/"
               f"v{systemd_ver}.tar.gz")
    tarball = os.path.join(src, f"systemd-{systemd_ver}.tar.gz")
    download(sd_url, tarball)
    bd = os.path.join(BUILD_TMP, "systemd")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    meson_install(bd, prefix,
        extra_args=[
            "-Dpam=disabled",
            "-Daudit=disabled",
            "-Dselinux=disabled",
            "-Dlibcryptsetup=disabled",
            "-Dlibcryptsetup-plugins=disabled",
            "-Dgcrypt=disabled",
            "-Dp11kit=disabled",
            "-Dapparmor=disabled",
            "-Dmicrohttpd=disabled",
            "-Dlibcurl=disabled",
            "-Dlibidn2=disabled",
            "-Dlibidn=disabled",
            "-Dqrencode=disabled",
            "-Dpolkit=disabled",
            "-Delfutils=disabled",
            "-Dkmod=disabled",
            "-Dukify=disabled",
            "-Dbootloader=disabled",
            "-Ddns-over-tls=false",
            "-Ddefault-dnssec=no",
            "-Dfallback-hostname=smechos",
            "-Dmode=release",
        ],
        env=env, build_dir=os.path.join(BUILD_TMP, "systemd-build"))
    log(f"systemd {systemd_ver} installed.", color=GREEN)

def phase_systemd_configure(target):
    """Configure systemd units for KDE Plasma desktop (graphical target, SDDM)."""
    log_phase("systemd-config", "Configure systemd for KDE Plasma desktop")
    ensure(os.path.join(target, "etc", "systemd", "system"))

    # default.target → graphical.target
    default_link = os.path.join(target, "etc", "systemd", "system", "default.target")
    graphical    = "/lib/systemd/system/graphical.target"
    if not os.path.lexists(default_link):
        os.symlink(graphical, default_link)

    # Enable SDDM
    gfx_wants = os.path.join(target, "etc", "systemd", "system", "graphical.target.wants")
    ensure(gfx_wants)
    sddm_bin = os.path.join(target, "usr", "bin", "sddm")
    if os.path.exists(sddm_bin):
        sddm_unit = os.path.join(target, "etc", "systemd", "system", "sddm.service")
        with open(sddm_unit, "w") as f:
            f.write(textwrap.dedent("""\
                [Unit]
                Description=Simple Desktop Display Manager
                After=systemd-user-sessions.service

                [Service]
                ExecStart=/usr/bin/sddm
                Restart=always

                [Install]
                Alias=display-manager.service
            """))
        sddm_link = os.path.join(gfx_wants, "sddm.service")
        if not os.path.lexists(sddm_link):
            os.symlink(sddm_unit, sddm_link)

    # machine-id placeholder
    mid = os.path.join(target, "etc", "machine-id")
    if not os.path.exists(mid):
        with open(mid, "w") as f:
            f.write("uninitialized\n")

    # hostname
    hn = os.path.join(target, "etc", "hostname")
    if not os.path.exists(hn):
        with open(hn, "w") as f:
            f.write("smechos\n")

    log("systemd desktop config applied.", color=GREEN)

def phase_calamares(target):
    """Build Calamares graphical installer and its deps (yaml-cpp, kpmcore)."""
    log_phase("calamares", f"Build Calamares {CALAMARES_VER} graphical installer")
    src    = sources(target)
    env    = build_env_glibc(target)
    prefix = f"{target}/usr"

    # yaml-cpp 0.8.0
    yaml_ver = "0.8.0"
    yaml_url = f"https://github.com/jbeder/yaml-cpp/archive/refs/tags/{yaml_ver}.tar.gz"
    tarball  = os.path.join(src, f"yaml-cpp-{yaml_ver}.tar.gz")
    download(yaml_url, tarball)
    bd = os.path.join(BUILD_TMP, "yaml-cpp")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=["-DYAML_BUILD_SHARED_LIBS=ON", "-DYAML_CPP_BUILD_TESTS=OFF",
                    f"-DCMAKE_PREFIX_PATH={prefix}"],
        env=env, build_dir=os.path.join(BUILD_TMP, "yaml-cpp-build"))

    # extra-cmake-modules (ECM) — needed by kpmcore + calamares
    ecm_ver = KF6_VER
    ecm_url = f"https://download.kde.org/stable/frameworks/6.27/extra-cmake-modules-{ecm_ver}.tar.xz"
    tarball = os.path.join(src, f"extra-cmake-modules-{ecm_ver}.tar.xz")
    download(ecm_url, tarball)
    bd = os.path.join(BUILD_TMP, "ecm")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=[f"-DCMAKE_PREFIX_PATH={prefix}", "-DBUILD_TESTING=OFF"],
        env=env, build_dir=os.path.join(BUILD_TMP, "ecm-build"))

    # kpmcore 24.08.3 (KDE Partition Manager library)
    kpm_ver = "24.08.3"
    kpm_url = (f"https://download.kde.org/stable/release-service/{kpm_ver}"
               f"/src/kpmcore-{kpm_ver}.tar.xz")
    tarball = os.path.join(src, f"kpmcore-{kpm_ver}.tar.xz")
    download(kpm_url, tarball)
    bd = os.path.join(BUILD_TMP, "kpmcore")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=[f"-DCMAKE_PREFIX_PATH={prefix}", "-DBUILD_TESTING=OFF"],
        env=env, build_dir=os.path.join(BUILD_TMP, "kpmcore-build"))

    # Calamares
    cal_url = (f"https://github.com/calamares/calamares/releases/download"
               f"/v{CALAMARES_VER}/calamares-{CALAMARES_VER}.tar.gz")
    tarball = os.path.join(src, f"calamares-{CALAMARES_VER}.tar.gz")
    download(cal_url, tarball)
    bd = os.path.join(BUILD_TMP, "calamares")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=[f"-DCMAKE_PREFIX_PATH={prefix}",
                    "-DWITH_PYTHON=ON", "-DWITH_QT6=ON",
                    "-DBUILD_TESTING=OFF", "-DINSTALL_CONFIG=ON"],
        env=env, build_dir=os.path.join(BUILD_TMP, "calamares-build"))

    # Calamares settings.conf
    cal_etc = os.path.join(target, "etc", "calamares")
    ensure(cal_etc)
    with open(os.path.join(cal_etc, "settings.conf"), "w") as f:
        f.write(textwrap.dedent("""\
            modules-search: [ local, /usr/lib/calamares/modules ]
            sequence:
              - show:
                - welcome
                - locale
                - keyboard
                - partition
                - users
                - summary
              - exec:
                - partition
                - mount
                - unpackfs
                - machineid
                - fstab
                - locale
                - keyboard
                - localecfg
                - users
                - networkcfg
                - grubcfg
                - bootloader
                - umount
              - show:
                - finished
            branding: smechos
            prompt-install: true
            dont-chroot: false
        """))

    # SmechOS branding for Calamares
    brand_dir = os.path.join(target, "usr", "share", "calamares", "branding", "smechos")
    ensure(brand_dir)
    with open(os.path.join(brand_dir, "branding.desc"), "w") as f:
        f.write(textwrap.dedent("""\
            componentName: smechos
            strings:
              productName: SmechOS
              shortProductName: SmechOS
              version: "1.0"
              shortVersion: "1.0"
              versionedName: SmechOS 1.0
              bootloaderEntryName: SmechOS
              productUrl: https://os.smech.xyz
              supportUrl: https://github.com/Smech-Labs
              knownIssuesUrl: https://github.com/Smech-Labs/smechos-site/issues
              releaseNotesUrl: https://os.smech.xyz
            images:
              productLogo: smechos.png
              productIcon: smechos.png
              productWelcome: show.png
            slideshow: show.qml
            style:
              sidebarBackground: "#1e1e2e"
              sidebarText: "#cdd6f4"
              sidebarTextHighlight: "#89b4fa"
        """))
    log("Calamares installed.", color=GREEN)

def phase_google_chrome(target):
    """Download and extract the Google Chrome stable .deb into target."""
    log_phase("chrome", "Install Google Chrome stable")
    src = sources(target)
    url = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
    deb = os.path.join(src, "google-chrome-stable_current_amd64.deb")
    download(url, deb)
    log("Extracting Chrome .deb...")
    _extract_deb(deb, target)
    # Strip Debian-specific cron/apt artifacts that don't apply on SmechOS
    for unwanted in [
        os.path.join(target, "etc", "cron.daily", "google-chrome"),
        os.path.join(target, "etc", "apt", "sources.list.d", "google-chrome.list"),
    ]:
        if os.path.exists(unwanted):
            os.remove(unwanted)
    log("Google Chrome installed.", color=GREEN)

def phase_live_initramfs(target):
    """Build a static busybox initramfs for live boot (squashfs + overlayfs)."""
    log_phase("live-initramfs", f"Build busybox {BUSYBOX_VER} live initramfs")
    src = sources(target)

    # Busybox static
    bb_url  = f"https://busybox.net/downloads/busybox-{BUSYBOX_VER}.tar.bz2"
    tarball = os.path.join(src, f"busybox-{BUSYBOX_VER}.tar.bz2")
    download(bb_url, tarball)
    bd = os.path.join(BUILD_TMP, "busybox")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    env = dict(os.environ)
    env.pop("CC",     None)
    env.pop("LDFLAGS",None)
    run(["make", "defconfig"], cwd=bd, env=env)
    cfg = os.path.join(bd, ".config")
    with open(cfg) as f: cfg_text = f.read()
    cfg_text = cfg_text.replace("CONFIG_TC=y", "# CONFIG_TC is not set")
    cfg_text += "\nCONFIG_STATIC=y\n"
    with open(cfg, "w") as f: f.write(cfg_text)
    run(["make", "oldconfig"], cwd=bd, env=env, check=False)
    run(["make", "-j", nproc()], cwd=bd, env=env)

    # Assemble initramfs tree
    init_tree = os.path.join(BUILD_TMP, "live-initramfs")
    shutil.rmtree(init_tree, ignore_errors=True)
    for d in ["bin", "dev", "proc", "sys",
              "mnt/cdrom", "mnt/squashfs", "mnt/overlay", "mnt/rootfs"]:
        ensure(os.path.join(init_tree, d))

    shutil.copy2(os.path.join(bd, "busybox"), os.path.join(init_tree, "bin", "busybox"))
    os.chmod(os.path.join(init_tree, "bin", "busybox"), 0o755)
    for applet in ["sh", "mount", "mkdir", "ln", "switch_root", "mdev"]:
        link = os.path.join(init_tree, "bin", applet)
        if not os.path.lexists(link):
            os.symlink("busybox", link)

    # Live init script
    init_script = os.path.join(init_tree, "init")
    with open(init_script, "w") as f:
        f.write(textwrap.dedent("""\
            #!/bin/sh
            mount -t proc  proc  /proc
            mount -t sysfs sysfs /sys
            mount -t devtmpfs devtmpfs /dev 2>/dev/null || mdev -s

            # Find and mount ISO (CD-ROM or USB)
            for dev in /dev/sr0 /dev/sda /dev/sdb /dev/sdc; do
                mount -t iso9660 -o ro "$dev" /mnt/cdrom 2>/dev/null && break
            done

            # Mount squashfs read-only base
            mount -t squashfs -o ro /mnt/cdrom/live/filesystem.squashfs /mnt/squashfs

            # overlayfs: tmpfs on top for a writable live session
            mount -t tmpfs tmpfs /mnt/overlay
            mkdir -p /mnt/overlay/upper /mnt/overlay/work
            mount -t overlay overlay \
                -o lowerdir=/mnt/squashfs,upperdir=/mnt/overlay/upper,workdir=/mnt/overlay/work \
                /mnt/rootfs

            # Move mounts into new root
            mkdir -p /mnt/rootfs/dev /mnt/rootfs/proc /mnt/rootfs/sys
            mount --move /dev  /mnt/rootfs/dev
            mount --move /proc /mnt/rootfs/proc
            mount --move /sys  /mnt/rootfs/sys

            exec switch_root /mnt/rootfs /sbin/init
        """))
    os.chmod(init_script, 0o755)

    # Pack initramfs: find | cpio | gzip
    initrd_path = os.path.join(target, "boot", "live-initrd.img")
    ensure(os.path.dirname(initrd_path))
    log("Packing live initramfs...")
    find_proc = subprocess.Popen(
        ["find", ".", "-print0"], cwd=init_tree, stdout=subprocess.PIPE)
    cpio_proc = subprocess.Popen(
        ["cpio", "--null", "--create", "--format=newc"],
        cwd=init_tree, stdin=find_proc.stdout, stdout=subprocess.PIPE)
    find_proc.stdout.close()
    with open(initrd_path, "wb") as out_f:
        gzip_proc = subprocess.Popen(["gzip", "-9"], stdin=cpio_proc.stdout, stdout=out_f)
    cpio_proc.stdout.close()
    gzip_proc.wait(); find_proc.wait(); cpio_proc.wait()
    log(f"Live initramfs: {initrd_path}", color=GREEN)

# ── SmechVisor phases ─────────────────────────────────────────────────────────

def phase_install_smechvisord(target):
    log_phase("smechvisord", "Install smechvisord daemon")
    url  = ("https://github.com/Smech-Labs/smechvisord/releases/download/"
            "v0.1.0-alpha/smechvisord")
    dest = os.path.join(target, "usr", "bin", "smechvisord")
    ensure(os.path.dirname(dest))
    download(url, dest)
    os.chmod(dest, 0o755)

    init = os.path.join(target, "etc", "init.d", "smechvisord")
    ensure(os.path.dirname(init))
    with open(init, "w") as f:
        f.write(textwrap.dedent("""\
            #!/sbin/openrc-run
            name="smechvisord"
            command="/usr/bin/smechvisord"
            command_background=true
            pidfile="/run/smechvisord.pid"
            environment="SMECHVISORD_BIND=0.0.0.0:8080"
            environment="SMECHVISORD_WEB_DIR=/usr/share/smechvisord/web"
            depend() { need localmount net.lo; }
        """))
    os.chmod(init, 0o755)
    log("smechvisord installed.", color=GREEN)

# ── ISO builders ──────────────────────────────────────────────────────────────

def _grub_mkrescue(iso_path, work_dir, grub_cfg, files, label):
    ensure(work_dir)
    grub_dir = os.path.join(work_dir, "boot", "grub")
    ensure(grub_dir)
    with open(os.path.join(grub_dir, "grub.cfg"), "w") as f:
        f.write(grub_cfg)
    for src_path, rel_dst in files:
        dst = os.path.join(work_dir, rel_dst)
        ensure(os.path.dirname(dst))
        if os.path.isfile(src_path):
            shutil.copy2(src_path, dst)
        elif os.path.isdir(src_path):
            shutil.copytree(src_path, dst, dirs_exist_ok=True)
        else:
            log(f"Warning: {src_path} not found, skipping from ISO", color=YELLOW)

    mkrescue = shutil.which("grub-mkrescue") or shutil.which("grub2-mkrescue")
    if not mkrescue:
        err("grub-mkrescue not found. Install grub-pc-bin.")
    run([mkrescue, "-o", iso_path, work_dir, "--", "-volid", label])

    import hashlib
    h = hashlib.sha256()
    with open(iso_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    mb = os.path.getsize(iso_path) // 1_048_576
    log(f"ISO: {iso_path} ({mb} MB)", color=GREEN)
    log(f"SHA-256: {h.hexdigest()}", color=GREEN)

def phase_iso_install_smechos(target):
    log_phase("iso-smechos", "Build SmechOS install ISO")
    work = os.path.join(BUILD_TMP, "iso-smechos")
    shutil.rmtree(work, ignore_errors=True)
    _grub_mkrescue("/tmp/smechos-install.iso", work,
        textwrap.dedent("""\
            set timeout=5
            set default=0
            menuentry "Install SmechOS" {
                linux /boot/vmlinuz quiet loglevel=3
                initrd /boot/initrd.img
            }
        """),
        [(os.path.join(target, "boot", "vmlinuz"), "boot/vmlinuz")],
        "SMECHOS_INSTALL")

def phase_iso_install_smechvisor(target):
    log_phase("iso-smechvisor", "Build SmechVisor install ISO")
    work = os.path.join(BUILD_TMP, "iso-smechvisor")
    shutil.rmtree(work, ignore_errors=True)
    _grub_mkrescue("/tmp/smechvisor-install.iso", work,
        textwrap.dedent("""\
            set timeout=5
            set default=0
            menuentry "Install SmechVisor" {
                linux /boot/vmlinuz quiet loglevel=3
                initrd /boot/initrd.img
            }
        """),
        [(os.path.join(target, "boot", "vmlinuz"), "boot/vmlinuz")],
        "SMECHVISOR_INSTALL")

def phase_iso_shim(target):
    log_phase("iso-shim", "Build SmechVisor deploy shim ISO")
    # The shim binary is built separately as the smechvisor-shim Rust crate
    shim_crate = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "repo-packs-smechvisor-shim"
    )
    shim_bin = os.path.join(shim_crate, "target", "release", "smechvisor-shim")
    if not os.path.exists(shim_bin):
        log("Building smechvisor-shim Rust crate...")
        run(["cargo", "build", "--release"], cwd=shim_crate)

    work = os.path.join(BUILD_TMP, "iso-shim")
    shutil.rmtree(work, ignore_errors=True)
    shim_dir = os.path.join(work, "shim")
    ensure(shim_dir)

    init = os.path.join(shim_dir, "smechvisor-shim-init")
    with open(init, "w") as f:
        f.write(textwrap.dedent("""\
            #!/bin/sh
            mount -t proc  none /proc
            mount -t sysfs none /sys
            mount -t devtmpfs none /dev
            mkdir -p /dev/pts /dev/shm /mnt/target
            echo "nameserver 1.1.1.1" > /etc/resolv.conf
            dhcpcd -t 20 2>/dev/null &
            exec /shim/smechvisor-shim
        """))
    os.chmod(init, 0o755)

    _grub_mkrescue("/tmp/smechvisor-deploy-shim.iso", work,
        textwrap.dedent("""\
            set timeout=0
            set default=0
            menuentry "SmechVisor Deploy Shim" {
                linux /boot/vmlinuz init=/shim/smechvisor-shim-init quiet
                initrd /boot/initrd.img
            }
        """),
        [
            (os.path.join(target, "boot", "vmlinuz"), "boot/vmlinuz"),
            (shim_bin, "shim/smechvisor-shim"),
        ],
        "SMECHVISOR_SHIM")

def phase_iso_live_smechos(target):
    """Build a SmechOS KDE Plasma live ISO (squashfs + overlayfs + Calamares)."""
    log_phase("iso-live", "Build SmechOS KDE Plasma live ISO")
    squashfs_path = "/tmp/smechos-filesystem.squashfs"
    iso_path      = "/tmp/smechos-plasma-live.iso"
    work          = os.path.join(BUILD_TMP, "iso-live")
    shutil.rmtree(work, ignore_errors=True)

    live_dir = os.path.join(work, "live")
    ensure(live_dir)

    # Squashfs the target root (exclude /boot — kernel lives separately in the ISO)
    log("Creating squashfs of root filesystem (this takes a while)...")
    if os.path.exists(squashfs_path):
        os.remove(squashfs_path)
    mksquashfs = shutil.which("mksquashfs")
    if not mksquashfs:
        err("mksquashfs not found. Install squashfs-tools.")
    run([mksquashfs, target, squashfs_path,
         "-comp", "xz", "-Xdict-size", "100%",
         "-e", os.path.join(target, "boot"),
         "-noappend"])
    shutil.copy2(squashfs_path, os.path.join(live_dir, "filesystem.squashfs"))

    grub_cfg = textwrap.dedent("""\
        set timeout=10
        set default=0

        menuentry "SmechOS KDE Plasma (Live)" {
            linux  /boot/vmlinuz boot=live quiet splash loglevel=3
            initrd /boot/live-initrd.img
        }
        menuentry "SmechOS KDE Plasma (Live, nomodeset)" {
            linux  /boot/vmlinuz boot=live nomodeset quiet loglevel=3
            initrd /boot/live-initrd.img
        }
        menuentry "Install SmechOS (Calamares)" {
            linux  /boot/vmlinuz boot=live calamares=1 quiet loglevel=3
            initrd /boot/live-initrd.img
        }
    """)

    _grub_mkrescue(iso_path, work, grub_cfg,
        [
            (os.path.join(target, "boot", "vmlinuz"),       "boot/vmlinuz"),
            (os.path.join(target, "boot", "live-initrd.img"), "boot/live-initrd.img"),
        ],
        "SMECHOS_LIVE")
    log(f"SmechOS Plasma Live ISO ready: {iso_path}", color=GREEN)

# ── Build profiles ────────────────────────────────────────────────────────────

SMECHOS_PHASES = [
    ("musl",             phase_bootstrap_musl,     "Bootstrap musl libc + toolchain shim"),
    ("userland",         phase_bootstrap_userland, "Bootstrap GNU userland against musl"),
    ("etc",              phase_write_etc,           "Write /etc skeleton"),
    ("openrc",           phase_openrc,              "Deploy OpenRC"),
    ("inittab",          phase_inittab,             "Write inittab"),
    ("grub",             phase_grub,                "Compile GRUB 2.12 EFI + BIOS"),
    ("qt-deps",          phase_qt_deps,             "Compile Qt6 modules"),
    ("mesa",             phase_mesa,                "Compile Mesa stack"),
    ("cmake-bootstrap",  phase_cmake_bootstrap,     f"Bootstrap CMake {CMAKE_BOOTSTRAP_VER}"),
    ("kde",              phase_kde,                 "Compile KDE Frameworks + Plasma"),
    ("plasma-configure", phase_plasma_configure,    "Configure Plasma/SDDM session"),
    ("kwin-deps",        phase_kwin_deps,           "Copy KWin dependencies"),
    ("qt6uitools",       phase_qt6uitools,          "Ensure Qt6UITools present"),
    ("kernel",           phase_kernel,              "Compile Linux 6.12.16"),
    ("patch-metadata",   phase_patch_metadata,      "Patch metadata for SmechOS branding"),
    ("discover",         phase_plasma_discover,     "Compile Plasma Discover + PackageKit"),
    ("bundle",           phase_bundle_packages,     "Bundle output into spk-installable .tar.xz packages"),
]

SMECHVISOR_PHASES = [
    ("musl",        phase_bootstrap_musl,       "Bootstrap musl libc + toolchain shim"),
    ("userland",    phase_bootstrap_userland,   "Bootstrap GNU userland against musl"),
    ("etc",         phase_write_etc,            "Write /etc skeleton"),
    ("openrc",      phase_openrc,               "Deploy OpenRC"),
    ("inittab",     phase_inittab,              "Write inittab"),
    ("kernel",      phase_kernel,               "Compile Linux 6.12.16"),
    ("grub",        phase_grub,                 "Compile GRUB 2.12 EFI + BIOS"),
    ("smechvisord", phase_install_smechvisord,  "Install smechvisord + OpenRC service"),
]

SMECHOS_PLASMA_LIVE_PHASES = [
    ("userland-glibc",  phase_bootstrap_userland_glibc, "Bootstrap GNU userland against host glibc"),
    ("etc",             phase_write_etc,                 "Write /etc skeleton"),
    ("systemd",         phase_systemd,                   "Install systemd from Debian packages"),
    ("systemd-config",  phase_systemd_configure,         "Configure systemd for KDE Plasma desktop"),
    ("grub",            phase_grub,                      "Compile GRUB 2.12 EFI + BIOS"),
    ("qt-deps",         phase_qt_deps,                   "Compile Qt6 modules"),
    ("mesa",            phase_mesa,                      "Compile Mesa stack"),
    ("cmake-bootstrap",    phase_cmake_bootstrap,        f"Bootstrap CMake {CMAKE_BOOTSTRAP_VER}"),
    ("wayland",            phase_wayland,                f"Build wayland {WAYLAND_VER}"),
    ("wayland-protocols",  phase_wayland_protocols,      f"Build wayland-protocols {WAYLAND_PROTO_VER}"),
    ("libinput",           phase_libinput,               f"Build libinput {LIBINPUT_VER}"),
    # libeis skipped: gitlab releases require auth; not in kwin's REQUIRED list (EIS feature optional)
    ("kde",                phase_kde,                    "Compile KDE Frameworks + Plasma"),
    ("plasma-configure",phase_plasma_configure,          "Configure Plasma/SDDM session"),
    ("kwin-deps",       phase_kwin_deps,                 "Copy KWin dependencies"),
    ("qt6uitools",      phase_qt6uitools,                "Ensure Qt6UITools present"),
    ("kernel",          phase_kernel,                    "Compile Linux 6.12.16"),
    ("patch-metadata",  phase_patch_metadata,            "Patch metadata for SmechOS branding"),
    ("discover",        phase_plasma_discover,           "Compile Plasma Discover + PackageKit"),
    ("calamares",       phase_calamares,                 "Build Calamares graphical installer"),
    ("chrome",          phase_google_chrome,             "Install Google Chrome stable"),
    ("live-initramfs",  phase_live_initramfs,            "Build busybox live initramfs"),
    ("bundle",          phase_bundle_packages,           "Bundle output into spk-installable .tar.xz packages"),
]

PROFILES = {
    "smechos":            SMECHOS_PHASES,
    "smechvisor":         SMECHVISOR_PHASES,
    "smechos-plasma-live": SMECHOS_PLASMA_LIVE_PHASES,
}

ISO_BUILDERS = {
    ("smechos",             "install"): phase_iso_install_smechos,
    ("smechvisor",          "install"): phase_iso_install_smechvisor,
    ("smechvisor",          "shim"):    phase_iso_shim,
    ("smechos-plasma-live", "live"):    phase_iso_live_smechos,
}

# ── CLI ───────────────────────────────────────────────────────────────────────

def cmd_list(profile):
    phases = PROFILES[profile]
    print(f"\n{BOLD}Phases for '{profile}':{R}\n")
    for i, (name, _, desc) in enumerate(phases, 1):
        print(f"  {GREEN}{i:2}. {name:<22}{R} {desc}")
    print()

def cmd_build(profile, target, only_phase=None):
    global PLASMA_VER, KF6_VER, PLASMA_URL, KF6_URL, _USE_GLIBC
    # Always resolve KDE versions from the mirror so the build never uses EoL releases
    _plasma_ver, _kf6_minor, _kf6_ver = _resolve_kde_versions()
    PLASMA_VER = _plasma_ver
    KF6_VER    = _kf6_ver
    PLASMA_URL = f"https://download.kde.org/stable/plasma/{PLASMA_VER}"
    KF6_URL    = f"https://download.kde.org/stable/frameworks/{_kf6_minor}"
    # smechos-plasma-live uses glibc/gcc; other profiles use musl
    _USE_GLIBC = (profile == "smechos-plasma-live")

    phases = PROFILES[profile]
    ensure(BUILD_TMP)
    if only_phase:
        matches = [(n, fn, d) for n, fn, d in phases if n == only_phase]
        if not matches:
            err(f"Unknown phase '{only_phase}' for '{profile}'. "
                f"Valid: {[n for n,_,_ in phases]}")
        phases = matches

    start = time.time()
    print(f"\n{MAGENTA}{BOLD}{'='*64}{R}")
    print(f"{MAGENTA}{BOLD}  spk-compile v{VERSION}  |  {profile}  |  target: {target}{R}")
    print(f"{MAGENTA}{BOLD}  KDE Plasma {PLASMA_VER}  |  KF6 {KF6_VER}{R}")
    print(f"{MAGENTA}{BOLD}{'='*64}{R}\n")

    for name, fn, desc in phases:
        if not only_phase and _phase_done(profile, name):
            log(f"'{name}' already built — skipping (delete {STAMP_DIR}/{profile}-{name}.done to rebuild)", color=YELLOW)
            continue
        t0 = time.time()
        fn(target)
        _mark_done(profile, name)
        log(f"'{name}' done in {time.time()-t0:.1f}s", color=GREEN)

    log(f"BUILD COMPLETE: {profile} in {time.time()-start:.0f}s", color=GREEN)

def cmd_iso(profile, iso_type, target):
    key = (profile, iso_type)
    if key not in ISO_BUILDERS:
        err(f"No ISO builder for '{profile} --iso {iso_type}'. "
            f"Valid: {[f'{p}/{t}' for p,t in ISO_BUILDERS]}")
    ensure(BUILD_TMP)
    ISO_BUILDERS[key](target)

def main():
    parser = argparse.ArgumentParser(
        prog="spk-compile",
        description="SmechOS/SmechVisor sovereign build orchestrator (Project SmechDeployV2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 spk-compile.py smechos
              python3 spk-compile.py smechvisor
              python3 spk-compile.py smechos-plasma-live
              python3 spk-compile.py smechos --phase kde
              python3 spk-compile.py smechos --iso install
              python3 spk-compile.py smechvisor --iso shim
              python3 spk-compile.py smechos-plasma-live --iso live
              python3 spk-compile.py --list smechos
        """))

    parser.add_argument("profile", nargs="?", choices=list(PROFILES))
    parser.add_argument("--target", default=DEFAULT_TARGET,
                        help=f"Target root (default: {DEFAULT_TARGET})")
    parser.add_argument("--phase", metavar="PHASE")
    parser.add_argument("--iso",   metavar="TYPE", choices=["install", "shim", "live"])
    parser.add_argument("--list",  metavar="PROFILE", choices=list(PROFILES),
                        dest="list_profile")
    parser.add_argument("--version", action="store_true")

    args = parser.parse_args()

    if args.version:
        print(f"spk-compile {VERSION}")
        sys.exit(0)
    if args.list_profile:
        cmd_list(args.list_profile)
        sys.exit(0)
    if not args.profile:
        parser.print_help()
        sys.exit(1)
    if args.iso:
        cmd_iso(args.profile, args.iso, args.target)
    else:
        cmd_build(args.profile, args.target, only_phase=args.phase)

if __name__ == "__main__":
    main()
