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
import json
import os
import sys
import subprocess
import shutil
import urllib.request
import time
import textwrap
import struct
import zlib

# ── Version & constants ───────────────────────────────────────────────────────

VERSION = "2.2.39"
DEFAULT_TARGET = "/mnt/smechos_build_root"
# Disk-backed, NOT /tmp: this host's /tmp is a 6.8G tmpfs (RAM-backed), and
# a heavy parallel Qt6/KDE compile blows straight through that -- hit real
# "Disk quota exceeded" errors on GCC's own .s/PCH temp files mid-build.
# /mnt has 489G of real disk free. See also TMPDIR in build_env()/
# build_env_glibc() -- BUILD_TMP alone doesn't cover GCC's OWN internal
# temp files (those follow $TMPDIR, independently of where source is
# extracted/built).
BUILD_TMP  = "/mnt/smechos_build_tmp"
STAMP_DIR  = "/mnt/spk-compile-sources/.stamps"  # persistent across reboots

# Source versions
LINUX_VER      = "6.12.16"
GRUB_VER       = "2.12"
MUSL_VER       = "1.2.5"
QT6_VER        = "6.10.3"
# SmechOS tracks the "bullet-proof KDE" LTS line (Kubuntu Focus +
# Techpaladin Software + KDE e.V., announced August 2026 -- 3 years of
# backported fixes across Plasma/Frameworks/Gear, through ~2029) instead
# of chasing the newest Plasma release every cycle. See _resolve_kde_versions
# for why: that function enforces this pin at build time, not just these
# two defaults. PLASMA_VER/KF6_VER below are the last-known-good point
# release within the pinned line, refreshed by that resolver; the LTS_MINOR
# constants are the actual pin and only change on a deliberate LTS-line move.
PLASMA_LTS_MINOR = "6.6"
KF6_LTS_MINOR    = "6.24"
PLASMA_VER     = "6.6.6"
KF6_VER        = "6.24.0"
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
BITCOIN_VER       = "28.0"
SGMINER_VER       = "5.6.1"

# Download URLs (KDE URLs are resolved dynamically at build time — see _resolve_kde_versions)
LINUX_URL    = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{LINUX_VER}.tar.xz"
GRUB_URL     = f"https://ftp.gnu.org/gnu/grub/grub-{GRUB_VER}.tar.xz"
MUSL_URL     = f"https://musl.libc.org/releases/musl-{MUSL_VER}.tar.gz"
QT6_MINOR    = ".".join(QT6_VER.split(".")[:2])
# download.qt.io is IPv4-only with no reachable NAT64 path from the build
# host; ftp.fau.de mirrors the same tree over genuine dual-stack IPv6.
QT6_BASE_URL = f"https://ftp.fau.de/qtproject/official_releases/qt/{QT6_MINOR}/{QT6_VER}/submodules"
MESA_URL     = f"https://mesa.freedesktop.org/archive/mesa-{MESA_VER}.tar.xz"
WAYLAND_PROTO_URL = f"https://gitlab.freedesktop.org/wayland/wayland-protocols/-/archive/{WAYLAND_PROTO_VER}/wayland-protocols-{WAYLAND_PROTO_VER}.tar.gz"
WAYLAND_URL       = f"https://gitlab.freedesktop.org/wayland/wayland/-/archive/{WAYLAND_VER}/wayland-{WAYLAND_VER}.tar.gz"
LIBINPUT_URL      = f"https://gitlab.freedesktop.org/libinput/libinput/-/archive/{LIBINPUT_VER}/libinput-{LIBINPUT_VER}.tar.gz"
LIBEIS_URL        = f"https://gitlab.freedesktop.org/libeis/libeis/-/releases/{LIBEIS_VER}/downloads/libeis-{LIBEIS_VER}.tar.xz"
OPENRC_URL   = f"https://github.com/OpenRC/openrc/archive/refs/tags/{OPENRC_VER}.tar.gz"
BITCOIN_URL  = f"https://bitcoincore.org/bin/bitcoin-core-{BITCOIN_VER}/bitcoin-{BITCOIN_VER}.tar.gz"
SGMINER_URL  = f"https://github.com/sgminer-dev/sgminer/archive/refs/tags/{SGMINER_VER}.tar.gz"
# Plasma + KF6 URLs are set by _resolve_kde_versions() before each build
PLASMA_URL   = f"https://download.kde.org/stable/plasma/{PLASMA_VER}"
KF6_URL      = f"https://download.kde.org/stable/frameworks/{KF6_LTS_MINOR}"
# KDE Gear (Konsole, Dolphin, ...) has its own release-service version series,
# independent of Plasma/KF6 -- do not conflate with PLASMA_VER.
GEAR_VER     = "25.08.3"
GEAR_URL     = f"https://download.kde.org/stable/release-service/{GEAR_VER}/src"

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

def _detect_gcc():
    """Resolve a CC/CXX pair with GCC >= 14 (needed for std::ranges::to /
    C++23 in libkscreen and other Plasma 6 packages). Hardcoding "gcc-14"
    broke on this host, which only ships "gcc" (GCC 16, newer than the
    requirement) -- no gcc-14 binary exists at all. Probe for whatever the
    host actually calls its compiler instead of assuming a specific
    versioned binary name exists.
    """
    for cc in ("gcc-14", "gcc-15", "gcc-16", "gcc-17", "gcc-18", "gcc"):
        path = shutil.which(cc)
        if not path:
            continue
        result = subprocess.run([cc, "-dumpversion"], capture_output=True, text=True)
        if result.returncode != 0:
            continue
        try:
            major = int(result.stdout.strip().split(".")[0])
        except ValueError:
            continue
        if major < 14:
            continue
        cxx = cc.replace("gcc", "g++")
        if not shutil.which(cxx):
            continue
        return cc, cxx
    err("No GCC >= 14 found (need C++23's std::ranges::to for Plasma 6 "
        "packages like libkscreen). Install a newer gcc/g++ package.")

def _resolve_kde_versions():
    """Query download.kde.org and return (plasma_ver, kf6_minor, kf6_ver).

    SmechOS tracks the "bullet-proof KDE" LTS line (Kubuntu Focus +
    Techpaladin Software + KDE e.V., announced August 2026: 3 years of
    backported fixes for Plasma 6.6 / Frameworks 6.24 / Gear 25.12,
    through ~2029) rather than whatever Plasma/Frameworks shipped most
    recently. The whole point of anchoring to an LTS line instead of
    chasing latest is stability for users, and Techpaladin's own CI-backed
    hardware validation -- so this resolves the newest *point release
    within that line* (e.g. 6.6.6 -> 6.6.7 as Techpaladin backports land),
    never jumping to 6.7+/6.8+. Bump PLASMA_LTS_MINOR deliberately if
    SmechOS ever moves to a newer LTS line; don't let this silently track
    latest again, which is what it did before and is exactly what an LTS
    pin exists to avoid.
    """
    import re
    log(f"Resolving latest {PLASMA_LTS_MINOR}.x LTS point release from download.kde.org...", )

    def _fetch(url):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                return r.read().decode()
        except Exception as e:
            err(f"Could not reach {url}: {e}")

    # Plasma: directory listing gives x.y.z/ entries -- keep only the
    # pinned LTS minor line, then take the highest patch within it.
    all_plasma_vers = re.findall(r'href="([0-9]+\.[0-9]+\.[0-9]+)/"',
                                  _fetch("https://download.kde.org/stable/plasma/"))
    plasma_vers = [v for v in all_plasma_vers if v.startswith(f"{PLASMA_LTS_MINOR}.")]
    if not plasma_vers:
        err(f"No {PLASMA_LTS_MINOR}.x release found under download.kde.org/stable/plasma/ "
            f"-- has the LTS line reached end-of-life?")
    plasma_ver = sorted(plasma_vers, key=lambda v: [int(x) for x in v.split(".")])[-1]

    # KF6 doesn't point-release the way Plasma does -- one tarball set per
    # X.Y -- so the minor is just the pinned LTS value directly, not
    # resolved from a directory listing.
    kf6_minor = KF6_LTS_MINOR

    # KF6 full version: parse from a known filename inside the minor directory
    kf6_listing = _fetch(f"https://download.kde.org/stable/frameworks/{kf6_minor}/")
    full = re.findall(rf'extra-cmake-modules-([0-9]+\.[0-9]+\.[0-9]+)\.tar', kf6_listing)
    kf6_ver = full[0] if full else f"{kf6_minor}.0"

    log(f"KDE Plasma {plasma_ver} (LTS {PLASMA_LTS_MINOR}.x)  |  KDE Frameworks {kf6_ver}", color=GREEN)
    return plasma_ver, kf6_minor, kf6_ver

def _in_matching_build_image():
    """True when this process is itself running inside the Ubuntu-24.04-ABI
    build container (see Dockerfile.build in the sde-shell/SmechDeploy repo,
    and build-one.sh's identical check) rather than directly on some
    arbitrary host. Phases that would otherwise spin up a throwaway podman
    container just to get a matching-ABI toolchain (phase_xwayland_deps,
    phase_locale) can skip that and act directly once this is true.
    """
    return os.path.exists("/etc/smechos-build-image")

def nproc():
    """Parallel job count for compiler invocations. Defaults to all cores;
    override with SMECH_BUILD_JOBS to leave headroom for whatever else is
    running on the machine during a build (a GPU-heavy app, a browser,
    etc.) instead of a compile job flood starving it of RAM/CPU."""
    override = os.environ.get("SMECH_BUILD_JOBS")
    if override:
        return override
    return str(os.cpu_count() or 4)

def ensure(path):
    """Create path, escalating via sudo if a parent (e.g. a bare-mounted
    /mnt) is root-owned. Every other phase in this script does plain,
    unprivileged file I/O once a directory exists, so a sudo-created dir is
    chowned back to the invoking user rather than left root-owned -- leaving
    it root-owned would just move this same PermissionError one level
    deeper, into the first plain open()/write() call inside it.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except PermissionError:
        if os.geteuid() == 0:
            raise
        subprocess.run(["sudo", "mkdir", "-p", path], check=True)
        subprocess.run(["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", path], check=True)

def symlink(src, dst):
    """os.symlink() with the same sudo-fallback as ensure() -- a parent
    directory can end up root-owned (e.g. created by an earlier phase's
    `sudo make install`) even when ensure() itself didn't need to escalate
    for it (os.makedirs is a no-op on an already-existing dir, so it never
    reveals whether that dir is actually writable). Hit this exact class of
    bug on phase_systemd_configure's default.target symlink.
    """
    try:
        os.symlink(src, dst)
    except PermissionError:
        if os.geteuid() == 0:
            raise
        subprocess.run(["sudo", "ln", "-sf", src, dst], check=True)
        subprocess.run(["sudo", "chown", "-h", f"{os.getuid()}:{os.getgid()}", dst], check=True)

def run(cmd, cwd=None, env=None, sudo=False, check=True):
    if sudo and os.geteuid() != 0:
        # Plain "sudo <cmd>" resets the environment by default (sudoers
        # env_reset), silently dropping whatever `env=` was passed here —
        # e.g. DESTDIR, which would otherwise make an install phase write
        # straight to the real host /usr instead of the staging root.
        # Route explicit env vars through the target-side `env` binary
        # (invoked post-privilege-escalation) so they survive regardless of
        # the sudoers env_reset/env_keep configuration.
        if env is not None:
            passthrough = {k: v for k, v in env.items() if os.environ.get(k) != v}
            cmd = ["sudo", "env"] + [f"{k}={v}" for k, v in passthrough.items()] + list(cmd)
        else:
            cmd = ["sudo"] + list(cmd)
    log(f"$ {' '.join(str(c) for c in cmd)}", color=R)
    result = subprocess.run(list(cmd), cwd=cwd, env=env)
    if check and result.returncode != 0:
        err(f"Command failed (exit {result.returncode}): {' '.join(str(c) for c in cmd)}")
    return result

def download(url, dest, retries=5, backoff=(5, 15, 30, 60, 60)):
    if os.path.exists(dest):
        log(f"Cached: {os.path.basename(dest)}")
        return
    ensure(os.path.dirname(dest))
    # KDE's download.kde.org (and similar Mirrorbits-fronted hosts) 302s to
    # a randomly geo/load-selected mirror per request -- most are fine, but
    # an occasional pick is unreachable from this specific IPv6-only host
    # and hangs until the OS-level TCP timeout (seen consistently as a
    # ~110s "Connection timed out"). A bare retry of the *same* URL often
    # lands on a different mirror next time (no URL rewriting needed), so
    # retry with backoff rather than failing the whole multi-hour build
    # over one bad mirror pick.
    part = dest + ".part"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            log(f"Downloading {os.path.basename(dest)}"
                + (f" (attempt {attempt}/{retries})" if attempt > 1 else "") + "...")
            urllib.request.urlretrieve(url, part)
            os.rename(part, dest)
            log(f"Saved: {dest}", color=GREEN)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            if os.path.exists(part):
                os.remove(part)
            if attempt < retries:
                delay = backoff[min(attempt - 1, len(backoff) - 1)]
                log(f"Download failed ({e}) -- retrying in {delay}s", color=YELLOW)
                time.sleep(delay)
    err(f"download failed after {retries} attempts: {url} ({last_err})")

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
    # GCC's own internal temp files (.s, PCH) follow $TMPDIR, independent of
    # BUILD_TMP -- route them off the tiny tmpfs /tmp too. See BUILD_TMP.
    ensure(BUILD_TMP)
    e["TMPDIR"] = BUILD_TMP
    return e

def build_env_glibc(target):
    """Like build_env() but uses system glibc/gcc instead of musl-gcc."""
    e = dict(os.environ)
    e["SMECH_TARGET"] = target
    e.pop("TARGET", None)
    # GCC >= 14 required for std::ranges::to (C++23) in libkscreen and other
    # Plasma 6 packages. Probed rather than hardcoded -- see _detect_gcc().
    e["CC"], e["CXX"] = _detect_gcc()
    prefix = f"{target}/usr"
    e["PATH"] = f"{prefix}/bin:{e.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    e["PKG_CONFIG_PATH"] = (
        f"{prefix}/lib/x86_64-linux-gnu/pkgconfig:{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig:"
        "/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig:/usr/lib/pkgconfig"
    )
    # -std=gnu17 (C only, NOT CXXFLAGS): GCC 14 switched its DEFAULT C
    # dialect from gnu17 to gnu23, and that switch (not C89-vs-C99 age) is
    # what broke bash's mkbuiltins.c -- gnu23 strictly checks old K&R
    # empty-parens prototypes like `write_documentation();` as "zero
    # arguments". -std=gnu89 "fixed" that but overcorrected: it also
    # disallows C99 features (coreutils' `for (int i = ...)` loop-variable
    # declarations), which regressed a different package. gnu17 -- GCC's
    # own pre-14 default -- is the actually-correct middle ground: fully
    # C99/C11-featured (coreutils is fine) without gnu23's new stricter
    # K&R-prototype checking (bash is fine). Scoped to C only because
    # C++23 strictness is the entire reason GCC>=14 is required here --
    # loosening CXXFLAGS the same way would undermine that.
    e["CFLAGS"]   = f"-I{prefix}/include -std=gnu17 -fpermissive"
    e["CXXFLAGS"] = f"-I{prefix}/include"
    e["LDFLAGS"]  = f"-L{prefix}/lib/x86_64-linux-gnu -L{prefix}/lib"
    e["LD_LIBRARY_PATH"] = f"{prefix}/lib/x86_64-linux-gnu:{prefix}/lib"
    # kdoctools' meinproc6 (a real, target-installed but container-executed
    # binary, invoked un-chrooted like everything else in this build) looks
    # up its own DTD/catalog files via QStandardPaths::GenericDataLocation,
    # which is driven entirely by XDG_DATA_DIRS -- left unset, it defaults
    # to the container's own /usr/share, which never has kdoctools' files
    # installed (only {target}/usr/share does), so any later package that
    # builds documentation through kdoctools (kpackage was first to hit
    # this) dies with "Could not find kdoctools catalogs". Prepending the
    # target's share dir fixes the lookup without needing an actual chroot.
    e["XDG_DATA_DIRS"] = f"{prefix}/share:" + e.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    e["FORCE_UNSAFE_CONFIGURE"] = "1"
    # GCC's own internal temp files (.s, PCH) follow $TMPDIR, independent of
    # BUILD_TMP -- route them off the tiny tmpfs /tmp too. See BUILD_TMP.
    ensure(BUILD_TMP)
    e["TMPDIR"] = BUILD_TMP
    return e

# Set to True by cmd_build when the active profile uses glibc instead of musl.
_USE_GLIBC = False

def active_env(target):
    """Return the right build environment for the currently running profile."""
    return build_env_glibc(target) if _USE_GLIBC else build_env(target)

def build_env_bitcoin(target):
    """build_env_glibc() with a conservative CPU baseline for old LGA775
    Pentium/Celeron chips (Core-derived budget SKUs, no SSE4.2/AVX).

    glibc rather than musl here, despite the rest of this profile's lean
    OpenRC lineage -- Bitcoin Core and GPU miners (cgminer/sgminer) are
    both far more commonly built and tested against glibc; musl support
    for either is a much less-trodden path this pipeline has no way to
    validate without a real build+boot cycle on the target hardware.
    """
    e = build_env_glibc(target)
    baseline = "-march=core2 -mtune=generic"
    e["CFLAGS"]   = f"{baseline} {e['CFLAGS']}"
    e["CXXFLAGS"] = f"{baseline} {e['CXXFLAGS']}"
    return e

def _split_staging_prefix(prefix):
    # Every caller passes prefix as exactly "<target>/usr" — the physical
    # staging location. CMAKE_INSTALL_PREFIX/--prefix must instead be the
    # *final runtime* prefix ("/usr"), with the staging root applied only at
    # install time via DESTDIR. Baking the staging path itself into
    # CMAKE_INSTALL_PREFIX/--prefix bakes the absolute build-root path into
    # RPATH/RUNPATH, compiled-in string constants, and installed unit files
    # (systemd, dbus, etc.) — those paths don't exist at boot on the real
    # target, causing "cannot open shared object file" / "No such file or
    # directory" failures at runtime.
    if not prefix.endswith("/usr"):
        raise ValueError(f"expected prefix ending in /usr, got: {prefix}")
    return prefix[: -len("/usr")]  # staging root ("target"), real prefix is always "/usr"

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
    target_root = _split_staging_prefix(prefix)
    run([cmake_bin, src_dir,
         "-G", "Ninja",
         "-DCMAKE_INSTALL_PREFIX=/usr",
         "-DCMAKE_BUILD_TYPE=Release",
         # KDE's ECM/KDECMakeSettings modules default to baking the build-time
         # library search path (derived from CMAKE_PREFIX_PATH, i.e. the
         # staging root) into each installed binary's RPATH. Since
         # CMAKE_INSTALL_PREFIX is now correctly "/usr" rather than the
         # staging path, that auto-derived RPATH would otherwise still leak
         # the staging root in — same failure mode as an unfixed
         # CMAKE_INSTALL_PREFIX, just via a different mechanism. Force the
         # install RPATH to the real runtime lib dir instead.
         "-DCMAKE_SKIP_BUILD_RPATH=FALSE",
         "-DCMAKE_BUILD_WITH_INSTALL_RPATH=FALSE",
         "-DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE",
         "-DCMAKE_INSTALL_RPATH=/usr/lib/x86_64-linux-gnu",
         # This rootfs's Qt6 was built with QT_INSTALL_PLUGINS=/usr/plugins
         # (non-multiarch), not the lib/<triplet>/plugins path KDE's ECM/
         # KDECMakeSettings computes by default from CMAKE_INSTALL_LIBDIR.
         # Without this override, KDE packages install their Qt plugins
         # (KCMs, kpart, applets, ...) to a directory Qt's plugin loader
         # never scans -- they build and link fine, and are simply invisible
         # at runtime (e.g. a KCM that never appears in System Settings).
         "-DKDE_INSTALL_PLUGINDIR=/usr/plugins",
         # Same mismatch, same fix, for QML modules: this Qt6 expects its
         # standard non-multiarch QT_INSTALL_QML=/usr/qml (confirmed by
         # ecm_find_qmlmodule()/find_package(...-QMLModule) searching
         # exactly {prefix}/qml -- not the lib/<triplet>/qml path ECM's
         # KDECMakeSettings computes by default from CMAKE_INSTALL_LIBDIR).
         # Without this, Kirigami (and every other KF6 package shipping
         # QML, e.g. kdeclarative/ksvg/plasma-framework) installs its QML
         # files somewhere Qt's own module lookup never checks, so every
         # later find_package(Foo-QMLModule) for it fails outright.
         "-DKDE_INSTALL_QMLDIR=/usr/qml",
         ] + (extra_args or []), cwd=bd, env=build_env)
    run([ninja_bin, "-j", nproc(), "-k", "0"], cwd=bd, env=build_env, check=False)
    install_env = dict(build_env)
    install_env["DESTDIR"] = target_root
    run([cmake_bin, "--install", bd], env=install_env, sudo=(os.geteuid() != 0))

def meson_install(src_dir, prefix, extra_args=None, env=None, build_dir=None):
    bd = build_dir or os.path.join(src_dir, "build")
    if os.path.exists(bd):
        shutil.rmtree(bd)
    target_root = _split_staging_prefix(prefix)
    # meson/ninja are HOST-side orchestration tools (meson probes compilers/
    # pkg-config; ninja is itself a dynamically-linked binary that has to
    # start up before it can spawn anything) -- none of them may inherit
    # LD_LIBRARY_PATH/PATH/PKG_CONFIG_PATH pointing at the target's own
    # dirs, or the host's tools try to resolve THEIR OWN shared libraries
    # (libpython, ninja's own libc dependency, etc.) against whatever
    # happens to already exist in target/usr/lib -- which can be an
    # incompatible version (hit this when target was pre-populated from an
    # existing built rootfs rather than built fresh; a from-scratch build
    # never has its own target/usr/lib/.../libc.so.6 yet by the time these
    # tools first run, which is why this was latent rather than a
    # from-scratch-build bug -- first hit as a meson/python crash, then as
    # meson's own pkg-config/cmake subprocess probes picking up stale
    # target-bundled configs, then as ninja itself crashing on startup).
    # meson_install() was originally only used for low-level meson-based C
    # libraries (Mesa, Wayland, libinput) that don't need to execute
    # target-built codegen tools mid-build the way Qt6/KDE's separate CMake
    # path does -- AppStream's -Dqt=true build broke that assumption (needs
    # moc/uic/rcc/lrelease, which only exist in our self-built target Qt6,
    # never in the container). Appending (not prepending) the target's Qt6
    # tool dirs to PATH lets meson find those specific tools as a fallback
    # without a host tool of the same name ever losing to a target one.
    # It's safe to actually *run* them despite LD_LIBRARY_PATH staying
    # stripped below: verified their RUNPATH is
    # "/usr/lib/x86_64-linux-gnu:$ORIGIN/../lib" -- the first (absolute,
    # broken when un-chrooted) entry just falls through to the second
    # ($ORIGIN-relative, i.e. real relative to the binary's own location on
    # disk) for every library, so they resolve their own Qt6 .so files
    # correctly with no extra environment at all.
    host_env = dict(env) if env else dict(os.environ)
    host_env.pop("LD_LIBRARY_PATH", None)
    host_env["PATH"] = (
        os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        + f":{prefix}/bin:{prefix}/libexec")
    # PKG_CONFIG_PATH (unlike PATH/LD_LIBRARY_PATH above) is safe to point at
    # the target prefix too: pkg-config only ever reads .pc text files, it
    # never dynamically links against what they describe, so this can't
    # trigger the host-tool-crash scenario the comment above warns about.
    # It has to include the target prefix, though -- packages built earlier
    # in this same phase by meson_install() itself (wayland, wayland-protocols)
    # land their .pc files only under the target staging root, never in any
    # apt-installed/host location, so later meson-based packages that
    # legitimately depend on them (e.g. xkbcommon's wayland support needing
    # wayland-client/wayland-protocols) can't find them without this.
    host_env["PKG_CONFIG_PATH"] = (
        f"{prefix}/lib/x86_64-linux-gnu/pkgconfig:{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig:"
        + os.environ.get(
            "PKG_CONFIG_PATH",
            "/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig:/usr/lib/pkgconfig"))
    # PKG_CONFIG_SYSROOT_DIR was tried here and reverted: it globally
    # prepends the sysroot to *every* resolved .pc file's absolute paths,
    # which fixed Qt6's own .pc files (their Cflags/-I bakes in the real
    # deployment prefix "/usr", not "{target}/usr" -- unlike CMake's
    # relocatable _IMPORT_PREFIX pattern, .pc files aren't relocatable this
    # way) but broke gobject-introspection-1.0.pc, whose g_ir_scanner tool
    # variable legitimately points at the *container's* own real /usr
    # (apt-installed there on purpose, see Dockerfile.build). PKG_CONFIG_PATH
    # mixes target-only and container-only .pc files by design, and
    # PKG_CONFIG_SYSROOT_DIR can't tell those two cases apart -- see
    # _fix_target_pc_prefix() instead, which corrects the target's own .pc
    # files in place (Qt6 specifically, so far) rather than reinterpreting
    # every .pc file's paths at lookup time.
    run(["meson", "setup", bd, src_dir,
         "--prefix=/usr", "--buildtype=release",
         ] + (extra_args or []), env=host_env)
    run(["ninja", "-C", bd, "-j", nproc()], env=host_env)
    install_env = dict(host_env)
    install_env["DESTDIR"] = target_root
    run(["ninja", "-C", bd, "install"], env=install_env, sudo=(os.geteuid() != 0))

def _fix_target_pc_prefix(target):
    """Rewrite `prefix=/usr` to `prefix={target}/usr`, but ONLY in .pc
    files belonging to packages this pipeline itself builds into the
    target (Qt6 so far) -- deliberately an include-list, not a blanket
    sweep of every .pc file under the target rootfs.

    Tried the blanket sweep first and had to revert it: the target rootfs
    (pre-populated from an earlier build, not created fresh by this
    session -- see project memory) turns out to carry a bunch of *stray*
    .pc files left over from that prior population (glib-2.0.pc,
    libxml-2.0.pc, gobject-introspection-1.0.pc, likely more) that don't
    correspond to any real headers/binaries actually present under
    {target}/usr at all -- they're apt-package .pc files that only work,
    to the extent they ever did, by accident: their unmangled "prefix=/usr"
    happens to coincidentally match the *container's* real installation of
    the same package (installed there deliberately, see Dockerfile.build),
    not the target's. Rewriting those breaks that accidental-but-working
    resolution and points them at target paths with nothing real behind
    them (confirmed: /mnt/smechos_build_root/usr/include/glib-2.0 doesn't
    exist). Qt6's own .pc files are different -- genuinely built by this
    pipeline (phase_qt_deps -> cmake_install(), --prefix=/usr per
    _split_staging_prefix's staging convention) with real matching headers
    on disk under target, so only those get rewritten.

    Unlike CMake's exports (relocatable via _IMPORT_PREFIX computed from
    the file's own on-disk location), pkg-config .pc files have no
    equivalent mechanism; PKG_CONFIG_SYSROOT_DIR was tried as a
    lookup-time alternative (see meson_install()'s comment) but rejected
    for the same reason as the blanket sweep -- it can't distinguish
    target-built .pc files from container-only ones sharing the same
    PKG_CONFIG_PATH search either.
    """
    import re
    fixed = 0
    for pat in (f"{target}/usr/lib/*/pkgconfig/Qt6*.pc",
                f"{target}/usr/lib/pkgconfig/Qt6*.pc"):
        for pc in glob.glob(pat):
            with open(pc) as f:
                txt = f.read()
            new_txt = re.sub(r"^prefix=/usr$", f"prefix={target}/usr", txt, flags=re.MULTILINE)
            if new_txt != txt:
                with open(pc, "w") as f:
                    f.write(new_txt)
                fixed += 1
    log(f"Rewrote prefix= in {fixed} target Qt6 .pc file(s) for un-chrooted pkg-config use", color=GREEN)

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
        # Read by spk (see Smech-Labs/spk's load_repos()) instead of a
        # hardcoded URL baked into the binary. pkg.smech.xyz is the
        # independent repo (Cloudflare Worker + R2); GitHub Releases stays
        # as a lower-priority fallback rather than being dropped outright.
        "spk-repo-conf.yaml": (
            "repos:\n"
            "  - name: smech-pkg\n"
            "    url: https://pkg.smech.xyz\n"
            "    priority: 1\n"
            "  - name: github-releases\n"
            "    url: https://github.com/Smech-Labs/SmechDeploy/releases/download/v1.0.0-packages\n"
            "    priority: 10\n"
        ),
        "fstab":       (
            "proc     /proc     proc    defaults  0 0\n"
            "sysfs    /sys      sysfs   defaults  0 0\n"
            "devtmpfs /dev      devtmpfs defaults 0 0\n"
        ),
        "shells":      "/bin/sh\n/bin/bash\n",
        # messagebus/systemd-{network,oom,resolve,timesync}: standard system
        # service accounts the shipped dbus-daemon system config and policy
        # files reference by name -- confirmed via a real boot as the actual
        # reason the system D-Bus never comes up at all: dbus-daemon --system
        # setuid/setgid's to "messagebus" to drop root privileges, and with
        # no such user in /etc/passwd it exits(1) immediately with "Could not
        # get UID and GID for username \"messagebus\"" (the systemd-* ones
        # only produce non-fatal "Unknown username" warnings from the policy
        # parser, but are added too since they're clearly expected). This is
        # the same root-cause class as the missing "render" group below --
        # phase_write_etc's account list was never reconciled against what
        # the actual shipped systemd/dbus config expects.
        "passwd":      (
            "root:x:0:0:root:/root:/bin/bash\n"
            "smech:x:1000:1000:SmechOS User:/home/smech:/bin/bash\n"
            "sddm:x:999:999:SDDM:/var/lib/sddm:/sbin/nologin\n"
            "messagebus:x:100:100:D-Bus Message Daemon User:/nonexistent:/sbin/nologin\n"
            "systemd-network:x:101:101:systemd Network Management:/:/sbin/nologin\n"
            "systemd-oom:x:102:102:systemd Userspace OOM Killer:/:/sbin/nologin\n"
            "systemd-resolve:x:103:103:systemd Resolver:/:/sbin/nologin\n"
            "systemd-timesync:x:105:105:systemd Time Synchronization:/:/sbin/nologin\n"
        ),
        # render group (GID 104, matching Debian/Ubuntu's udev/systemd
        # convention): the shipped 60-drm.rules udev rule sets
        # /dev/dri/renderD* to GROUP="render" MODE="0660", but with no
        # "render" group in /etc/group that GID never resolves, so the
        # render node stays root:root 600 -- confirmed directly on a real
        # boot as the reason kwin_wayland_wr immediately dumped core: smech
        # was in "video" (grants /dev/dri/card0) but had no access at all to
        # /dev/dri/renderD128, which Mesa/EGL/GBM needs for the actual
        # rendering context.
        "group":       (
            "root:x:0:\nwheel:x:10:smech\nvideo:x:14:smech\n"
            "audio:x:29:smech\nrender:x:104:smech\n"
            "messagebus:x:100:\nsystemd-network:x:101:\nsystemd-oom:x:102:\n"
            "systemd-resolve:x:103:\nsystemd-timesync:x:105:\n"
            "smech:x:1000:\nsddm:x:999:\n"
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
                    symlink(f"/etc/init.d/{svc}", dst)
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
    #
    # SQUASHFS/OVERLAY_FS: neither is in x86_64 defconfig's baseline, and
    # neither was ever explicitly forced here -- confirmed via the actual
    # compiled .config ("# CONFIG_SQUASHFS is not set", "# CONFIG_OVERLAY_FS
    # is not set") after boot-testing the live ISO hit a real kernel panic
    # ("Attempted to kill init!", Comm: switch_root): busybox's `mount -t
    # squashfs`/`mount -t overlay` in the live init script both failed
    # silently (no `set -e`), so /mnt/rootfs never became a real mountpoint,
    # and switch_root correctly refused + exited, killing PID 1. Both are
    # hard requirements for the live-boot design (squashfs holds the
    # compressed rootfs, overlay provides the writable live session layer),
    # not optional. SQUASHFS_XZ specifically because phase_iso_live_smechos
    # builds the squashfs with `-comp xz`; SQUASHFS_COMPILE_DECOMP_SINGLE
    # satisfies XZ's decompressor-backend `select`, since SQUASHFS_XZ alone
    # doesn't imply one.
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
        CONFIG_DRM_I915=y
        CONFIG_DRM_RADEON=m
        CONFIG_DRM_VIRTIO_GPU=m
        CONFIG_FW_LOADER_COMPRESS=y
        CONFIG_FW_LOADER_COMPRESS_XZ=y
        CONFIG_SQUASHFS=y
        CONFIG_SQUASHFS_XZ=y
        CONFIG_SQUASHFS_FILE_DIRECT=y
        CONFIG_SQUASHFS_COMPILE_DECOMP_SINGLE=y
        CONFIG_SQUASHFS_XATTR=y
        CONFIG_OVERLAY_FS=y
    """)
    with open(os.path.join(bd, ".config"), "a") as f:
        f.write(extras)
    run(["make", "olddefconfig"], cwd=bd, env=env)
    # -Wno-error=unused-but-set-variable: a harmless set-but-unused local in
    # drivers/gpu/drm/amd/amdgpu/amdgpu_gart.c, not a real bug, disabled
    # rather than patching source. This warning has existed forever in GCC,
    # safe unconditionally.
    #
    # -Wno-error=unterminated-string-initialization: only needed on GCC>=16,
    # which promotes unterminated ACPICA signature-array initializers
    # (include/acpi/actbl*.h) to a hard error under -Werror -- but this
    # warning was only ever *added* in GCC 15 (see gcc.gnu.org release
    # notes), and gcc -Wno-error=<X> is a hard configure-time error if
    # warning X doesn't exist in the compiler at all -- not silently
    # ignored the way -Wno-<X> would be. Inside this container the bare
    # `gcc` this build resolves is Ubuntu 24.04's default (GCC 13), so
    # unconditionally passing this flag was breaking the very first
    # compile (scripts/mod/empty.o) with "no option
    # '-Wunterminated-string-initialization'" -- gate it on the real
    # detected version instead of assuming GCC 16.
    gcc_ver_str = subprocess.run(["gcc", "-dumpversion"], capture_output=True,
                                  text=True, check=True).stdout.strip()
    gcc_major = int(gcc_ver_str.split(".")[0])
    kcflags = "-Wno-error=unused-but-set-variable"
    if gcc_major >= 15:
        kcflags += " -Wno-error=unterminated-string-initialization"
    run(["make", "-j", nproc(), f"KCFLAGS={kcflags}", "bzImage", "modules"],
        cwd=bd, env=env)

    boot = os.path.join(target, "boot")
    ensure(boot)
    shutil.copy2(os.path.join(bd, "arch/x86/boot/bzImage"),
                 os.path.join(boot, "vmlinuz"))
    run(["make", f"INSTALL_MOD_PATH={target}", "modules_install"],
        cwd=bd, env=env, sudo=(os.geteuid() != 0))
    # modules_install alone does not reliably regenerate modules.dep/modules.alias
    # for a cross-target INSTALL_MOD_PATH -- confirmed missing entirely on a real
    # build (no modules.dep at all under target/lib/modules/6.12.16). Without it,
    # udev/modprobe has no alias index, so no `=m` module (e.g. CONFIG_DRM_VIRTIO_GPU=m)
    # can ever auto-load for any PCI device, built-in drivers only. Root cause of the
    # permanently black QEMU framebuffer this session -- not a display-backend issue.
    run(["depmod", "-a", "-b", target, LINUX_VER],
        cwd=bd, env=env, sudo=(os.geteuid() != 0))
    log(f"Linux {LINUX_VER} installed.", color=GREEN)

_FW_COMPRESSED_EXTS = {".xz": "unxz -f", ".zst": "unzstd -f --rm"}

def _decompress_firmware_dir(fw_dir):
    """Decompress every compressed firmware blob in a dir in place.

    linux-firmware ships blobs compressed, but which codec depends on the
    package version -- Ubuntu 24.04's ships them as .zst (verified: all
    675 files under amdgpu/ this run), older releases used .xz. This
    kernel's config has CONFIG_FW_LOADER_COMPRESS unverified end-to-end
    (added defensively elsewhere, never build-tested this session) --
    decompressing here guarantees the firmware loads regardless of that.
    Many blobs are symlinks to a sibling compressed file (dedup for chip
    variants sharing microcode); plain unxz/unzstd refuse to follow those,
    so regular files are decompressed first, then symlinks are repointed
    at the now-decompressed target name. `shopt -s nullglob` is required
    here -- without it, a glob with zero matches (e.g. *.xz in a dir that
    turned out to be all .zst) stays literal, `[ -f "*.xz" ]` fails, and
    that failure becomes the whole command's exit status. The trailing
    `; true` matters for the same reason from the other end: the `&&`
    chain inside the loop body means the LAST glob match's test result
    is what the whole script exits with -- and the last file alphabetically
    is routinely a dedup symlink (intentionally skipped, not an error),
    which would otherwise report success as a false failure.
    """
    need_sudo = (os.geteuid() != 0)
    loop = " ".join(
        f'for f in *{ext}; do [ -f "$f" ] && [ ! -L "$f" ] && {tool} "$f"; done;'
        for ext, tool in _FW_COMPRESSED_EXTS.items()
    )
    run(["bash", "-c", f'cd "{fw_dir}" && shopt -s nullglob && {loop} true'],
        sudo=need_sudo)
    # Multi-pass symlink repointing: linux-firmware ships alias chains
    # (bare-name -> versioned-name -> another versioned name, seen on
    # iwlwifi especially) where a symlink's target is itself still an
    # unresolved compressed symlink on the first pass. Keep repointing
    # until a full pass makes no further progress, then whatever's left
    # is genuinely dangling (a superseded/alternate name the packaged
    # tree never shipped a real target for) rather than a chain we gave
    # up on too early.
    fixed = 0
    while True:
        progressed = False
        for ext in _FW_COMPRESSED_EXTS:
            for link in glob.glob(os.path.join(fw_dir, f"*{ext}")):
                if not os.path.islink(link):
                    continue
                tgt = os.readlink(link)
                newname = link[:-len(ext)]
                newtgt  = tgt[:-len(ext)] if tgt.endswith(ext) else tgt
                if os.path.exists(os.path.join(fw_dir, newtgt)):
                    run(["ln", "-sf", newtgt, newname], sudo=need_sudo)
                    run(["rm", "-f", link], sudo=need_sudo)
                    fixed += 1
                    progressed = True
        if not progressed:
            break
    broken = [p for ext in _FW_COMPRESSED_EXTS
              for p in glob.glob(os.path.join(fw_dir, f"*{ext}")) if os.path.islink(p)]
    if broken:
        # Non-fatal: these are dangling aliases already present in the
        # upstream/Ubuntu-packaged tree (e.g. a superseded chip-revision
        # name), not something this pipeline caused. Failing the whole
        # build over a handful of alternate-name symlinks for hardware
        # nobody's targeting would be disproportionate.
        log(f"{fw_dir}: {len(broken)} firmware symlink(s) still point at missing "
            f"targets (upstream alias for unshipped variant, not fatal): "
            f"{broken[:5]}", color=YELLOW)
    remaining = [p for ext in _FW_COMPRESSED_EXTS
                 for p in glob.glob(os.path.join(fw_dir, f"*{ext}"))
                 if not os.path.islink(p)]
    if remaining:
        err(f"{fw_dir}: {len(remaining)} compressed file(s) left undecompressed: "
            f"{remaining[:5]}")
    log(f"{fw_dir}: firmware decompressed ({fixed} symlinks repointed, "
        f"{len(broken)} dangling)", color=GREEN)

def phase_firmware(target):
    """Bundle the entire linux-firmware tree from the build container.

    Previously cherry-picked only amdgpu/i915/radeon on the assumption that
    "GPU firmware" covered the hardware that would silently break without
    it. That assumption was wrong for a general-purpose live/install ISO:
    USB controllers, Wi-Fi (iwlwifi, ath10k/ath11k, rtw88/rtw89, brcm),
    Bluetooth, NVIDIA (nouveau + the GSP firmware the proprietary driver
    needs), and audio DSP topology (sof-audio) all load blobs from this
    same tree and none of them live under those three directories. A live
    ISO that boots on unknown hardware doesn't get to guess which of those
    someone needs -- it bundles all of it. This costs real disk space
    (linux-firmware is ~650-700MB uncompressed) but that's the deliberate
    trade: broken Wi-Fi/USB/GPU on real hardware is worse than a bigger
    ISO, and it's still XZ-compressed like everything else in the squashfs.
    """
    log_phase("firmware", "Bundle the full linux-firmware tree from the build container")
    fw_root = "/usr/lib/firmware"
    dst_root = os.path.join(target, "usr", "lib", "firmware")
    if not os.path.isdir(fw_root):
        err(f"{fw_root} not found on the build container -- install linux-firmware "
            f"in the Dockerfile (this phase needs the FULL tree, not a curated subset)")
    if os.path.isdir(dst_root):
        shutil.rmtree(dst_root)
    shutil.copytree(fw_root, dst_root, symlinks=True)
    for dirpath, _dirnames, filenames in os.walk(dst_root):
        if any(f.endswith(ext) for ext in _FW_COMPRESSED_EXTS for f in filenames):
            _decompress_firmware_dir(dirpath)
    log(f"linux-firmware bundled wholesale ({dst_root}).", color=GREEN)

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
        # FEATURE_xcb must be forced ON: it silently auto-disables unless every
        # XCB extension dev package is present at configure time (no hard build
        # failure), which breaks QX11Info/KWindowSystem's X11 backend later.
        # FEATURE_testlib must stay ON despite QT_BUILD_TESTS=OFF below: that
        # flag only controls whether Qt's *own* internal test suite gets
        # compiled, not whether the QtTest library itself is built. Several
        # KF6 packages (threadweaver first surfaced it) unconditionally
        # add_subdirectory(examples), and those examples find_package(Qt6Test)
        # with no BUILD_EXAMPLES/BUILD_TESTING gate at all -- forcing it OFF
        # here just means Qt6TestConfig.cmake never exists anywhere, breaking
        # every KF6 package structured that way.
        ("qtbase",        ["-DFEATURE_testlib=ON", "-DFEATURE_fontconfig=ON",
                            "-DFEATURE_xcb=ON"]),
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

        if name == "qtbase":
            # FEATURE_xcb silently auto-disables (no build failure) if even one
            # of ~20 XCB extension dev packages is missing at configure time.
            # A silently-disabled FEATURE_xcb only surfaces hours later as
            # kwin_wayland/KWindowSystem crashing with an undefined QX11Info
            # symbol -- verify it actually landed here instead, immediately,
            # while the fix (install the missing libxcb-*-dev package) is
            # still obvious from context.
            cache_file = os.path.join(BUILD_TMP, "qt6-qtbase-build", "CMakeCache.txt")
            with open(cache_file) as f:
                cache = f.read()
            if "FEATURE_xcb:BOOL=ON" not in cache:
                notfound = [l for l in cache.splitlines() if "X11_xcb_" in l and "NOTFOUND" in l]
                err("qtbase built with FEATURE_xcb=OFF despite being forced ON -- "
                    "an XCB extension dev package is missing on this build host. "
                    "Unresolved X11_xcb_*_LIB entries:\n  " + "\n  ".join(notfound))
            log("Verified: qtbase FEATURE_xcb=ON", color=GREEN)

    # KF6 tools embed RUNPATH pointing to the arch-specific lib dir (e.g.
    # lib/x86_64-linux-gnu) but Qt is installed to lib/ directly.  Symlink
    # all Qt shared libs into the arch dir so dynamic linker finds them.
    arch_libdir = f"{prefix}/lib/x86_64-linux-gnu"
    ensure(arch_libdir)
    for sopath in glob.glob(f"{prefix}/lib/libQt6*.so*"):
        dest = os.path.join(arch_libdir, os.path.basename(sopath))
        if not os.path.lexists(dest):
            symlink(sopath, dest)
    log("Qt6 arch-dir symlinks created.", color=GREEN)

    _fix_target_pc_prefix(target)

CMAKE_BOOTSTRAP_VER = "3.31.6"
CMAKE_BOOTSTRAP_URL = f"https://github.com/Kitware/CMake/releases/download/v{CMAKE_BOOTSTRAP_VER}/cmake-{CMAKE_BOOTSTRAP_VER}-linux-x86_64.tar.gz"

FASTFETCH_VER = "2.66.0"
FASTFETCH_URL = f"https://github.com/fastfetch-cli/fastfetch/archive/refs/tags/{FASTFETCH_VER}.tar.gz"

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
    _patch_mesa_c11_threads(bd)
    # _patch_mesa_clc_clang_api / _patch_mesa_ac_llvm_api rewrite Mesa's
    # intel-clc/radeonsi LLVM helper code for Clang/LLVM 22.x's API shape
    # (TextDiagnosticPrinter&, protected TargetOpts, Triple-typed
    # createTargetMachine). Verified directly against real llvmorg-20.1.2
    # headers that LLVM 20 (what Dockerfile.build actually installs -- see
    # its own comment) still has the OLD shape Mesa 24.3.4 already handles
    # unpatched, so applying these patches against LLVM 20 would break the
    # build the other way. Only apply them if the installed toolchain is
    # genuinely >= 22, so this self-corrects if the pinned LLVM version ever
    # changes without anyone remembering to touch this gate.
    _llvm_major = 0
    try:
        _llvm_ver_out = subprocess.run(["llvm-config", "--version"],
            capture_output=True, text=True, check=True).stdout.strip()
        _llvm_major = int(_llvm_ver_out.split(".")[0])
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        pass
    if _llvm_major >= 22:
        _patch_mesa_clc_clang_api(bd)
        _patch_mesa_ac_llvm_api(bd)
    else:
        log(f"Skipping LLVM-22.x-specific mesa patches (llvm-config reports "
            f"major version {_llvm_major or 'unknown'})", color=YELLOW)
    # Unlike the three call sites above (genuinely still fine at LLVM 20,
    # confirmed against real llvmorg-20.1.2 headers), this one breaks
    # starting at 20 -- CompilerInstance::createDiagnostics(DiagnosticConsumer*)
    # was removed by then. But it is NOT gone yet at 18/19 -- confirmed
    # directly against the real Ubuntu 24.04 libclang-18-dev and
    # libclang-19-dev headers, both still carry the single-arg overload
    # unpatched Mesa 24.3.4 already calls. Applying this patch below that
    # threshold breaks the build the other way (no matching overload for
    # the FileSystem-taking call this patch introduces), so gate it the
    # same as the >=22 patches above rather than applying unconditionally.
    if _llvm_major >= 20:
        _patch_mesa_clc_create_diagnostics(bd)
    else:
        log(f"Skipping LLVM-20+ clc_helpers.cpp createDiagnostics patch "
            f"(llvm-config reports major version {_llvm_major or 'unknown'})",
            color=YELLOW)
    _patch_mesa_loader_wayland_timespec(bd)
    # Mesa doesn't depend on anything from target/usr -- it's built early
    # (before Wayland/KDE, which DO need to link against target-installed
    # Qt6/etc.), so the -I{target}/include / -L{target}/lib CFLAGS/LDFLAGS
    # that later phases legitimately need are actively harmful here: Mesa's
    # own intel_clc build tool links against BOTH Mesa's static libs AND
    # host LLVM/libedit shared libraries in the same command, and with
    # -L{target}/lib/x86_64-linux-gnu searched first, the linker resolves
    # glibc itself from there -- RC1's older bundled libc.so, which is
    # missing GLIBC_2.42-versioned termios symbols (cfgetispeed etc.) that
    # the HOST's libedit.so.0 was built against ("undefined reference to
    # cfgetispeed@GLIBC_2.42"). Same root cause as the meson/ninja
    # env fixes above, just showing up in linker flags instead of PATH/
    # LD_LIBRARY_PATH this time. Strip the target -I/-L entirely for Mesa.
    mesa_env = dict(active_env(target))
    mesa_env["CFLAGS"]   = "-std=gnu17 -fpermissive"
    mesa_env["CXXFLAGS"] = ""
    mesa_env["LDFLAGS"]  = ""
    meson_install(bd, f"{target}/usr",
        extra_args=[
            "-Dgallium-drivers=radeonsi,nouveau,iris,crocus,r300,svga,virgl,zink,swrast",
            "-Dvulkan-drivers=amd,intel,virtio",
            "-Dglx=dri", "-Degl=enabled", "-Dgbm=enabled",
            "-Dopengl=true", "-Dgles1=enabled", "-Dgles2=enabled",
            "-Dshared-glapi=enabled",
            "-Dplatforms=x11,wayland",
            "-Dglvnd=enabled", "-Db_lto=false",
            # Intel ray-tracing (GRL) pulls in the intel-clc compiler, which
            # uses internal clang::driver::Driver/llvm::Target C++ APIs that
            # genuinely changed between whatever LLVM Mesa 24.3.4 was built
            # against and LLVM 22.x on this host (Driver::GetResourcesPath
            # removed, Target::createTargetMachine's first param changed
            # from const char* to const llvm::Triple&) -- a real upstream
            # API break, not an environment issue. Ray tracing acceleration
            # is irrelevant to both VirtIO GPU (virgl/venus) and basic
            # Intel integrated graphics (e.g. the NUC7PJYHN's i915) -- the
            # rest of the intel Vulkan driver builds and works fine without
            # it, so disable just this piece rather than dropping intel
            # from vulkan-drivers entirely.
            "-Dintel-rt=disabled",
        ],
        env=mesa_env,
        build_dir=os.path.join(BUILD_TMP, "mesa-build"))
    log(f"Mesa {MESA_VER} installed.", color=GREEN)

def _patch_tar_acl(bd):
    """tar 1.35's src/xattrs.c hand-rolls three private static "at"-variant
    wrappers around libacl (its own comment: "acl-at wrappers, TODO: move to
    gnulib in future?"): acl_delete_def_file_at, acl_get_file_at, and
    acl_set_file_at. Fedora 44's libacl-devel 2.4.0 has since added *real*,
    differently-signed (extra at_flags int) functions of all three exact
    names to the public sys/acl.h -- a genuine name collision with tar's
    private helpers, not a dialect/strictness issue, so no CFLAGS change
    fixes it. (file_has_acl_at is untouched -- it's gnulib-only, never
    existed in libacl's public API, confirmed absent from sys/acl.h.)
    Rename tar's three private symbols out of the way of the now-real
    system functions of the same name.
    """
    path = os.path.join(bd, "src", "xattrs.c")
    with open(path) as f:
        txt = f.read()
    replacements = [
        ("static int acl_delete_def_file_at (int, char const *);",
         "static int tar_acl_delete_def_file_at (int, char const *);"),
        ("#define AT_FUNC_NAME acl_delete_def_file_at",
         "#define AT_FUNC_NAME tar_acl_delete_def_file_at"),
        ("if (acl_delete_def_file_at (chdir_fd, file_name))",
         "if (tar_acl_delete_def_file_at (chdir_fd, file_name))"),
        ("static acl_t acl_get_file_at (int, const char *, acl_type_t);",
         "static acl_t tar_acl_get_file_at (int, const char *, acl_type_t);"),
        ("#define AT_FUNC_NAME acl_get_file_at",
         "#define AT_FUNC_NAME tar_acl_get_file_at"),
        ("if (!(acl = acl_get_file_at (parentfd, file_name, type)))",
         "if (!(acl = tar_acl_get_file_at (parentfd, file_name, type)))"),
        ("static int acl_set_file_at (int, const char *, acl_type_t, acl_t);",
         "static int tar_acl_set_file_at (int, const char *, acl_type_t, acl_t);"),
        ("#define AT_FUNC_NAME acl_set_file_at",
         "#define AT_FUNC_NAME tar_acl_set_file_at"),
        ("if (acl_set_file_at (chdir_fd, file_name, type, acl) == -1)",
         "if (tar_acl_set_file_at (chdir_fd, file_name, type, acl) == -1)"),
    ]
    missing = [old for old, _ in replacements if old not in txt]
    if missing:
        err(f"_patch_tar_acl: expected string(s) not found in xattrs.c "
            f"(tar source changed?): {missing}")
    for old, new in replacements:
        txt = txt.replace(old, new)
    with open(path, "w") as f:
        f.write(txt)
    log("Patched tar/src/xattrs.c: renamed private acl_{delete_def,get,set}_file_at "
        "to avoid colliding with libacl 2.4.0's new functions of the same names.",
        color=GREEN)

def _patch_mesa_c11_threads(bd):
    """Mesa 24.3.4 deliberately keeps HAVE_THRD_CREATE (real glibc C11
    <threads.h>) restricted to Android in meson.build:

        if cc.has_function('thrd_create', prefix: '#include <threads.h>')
          if with_platform_android
            # Current only Android's c11 <threads.h> are verified
            pre_args += '-DHAVE_THRD_CREATE'

    That gate is intentional and correct, not a bug to route around --
    confirmed by trying exactly that first: enabling it broadly makes
    src/util/cnd_monotonic.c fail to compile, because it passes an mtx_t*
    directly to pthread_cond_wait()/pthread_cond_timedwait() (which expect
    pthread_mutex_t*), relying on Mesa's own mtx_t being a bare typedef
    alias of pthread_mutex_t -- true for Mesa's pthread-based shim, NOT
    guaranteed for glibc's real (opaque) C11 mtx_t. Mesa's own codebase
    genuinely isn't audited for that outside Android, so leave
    HAVE_THRD_CREATE/mtx_t/cnd_t/thrd_t alone entirely.

    The ACTUAL, narrower problem: on glibc >= 2.34, <stdlib.h> unconditionally
    pulls in <bits/types/once_flag.h>, which declares once_flag/call_once
    regardless of whether HAVE_THRD_CREATE is set -- so Mesa's own
    once_flag typedef + call_once() declaration (in c11/threads.h) and
    definition (in c11/impl/threads_posix.c) collide with glibc's, even
    though nothing else about C11 threads is being used. glibc's call_once
    is a real, strong exported libc symbol (confirmed via `nm -D libc.so.6`),
    so this needs guards in BOTH the header (declaration) and the impl file
    (definition), not just one -- otherwise a link-time duplicate-symbol
    error replaces the compile-time conflicting-types error. mtx_t/cnd_t/
    thrd_t stay exactly as Mesa's own pthread-based typedefs throughout;
    only the two colliding names are affected, and only when glibc's own
    guard macro proves they're already declared.
    """
    header_path = os.path.join(bd, "src", "c11", "threads.h")
    with open(header_path) as f:
        header = f.read()
    header_old = (
        "typedef pthread_once_t  once_flag;\n"
        "#  define ONCE_FLAG_INIT PTHREAD_ONCE_INIT\n"
    )
    header_new = (
        "#ifndef __once_flag_defined\n"
        "typedef pthread_once_t  once_flag;\n"
        "#  define ONCE_FLAG_INIT PTHREAD_ONCE_INIT\n"
        "#endif\n"
    )
    call_once_old = "void call_once(once_flag *, void (*)(void));\n"
    call_once_new = (
        "#ifndef __once_flag_defined\n"
        "void call_once(once_flag *, void (*)(void));\n"
        "#endif\n"
    )
    if header_old not in header or call_once_old not in header:
        err("_patch_mesa_c11_threads: expected once_flag/call_once "
            "declarations not found in c11/threads.h (mesa source changed?)")
    header = header.replace(header_old, header_new).replace(call_once_old, call_once_new)
    with open(header_path, "w") as f:
        f.write(header)

    impl_path = os.path.join(bd, "src", "c11", "impl", "threads_posix.c")
    with open(impl_path) as f:
        impl = f.read()
    impl_old = (
        "void\n"
        "call_once(once_flag *flag, void (*func)(void))\n"
        "{\n"
        "    pthread_once(flag, func);\n"
        "}\n"
    )
    impl_new = (
        "#ifndef __once_flag_defined\n"
        "void\n"
        "call_once(once_flag *flag, void (*func)(void))\n"
        "{\n"
        "    pthread_once(flag, func);\n"
        "}\n"
        "#endif\n"
    )
    if impl_old not in impl:
        err("_patch_mesa_c11_threads: expected call_once() definition not "
            "found in c11/impl/threads_posix.c (mesa source changed?)")
    with open(impl_path, "w") as f:
        f.write(impl.replace(impl_old, impl_new))
    log("Patched mesa c11/threads.h + threads_posix.c: defer to glibc's "
        "native once_flag/call_once (a real symbol collision on glibc "
        ">= 2.34) without touching mtx_t/cnd_t/thrd_t or HAVE_THRD_CREATE.",
        color=GREEN)

def _patch_mesa_clc_clang_api(bd):
    """src/compiler/clc/clc_helpers.cpp (intel-clc's internal-shader
    compiler, needed by both the Intel Vulkan driver AND iris/OpenGL --
    "-Dintel-rt=disabled" does NOT skip this, it's required regardless)
    already has #if LLVM_VERSION_MAJOR guards for several past Clang/LLVM
    API breaks, but three more calls broke again on LLVM/Clang 22.x, past
    what Mesa 24.3.4 (built against an older LLVM) anticipated:

    1. clang::driver::Driver::GetResourcesPath() was removed entirely (not
       just resignatured -- Mesa's own ">= 20" branch already calls a
       1-arg form that doesn't exist either). Traced what it actually
       computed instead of guessing: clang_path is the real path to the
       loaded libclang shared library (via dladdr), and CLANG_RESOURCE_DIR
       (already a macro Mesa's own "< 20" branch references, currently
       "../lib/clang/22") is a relative suffix joined against that
       library's parent directory -- so compute it directly with
       std::filesystem instead of calling the now-removed API, for every
       LLVM version, replacing both existing branches with one.
    2. CompilerInvocation::TargetOpts changed from a plain public
       TargetOptions value to a protected std::shared_ptr<TargetOptions>
       -- direct member access no longer compiles at all regardless of
       dereferencing, use the existing public getTargetOpts() accessor
       instead (which already dereferences it internally).
    3. Target::createTargetMachine()'s first parameter changed from
       const char* to const llvm::Triple& -- wrap the existing triple
       string in llvm::Triple(...).
    4. TextDiagnosticPrinter's constructor took DiagnosticOptions* before;
       now it takes DiagnosticOptions& -- Mesa's call still does
       &c->getDiagnosticOpts(), taking the address of a function that
       already returns a reference (producing a pointer where a
       reference is now required). Drop the &.

    None of these change what gets computed, only how -- same resource
    path, same target options object, same SPIR-V triple, same
    diagnostics options, just expressed against the current API shape.
    """
    path = os.path.join(bd, "src", "compiler", "clc", "clc_helpers.cpp")
    with open(path) as f:
        txt = f.read()

    diag_opts_old = (
        "   c->createDiagnostics(new clang::TextDiagnosticPrinter(\n"
        "                           diag_log_stream,\n"
        "                           &c->getDiagnosticOpts()));\n"
    )
    diag_opts_new = (
        "   c->createDiagnostics(new clang::TextDiagnosticPrinter(\n"
        "                           diag_log_stream,\n"
        "                           c->getDiagnosticOpts()));\n"
    )

    # A second, separate occurrence of the same DiagnosticOptions*/&
    # mismatch, in a different function -- a DiagnosticsEngine constructed
    # directly via brace-init instead of through CompilerInstance's
    # createDiagnostics() helper. Two distinct problems in this one block:
    # DiagnosticsEngine's own 2nd constructor arg now wants
    # DiagnosticOptions& too (was pointer-accepting before), and the same
    # &c->getDiagnosticOpts() pattern as above is nested inside it.
    diag_engine_old = (
        "   clang::DiagnosticsEngine diag {\n"
        "      new clang::DiagnosticIDs,\n"
        "      new clang::DiagnosticOptions,\n"
        "      new clang::TextDiagnosticPrinter(diag_log_stream,\n"
        "                                       &c->getDiagnosticOpts())\n"
        "   };\n"
    )
    diag_engine_new = (
        "   clang::DiagnosticsEngine diag {\n"
        "      new clang::DiagnosticIDs,\n"
        "      *new clang::DiagnosticOptions,\n"
        "      new clang::TextDiagnosticPrinter(diag_log_stream,\n"
        "                                       c->getDiagnosticOpts())\n"
        "   };\n"
    )

    resource_path_old = (
        "   auto tmp_res_path =\n"
        "#if LLVM_VERSION_MAJOR >= 20\n"
        "      Driver::GetResourcesPath(std::string(clang_path));\n"
        "#else\n"
        "      Driver::GetResourcesPath(std::string(clang_path), CLANG_RESOURCE_DIR);\n"
        "#endif\n"
    )
    resource_path_new = (
        "   auto tmp_res_path =\n"
        "      (fs::path(clang_path).parent_path() / CLANG_RESOURCE_DIR).string();\n"
    )

    target_opts_old = (
        "   c->setTarget(clang::TargetInfo::CreateTargetInfo(\n"
        "                   c->getDiagnostics(), c->getInvocation().TargetOpts));\n"
    )
    target_opts_new = (
        "   c->setTarget(clang::TargetInfo::CreateTargetInfo(\n"
        "                   c->getDiagnostics(), c->getInvocation().getTargetOpts()));\n"
    )

    triple_old = (
        "         auto TM = target->createTargetMachine(\n"
        "            triple, \"\", \"\", {}, std::nullopt, std::nullopt,\n"
    )
    triple_new = (
        "         auto TM = target->createTargetMachine(\n"
        "            llvm::Triple(triple), \"\", \"\", {}, std::nullopt, std::nullopt,\n"
    )

    missing = [name for name, old in [
        ("TextDiagnosticPrinter diag opts arg", diag_opts_old),
        ("DiagnosticsEngine brace-init block", diag_engine_old),
        ("GetResourcesPath block", resource_path_old),
        ("TargetOpts call", target_opts_old),
        ("createTargetMachine triple arg", triple_old),
    ] if old not in txt]
    if missing:
        err(f"_patch_mesa_clc_clang_api: expected string(s) not found in "
            f"clc_helpers.cpp (mesa source changed?): {missing}")

    txt = (txt.replace(diag_opts_old, diag_opts_new)
              .replace(diag_engine_old, diag_engine_new)
              .replace(resource_path_old, resource_path_new)
              .replace(target_opts_old, target_opts_new)
              .replace(triple_old, triple_new))
    with open(path, "w") as f:
        f.write(txt)
    log("Patched mesa clc_helpers.cpp for Clang/LLVM 22.x API changes: "
        "fixed two DiagnosticOptions pointer/reference mismatches "
        "(TextDiagnosticPrinter's arg + DiagnosticsEngine's own brace-init "
        "constructor), removed Driver::GetResourcesPath (computed the "
        "same path directly), switched to the public getTargetOpts() "
        "accessor (the field itself is now protected), wrapped the "
        "SPIR-V triple in llvm::Triple for createTargetMachine.",
        color=GREEN)

def _patch_mesa_clc_create_diagnostics(bd):
    """src/compiler/clc/clc_helpers.cpp's single-arg
    CompilerInstance::createDiagnostics(DiagnosticConsumer*) call was
    removed by LLVM 20 (confirmed directly against real llvmorg-20.1.2
    clang/Frontend/CompilerInstance.h -- the only two overloads left both
    take a llvm::vfs::FileSystem& as their first parameter, one a member
    void-returning form, one a static DiagnosticsEngine-returning form).
    Unlike the three breaks _patch_mesa_clc_clang_api fixes (all still fine
    at LLVM 20, only broken at 22+), this one is broken already at 20 --
    real compile error: "no matching function for call to
    CompilerInstance::createDiagnostics(clang::TextDiagnosticPrinter*)".

    Fix: pass a real filesystem as the new required first argument, via
    llvm::vfs::getRealFileSystem() (dereferenced -- it returns an
    IntrusiveRefCntPtr<FileSystem>), matching the member overload's
    signature. `c` has no VFS of its own configured yet at this point in
    the function (freshly constructed two lines above, no createFileManager
    call in between), so getVirtualFileSystem() would be the wrong thing to
    reach for here -- a fresh real filesystem is what every other LLVM tool
    calling this same overload this early passes.
    """
    path = os.path.join(bd, "src", "compiler", "clc", "clc_helpers.cpp")
    with open(path) as f:
        txt = f.read()
    old = (
        "   c->createDiagnostics(new clang::TextDiagnosticPrinter(\n"
        "                           diag_log_stream,\n"
    )
    new = (
        "   c->createDiagnostics(*llvm::vfs::getRealFileSystem(),\n"
        "                        new clang::TextDiagnosticPrinter(\n"
        "                           diag_log_stream,\n"
    )
    if old not in txt:
        err("_patch_mesa_clc_create_diagnostics: expected createDiagnostics "
            "call not found in clc_helpers.cpp (mesa source changed?)")
    txt = txt.replace(old, new)
    include_old = "#include <clang/Frontend/CompilerInstance.h>\n"
    include_new = (
        "#include <clang/Frontend/CompilerInstance.h>\n"
        "#include <llvm/Support/VirtualFileSystem.h>\n"
    )
    if include_old not in txt:
        err("_patch_mesa_clc_create_diagnostics: expected #include line not "
            "found in clc_helpers.cpp (mesa source changed?)")
    txt = txt.replace(include_old, include_new, 1)
    with open(path, "w") as f:
        f.write(txt)
    log("Patched mesa clc_helpers.cpp: pass a real llvm::vfs::FileSystem to "
        "createDiagnostics() (its single-arg DiagnosticConsumer* overload "
        "was removed by LLVM 20).", color=GREEN)

def _patch_mesa_ac_llvm_api(bd):
    """src/amd/llvm/ac_llvm_helper.cpp (radeonsi's LLVM-based shader
    compiler helper) hits the same class of LLVM 22.x API break as
    clc_helpers.cpp, just a different call: llvm::Module::setTargetTriple()
    used to take a std::string/StringRef, now takes a llvm::Triple
    directly. Mesa's code still converts TM->getTargetTriple() (already a
    Triple) down to a string via .getTriple() before passing it -- drop
    that conversion and pass the Triple straight through.
    """
    path = os.path.join(bd, "src", "amd", "llvm", "ac_llvm_helper.cpp")
    with open(path) as f:
        txt = f.read()
    old = "   unwrap(module)->setTargetTriple(TM->getTargetTriple().getTriple());\n"
    new = "   unwrap(module)->setTargetTriple(TM->getTargetTriple());\n"
    if old not in txt:
        err("_patch_mesa_ac_llvm_api: expected setTargetTriple() call not "
            "found in ac_llvm_helper.cpp (mesa source changed?)")
    with open(path, "w") as f:
        f.write(txt.replace(old, new))
    log("Patched mesa ac_llvm_helper.cpp: pass llvm::Triple directly to "
        "setTargetTriple() instead of converting to std::string first "
        "(LLVM 22.x API change).", color=GREEN)

def _patch_mesa_loader_wayland_timespec(bd):
    """src/loader/loader_wayland_helper.c calls timespec_sub_saturate()
    (declared in util/timespec.h) but never includes that header -- a
    genuine missing #include in Mesa's own source, not an API/environment
    issue. Presumably not exercised in Mesa's usual CI build configuration.
    Confirmed the exact include convention against two other Mesa files
    that use the same function (cnd_monotonic.c, lp_fence.c): both use
    #include "util/timespec.h".
    """
    path = os.path.join(bd, "src", "loader", "loader_wayland_helper.c")
    with open(path) as f:
        txt = f.read()
    old = (
        "#include \"util/perf/cpu_trace.h\"\n"
        "\n"
        "#include \"loader_wayland_helper.h\"\n"
    )
    new = (
        "#include \"util/perf/cpu_trace.h\"\n"
        "#include \"util/timespec.h\"\n"
        "\n"
        "#include \"loader_wayland_helper.h\"\n"
    )
    if old not in txt:
        err("_patch_mesa_loader_wayland_timespec: expected include block "
            "not found in loader_wayland_helper.c (mesa source changed?)")
    with open(path, "w") as f:
        f.write(txt.replace(old, new))
    log("Patched mesa loader_wayland_helper.c: added missing "
        "#include \"util/timespec.h\" for timespec_sub_saturate().",
        color=GREEN)

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

def _patch_xdg_desktop_portal_kde(bd):
    """xdg-desktop-portal-kde's top-level CMakeLists.txt does
    add_subdirectory(autotests) with no BUILD_TESTING guard, so
    -DBUILD_TESTING=OFF does not stop it configuring tests that link
    Qt::Test -- which nothing else in the project find_package()s.
    """
    cmake = os.path.join(bd, "CMakeLists.txt")
    if not os.path.exists(cmake):
        return
    with open(cmake) as f:
        txt = f.read()
    old = "add_subdirectory(autotests)"
    new = "if(BUILD_TESTING)\n  add_subdirectory(autotests)\nendif()"
    if old in txt:
        with open(cmake, "w") as f:
            f.write(txt.replace(old, new))
    # print.cpp includes QtPrintSupport/private/qcups_p.h -- a Qt private
    # header only generated when qtbase's own PrintSupport module was
    # built with CUPS available. Our qtbase was built during phase_qt_deps,
    # long before cups (only added to the container for print-manager,
    # much later in the package list) ever existed here, so the header
    # was never generated -- not a bug in this package, a real gap
    # upstream in ours. Rebuilding qtbase now to pick up CUPS would cost
    # another full Qt6 pass (~70+ minutes) for one non-essential portal
    # (print-dialog integration for sandboxed/Flatpak apps, not core
    # desktop function) -- drop the print portal instead. Contained to
    # exactly 3 files: source list, one include + one member in
    # desktopportal.{h,cpp}.
    src_cmake = os.path.join(bd, "src", "CMakeLists.txt")
    if os.path.exists(src_cmake):
        with open(src_cmake) as f:
            s = f.read()
        s = s.replace("    print.cpp\n", "")
        s = s.replace("    print.h\n", "")
        with open(src_cmake, "w") as f:
            f.write(s)
    # Removing print.cpp from the sources list alone isn't enough: CMake's
    # AUTOMOC scans every header under the source dir for Q_OBJECT
    # regardless of whether its .cpp is actually compiled, so print.h's
    # PrintPortal class still got moc'd into mocs_compilation.cpp -- with
    # print.cpp excluded, that generated qt_static_metacall() referenced
    # PrintPortal::Print()/PreparePrint() with no implementation anywhere,
    # failing the final link with "undefined reference". Delete both files
    # outright so AUTOMOC never sees the class at all.
    for fname in ("print.cpp", "print.h"):
        fpath = os.path.join(bd, "src", fname)
        if os.path.exists(fpath):
            os.remove(fpath)
    dp_h = os.path.join(bd, "src", "desktopportal.h")
    if os.path.exists(dp_h):
        with open(dp_h) as f:
            h = f.read()
        h = h.replace("class PrintPortal;\n", "")
        h = h.replace("    PrintPortal *const m_print;\n", "")
        with open(dp_h, "w") as f:
            f.write(h)
    dp_cpp = os.path.join(bd, "src", "desktopportal.cpp")
    if os.path.exists(dp_cpp):
        with open(dp_cpp) as f:
            c = f.read()
        c = c.replace('#include "print.h"\n', "")
        c = c.replace(", m_print(new PrintPortal(this))", "")
        with open(dp_cpp, "w") as f:
            f.write(c)

def _patch_spectacle_opencv(bd):
    """spectacle's CMakeLists.txt hard-requires OpenCV >= 4.7, but
    ubuntu:24.04's libopencv-dev is 4.6.0 -- one point release short.
    Its actual OpenCV usage (ImagePlatformKWin.cpp: cv::Rect, cv::resize,
    cv::INTER_AREA/INTER_LANCZOS4 for screenshot DPI downscaling) is
    trivial core/imgproc API stable since OpenCV 2.x/3.x, nothing specific
    to 4.7 -- the version pin reads as a defensive "whatever we tested
    against" floor, not a real technical requirement, so lowering it to
    match what's actually available is safe here.
    """
    cmake = os.path.join(bd, "CMakeLists.txt")
    if not os.path.exists(cmake):
        return
    with open(cmake) as f:
        txt = f.read()
    old = "find_package(OpenCV 4.7 REQUIRED core imgproc)"
    new = "find_package(OpenCV 4.6 REQUIRED core imgproc)"
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
    if name == "xdg-desktop-portal-kde":
        _patch_xdg_desktop_portal_kde(bd)
    if name == "spectacle":
        _patch_spectacle_opencv(bd)
    # Per-package extra cmake args for packages with optional/missing system deps
    pkg_extra = {
        # WITH_ZXING flipped ON: spectacle's main binary unconditionally
        # target_link_libraries(KF6::PrisonScanner) -- not gateable, no
        # feature flag on spectacle's side -- so prison must provide it.
        # libzxing-dev (Ubuntu universe, real ZXingConfig.cmake) makes
        # find_package(ZXing CONFIG) succeed; target rootfs gets the real
        # runtime .so copied in the same way as libical/libhunspell/etc.
        "prison":            ["-DWITH_ZXING=ON"],
        "plasma-workspace":  ["-DWITH_X11=OFF"],
        "breeze":            ["-DBUILD_QT5=OFF"],
        "plasma-integration":["-DBUILD_QT5=OFF"],      # Qt5 not installed; Qt6-only build
        "oxygen":            ["-DBUILD_QT5=OFF"],      # legacy window-deco theme; both BUILD_QT5/BUILD_QT6 default ON
        # kquickimageeditor's cv::stackBlur (its only real OpenCV call) was
        # only added to OpenCV in 4.7, genuinely absent from ubuntu:24.04's
        # 4.6.0 -- unlike spectacle's OpenCV usage (cv::Rect/resize, stable
        # since ancient versions), this one is a real version gap, not just
        # a defensive floor. WITH_OPENCV is a real cmake_dependent_option
        # (defaults ON on Linux) with a full non-OpenCV stackblur.cpp
        # fallback already in the source -- use that instead of building a
        # newer OpenCV from source just for this one optional blur effect.
        "kquickimageeditor": ["-DWITH_OPENCV=OFF"],
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
                symlink(os.path.join("x86_64-linux-gnu", fname), dest)

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
    _profile = "smechos-plasma-live"
    # Some -dev packages this session needed (see Dockerfile.build) back a
    # library the target rootfs never shipped at all, unlike the far more
    # common case of "runtime .so already present, only headers missing".
    # kcalendarcore hard-requires LibIcal >= 3.0; sonnet hard-requires at
    # least one spell-check backend, and hunspell was picked since none
    # were present. Both need their real, ABI-matched runtime .so copied
    # into the target once -- same as phase_xwayland_deps already does for
    # binaries the target rootfs never had either.
    for libname, glob_pat in (("libical", "/usr/lib/x86_64-linux-gnu/libical*.so*"),
                               ("libhunspell", "/usr/lib/x86_64-linux-gnu/libhunspell*.so*"),
                               ("libsecret", "/usr/lib/x86_64-linux-gnu/libsecret*.so*"),
                               ("libnm", "/usr/lib/x86_64-linux-gnu/libnm.so*"),
                               ("libmm-glib", "/usr/lib/x86_64-linux-gnu/libmm-glib.so*"),
                               ("libxcb-xtest", "/usr/lib/x86_64-linux-gnu/libxcb-xtest.so*"),
                               ("libavcodec", "/usr/lib/x86_64-linux-gnu/libavcodec.so*"),
                               ("libavutil", "/usr/lib/x86_64-linux-gnu/libavutil.so*"),
                               ("libavformat", "/usr/lib/x86_64-linux-gnu/libavformat.so*"),
                               ("libavfilter", "/usr/lib/x86_64-linux-gnu/libavfilter.so*"),
                               ("libswscale", "/usr/lib/x86_64-linux-gnu/libswscale.so*"),
                               ("libva", "/usr/lib/x86_64-linux-gnu/libva*.so*"),
                               ("libfreerdp", "/usr/lib/x86_64-linux-gnu/libfreerdp*.so*"),
                               ("libwinpr", "/usr/lib/x86_64-linux-gnu/libwinpr*.so*"),
                               ("libopencv_core", "/usr/lib/x86_64-linux-gnu/libopencv_core.so*"),
                               ("libopencv_imgproc", "/usr/lib/x86_64-linux-gnu/libopencv_imgproc.so*"),
                               ("libZXing", "/usr/lib/x86_64-linux-gnu/libZXing.so*")):
        stamp = f"kde-{libname}-runtime"
        if _phase_done(_profile, stamp):
            continue
        arch_libdir = os.path.join(target, "usr", "lib", "x86_64-linux-gnu")
        ensure(arch_libdir)
        copied = 0
        for f in glob.glob(glob_pat):
            # Idempotent: a prior crash mid-loop (before this libname's
            # stamp got set) can leave a partial copy already in place --
            # same failure mode fixed in phase_systemd's lz4/acl/seccomp/
            # archive copies (FileExistsError on a pre-existing symlink).
            dst = os.path.join(arch_libdir, os.path.basename(f))
            if os.path.lexists(dst):
                os.remove(dst)
            shutil.copy2(f, dst, follow_symlinks=False)
            copied += 1
        if copied == 0:
            err(f"No {libname}*.so* found in the build container -- is the matching "
                "-dev package installed? (see Dockerfile.build)")
        log(f"Copied {copied} {libname} runtime file(s) into target rootfs", color=GREEN)
        _mark_done(_profile, stamp)

    # qca-qt6 2.3.12 -- kwallet's crypto backend. The mirroring workaround
    # just below assumes this is already built into the target rootfs (a
    # leftover assumption from whatever seeded an earlier dev machine's
    # rootfs); a genuinely from-scratch target has nothing here yet, so
    # build it first, same cmake_install() pattern as qtkeychain/
    # pulseaudio-qt above. QCA's own CMakeLists picks the Qt6 flavor
    # (libqca-qt6.so, Qca-qt6/ headers) automatically once it finds our
    # target's Qt6 via CMAKE_PREFIX_PATH.
    _qca_stamp = "qca-2.3.12"
    if not _phase_done(_profile, _qca_stamp):
        env = active_env(target)
        _qca_ver = "2.3.12"
        _qca_url = f"https://download.kde.org/stable/qca/{_qca_ver}/qca-{_qca_ver}.tar.xz"
        _qca_tb  = os.path.join(sources(target), f"qca-{_qca_ver}.tar.xz")
        download(_qca_url, _qca_tb)
        _qca_bd  = os.path.join(BUILD_TMP, "qca")
        shutil.rmtree(_qca_bd, ignore_errors=True)
        extract(_qca_tb, _qca_bd)
        cmake_install(_qca_bd, f"{target}/usr",
            extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                        "-DBUILD_WITH_QT6=ON",
                        "-DBUILD_TESTS=OFF", "-DBUILD_TESTING=OFF",
                        "-DBUILD_TOOLS=OFF"],
            env=env)
        _mark_done(_profile, _qca_stamp)
        log(f"qca-qt6 {_qca_ver} done.", color=GREEN)
    else:
        log("qca-qt6 already built — skipping", color=YELLOW)

    # qca-qt6 (built just above, or pre-existing in the target rootfs from
    # some earlier seeding) exports a Qca-qt6Targets.cmake that,
    # unlike every ECM-based KF6 package, hardcodes an absolute non-
    # relocatable `_IMPORT_PREFIX "/usr"` for both its .so
    # (IMPORTED_LOCATION) and its headers (INTERFACE_INCLUDE_DIRECTORIES),
    # instead of the standard CMake pattern computed relative to the
    # Targets.cmake file's own on-disk location. Running un-chrooted, that
    # literal "/usr/..." resolves against the *container's* real root, not
    # the target rootfs, so any later package that find_package()s Qca-qt6
    # (kwallet was first) dies with "the imported target ... references
    # the file ... but this file does not exist." Mirroring the real files
    # into the container's own real /usr at the same absolute paths is the
    # only fix available short of an actual chroot -- same idea as the
    # container-side libical/libhunspell copies above, just the other
    # direction (target -> container, since this specific package's own
    # export is what's broken, not a container-side gap).
    #
    # NOT stamped, deliberately: `podman run --rm` starts a brand-new,
    # ephemeral container on every single invocation of this pipeline, so
    # anything written to the container's own real filesystem (as opposed
    # to the bind-mounted /mnt) never survives to the next run. A prior
    # version of this fix stamped it via the normal (persistent, /mnt-
    # backed) _phase_done/_mark_done mechanism, which marked it "done"
    # forever after the first run's container -- every subsequent run then
    # saw a fresh container with none of the mirrored files, but skipped
    # rewriting them anyway, and this exact error came right back. Just
    # always redo it; it's a handful of file copies, not a rebuild.
    qca_so_glob = os.path.join(target, "usr", "lib", "libqca-qt6.so*")
    qca_so_files = glob.glob(qca_so_glob)
    qca_include = os.path.join(target, "usr", "include", "Qca-qt6")
    if not qca_so_files or not os.path.isdir(qca_include):
        err(f"qca-qt6 not found under {target}/usr (expected {qca_so_glob} and "
            f"{qca_include}) -- can't mirror it into the container for the "
            "Qca-qt6Targets.cmake absolute-path workaround.")
    for f in qca_so_files:
        shutil.copy2(f, os.path.join("/usr/lib", os.path.basename(f)), follow_symlinks=False)
    dest_include = "/usr/include/Qca-qt6"
    if os.path.isdir(dest_include):
        shutil.rmtree(dest_include)
    shutil.copytree(qca_include, dest_include)
    log(f"Mirrored qca-qt6 ({len(qca_so_files)} .so file(s) + headers) "
        "into the container's real /usr", color=GREEN)
    # Purge stale KF6/KDE cmake configs installed by Ubuntu packages into the
    # build root's multiarch cmake path — they carry wrong versions (e.g. 6.6.0)
    # that cmake prefers over our freshly built KF6_VER ones.
    if not _phase_done(_profile, "kde-cmake-purge"):
        multiarch_cmake = os.path.join(target, "usr/lib/x86_64-linux-gnu/cmake")
        if os.path.isdir(multiarch_cmake):
            for entry in os.listdir(multiarch_cmake):
                if any(entry.startswith(p) for p in ("KF6", "KDE", "KDecoration", "Plasma", "KWin")):
                    shutil.rmtree(os.path.join(multiarch_cmake, entry), ignore_errors=True)
            log("Purged stale KF6/KDE cmake configs from multiarch path", color=YELLOW)
        _mark_done(_profile, "kde-cmake-purge")
    env = active_env(target)

    # kguiaddons (Tier 0, built via the kf6 loop below) hard-requires
    # PlasmaWaylandProtocols >= 1.15.0 via find_package(), which is neither
    # an apt package nor built anywhere else in this pipeline -- just XML
    # Wayland protocol definitions + a CMake export, no C++ compilation.
    # Needs extra-cmake-modules already installed to configure, so build
    # that first (idempotent -- _kde_pkg skips it via its own stamp when
    # the kf6 loop below reaches it again).
    _kde_pkg("extra-cmake-modules", KF6_VER, KF6_URL, target, env)
    _pwp_stamp = "plasma-wayland-protocols-1.22.0"
    if not _phase_done(_profile, _pwp_stamp):
        _pwp_ver = "1.22.0"
        _pwp_url = f"https://download.kde.org/stable/plasma-wayland-protocols/plasma-wayland-protocols-{_pwp_ver}.tar.xz"
        _pwp_tb  = os.path.join(sources(target), f"plasma-wayland-protocols-{_pwp_ver}.tar.xz")
        download(_pwp_url, _pwp_tb)
        _pwp_bd  = os.path.join(BUILD_TMP, "plasma-wayland-protocols")
        shutil.rmtree(_pwp_bd, ignore_errors=True)
        extract(_pwp_tb, _pwp_bd)
        cmake_install(_pwp_bd, f"{target}/usr",
            extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr"],
            env=env)
        _mark_done(_profile, _pwp_stamp)
        log(f"plasma-wayland-protocols {_pwp_ver} done.", color=GREEN)
    else:
        log("plasma-wayland-protocols already built — skipping", color=YELLOW)

    kf6 = [
        # Tier 0 — no KF6 deps
        "extra-cmake-modules",
        "karchive", "kcodecs", "kcoreaddons", "kdbusaddons",
        "kguiaddons", "ki18n", "kitemmodels", "kitemviews",
        "kunitconversion",      # required by plasma5support
        "kwidgetsaddons", "kwindowsystem",
        # kidletime moved up from its original Tier 8 spot: it only needs
        # kcoreaddons, but baloo (Tier 6) hard-requires KF6::IdleTime via
        # find_package(KF6 ... COMPONENTS IdleTime) -- built this late it
        # failed baloo's configure with "Missing required components: IdleTime".
        "kidletime",
        "threadweaver",         # KF6ThreadWeaver: job-queue threading helper, no KF6 deps
        "kplotting",            # KF6Plotting: 2D plotting widgets, no KF6 deps
        # oxygen-icons has its own release schedule, well behind KF6_VER --
        # built separately below, not through this KF6_URL/KF6_VER loop.
        "ktexttemplate",        # KF6TextTemplate: Django-style templating, Qt6-only deps
        "kdnssd",               # KF6DNSSD: zeroconf/mDNS service discovery
        "kcalendarcore",        # KF6CalendarCore: iCal data model, Qt6-only deps
        # kmime is released via Gear (release-service), not the Frameworks
        # train -- confirmed absent from download.kde.org/stable/frameworks/
        # 6.24/ entirely; its real tarball is kmime-{GEAR_VER}.tar.xz. Built
        # separately below, same as oxygen-icons/kirigami-addons.
        "bluez-qt",             # KF6BluezQt: Bluetooth stack; required by bluedevil
        "kimageformats",        # extra QImage plugins (karchive, kcoreaddons)
        # Tier 1 — depend on Tier 0
        "kconfig", "kdoctools",   # kdoctools: docbook/man page generator; required by plasma-desktop
        "kcontacts",            # KF6Contacts: vCard/address-book data model (kcodecs, kconfig)
        "kuserfeedback",        # KF6UserFeedback: opt-in telemetry framework (kconfig, kitemmodels)
        # Tier 2 — depend on kconfig
        "kcolorscheme", "kauth",
        "kpeople",              # KF6People: contact aggregation (kcontacts, kitemmodels)
        # Tier 3 — depend on kauth/kcolorscheme
        "kconfigwidgets",
        # Tier 4 — knotifications must precede kjobwidgets
        "kcompletion", "kglobalaccel", "kcrash", "knotifications",
        "kjobwidgets", "sonnet", "kpackage", "kservice",
        # Tier 5 — breeze-icons required by kiconthemes
        "breeze-icons", "kiconthemes", "kxmlgui", "solid",
        # Tier 6
        "kbookmarks", "kio", "kfilemetadata",
        "baloo",                # KF6Baloo: file indexer/desktop search (kfilemetadata, kio, kservice)
        "kdav",                 # KF6DAV: WebDAV client library (kio)
        "syndication",          # KF6Syndication: RSS/Atom parsing (kcodecs, kcoreaddons, kio)
        # Tier 7
        "ktextwidgets", "knotifyconfig", "kparts", "kwallet",
        # Tier 8 — kirigami must precede ksvg (KirigamiPlatform dep)
        "kirigami", "kdeclarative", "ksvg",
        "kstatusnotifieritem",  # kidletime moved to Tier 0 above
        "qqc2-desktop-style",  # QQC2 desktop style; required by plasma-desktop
        # frameworkintegration moved after knewstuff below: it hard-requires
        # KF6NewStuffCore (find_package(KF6 ... COMPONENTS NewStuffCore)),
        # which knewstuff wasn't providing yet at this position in the list --
        # failed with "Could not find a package configuration file provided
        # by KF6NewStuffCore".
        # Tier 9 — kcmutils required by kwin/plasma; kholidays required by knighttime; attica required by knewstuff
        "kcmutils", "kholidays", "attica", "knewstuff", "krunner",
        "frameworkintegration", # Qt platform theme plugin: KDE file dialogs/theming in Qt apps (kiconthemes, kirigami, knotifications, knewstuff)
        # Tier 10 — required by plasma-workspace / plasma-nm
        "prison",              # KF6Prison: barcode/QR generator (required by plasma-workspace)
        "syntax-highlighting",  # KF6SyntaxHighlighting: required by ktexteditor
        "ktexteditor",         # KF6TextEditor: text editor component (required by plasma-workspace)
        "kded",                # KF6KDED: KDE daemon infrastructure (required by plasma-workspace)
        "kpty",                # KF6Pty: pseudo-terminal support. Hard requirement
                               # of konsole and kwrited -- no terminal emulator can
                               # be built without it.
        "kdesu",                # KF6Su: framework-level privilege escalation (kpty, kservice, kiconthemes)
        "networkmanager-qt",   # KF6NetworkManagerQt: REQUIRED by plasma-workspace on Linux
        "modemmanager-qt",     # KF6ModemManagerQt: required by plasma-nm for mobile broadband
        "kquickcharts",        # KF6QuickCharts: required by plasma-pa (volume applet charts)
        "purpose",             # KF6Purpose: sharing/intent framework (required by Discover)
    ]
    for mod in kf6:
        _kde_pkg(mod, KF6_VER, KF6_URL, target, env)

    # oxygen-icons — legacy icon theme shipped alongside breeze-icons, but
    # unlike everything else in `kf6` it is not part of the Frameworks
    # release train at all: it ships flat at its own dedicated download path
    # with its own far-slower version numbering (still 6.2.0 as of the
    # 6.24/6.6.6 LTS line) -- confirmed via the actual directory listing at
    # download.kde.org/stable/oxygen-icons/, same pattern as kirigami-addons
    # and pulseaudio-qt below.
    _kde_pkg("oxygen-icons", "6.2.0",
             "https://download.kde.org/stable/oxygen-icons",
             target, env)

    # kmime (KF6Mime: MIME/RFC822 message parsing) -- released via Gear
    # (release-service), not Frameworks; see the comment left in the kf6
    # list above. Depends only on kcodecs/kconfig, both already built above.
    _kde_pkg("kmime", GEAR_VER, GEAR_URL, target, env)

    # kquickimageeditor -- required by spectacle (screenshot tool) for its
    # image-editing capability. Own release schedule, own dedicated flat
    # download path, same pattern as kirigami-addons/oxygen-icons/kmime
    # above -- currently at 0.6.2.1 regardless of the Plasma/KF6 line.
    _kde_pkg("kquickimageeditor", "0.6.2.1",
             "https://download.kde.org/stable/kquickimageeditor",
             target, env)

    # libdisplay-info 0.3.0 -- kwin hard-requires >= 0.2.0 via pkg-config;
    # Ubuntu 24.04 only packages 0.1.1 (too old), so build from source like
    # layer-shell-qt above. Not a KDE project (freedesktop.org/emersion),
    # plain meson, no Qt dependency -- installs straight into the target
    # since kwin (built into the target) links against it at runtime.
    _ldi_stamp = "libdisplay-info-0.3.0"
    if not _phase_done(_profile, _ldi_stamp):
        _ldi_ver = "0.3.0"
        _ldi_url = (f"https://gitlab.freedesktop.org/emersion/libdisplay-info/"
                    f"-/archive/{_ldi_ver}/libdisplay-info-{_ldi_ver}.tar.gz")
        _ldi_tb  = os.path.join(sources(target), f"libdisplay-info-{_ldi_ver}.tar.gz")
        download(_ldi_url, _ldi_tb)
        _ldi_bd  = os.path.join(BUILD_TMP, "libdisplay-info")
        shutil.rmtree(_ldi_bd, ignore_errors=True)
        extract(_ldi_tb, _ldi_bd)
        meson_install(_ldi_bd, f"{target}/usr", env=env)
        _mark_done(_profile, _ldi_stamp)
        log(f"libdisplay-info {_ldi_ver} done.", color=GREEN)
    else:
        log("libdisplay-info already built — skipping", color=YELLOW)

    # QCoro6 -- plasma-workspace hard-requires it (find_package(QCoro6)) for
    # C++20 coroutine support wrapping Qt's async APIs. Not a KDE project
    # (github.com/qcoro/qcoro, formerly danvratil/qcoro), not in apt at
    # all. Its own qcoro_find_qt() macro auto-detects Qt6 over Qt5, no
    # explicit flag needed -- only trims examples/tests to keep it lean.
    _qcoro_stamp = "qcoro-0.13.0"
    if not _phase_done(_profile, _qcoro_stamp):
        _qcoro_ver = "0.13.0"
        _qcoro_url = f"https://github.com/qcoro/qcoro/archive/refs/tags/v{_qcoro_ver}.tar.gz"
        _qcoro_tb  = os.path.join(sources(target), f"qcoro-{_qcoro_ver}.tar.gz")
        download(_qcoro_url, _qcoro_tb)
        _qcoro_bd  = os.path.join(BUILD_TMP, "qcoro")
        shutil.rmtree(_qcoro_bd, ignore_errors=True)
        extract(_qcoro_tb, _qcoro_bd)
        cmake_install(_qcoro_bd, f"{target}/usr",
            extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                        "-DQCORO_BUILD_EXAMPLES=OFF",
                        "-DQCORO_BUILD_TESTING=OFF",
                        # Qt6WebSockets was never built as part of our
                        # custom Qt6 (not needed by anything else in this
                        # pipeline) -- QCoro defaults this component ON,
                        # which then hard-fails Qt6's own find_package()
                        # for a component that plain doesn't exist here.
                        "-DQCORO_WITH_QTWEBSOCKETS=OFF"],
            env=env)
        _mark_done(_profile, _qcoro_stamp)
        log(f"QCoro {_qcoro_ver} done.", color=GREEN)
    else:
        log("QCoro already built — skipping", color=YELLOW)

    # polkit-qt-1 (PolkitQt6-1) -- plasma-workspace's region/language KCM
    # talks to its localegen helper over this. KDE project, own release
    # schedule (download.kde.org/stable/polkit-qt-1/), defaults to Qt5
    # unless QT_MAJOR_VERSION=6 is passed explicitly -- Ubuntu only
    # packages the Qt5 build (libpolkit-qt5-1-dev), no Qt6 apt package
    # exists at all.
    _pqt_stamp = "polkit-qt-1-0.201.1"
    if not _phase_done(_profile, _pqt_stamp):
        _pqt_ver = "0.201.1"
        _pqt_url = f"https://download.kde.org/stable/polkit-qt-1/polkit-qt-1-{_pqt_ver}.tar.xz"
        _pqt_tb  = os.path.join(sources(target), f"polkit-qt-1-{_pqt_ver}.tar.xz")
        download(_pqt_url, _pqt_tb)
        _pqt_bd  = os.path.join(BUILD_TMP, "polkit-qt-1")
        shutil.rmtree(_pqt_bd, ignore_errors=True)
        extract(_pqt_tb, _pqt_bd)
        cmake_install(_pqt_bd, f"{target}/usr",
            extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                        "-DQT_MAJOR_VERSION=6",
                        "-DBUILD_EXAMPLES=OFF", "-DBUILD_TEST=OFF"],
            env=env)
        _mark_done(_profile, _pqt_stamp)
        log(f"polkit-qt-1 {_pqt_ver} done.", color=GREEN)
    else:
        log("polkit-qt-1 already built — skipping", color=YELLOW)

    plasma = [
        # plasma-activities must precede libplasma
        "plasma-activities", "plasma-activities-stats",
        # kactivitymanagerd is the *daemon* behind plasma-activities' client
        # libs. plasmashell hard-aborts shell load if it is not running
        # ("Aborting shell load: The activity manager daemon ... is not
        # running"), leaving a black screen with only a cursor -- so it is
        # mandatory, not optional. Ships in the Plasma release alongside the
        # libs, and must stay version-matched to them.
        "kactivitymanagerd",
        # libplasma (was plasma-framework in KF5) ships with Plasma release.
        # Must precede milou below: milou's own CMakeLists find_package()s
        # "Plasma" (provided by libplasma) directly, failed with "Could not
        # find a package configuration file provided by Plasma" when milou
        # was built first.
        "libplasma",
        # milou provides the org.kde.milou QML module that KWin's Overview
        # effect imports. Without it the effect fails at runtime with
        # 'module "org.kde.milou" is not installed' -- a visible desktop
        # feature breaking, not an optional extra.
        "milou",
        # kdecoration provides KDecoration3; kwayland requires wayland >= 1.24 (now built)
        # libkscreen provides KF6Screen required by kscreenlocker; must come first
        # knighttime and kscreenlocker are required by kwin
        "kdecoration", "kwayland", "libkscreen",
        # layer-shell-qt must be built from source (via this same loop, at
        # PLASMA_VER) before kscreenlocker: the apt/system package build
        # (was 6.6.5) is ABI-incompatible with our self-built Qt 6.10.3's
        # private API. The fix is building from source against our own Qt,
        # not any particular version number -- confirmed layer-shell-qt-
        # {PLASMA_VER}.tar.xz is published for every Plasma point release,
        # so this stays correct across the 6.7.2 -> 6.6.6 LTS pin.
        "layer-shell-qt",
        "knighttime", "kscreenlocker",
        "libksysguard",        # KSysGuard libs: required by ksystemstats and optional in plasma-workspace
        # kglobalacceld: separate repo/package from the KF6 kglobalaccel
        # library built above (KDE/kglobalacceld on GitHub, released with
        # Plasma at PLASMA_VER, not with Frameworks) -- it's the actual
        # global-shortcut daemon binary. kwin hard-requires it via
        # find_package(KGlobalAccelD), which the kglobalaccel library alone
        # never provides.
        "kglobalacceld",
        "kwin", "plasma-workspace", "plasma5support", "plasma-desktop",
        "plasma-nm", "plasma-pa", "powerdevil", "breeze",
        "systemsettings", "plasma-integration", "kdeplasma-addons",
        "ksystemstats", "kscreen",
        # --- Tier 11: standalone apps/KCMs that plug into an already-built
        # plasma-workspace/systemsettings. Order among these mostly does not
        # matter -- their real dependencies (kio, kcmutils, kwallet, ...) are
        # all Tier 0-10 above -- except where noted.
        "kde-cli-tools",        # kdesu, kioclient6, KDE URL/mime helpers
        "polkit-kde-agent-1",   # PolicyKit auth agent; no prompts at all without it
        "kwrited",              # writes to ptys (kpty); "wall"-style message daemon
        "kwallet-pam",          # unlocks kwallet automatically at login (PAM module)
        "ksshaskpass",          # SSH passphrase prompt via kwallet
        "kmenuedit",            # application-menu editor
        "kinfocenter",          # "About this System" page in System Settings (#8)
        "plasma-systemmonitor", # system monitor (needs libksysguard/ksystemstats above)
        "drkonqi",              # crash handler; crashes are currently silent
        "print-manager",        # printing configuration (needs libcups2-dev)
        "bluedevil",            # Bluetooth applet/KCM (needs bluez-qt above)
        "xdg-desktop-portal-kde",  # portal backend; fixes portal errors + screen sharing (#11)
        # kwayland-integration deliberately NOT built: it's an unported
        # KF5/Qt5-only legacy package -- its CMakeLists.txt hard-requires
        # find_package(Qt5 ...) with no Qt6 option at all, confirmed true
        # even in upstream's current master branch (checked invent.kde.org
        # directly), not just our 6.6.6 pin. Plasma 6's own Qt6/QtWayland
        # stack plus layer-shell-qt (already built above) supersede what
        # this plugin did for Qt5-based apps; SmechOS never builds Qt5 at
        # all, so this package simply cannot be built here, by design.
        "qqc2-breeze-style",    # Breeze QQC2 style (needs kirigami/kiconthemes above)
        "plasma-browser-integration",  # native-messaging host for the KDE browser extension
        "plasma-welcome", "plasma-setup",  # first-run onboarding
        "plasma-workspace-wallpapers",     # extra wallpaper set, data-only
        "kgamma",               # gamma-correction KCM
        "plasma-firewall", "plasma-disks", "plasma-vault", "plasma-thunderbolt",
        "sddm-kcm", "plymouth-kcm",
        # Theme/decoration assets -- effectively data-only, safe this late
        "aurorae", "oxygen", "oxygen-sounds", "ocean-sound-theme",
        # breeze-grub deliberately NOT built: unlike every other package in
        # this list it ships no CMakeLists.txt at all -- it's just static
        # assets (background images, a font, mkfont.sh) meant to be copied
        # into place directly, not built. _kde_pkg()'s cmake_install() path
        # can't handle that, and it's a purely cosmetic GRUB boot-splash
        # theme (not required for a working desktop), so it's skipped
        # rather than writing one-off copy logic for it.
        "breeze-gtk", "breeze-plymouth",
        "plasma-sdk",           # developer tools (cuttlefish, kwin scripting console, ...)
        # --- Known to need system deps this container does not currently
        # provide -- listed for completeness (audit #14 Tier 3) but expect
        # the build to fail until Dockerfile.build grows the matching -dev
        # package. Not blockers for RC3; tracked as follow-up.
        "kpipewire",            # needs libpipewire-0.3-dev, libdrm-dev
        "krdp",                 # needs kpipewire above
        "kde-gtk-config",       # needs libgtk-3-dev + gsettings-desktop-schemas
                                # (GTK4 was never actually required, despite this
                                # comment's old claim -- checked its CMakeLists directly)
        "flatpak-kcm",          # needs libflatpak-dev
        "wacomtablet",          # needs libwacom-dev
        # union deliberately NOT built: no release tarball exists for it
        # anywhere in KDE's stable download infrastructure -- checked
        # frameworks/6.24, release-service (Gear), the top-level stable/
        # tree, and even unstable/plasma/, all empty. It's git-only on
        # invent.kde.org, a developer-facing Plasma theme SDK base, not
        # something a working desktop needs -- _kde_pkg() has no git-clone
        # path anyway, only tarball download.
        "spectacle",            # screenshot tool (#13): needs OpenCV >= 4.7,
                                 # kpipewire above for screen-recording, and
                                 # KF6Prison built with PrisonScanner (WITH_ZXING=ON
                                 # + zxing-cpp) for the QR/barcode reader -- the
                                 # prison entry above builds -DWITH_ZXING=OFF.
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

    # KDE Gear apps -- separate version series from Plasma/KF6 (see GEAR_VER above)
    gear = [
        "konsole",   # terminal emulator (#9); needs kpty above, plus kdoctools
                     # for its handbook -- see the kdoctools DocBook catalog
                     # note on _kde_pkg's XDG_DATA_DIRS/XML_CATALOG_FILES handling.
    ]
    for mod in gear:
        _kde_pkg(mod, GEAR_VER, GEAR_URL, target, env)

def _pam_ensure_line(pam_file, marker_re, line):
    """Idempotently append `line` to a PAM service file unless a line already
    matches marker_re. Used to patch upstream-shipped PAM templates that are
    missing a module this rootfs actually needs (see phase_plasma_configure).
    """
    import re
    with open(pam_file) as f:
        content = f.read()
    if re.search(marker_re, content, re.MULTILINE):
        return False
    with open(pam_file, "a") as f:
        if not content.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return True

def phase_plasma_configure(target):
    """Configure the display manager: PLM (plasmalogin) if built, else SDDM.

    This profile's actual shipping display manager is PLM
    ("plasma-login-manager", sourced from a Fedora SRPM -- see
    /home/smech/smechos-work/plm/src). It is NOT currently built by any
    phase in this pipeline (no phase_kde plasma[] entry, no dedicated
    phase) -- that gap is real and this function does not close it. What
    this function does is configure PLM correctly *if* its binary is
    already present in target (e.g. built out-of-band, as it was for every
    image tested this session), while still falling back to SDDM
    configuration if plasmalogin isn't there. A `plasma-login-manager`
    build phase (cmake+ECM, Fedora patches reapplied for Debian
    conventions) is still needed as a follow-up.

    PLM's PAM template is Fedora/authselect-flavored (references
    "postlogin", pam_passwdqc, system-auth) despite this rootfs being
    Debian/Ubuntu ABI throughout -- that mismatch is the root cause behind
    several of the fixes below, not something introduced here.
    """
    log_phase("plasma-configure", "Configure display manager (PLM, fallback SDDM)")
    etc = os.path.join(target, "etc")
    plasmalogin_bin = os.path.join(target, "usr", "bin", "plasmalogin")

    if os.path.exists(plasmalogin_bin):
        # --- Autologin config -------------------------------------------
        # Group name is "Autologin" (lowercase "login") per mainconfig.kcfg
        # -- KConfig group names are case-sensitive, and a wrong-case group
        # here means autologinUser() silently returns empty and the daemon
        # skips autologin with no error logged at all. Both the daemon's own
        # config object (/etc/plasmalogin.conf, read by MainConfigLoader) and
        # the frontend settings config object (/usr/lib/plasmalogin/
        # defaults.conf, a *different* KSharedConfig, read by
        # plasmaloginsettingsdefaults.cpp) need this written identically --
        # they are two separate config objects in PLM's own source, not one
        # cascading file.
        autologin_conf = "[Autologin]\nUser=smech\nSession=plasma\nRelogin=false\n"
        with open(os.path.join(etc, "plasmalogin.conf"), "w") as f:
            f.write(autologin_conf)
        defaults_dir = os.path.join(target, "usr", "lib", "plasmalogin")
        ensure(defaults_dir)
        with open(os.path.join(defaults_dir, "defaults.conf"), "w") as f:
            f.write(autologin_conf)

        # --- postlogin: guarantee it exists ------------------------------
        # plasmalogin-autologin's PAM file (as shipped) has "auth include
        # postlogin" / "session include postlogin". If that file is missing
        # -- true for this rootfs, which has no authselect-style postlogin
        # at all -- the include is a hard PAM_ABORT for that stack position,
        # so pam_authenticate() returns PAM_AUTH_ERR ("Autologin failed!")
        # even though pam_permit.so earlier in the same stack already
        # unconditionally succeeded. A comment-only stub is sufficient: an
        # include with zero lines in the target file contributes nothing to
        # the combined result, it just needs to exist.
        pam_dir = os.path.join(etc, "pam.d")
        ensure(pam_dir)
        postlogin = os.path.join(pam_dir, "postlogin")
        if not os.path.exists(postlogin):
            with open(postlogin, "w") as f:
                f.write("# Intentionally minimal -- see phase_plasma_configure "
                         "in spk-compile.py for why this needs to exist at all.\n")

        # --- plasmalogin-autologin: add pam_systemd.so -------------------
        # Even with auth fixed, the shipped session stack (ending in
        # "session include system-auth", whose own session stack is just
        # pam_limits/pam_env/pam_unix) never loads pam_systemd.so. Without
        # it no logind session gets registered, so no systemd --user
        # manager comes up, and startplasma falls back to a bare
        # dbus-run-session with no working session bus -- kwin_wayland
        # never starts. Observed effect: kwin_wayland to plasmashell gap
        # went from 3 minutes (kcminit/ksmserver each hard-timeout at 90s)
        # to ~7 seconds once this was added.
        autologin_pam = os.path.join(target, "usr", "lib", "pam.d", "plasmalogin-autologin")
        if os.path.exists(autologin_pam):
            _pam_ensure_line(autologin_pam, r"^session\s+optional\s+pam_systemd\.so",
                              "session    optional    pam_systemd.so")

        # --- enable plasmalogin.service -----------------------------------
        # PLM's own install already ships /usr/lib/systemd/system/
        # plasmalogin.service (Alias=display-manager.service) -- only the
        # enable symlink is needed, not a hand-written unit (unlike the
        # SDDM fallback below, which has no shipped unit to enable).
        unit = os.path.join(target, "usr", "lib", "systemd", "system", "plasmalogin.service")
        if os.path.exists(unit):
            gfx_wants = os.path.join(etc, "systemd", "system", "graphical.target.wants")
            ensure(gfx_wants)
            link = os.path.join(gfx_wants, "plasmalogin.service")
            if not os.path.lexists(link):
                symlink(unit, link)
        else:
            log("plasmalogin binary present but no systemd unit shipped with it "
                "-- service not enabled.", color=YELLOW)

        log("PLM (plasmalogin) configured.", color=GREEN)
        return

    # --- SDDM fallback (used only if plasmalogin was never built) --------
    log("plasmalogin not found -- falling back to SDDM config.", color=YELLOW)
    sddm_dir = os.path.join(etc, "sddm.conf.d")
    ensure(sddm_dir)
    with open(os.path.join(sddm_dir, "autologin.conf"), "w") as f:
        f.write("[Autologin]\nUser=smech\nSession=plasma\n")
    with open(os.path.join(etc, "sddm.conf"), "w") as f:
        f.write("[Theme]\nCurrent=breeze\n\n[General]\nDisplayServer=wayland\n")

    sddm_bin = os.path.join(target, "usr", "bin", "sddm")
    if os.path.exists(sddm_bin):
        sddm_unit_path = os.path.join(etc, "systemd", "system", "sddm.service")
        ensure(os.path.dirname(sddm_unit_path))
        with open(sddm_unit_path, "w") as f:
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
        gfx_wants = os.path.join(etc, "systemd", "system", "graphical.target.wants")
        ensure(gfx_wants)
        link = os.path.join(gfx_wants, "sddm.service")
        if not os.path.lexists(link):
            symlink(sddm_unit_path, link)
    log("SDDM configured (fallback).", color=GREEN)

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

def phase_xwayland_deps(target):
    """Fetch Xwayland + xkbcomp + xkb-data from an Ubuntu 24.04 container.

    A built image this session shipped with NO Xwayland binary at all --
    kwin_wayland logged "Xwayland process failed to start" once and moved
    on, but downstream components (kcminit, ksmserver) block on Xwayland
    becoming ready and hard-timeout after 90s each, which is what actually
    caused a "just a cursor on a black screen, forever" desktop. Getting
    xkbcomp right matters too: without it Xwayland crash-loops (XKB keymap
    compile failure -> "Failed to activate virtual core keyboard" -> fatal),
    and kwin gives up on Xwayland after 4 crashes in 10 minutes.

    These are pulled from a matching-ABI Ubuntu 24.04 container rather than
    the build host directly -- this pipeline's target rootfs is Debian/
    Ubuntu glibc ABI (see build_env_glibc), but the build host itself may be
    a different distro (this fix was discovered on a Fedora host); copying
    Xwayland's binary/libs from a host with a different glibc/libstdc++
    build risks the same silent-ABI-break class of bug already hit once
    this session with a Qt private-symbol rebuild.
    """
    log_phase("xwayland-deps", "Fetch Xwayland + xkbcomp + xkb-data (Ubuntu 24.04 ABI)")

    extra_libs = ["libXfont2.so.2", "libfontenc.so.1", "libxkbfile.so.1"]
    tmp = os.path.join(BUILD_TMP, "xwayland-import")
    shutil.rmtree(tmp, ignore_errors=True)
    ensure(tmp)

    if _in_matching_build_image():
        # Already running inside an Ubuntu-24.04-ABI-matched container (see
        # _in_matching_build_image) -- no need to spin up a nested one just
        # to apt-get install into it, install straight into this container.
        run(["apt-get", "update", "-qq"])
        run(["apt-get", "install", "-y", "-qq", "xwayland", "x11-xkb-utils"])
        shutil.copy2("/usr/bin/Xwayland", tmp)
        shutil.copy2("/usr/bin/xkbcomp", tmp)
        shutil.copytree("/usr/share/X11/xkb", os.path.join(tmp, "xkb"), dirs_exist_ok=True)
        for lib in extra_libs:
            real = os.path.realpath(f"/lib/x86_64-linux-gnu/{lib}")
            shutil.copy2(real, os.path.join(tmp, os.path.basename(real)))
    else:
        # Not already in a matching container (e.g. running spk-compile.py
        # directly on a bare host) -- pull from a throwaway one instead, so
        # these binaries/libs are never copied from a host whose glibc/
        # libstdc++ build might not match the Debian/Ubuntu ABI this
        # pipeline's target rootfs expects (see build_env_glibc). That
        # mismatch is exactly the class of bug this phase exists to avoid.
        if not shutil.which("podman"):
            err("podman not found on build host -- required to fetch Xwayland/xkbcomp "
                "from a matching-ABI Ubuntu 24.04 container. Install podman and re-run "
                "this phase (--phase xwayland-deps).")

        container = "spk-xwayland-build"
        run(["podman", "rm", "-f", container], check=False)
        run(["podman", "run", "-d", "--name", container, "ubuntu:24.04", "sleep", "infinity"])
        try:
            run(["podman", "exec", container, "bash", "-c",
                 "apt-get update -qq && apt-get install -y -qq xwayland x11-xkb-utils"])
            run(["podman", "cp", f"{container}:/usr/bin/Xwayland", tmp])
            run(["podman", "cp", f"{container}:/usr/bin/xkbcomp", tmp])
            run(["podman", "cp", f"{container}:/usr/share/X11/xkb", os.path.join(tmp, "xkb")])

            # Only libs not already covered elsewhere in the pipeline
            # (kwin-deps, qt-deps, mesa) -- checked against those phases.
            for lib in extra_libs:
                proc = subprocess.run(
                    ["podman", "exec", container, "bash", "-c",
                     f"readlink -f /lib/x86_64-linux-gnu/{lib}"],
                    capture_output=True, text=True, check=True)
                real = proc.stdout.strip()
                run(["podman", "cp", f"{container}:{real}", os.path.join(tmp, os.path.basename(real))])
        finally:
            run(["podman", "rm", "-f", container], check=False)

    arch_libdir = os.path.join(target, "usr", "lib", "x86_64-linux-gnu")
    ensure(arch_libdir)
    ensure(os.path.join(target, "usr", "bin"))
    ensure(os.path.join(target, "usr", "share", "X11"))

    for binname in ("Xwayland", "xkbcomp"):
        dest = os.path.join(target, "usr", "bin", binname)
        shutil.copy2(os.path.join(tmp, binname), dest)
        os.chmod(dest, 0o755)

    xkb_dst = os.path.join(target, "usr", "share", "X11", "xkb")
    if os.path.isdir(xkb_dst):
        shutil.rmtree(xkb_dst)
    shutil.copytree(os.path.join(tmp, "xkb"), xkb_dst)

    for f in glob.glob(os.path.join(tmp, "*.so.*")):
        base = os.path.basename(f)
        # base is e.g. libXfont2.so.2.0.0 -- versioned .so + unversioned-minor symlink
        dest = os.path.join(arch_libdir, base)
        shutil.copy2(f, dest)
        parts = base.split(".so.")
        soname = f"{parts[0]}.so.{parts[1].split('.')[0]}"
        link = os.path.join(arch_libdir, soname)
        if not os.path.lexists(link):
            symlink(base, link)

    log("Xwayland + xkbcomp + xkb-data installed.", color=GREEN)

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
            "LOGO":         "smechos-logo",
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

    # Logo: drop your downloaded SmechOS logo into config/branding/ under
    # either/both of these names before running this phase --
    #   config/branding/smechos-logo.svg  (preferred, scales cleanly)
    #   config/branding/smechos-logo.png  (256x256 recommended fallback)
    # This picks up whichever is present and installs it to the standard
    # icon-theme paths KDE's kinfocenter (About This System) and the LOGO=
    # os-release field above both resolve against.
    repo_root = os.path.dirname(os.path.abspath(__file__))
    branding_src = os.path.join(repo_root, "config", "branding")
    svg_src = os.path.join(branding_src, "smechos-logo.svg")
    png_src = os.path.join(branding_src, "smechos-logo.png")

    if os.path.exists(svg_src):
        dest_dir = os.path.join(target, "usr", "share", "icons", "hicolor", "scalable", "apps")
        ensure(dest_dir)
        shutil.copy2(svg_src, os.path.join(dest_dir, "smechos-logo.svg"))
        log("Installed smechos-logo.svg to hicolor scalable icon theme.", color=GREEN)
    if os.path.exists(png_src):
        dest_dir = os.path.join(target, "usr", "share", "icons", "hicolor", "256x256", "apps")
        ensure(dest_dir)
        shutil.copy2(png_src, os.path.join(dest_dir, "smechos-logo.png"))
        # Also drop a copy in pixmaps -- the simplest, most broadly-checked
        # fallback path for tools that don't walk the full hicolor theme.
        pixmaps_dir = os.path.join(target, "usr", "share", "pixmaps")
        ensure(pixmaps_dir)
        shutil.copy2(png_src, os.path.join(pixmaps_dir, "smechos-logo.png"))
        log("Installed smechos-logo.png to hicolor + pixmaps.", color=GREEN)
    if not os.path.exists(svg_src) and not os.path.exists(png_src):
        log(f"No logo found in {branding_src} -- LOGO= field is set in "
            f"os-release but has nothing to point at yet. Drop "
            f"smechos-logo.svg or smechos-logo.png there and re-run this "
            f"phase.", color=YELLOW)

    _phase_fastfetch(target)

def _phase_fastfetch(target):
    log_phase("fastfetch", f"Compile fastfetch {FASTFETCH_VER} + SmechOS branding config")
    src    = sources(target)
    prefix = f"{target}/usr"

    tarball = os.path.join(src, f"fastfetch-{FASTFETCH_VER}.tar.gz")
    download(FASTFETCH_URL, tarball)
    bd = os.path.join(BUILD_TMP, "fastfetch")
    shutil.rmtree(bd, ignore_errors=True)
    # extract() already strips the tarball's own top-level directory
    # (default strip=1), so the real source (CMakeLists.txt etc.) lands
    # directly in bd -- there's no separate "fastfetch-{VER}" subdirectory
    # to descend into on top of that.
    extract(tarball, bd)
    cmake_install(bd, prefix)

    # System-wide default config: fastfetch checks /etc/xdg/fastfetch first
    # when no per-user config exists, so this is what a fresh SmechOS user
    # sees by default without needing to run `fastfetch --gen-config` first.
    cfg_dir = os.path.join(target, "etc", "xdg", "fastfetch")
    ensure(cfg_dir)
    fastfetch_config = {
        "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
        "logo": {
            "type": "auto",
            "source": "smechos-logo",
            "padding": {"top": 1, "left": 1, "right": 2}
        },
        "display": {"separator": " -> "},
        "modules": [
            "title",
            "separator",
            {"type": "os", "key": "OS"},
            {"type": "kernel", "key": "Kernel"},
            {"type": "packages", "key": "Packages"},
            {"type": "de", "key": "DE"},
            {"type": "wm", "key": "WM"},
            {"type": "shell", "key": "Shell"},
            {"type": "terminal", "key": "Terminal"},
            {"type": "cpu", "key": "CPU"},
            {"type": "gpu", "key": "GPU"},
            {"type": "memory", "key": "Memory"},
            {"type": "disk", "key": "Disk"},
            "break",
            "colors"
        ]
    }
    with open(os.path.join(cfg_dir, "config.jsonc"), "w") as f:
        json.dump(fastfetch_config, f, indent=2)
    log("fastfetch installed with SmechOS-branded default config.", color=GREEN)

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

    # The real spk binary itself. Never previously downloaded by this
    # pipeline at all -- whatever was at usr/bin/spk in the target was
    # stale/pre-seeded cruft from before this session's work (a genuine
    # v1-era build: `spk help` showed only system-install/userland-install/
    # entire-system-upgrade/about/help, no `packagekit-backend` subcommand
    # at all). That matters because pk-backend-spk.py below execs
    # `spk packagekit-backend` -- with the stale binary that call fails
    # outright ("Unknown command"), meaning Discover could never actually
    # install or update anything on this build. Confirmed the real v2.0.1
    # release genuinely has `packagekit-backend` (and `install`/
    # `system-upgrade`/`compile`/deploy commands) before wiring this in.
    spk_bin_url = "https://github.com/Smech-Labs/spk/releases/download/v2.0.1/spk"
    spk_bin_dst = os.path.join(target, "usr", "bin", "spk")
    spk_bin_tmp = os.path.join(src, "spk-v2.0.1")
    download(spk_bin_url, spk_bin_tmp)
    ensure(os.path.dirname(spk_bin_dst))
    shutil.copy2(spk_bin_tmp, spk_bin_dst)
    os.chmod(spk_bin_dst, 0o755)
    log("Installed real spk v2.0.1 binary to usr/bin/spk.", color=GREEN)

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
    # NOT /tmp: this build always runs inside an ephemeral `podman run --rm`
    # container (see the whole session's workflow) whose /tmp is the
    # container's own writable layer, discarded the instant it exits --
    # only /mnt (bind-mounted from the host) survives. A prior run
    # confirmed this the hard way: the bundle step logged real non-zero
    # .tar.xz sizes and sha256 hashes, but /tmp/smechos-packages was
    # completely gone moments after the container exited -- every package
    # silently lost despite "BUILD COMPLETE".
    out = "/mnt/smechos-packages"
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
        "etc/spk-repo-conf.yaml",
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

    # dri/gallium-pipe are only installed under the arch-qualified dir --
    # confirmed via find, unlike the individual libGL/libEGL/etc SONAMEs
    # above which _symlink_arch_libs() mirrors to bare usr/lib/ too.
    tar_paths("mesa-graphics", [
        "usr/lib/libGL.so.1", "usr/lib/libEGL.so.1",
        "usr/lib/libgbm.so.1", "usr/lib/libglapi.so.0",
        "usr/lib/libvulkan.so.1",
        "usr/lib/x86_64-linux-gnu/dri", "usr/lib/x86_64-linux-gnu/gallium-pipe",
        "usr/share/vulkan", "usr/share/glvnd",
    ])

    # Real names verified directly against actual build output (not the
    # approximate/guessed names this manifest originally had -- e.g. there
    # is no "KF6Core", KDE Frameworks names each split module explicitly:
    # KF6CoreAddons, KF6ConfigCore/ConfigGui, KF6KIOCore/KIOWidgets/etc.
    tar_paths("kde-frameworks", [
        "usr/lib/libKF6CoreAddons.so.6", "usr/lib/libKF6ConfigCore.so.6",
        "usr/lib/libKF6ConfigGui.so.6",
        "usr/lib/libKF6ConfigWidgets.so.6", "usr/lib/libKF6I18n.so.6",
        "usr/lib/libKF6IconThemes.so.6", "usr/lib/libKF6KIOCore.so.6",
        "usr/lib/libKF6KIOWidgets.so.6", "usr/lib/libKF6KIOGui.so.6",
        "usr/lib/libKF6KIOFileWidgets.so.6",
        "usr/lib/libKF6Parts.so.6", "usr/lib/libKF6Service.so.6",
        "usr/lib/libKF6Solid.so.6", "usr/lib/libKF6WindowSystem.so.6",
        "usr/lib/libKF6XmlGui.so.6",
        "usr/share/kf6", "usr/share/locale",
    ])

    # kwin_x11 deliberately not listed: SmechOS is Wayland-only by design
    # (see project memory on the Plasma 6.6 LTS pivot -- X11 *session*
    # support doesn't matter here, only XWayland app compatibility does,
    # handled separately by phase_xwayland_deps). libPlasma/libPlasmaQuick
    # are capitalized this way in the real build output.
    #
    # Re-verified against actual build output post-BUILD-COMPLETE (the
    # previous version of this manifest silently skipped 3 of 11 paths --
    # never caught because a skip only logs a warning, not a hard error):
    #   - libPlasma's real current SONAME is .so.7 (confirmed via
    #     usr/lib/x86_64-linux-gnu/libPlasma.so.7 -> libPlasma.so.6.6.6,
    #     the real, root-owned, freshly-built symlink) -- ".so.6" doesn't
    #     exist as a bare SONAME file at all, only full point-release
    #     filenames like libPlasma.so.6.6.6 do. A STALE, wrongly-versioned
    #     bare usr/lib/libPlasma.so.7 -> libPlasma.so.6.7.2 symlink also
    #     exists (smech-owned, older, from earlier session cruft) --
    #     deliberately using the arch-qualified path here to bypass it.
    #   - kwin has no usr/lib/kwin at all; its real plugin dir is
    #     usr/lib/x86_64-linux-gnu/plugins/kwin.
    #   - plasma-desktop is not a literal directory/binary in modern
    #     Plasma 6 -- its functionality is plasmashell plus a long list of
    #     individually-named plasma-apply-*/plasma-open-settings/etc.
    #     binaries (already living in usr/bin, not separately bundled
    #     here) and KCM .so's scattered directly under
    #     usr/lib/x86_64-linux-gnu/ by name -- no single path to include,
    #     so the entry is dropped rather than pointing at something fake.
    tar_paths("plasma", [
        "usr/bin/plasmashell", "usr/bin/kwin_wayland",
        "usr/bin/sddm", "usr/bin/startplasma-wayland",
        "usr/bin/krunner", "usr/bin/kscreen-doctor",
        "usr/lib/x86_64-linux-gnu/libPlasma.so.7",
        "usr/lib/libPlasmaQuick.so.7",
        "usr/lib/x86_64-linux-gnu/plugins/kwin",
        "usr/share/plasma", "usr/share/sddm",
        "usr/share/applications/org.kde.plasmashell.desktop",
        "etc/sddm.conf.d",
    ])

    tar_paths("plasma-discover", [
        "usr/bin/plasma-discover",
        "usr/lib/plasma-discover",
        "usr/share/applications/org.kde.discover.desktop",
    ])

    # packagekitd installs to usr/libexec, not usr/bin (confirmed via find --
    # meson's default libexecdir). usr/lib/x86_64-linux-gnu/packagekit-backend
    # holds PackageKit's own compiled stub/test backends (dummy, apt, test_*)
    # -- irrelevant to SmechOS (the "apt" one would try to exec apt-get,
    # which doesn't exist on target) but harmless to leave in place.
    # usr/lib/packagekit-backend (bare, NOT arch-qualified -- a genuinely
    # different directory) is where the actual pk-backend-spk.py script
    # backend lives, confirmed via DefaultBackend=spk in PackageKit.conf --
    # this manifest previously omitted it entirely, meaning the one backend
    # that actually matters was never shipped. usr/bin/spk is the real spk
    # binary that pk-backend-spk.py execs into ("spk packagekit-backend") --
    # without it the script backend fails immediately at runtime.
    tar_paths("packagekit-spk", [
        "usr/libexec/packagekitd",
        "usr/lib/x86_64-linux-gnu/packagekit-backend",
        "usr/lib/packagekit-backend",
        "usr/bin/spk",
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
        if name == "tar":
            _patch_tar_acl(bd)
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
            "-Dkmod=enabled",
            "-Dukify=disabled",
            "-Dbootloader=disabled",
            "-Ddns-over-tls=false",
            "-Ddefault-dnssec=no",
            "-Dfallback-hostname=smechos",
            "-Dmode=release",
        ],
        env=env, build_dir=os.path.join(BUILD_TMP, "systemd-build"))
    log(f"systemd {systemd_ver} installed.", color=GREEN)

    # liblz4-dev (see Dockerfile.build) satisfies the build-time header
    # need, but systemd links a real NEEDED liblz4.so.1 once HAVE_LZ4=1 --
    # the resulting binaries can't even start without it present in the
    # shipped rootfs. Copy the container's real runtime .so in, same
    # pattern as the libical/libhunspell/etc. seeds in phase_kde -- this
    # one has to live here instead since systemd builds far earlier.
    arch_libdir = os.path.join(target, "usr", "lib", "x86_64-linux-gnu")
    ensure(arch_libdir)
    # A re-run of this phase after a later step fails (the normal case while
    # iterating on the build) re-enters here from the top -- these dest
    # files from the previous attempt are already in place, so copy2 must
    # overwrite rather than crash on FileExistsError for the symlinks.
    lz4_copied = 0
    for f in glob.glob("/usr/lib/x86_64-linux-gnu/liblz4.so*"):
        dst = os.path.join(arch_libdir, os.path.basename(f))
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copy2(f, dst, follow_symlinks=False)
        lz4_copied += 1
    if lz4_copied == 0:
        err("No liblz4.so* found in the build container -- is liblz4-dev "
            "installed? (see Dockerfile.build)")
    log(f"Copied {lz4_copied} liblz4 runtime file(s) into target rootfs", color=GREEN)

    # libkmod-dev (see Dockerfile.build): -Dkmod=enabled links a real NEEDED
    # libkmod.so.2 into systemd-udevd. Without it (and without this copy,
    # same pattern as liblz4 above), udev has no way to auto-load any
    # module-only (=m) driver for hotplugged/PCI-probed hardware -- confirmed
    # via boot-test as the reason virtio_gpu, and by extension any real GPU/
    # NIC/storage driver built as a module, never loads on a live boot.
    kmod_copied = 0
    for f in glob.glob("/usr/lib/x86_64-linux-gnu/libkmod.so*"):
        dst = os.path.join(arch_libdir, os.path.basename(f))
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copy2(f, dst, follow_symlinks=False)
        kmod_copied += 1
    if kmod_copied == 0:
        err("No libkmod.so* found in the build container -- is libkmod-dev "
            "installed? (see Dockerfile.build)")
    log(f"Copied {kmod_copied} libkmod runtime file(s) into target rootfs", color=GREEN)

    acl_copied = 0
    for f in glob.glob("/usr/lib/x86_64-linux-gnu/libacl.so*"):
        dst = os.path.join(arch_libdir, os.path.basename(f))
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copy2(f, dst, follow_symlinks=False)
        acl_copied += 1
    if acl_copied == 0:
        err("No libacl.so* found in the build container -- is libacl1-dev "
            "installed? (see Dockerfile.build)")
    log(f"Copied {acl_copied} libacl runtime file(s) into target rootfs", color=GREEN)

    seccomp_copied = 0
    for f in glob.glob("/usr/lib/x86_64-linux-gnu/libseccomp.so*"):
        dst = os.path.join(arch_libdir, os.path.basename(f))
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copy2(f, dst, follow_symlinks=False)
        seccomp_copied += 1
    if seccomp_copied == 0:
        err("No libseccomp.so* found in the build container -- is libseccomp-dev "
            "installed? (see Dockerfile.build)")
    log(f"Copied {seccomp_copied} libseccomp runtime file(s) into target rootfs", color=GREEN)

    archive_copied = 0
    for f in glob.glob("/usr/lib/x86_64-linux-gnu/libarchive.so*"):
        dst = os.path.join(arch_libdir, os.path.basename(f))
        if os.path.lexists(dst):
            os.remove(dst)
        shutil.copy2(f, dst, follow_symlinks=False)
        archive_copied += 1
    if archive_copied == 0:
        err("No libarchive.so* found in the build container -- is libarchive-dev "
            "installed? (see Dockerfile.build)")
    log(f"Copied {archive_copied} libarchive runtime file(s) into target rootfs", color=GREEN)

def phase_systemd_configure(target):
    """Configure baseline systemd state (graphical target, machine-id, hostname).

    Display-manager service enablement (SDDM/PLM) deliberately does NOT live
    here: this phase runs right after "systemd" in the phase list, before
    "kde" has built anything -- neither sddm nor plasmalogin exists on disk
    yet at this point in a fresh build, so an os.path.exists() check here was
    dead code on every from-scratch run. That logic lives in
    phase_plasma_configure instead, which runs after "kde".
    """
    log_phase("systemd-config", "Configure baseline systemd state")
    ensure(os.path.join(target, "etc", "systemd", "system"))

    # default.target → graphical.target
    default_link = os.path.join(target, "etc", "systemd", "system", "default.target")
    graphical    = "/lib/systemd/system/graphical.target"
    if not os.path.lexists(default_link):
        symlink(graphical, default_link)

    # Explicit GPU module load, independent of udev's own module auto-loading.
    # This systemd build compiles with -Dkmod=enabled (meson confirms
    # "Run-time dependency libkmod found"), but readelf shows no binary in the
    # final install actually links libkmod.so -- systemd 261's udev "kmod"
    # builtin apparently isn't wired the way older systemd versions were
    # (a stale libsystemd-shared-255.so left over from an earlier build DOES
    # link libkmod, 261 does not), so udev's own MODALIAS-triggered autoload
    # can't be trusted here. depmod (see phase_kernel) still populates a real
    # modules.dep/modules.alias, and standalone /usr/sbin/modprobe reads that
    # directly with zero dependency on systemd's own kmod linkage -- so call
    # it explicitly instead of relying on an auto-load path that may be inert.
    #
    # This is a drop-in on plasmalogin.service itself, NOT a standalone unit.
    # Three different standalone-unit strategies (DefaultDependencies=no +
    # Before=sysinit.target; the same with WantedBy=graphical.target +
    # Before=display-manager.service; both with output forced to console)
    # all passed `systemctl is-enabled`/`systemd-analyze verify` cleanly yet
    # produced zero trace in a real boot log across many boot-tested
    # rebuilds -- no Starting/Finished status line, not even a raw `echo
    # >/dev/console` from inside the unit's own ExecStart. Root cause never
    # isolated. plasmalogin.service is proven to run every single boot
    # (visible "Starting/Started Plasma Login Manager" every time), so
    # piggybacking on its own ExecStartPre via a drop-in sidesteps whatever
    # was silently dropping the standalone unit's job from the transaction.
    plasmalogin_dropin_dir = os.path.join(target, "etc", "systemd", "system",
                                           "plasmalogin.service.d")
    ensure(plasmalogin_dropin_dir)
    with open(os.path.join(plasmalogin_dropin_dir, "10-smechos-gpu-modules.conf"), "w") as f:
        f.write(textwrap.dedent("""\
            [Service]
            ExecStartPre=/bin/sh -c '\
                /usr/sbin/modprobe -v virtio_pci; \
                /usr/sbin/modprobe -v virtio_gpu; \
                /usr/sbin/modprobe -v amdgpu; \
                /usr/sbin/modprobe -v i915; \
                /usr/sbin/modprobe -v nouveau; \
                /usr/sbin/modprobe -v radeon; \
                true'
        """))

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

    log("Baseline systemd config applied.", color=GREEN)

def phase_locale(target):
    """Generate a real en_US.UTF-8 locale and install it into the rootfs.

    /etc/locale.conf has always correctly said LANG=en_US.UTF-8 (see
    phase_write_etc), but that locale was never actually *compiled* --
    /usr/share/i18n (locale source data) and /usr/lib/locale (the compiled
    archive) didn't exist at all, so every Qt/KDE process fell back to the
    "C" locale (issue #5: "No UTF-8 locale generated"). glibc's setlocale()
    doesn't error loudly on this -- callers get "C" silently -- but Qt
    checks explicitly and logs "Qt depends on a UTF-8 locale, but has
    failed to switch to one." QML/Kirigami text rendering doesn't route
    through the affected codepath and looks fine either way, which is why
    this was easy to miss: KWin's own window-decoration text (drawn via
    classic QPainter, not QML) is the one place it visibly breaks, as
    tofu-box glyphs in every window titlebar.

    en_US.UTF-8 (not just C.UTF-8) specifically because C.UTF-8 has no X11
    Compose file in any distro, so it doesn't actually establish Compose
    file lookups don't fail -- see "couldn't find a Compose file for locale
    C.UTF-8" in the same issue's downstream symptom.

    Locale data has no source in this pipeline the way KF6/Plasma do
    (there's no "locale-6.27.0.tar.xz" to download) -- it comes from the
    real Ubuntu 24.04 locales + libx11-data packages, matching how systemd
    itself is sourced in phase_systemd. Verify with a real chroot, not an
    LD_LIBRARY_PATH-substituted container run: glibc's locale loader treats
    /usr/lib/locale/locale-archive as an absolute host path via a plain
    openat(), so without an actual chroot it silently resolves against the
    container's own root instead of target's, and looks broken when it
    isn't (or vice versa).
    """
    log_phase("locale", "Generate en_US.UTF-8 locale")
    if os.path.exists(os.path.join(target, "usr/lib/locale/locale-archive")):
        log("locale already generated -- skipping", color=YELLOW)
        return

    dirs = (("/usr/share/i18n",       "usr/share/i18n"),
            ("/usr/share/X11/locale", "usr/share/X11/locale"),
            ("/usr/lib/locale",       "usr/lib/locale"))

    if _in_matching_build_image():
        # Already running inside an Ubuntu-24.04-ABI-matched container --
        # generate straight into it and copy out, no nested container needed.
        run(["apt-get", "update", "-qq"])
        run(["apt-get", "install", "-y", "-qq", "--no-install-recommends", "locales", "libx11-data"])
        run(["locale-gen", "en_US.UTF-8"])
        for src, dst in dirs:
            dst_path = os.path.join(target, dst)
            shutil.rmtree(dst_path, ignore_errors=True)
            ensure(os.path.dirname(dst_path))
            shutil.copytree(src, dst_path, dirs_exist_ok=True)
    else:
        cid = subprocess.run(
            ["sudo", "podman", "run", "-d", "ubuntu:24.04", "sleep", "300"],
            capture_output=True, text=True, check=True).stdout.strip()
        try:
            run(["sudo", "podman", "exec", cid, "bash", "-c",
                 "apt-get update -qq && "
                 "apt-get install -y -qq --no-install-recommends locales libx11-data && "
                 "locale-gen en_US.UTF-8"])
            for src, dst in dirs:
                dst_path = os.path.join(target, dst)
                shutil.rmtree(dst_path, ignore_errors=True)
                ensure(os.path.dirname(dst_path))
                run(["sudo", "podman", "cp", f"{cid}:{src}", dst_path])
        finally:
            run(["sudo", "podman", "stop", "-t", "0", cid])
    log("en_US.UTF-8 locale installed.", color=GREEN)

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
    # (already built once in phase_kde too; rebuilt here since this phase
    # can run standalone. Uses KF6_URL rather than a second hardcoded
    # frameworks path -- that was stale at the old "6.27" bleeding-edge
    # line and would 404 against the LTS-pinned 6.24 line.)
    ecm_ver = KF6_VER
    ecm_url = f"{KF6_URL}/extra-cmake-modules-{ecm_ver}.tar.xz"
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

    # Per-module Calamares configuration.
    #
    # settings.conf's `sequence:` names 15 modules, but building Calamares
    # itself installs none of their .conf files: CMakeAddModuleSubdirectory's
    # config-file install() only fires for the *module.desc* (job-module)
    # branch, gated on -DINSTALL_CONFIG, and even then it installs to
    # /usr/share/calamares/modules -- for CMakeLists.txt (C++/Qt-plugin)
    # modules, which is what welcome/locale/keyboard/partition/users/summary
    # are, the glob'd .conf is synced only into the *build directory* (for
    # `calamares -d`), never installed anywhere at all. Every real distro's
    # Calamares packaging supplies its own /etc/calamares/modules/*.conf --
    # upstream's tarball is not meant to be usable un-configured. Without
    # these, the ModuleManager fails to instantiate the modules named in
    # `sequence:` and Calamares does not present a working UI when launched.
    #
    # Copied from the still-on-disk extracted source (`bd`) rather than
    # hand-written, since these are exactly the same stock per-module
    # defaults every Calamares-based distro ships unless it has a reason to
    # deviate -- verified sane for SmechOS specifically: users.conf's
    # `sudoersGroup: wheel` matches the "wheel" (not Debian's "sudo") group
    # SmechOS's own rootfs already uses; bootloader.conf's grubInstall/
    # grubMkconfig/grubCfg defaults match our self-built GRUB 2.12 at
    # /boot/grub/grub.cfg; efi.mountPoint "/boot/efi" matches our grub-efi
    # build. summary/localecfg/networkcfg ship no .conf upstream either --
    # they're genuinely configless, not another instance of this gap.
    modules_etc = os.path.join(cal_etc, "modules")
    ensure(modules_etc)
    for mod in ("welcome", "locale", "keyboard", "partition", "users", "mount",
                "unpackfs", "machineid", "fstab", "grubcfg", "bootloader", "umount",
                "finished"):
        src_conf = os.path.join(bd, "src", "modules", mod, f"{mod}.conf")
        if os.path.isfile(src_conf):
            shutil.copy2(src_conf, os.path.join(modules_etc, f"{mod}.conf"))
        else:
            log(f"No stock {mod}.conf found in Calamares source — skipping", color=YELLOW)

    # unpackfs.conf's stock content is a dummy example (copies CHANGES and a
    # slideshow dir) -- point it at what phase_live_iso() actually produces:
    # a single squashfs holding the whole target rootfs, unsquashed to /.
    # Source path matches the live-initramfs init script (phase_live_initramfs),
    # which now bind-persists /mnt/cdrom into the switched-to root at the
    # same path specifically so this is still readable once the live desktop
    # session (and thus Calamares) is running.
    with open(os.path.join(modules_etc, "unpackfs.conf"), "w") as f:
        f.write(textwrap.dedent("""\
            ---
            unpack:
                -   source: "/mnt/cdrom/live/filesystem.squashfs"
                    sourcefs: "squashfs"
                    destination: ""
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

    # branding.desc references smechos.png/show.png/show.qml, but nothing
    # ever created them -- confirmed via a real boot test: `calamares` binary
    # itself launches fine (dbus/render-node/messagebus fixes all hold), but
    # immediately bails FATAL: "Slideshow file .../show.qml does not exist or
    # is not a valid QML file." Real SmechOS branding art doesn't exist yet,
    # so generate minimal placeholders (pure Python stdlib PNG writer, no new
    # build dependency) rather than block the installer on branding assets.
    def _write_minimal_png(path, size=256, rgb=(30, 30, 46)):
        w = h = size
        raw = bytearray()
        for _ in range(h):
            raw.append(0)
            raw.extend(bytes(rgb) * w)
        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        png += chunk(b"IEND", b"")
        with open(path, "wb") as f:
            f.write(png)
    _write_minimal_png(os.path.join(brand_dir, "smechos.png"), rgb=(137, 180, 250))
    _write_minimal_png(os.path.join(brand_dir, "show.png"), rgb=(30, 30, 46))
    with open(os.path.join(brand_dir, "show.qml"), "w") as f:
        f.write(textwrap.dedent("""\
            import QtQuick 2.0
            Item {
                id: presentation
                function activate() {}
                function deactivate() {}
            }
        """))

    # Auto-launch Calamares when booted from the "Install SmechOS
    # (Calamares)" GRUB entry. That menuentry has always passed
    # calamares=1 on the kernel command line, but nothing anywhere in this
    # codebase ever read it -- confirmed via a real boot test: selecting
    # that entry just landed on the normal live desktop with no installer
    # in sight. Standard live-CD pattern: an XDG autostart entry that
    # unconditionally runs every Plasma session, gated by a plain
    # /proc/cmdline grep so it's a no-op on the regular "Live" entries.
    autostart_dir = os.path.join(target, "etc", "xdg", "autostart")
    ensure(autostart_dir)
    with open(os.path.join(autostart_dir, "smechos-calamares-autostart.desktop"), "w") as f:
        f.write(textwrap.dedent("""\
            [Desktop Entry]
            Type=Application
            Name=SmechOS Installer
            Exec=/bin/sh -c 'grep -qw calamares=1 /proc/cmdline && exec calamares'
            NoDisplay=true
            X-KDE-autostart-phase=1
        """))
    log("Calamares installed.", color=GREEN)

def phase_firefox(target):
    """Download and extract the official Mozilla Firefox linux64 tarball
    into /opt/firefox. Unlike Chrome's Debian-repackaged .deb, upstream
    Firefox ships no system .desktop entry or apt/cron artifacts to strip --
    confirmed via the real tarball listing (firefox-154.0.tar.xz): a single
    top-level firefox/ dir containing the `firefox` binary directly, plus
    browser/chrome/icons/default/default{16,32,48,64,128}.png icons. Both
    facts (binary location, icon paths) drive the symlink/.desktop below."""
    log_phase("firefox", "Install Mozilla Firefox stable")
    src = sources(target)
    # download.mozilla.org 302-redirects to the real versioned tarball --
    # urlretrieve follows redirects automatically, and extract()'s `tar -xf`
    # auto-detects the compression format, so the exact upstream extension
    # (tar.xz today) doesn't need to be hardcoded here.
    url = "https://download.mozilla.org/?product=firefox-latest&os=linux64&lang=en-US"
    tarball = os.path.join(src, "firefox-latest-linux64.tar.xz")
    download(url, tarball)
    log("Extracting Firefox tarball...")
    opt_dir = os.path.join(target, "opt", "firefox")
    shutil.rmtree(opt_dir, ignore_errors=True)
    extract(tarball, opt_dir)  # strip=1 drops the tarball's own firefox/ top dir

    bin_link = os.path.join(target, "usr", "bin", "firefox")
    ensure(os.path.dirname(bin_link))
    if os.path.lexists(bin_link):
        os.remove(bin_link)
    symlink("/opt/firefox/firefox", bin_link)

    desktop_dir = os.path.join(target, "usr", "share", "applications")
    ensure(desktop_dir)
    with open(os.path.join(desktop_dir, "firefox.desktop"), "w") as f:
        f.write(textwrap.dedent("""\
            [Desktop Entry]
            Type=Application
            Name=Firefox
            GenericName=Web Browser
            Comment=Browse the World Wide Web
            Exec=/usr/bin/firefox %u
            Icon=/opt/firefox/browser/chrome/icons/default/default128.png
            Terminal=false
            MimeType=text/html;text/xml;application/xhtml+xml;application/xml;application/rss+xml;application/rdf+xml;image/gif;image/jpeg;image/png;x-scheme-handler/http;x-scheme-handler/https;
            StartupNotify=true
            Categories=Network;WebBrowser;
            """))
    log("Mozilla Firefox installed.", color=GREEN)

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
    # In-place substitution of the real "not set" line, not a blind append:
    # appending a second, duplicate CONFIG_STATIC=y entry while the original
    # "# CONFIG_STATIC is not set" line was still present got silently
    # dropped by `make oldconfig` (verified directly -- confirmed by
    # `ldd busybox` reporting real shared-lib dependencies afterward,
    # tracing back to the actual kernel panic "No working init found": the
    # dynamically-linked busybox couldn't find its interpreter inside the
    # initrd's own minimal filesystem, so /init could never exec at all).
    # No explicit config-sync step needed at all: unlike Linux's kbuild,
    # busybox 1.36.1's Makefile has no `olddefconfig` target (confirmed --
    # "No rule to make target"), and `oldconfig` is interactive-only and
    # unreliable fed EOF stdin under a non-interactive container run. Going
    # straight from the sed-fixed .config to `make` works: busybox's build
    # system regenerates/validates the config internally as part of the
    # normal build dependency chain. Re-verified end to end -- the build
    # log itself prints "Static linking against glibc..." and the resulting
    # `busybox` binary is confirmed by `ldd` to be "not a dynamic executable".
    cfg_text = cfg_text.replace("# CONFIG_STATIC is not set", "CONFIG_STATIC=y")
    with open(cfg, "w") as f: f.write(cfg_text)
    run(["make", "-j", nproc()], cwd=bd, env=env)

    # Assemble initramfs tree
    init_tree = os.path.join(BUILD_TMP, "live-initramfs")
    shutil.rmtree(init_tree, ignore_errors=True)
    for d in ["bin", "dev", "proc", "sys",
              "mnt/cdrom", "mnt/squashfs", "mnt/overlay", "mnt/rootfs"]:
        ensure(os.path.join(init_tree, d))

    shutil.copy2(os.path.join(bd, "busybox"), os.path.join(init_tree, "bin", "busybox"))
    os.chmod(os.path.join(init_tree, "bin", "busybox"), 0o755)
    for applet in ["sh", "mount", "mkdir", "ln", "switch_root", "mdev", "chroot", "sleep", "chmod"]:
        link = os.path.join(init_tree, "bin", applet)
        if not os.path.lexists(link):
            symlink("busybox", link)

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

            # Load GPU modules here, in the initramfs, before switch_root --
            # not via a systemd unit. Every systemd-based attempt (a
            # standalone unit under several different orderings, then a
            # plasmalogin.service.d ExecStartPre drop-in piggybacked on a
            # unit proven to run every boot) reported success via
            # `systemctl is-enabled`/real "Started" boot-status lines yet
            # produced zero evidence the modprobe calls inside ever actually
            # ran (no console output via any redirect method, no resulting
            # [drm] kernel printk). Root cause never isolated. This runs as
            # real root with no systemd/sandboxing involved at all, and a
            # loaded kernel module's state isn't tied to which userspace
            # root is active, so it persists across switch_root regardless.
            chroot /mnt/rootfs /usr/sbin/modprobe -v virtio_pci
            chroot /mnt/rootfs /usr/sbin/modprobe -v virtio_gpu
            chroot /mnt/rootfs /usr/sbin/modprobe -v amdgpu
            chroot /mnt/rootfs /usr/sbin/modprobe -v i915
            chroot /mnt/rootfs /usr/sbin/modprobe -v nouveau
            chroot /mnt/rootfs /usr/sbin/modprobe -v radeon

            # 60-drm.rules ships GROUP="render" MODE="0666" for renderD*,
            # but a real boot confirmed via a delayed diagnostic that this
            # never actually applies -- the device stays root:root 600
            # regardless (adding a "render" group to /etc/group made no
            # difference either, since the rule's own MODE=0666 would have
            # made group membership irrelevant anyway had the rule run at
            # all). udev's own rule application isn't functioning for this
            # device in this build for reasons not otherwise isolated, so
            # fix permissions directly here instead of relying on it --
            # this is what caused kwin_wayland_wr to immediately dump core
            # on every boot despite virtio_gpu itself loading correctly.
            chroot /mnt/rootfs /bin/chmod 666 /dev/dri/renderD128
            chroot /mnt/rootfs /bin/chmod 666 /dev/dri/card0

            # Also persist the boot medium itself at the same path post-switch:
            # switch_root discards every mount left in the old root except
            # ones explicitly moved first, and Calamares' unpackfs module
            # (running later, inside the live desktop session) needs to read
            # /mnt/cdrom/live/filesystem.squashfs to actually install --
            # without this move it would find nothing there at all.
            mkdir -p /mnt/rootfs/mnt/cdrom
            mount --move /mnt/cdrom /mnt/rootfs/mnt/cdrom

            # Delayed background diagnostic, forked BEFORE switch_root so it
            # survives as an orphan process outside PID 1's own image
            # (switch_root replaces PID 1 via exec + deletes the old root's
            # files, but doesn't SIGKILL unrelated sibling processes). Every
            # systemd-side attempt to get real diagnostic output post-boot
            # (a standalone unit under 3 different orderings, a
            # plasmalogin.service.d ExecStartPre drop-in, then an
            # ExecStartPost drop-in writing straight to /dev/ttyS0) produced
            # zero visible trace despite plasmalogin.service reliably
            # reporting "Started" -- this sidesteps the whole unit-execution
            # mystery by never going through systemd's job/unit machinery
            # for the diagnostic at all.
            #
            # chroot immediately (while the initramfs's own busybox, holding
            # the chroot applet, still exists) rather than after sleeping:
            # a first attempt slept 40s *then* chrooted and hit "chroot: not
            # found" -- switch_root's cleanup of the old initramfs root had
            # already deleted the busybox binary out from under this
            # already-running background process by then. Sleeping *inside*
            # the chroot instead uses the target rootfs's own persistent
            # /bin/sh + sleep, unaffected by the old root's deletion.
            chroot /mnt/rootfs /bin/sh -c '\
                sleep 40; \
                echo "=== SMECHOS DELAYED DIAG ===" >/dev/ttyS0; \
                systemctl status plasmalogin.service --no-pager -l >>/dev/ttyS0 2>&1; \
                echo "=== JOURNAL ===" >>/dev/ttyS0; \
                journalctl -u plasmalogin --no-pager -n 100 >>/dev/ttyS0 2>&1; \
                echo "=== KWIN JOURNAL ===" >>/dev/ttyS0; \
                journalctl _COMM=kwin_wayland_wr --no-pager -n 100 >>/dev/ttyS0 2>&1; \
                echo "=== COREDUMP ===" >>/dev/ttyS0; \
                coredumpctl info --no-pager -1 >>/dev/ttyS0 2>&1; \
                echo "=== CMDLINE ===" >>/dev/ttyS0; \
                cat /proc/cmdline >>/dev/ttyS0 2>&1; \
                echo "" >>/dev/ttyS0; \
                echo "=== CALAMARES PROC ===" >>/dev/ttyS0; \
                ps aux | grep calamares.bin >>/dev/ttyS0 2>&1; \
                echo "=== AUTOSTART EXEC TEST ===" >>/dev/ttyS0; \
                grep -qw calamares=1 /proc/cmdline; echo "grep exit: $?" >>/dev/ttyS0; \
                echo "=== CALAMARES DIRECT LAUNCH TEST (5s) ===" >>/dev/ttyS0; \
                su smech -c "DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 XDG_RUNTIME_DIR=/run/user/1000 timeout 5 calamares" >>/dev/ttyS0 2>&1; \
                echo "calamares direct exit: $?" >>/dev/ttyS0; \
                echo "=== DBUS STATUS ===" >>/dev/ttyS0; \
                systemctl status dbus-daemon.service dbus-broker.service --no-pager -l >>/dev/ttyS0 2>&1; \
                echo "=== DBUS JOURNAL ===" >>/dev/ttyS0; \
                journalctl -u dbus-daemon -u dbus-broker --no-pager -n 60 >>/dev/ttyS0 2>&1; \
                echo "=== DRI ===" >>/dev/ttyS0; \
                ls -la /dev/dri >>/dev/ttyS0 2>&1; \
                echo "=== DIAG END ===" >>/dev/ttyS0' &

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

# ── SmechOS Bitcoin Edition phases ──────────────────────────────────────────────
#
# NOTE: unlike the rest of this file, these four phases have not been build-
# tested end-to-end -- there's no way to run a multi-hour Bitcoin Core
# compile or validate OpenCL mining against a real old-Radeon card in this
# environment. Written as carefully as reasoning-from-documentation allows;
# expect real iteration once actually run on the target P5KPL-class hardware,
# same as everything else in this pipeline that started as a first pass.

def phase_mesa_cl(target):
    """Narrow Mesa build: just Clover (OpenCL) + the r600 gallium driver.

    Unlike phase_mesa() (built for the live-desktop profile -- Vulkan, EGL,
    GLX, the works), this profile is headless and only needs GPU *compute*
    for the miner, not a display stack. r600 covers the classic pre-GCN
    Radeon HD 2000-6000 series (matches CONFIG_DRM_RADEON, not amdgpu).
    """
    log_phase("mesa-cl", f"Compile Mesa {MESA_VER} (Clover/OpenCL + r600 only)")
    src     = sources(target)
    tarball = os.path.join(src, f"mesa-{MESA_VER}.tar.xz")
    download(MESA_URL, tarball)
    bd = os.path.join(BUILD_TMP, "mesa-cl")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    meson_install(bd, f"{target}/usr",
        extra_args=[
            "-Dgallium-drivers=r600",
            "-Dgallium-opencl=icd",
            "-Dvulkan-drivers=",
            "-Dglx=disabled", "-Degl=disabled", "-Dgbm=disabled",
            "-Dopengl=false", "-Dgles1=disabled", "-Dgles2=disabled",
            "-Dplatforms=", "-Dglvnd=disabled", "-Db_lto=false",
        ],
        env=build_env_bitcoin(target),
        build_dir=os.path.join(BUILD_TMP, "mesa-cl-build"))
    log(f"Mesa {MESA_VER} (Clover/r600) installed.", color=GREEN)

def phase_bitcoind(target):
    """Build Bitcoin Core from source via its own contrib/depends system.

    depends builds Boost, libevent, sqlite, and everything else Bitcoin
    Core needs from scratch in a self-contained way -- deliberately reused
    here rather than hand-rolling separate from-source phases for each of
    those (Bitcoin Core's own build system is the actively-maintained,
    correct-by-construction way to get this dependency set right; this
    pipeline has no way to independently verify hand-rolled equivalents
    without a real build cycle).

    NO_QT=1 skips the GUI entirely (headless target, no Qt needed here).
    """
    log_phase("bitcoind", f"Compile Bitcoin Core {BITCOIN_VER} (bitcoind, headless)")
    src     = sources(target)
    tarball = os.path.join(src, f"bitcoin-{BITCOIN_VER}.tar.gz")
    download(BITCOIN_URL, tarball)
    bd = os.path.join(BUILD_TMP, "bitcoin")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    env = build_env_bitcoin(target)
    host_triplet = "x86_64-pc-linux-gnu"

    run(["make", f"-j{nproc()}", f"HOST={host_triplet}",
         "NO_QT=1", "NO_UPNP=1", "NO_NATPMP=1", "NO_ZMQ=1"],
        cwd=os.path.join(bd, "depends"), env=env)

    depends_prefix = os.path.join(bd, "depends", host_triplet)
    run(["./autogen.sh"], cwd=bd, env=env)
    run(["./configure", f"--prefix={depends_prefix}",
         "--disable-tests", "--disable-bench", "--without-gui",
         "--disable-fuzz-binary"],
        cwd=bd, env=env)
    run(["make", f"-j{nproc()}"], cwd=bd, env=env)

    dest_bin = os.path.join(target, "usr", "bin")
    ensure(dest_bin)
    for binname in ("bitcoind", "bitcoin-cli"):
        shutil.copy2(os.path.join(bd, "src", binname), os.path.join(dest_bin, binname))
        os.chmod(os.path.join(dest_bin, binname), 0o755)
    log(f"Bitcoin Core {BITCOIN_VER} (bitcoind, bitcoin-cli) installed.", color=GREEN)

def phase_gpu_miner(target):
    """Build sgminer (OpenCL GPU mining, cgminer's actively-maintained fork
    for GPU support -- upstream cgminer dropped OpenCL/GPU mining in favor
    of ASIC-only once GPU mining stopped being remotely competitive; sgminer
    forked specifically to keep GPU support alive). --disable-curses since
    this profile is headless -- no terminal to attach a local UI to anyway.
    """
    log_phase("gpu-miner", f"Compile sgminer {SGMINER_VER} (OpenCL)")
    src     = sources(target)
    tarball = os.path.join(src, f"sgminer-{SGMINER_VER}.tar.gz")
    download(SGMINER_URL, tarball)
    bd = os.path.join(BUILD_TMP, "sgminer")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    env = build_env_bitcoin(target)
    run(["./autogen.sh"], cwd=bd, env=env, check=False)  # autogen.sh commonly warns, non-fatal
    run(["./configure", f"--prefix={target}/usr",
         "--enable-opencl", "--disable-curses", "--disable-adl"],
        cwd=bd, env=env)
    run(["make", f"-j{nproc()}"], cwd=bd, env=env)
    run(["make", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))
    log(f"sgminer {SGMINER_VER} installed.", color=GREEN)

def phase_bitcoin_services(target):
    """Dedicated bitcoin system user, default bitcoin.conf (pruned -- assume
    modest disk unless told otherwise), OpenRC services for bitcoind +
    sgminer, and a minimal headless status page (no local display on this
    profile, so status has to be network-reachable).

    Also removes the sddm symlink phase_openrc() unconditionally wires into
    the default runlevel for every profile -- this profile has no display
    manager at all.
    """
    log_phase("bitcoin-services", "Configure bitcoind + sgminer services, status page")
    etc = os.path.join(target, "etc")

    passwd = os.path.join(etc, "passwd")
    if os.path.exists(passwd):
        with open(passwd) as f:
            has_user = any(line.startswith("bitcoin:") for line in f)
        if not has_user:
            with open(passwd, "a") as f:
                f.write("bitcoin:x:900:900:bitcoind service:/var/lib/bitcoind:/sbin/nologin\n")
        group = os.path.join(etc, "group")
        with open(group) as f:
            has_group = any(line.startswith("bitcoin:") for line in f)
        if not has_group:
            with open(group, "a") as f:
                f.write("bitcoin:x:900:\n")

    data_dir = os.path.join(target, "var", "lib", "bitcoind")
    ensure(data_dir)
    bitcoin_conf_dir = os.path.join(etc, "bitcoin")
    ensure(bitcoin_conf_dir)
    with open(os.path.join(bitcoin_conf_dir, "bitcoin.conf"), "w") as f:
        f.write(textwrap.dedent("""\
            # Pruned mode -- assumes modest disk on old hardware. Raise or
            # remove `prune` if the target has room for a full/unpruned node.
            prune=550
            server=1
            daemon=0
            datadir=/var/lib/bitcoind
            rpcbind=127.0.0.1
            rpcallowip=127.0.0.1
        """))

    init_dir = os.path.join(etc, "init.d")
    ensure(init_dir)
    bitcoind_init = os.path.join(init_dir, "bitcoind")
    with open(bitcoind_init, "w") as f:
        f.write(textwrap.dedent("""\
            #!/sbin/openrc-run
            name="bitcoind"
            command="/usr/bin/bitcoind"
            command_args="-conf=/etc/bitcoin/bitcoin.conf"
            command_user="bitcoin:bitcoin"
            command_background=true
            pidfile="/run/bitcoind.pid"
            depend() { need localmount net.lo; }
        """))
    os.chmod(bitcoind_init, 0o755)

    sgminer_init = os.path.join(init_dir, "sgminer")
    with open(sgminer_init, "w") as f:
        f.write(textwrap.dedent("""\
            #!/sbin/openrc-run
            name="sgminer"
            command="/usr/bin/sgminer"
            command_args="--opencl-platform 0 --opencl-devices 0 -o localhost:0 --quiet"
            command_background=true
            pidfile="/run/sgminer.pid"
            depend() { need bitcoind; }
        """))
    os.chmod(sgminer_init, 0o755)

    status_dir = os.path.join(target, "usr", "share", "smechos-bitcoin")
    ensure(status_dir)
    with open(os.path.join(status_dir, "status.py"), "w") as f:
        f.write(textwrap.dedent("""\
            #!/usr/bin/env python3
            # Minimal headless status page -- no local display on this
            # profile, so this is the only way to see sync/mining state.
            import json
            import subprocess
            from http.server import BaseHTTPRequestHandler, HTTPServer

            def bitcoin_cli(*args):
                try:
                    out = subprocess.run(
                        ["bitcoin-cli", "-conf=/etc/bitcoin/bitcoin.conf", *args],
                        capture_output=True, text=True, timeout=10)
                    return out.stdout.strip()
                except Exception as e:
                    return f"error: {e}"

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    info = bitcoin_cli("getblockchaininfo")
                    body = f"<pre>bitcoind:\\n{info}\\n</pre>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(body.encode())

            if __name__ == "__main__":
                HTTPServer(("0.0.0.0", 8333), Handler).serve_forever()
        """))
    os.chmod(os.path.join(status_dir, "status.py"), 0o755)

    status_init = os.path.join(init_dir, "smechos-bitcoin-status")
    with open(status_init, "w") as f:
        f.write(textwrap.dedent("""\
            #!/sbin/openrc-run
            name="smechos-bitcoin-status"
            command="/usr/bin/python3"
            command_args="/usr/share/smechos-bitcoin/status.py"
            command_background=true
            pidfile="/run/smechos-bitcoin-status.pid"
            depend() { need bitcoind net.lo; }
        """))
    os.chmod(status_init, 0o755)

    default_rl = os.path.join(etc, "runlevels", "default")
    ensure(default_rl)
    sddm_link = os.path.join(default_rl, "sddm")
    if os.path.lexists(sddm_link):
        os.remove(sddm_link)
    for svc in ("bitcoind", "sgminer", "smechos-bitcoin-status"):
        link = os.path.join(default_rl, svc)
        if not os.path.lexists(link):
            symlink(f"/etc/init.d/{svc}", link)

    log("bitcoind + sgminer services configured, sddm disabled (headless profile).", color=GREEN)

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
    # NOT /tmp: this build always runs inside an ephemeral `podman run --rm`
    # container whose /tmp is discarded the instant it exits -- same bug
    # class already fixed once for phase_bundle_packages's output path.
    iso_out = os.path.join(os.path.dirname(target.rstrip("/")), "smechos-iso-output")
    ensure(iso_out)
    squashfs_path = os.path.join(iso_out, "smechos-filesystem.squashfs")
    iso_path      = os.path.join(iso_out, "smechos-plasma-live.iso")
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
    # -Xdict-size 100% (once used for maximum XZ compression) silently
    # corrupts small-file content on this ~1.8GB rootfs -- confirmed directly:
    # rebuilding identical content with plain "-comp xz" (squashfs-tools'
    # sane default dict size) or "-comp gzip" both correctly captured
    # /etc/passwd edits (474 bytes, verified via mount+cat+wc), while
    # "-Xdict-size 100%" reproducibly wrote back a stale 3-line/~150-byte
    # version every single time across many rebuilds. Root cause not
    # isolated further (likely a squashfs-tools bug or resource limit at
    # that extreme a dictionary size for a filesystem this large), but the
    # fix is simply not to use it -- default XZ dict sizing still compresses
    # well and doesn't corrupt data.
    run([mksquashfs, target, squashfs_path,
         "-comp", "xz",
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
        menuentry "SmechOS KDE Plasma (Live, debug console)" {
            linux  /boot/vmlinuz boot=live console=ttyS0,115200n8 console=tty0 loglevel=7 systemd.log_level=debug
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
    ("systemd-config",  phase_systemd_configure,         "Configure baseline systemd state"),
    ("locale",          phase_locale,                    "Generate en_US.UTF-8 locale"),
    ("grub",            phase_grub,                      "Compile GRUB 2.12 EFI + BIOS"),
    ("qt-deps",         phase_qt_deps,                   "Compile Qt6 modules"),
    ("mesa",            phase_mesa,                      "Compile Mesa stack"),
    ("cmake-bootstrap",    phase_cmake_bootstrap,        f"Bootstrap CMake {CMAKE_BOOTSTRAP_VER}"),
    ("wayland",            phase_wayland,                f"Build wayland {WAYLAND_VER}"),
    ("wayland-protocols",  phase_wayland_protocols,      f"Build wayland-protocols {WAYLAND_PROTO_VER}"),
    ("libinput",           phase_libinput,               f"Build libinput {LIBINPUT_VER}"),
    # libeis skipped: gitlab releases require auth; not in kwin's REQUIRED list (EIS feature optional)
    ("kde",                phase_kde,                    "Compile KDE Frameworks + Plasma"),
    ("plasma-configure",phase_plasma_configure,          "Configure display manager (PLM, fallback SDDM)"),
    ("kwin-deps",       phase_kwin_deps,                 "Copy KWin dependencies"),
    ("xwayland-deps",   phase_xwayland_deps,             "Fetch Xwayland + xkbcomp + xkb-data"),
    ("qt6uitools",      phase_qt6uitools,                "Ensure Qt6UITools present"),
    ("kernel",          phase_kernel,                    "Compile Linux 6.12.16"),
    ("firmware",        phase_firmware,                  "Bundle full linux-firmware tree"),
    ("patch-metadata",  phase_patch_metadata,            "Patch metadata for SmechOS branding"),
    ("discover",        phase_plasma_discover,           "Compile Plasma Discover + PackageKit"),
    ("calamares",       phase_calamares,                 "Build Calamares graphical installer"),
    ("firefox",         phase_firefox,                   "Install Mozilla Firefox stable"),
    ("live-initramfs",  phase_live_initramfs,            "Build busybox live initramfs"),
    ("bundle",          phase_bundle_packages,           "Bundle output into spk-installable .tar.xz packages"),
]

# Headless, no desktop -- old LGA775 Pentium/Celeron (Core-derived, no
# SSE4.2/AVX) + a pre-GCN Radeon card for OpenCL mining. Reuses the lean
# OpenRC/no-desktop phases already proven by SMECHVISOR_PHASES, but glibc
# for the userland (see build_env_bitcoin()) since Bitcoin Core and sgminer
# are both far more commonly built against glibc than musl.
SMECHOS_BITCOIN_PHASES = [
    ("userland-glibc",     phase_bootstrap_userland_glibc, "Bootstrap GNU userland against host glibc"),
    ("etc",                phase_write_etc,                 "Write /etc skeleton"),
    ("openrc",              phase_openrc,                   "Deploy OpenRC"),
    ("inittab",             phase_inittab,                  "Write inittab"),
    ("kernel",              phase_kernel,                   "Compile Linux 6.12.16 (CONFIG_DRM_RADEON)"),
    ("grub",                phase_grub,                     "Compile GRUB 2.12 EFI + BIOS"),
    ("firmware",            phase_firmware,                 "Bundle full linux-firmware tree"),
    ("mesa-cl",             phase_mesa_cl,                  f"Compile Mesa {MESA_VER} (Clover/OpenCL + r600 only)"),
    ("bitcoind",            phase_bitcoind,                 f"Compile Bitcoin Core {BITCOIN_VER} (headless)"),
    ("gpu-miner",           phase_gpu_miner,                f"Compile sgminer {SGMINER_VER} (OpenCL)"),
    ("bitcoin-services",    phase_bitcoin_services,         "Configure bitcoind + sgminer services, status page"),
]

PROFILES = {
    "smechos":            SMECHOS_PHASES,
    "smechvisor":         SMECHVISOR_PHASES,
    "smechos-plasma-live": SMECHOS_PLASMA_LIVE_PHASES,
    "smechos-bitcoin":     SMECHOS_BITCOIN_PHASES,
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
