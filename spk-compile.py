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

import argparse
import os
import sys
import subprocess
import shutil
import urllib.request
import time
import textwrap

# ── Version & constants ───────────────────────────────────────────────────────

VERSION = "2.2.0"
DEFAULT_TARGET = "/mnt/smechos_build_root"
BUILD_TMP = "/tmp/smechos_build"

# Source versions
LINUX_VER      = "6.12.16"
GRUB_VER       = "2.12"
MUSL_VER       = "1.2.5"
QT6_VER        = "6.8.2"
PLASMA_VER     = "6.3.4"
KF6_VER        = "6.10.0"
MESA_VER       = "24.3.4"
OPENRC_VER     = "0.54"
APPSTREAM_VER  = "1.0.3"
PACKAGEKIT_VER = "1.3.0"
SYSTEMD_VER    = "256.7"
CALAMARES_VER  = "3.3.10"
BUSYBOX_VER    = "1.36.1"

# Download URLs
LINUX_URL    = f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{LINUX_VER}.tar.xz"
GRUB_URL     = f"https://ftp.gnu.org/gnu/grub/grub-{GRUB_VER}.tar.xz"
MUSL_URL     = f"https://musl.libc.org/releases/musl-{MUSL_VER}.tar.gz"
QT6_BASE_URL = f"https://download.qt.io/official_releases/qt/6.8/{QT6_VER}/submodules"
PLASMA_URL   = f"https://download.kde.org/stable/plasma/{PLASMA_VER}"
KF6_URL      = f"https://download.kde.org/stable/frameworks/6.10"
MESA_URL     = f"https://mesa.freedesktop.org/archive/mesa-{MESA_VER}.tar.xz"
OPENRC_URL   = f"https://github.com/OpenRC/openrc/archive/refs/tags/{OPENRC_VER}.tar.gz"

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
    return e

def build_env_glibc(target):
    """Like build_env() but uses system glibc/gcc instead of musl-gcc."""
    e = dict(os.environ)
    e["SMECH_TARGET"] = target
    e.pop("TARGET", None)
    e.pop("CC",  None)
    e.pop("CXX", None)
    prefix = f"{target}/usr"
    e["PATH"] = f"{prefix}/bin:{e.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    e["PKG_CONFIG_PATH"] = (
        f"{prefix}/lib/pkgconfig:{prefix}/share/pkgconfig:"
        "/usr/lib/x86_64-linux-gnu/pkgconfig:/usr/share/pkgconfig:/usr/lib/pkgconfig"
    )
    e["CFLAGS"]   = f"-I{prefix}/include"
    e["CXXFLAGS"] = f"-I{prefix}/include"
    e["LDFLAGS"]  = f"-L{prefix}/lib"
    e["LD_LIBRARY_PATH"] = f"{prefix}/lib"
    return e

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
    run(["cmake", src_dir,
         "-G", "Ninja",
         f"-DCMAKE_INSTALL_PREFIX={prefix}",
         "-DCMAKE_BUILD_TYPE=Release",
         ] + (extra_args or []), cwd=bd, env=env)
    run(["ninja", "-j", nproc()], cwd=bd, env=env)
    run(["ninja", "install"], cwd=bd, env=env, sudo=(os.geteuid() != 0))

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
    env = build_env(target)
    prefix = f"{target}/usr"

    modules = [
        ("qtbase",        ["-DFEATURE_sql=OFF", "-DFEATURE_testlib=OFF"]),
        ("qtshadertools", []),
        ("qtdeclarative", []),
        ("qtsvg",         []),
        ("qtwayland",     []),
        ("qtmultimedia",  []),
        ("qt5compat",     []),
        ("qttranslations",[]),
    ]
    for name, extra in modules:
        fname   = f"{name}-everywhere-src-{QT6_VER}.tar.xz"
        url     = f"{QT6_BASE_URL}/{fname}"
        tarball = os.path.join(src, fname)
        download(url, tarball)
        bd = os.path.join(BUILD_TMP, f"qt6-{name}")
        if not os.path.exists(bd):
            extract(tarball, bd)
        cmake_install(bd, prefix,
            extra_args=[f"-DCMAKE_PREFIX_PATH={prefix}",
                        "-DBUILD_TESTING=OFF",
                        "-DQT_BUILD_TESTS=OFF",
                        "-DQT_BUILD_EXAMPLES=OFF"] + extra,
            env=env,
            build_dir=os.path.join(BUILD_TMP, f"qt6-{name}-build"))
        log(f"Qt6/{name} done.", color=GREEN)

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
            "-Dgallium-drivers=radeonsi,nouveau,swrast",
            "-Dvulkan-drivers=amd,nouveau",
            "-Dglx=dri", "-Degl=enabled", "-Dgbm=enabled",
            "-Dopengl=true", "-Dgles1=enabled", "-Dgles2=enabled",
            "-Dshared-glapi=enabled",
            "-Dplatforms=x11,wayland",
            "-Dglvnd=disabled", "-Db_lto=false",
        ],
        env=build_env(target),
        build_dir=os.path.join(BUILD_TMP, "mesa-build"))
    log(f"Mesa {MESA_VER} installed.", color=GREEN)

def _kde_pkg(name, version, base_url, target, env):
    src     = sources(target)
    fname   = f"{name}-{version}.tar.xz"
    tarball = os.path.join(src, fname)
    download(f"{base_url}/{fname}", tarball)
    bd = os.path.join(BUILD_TMP, f"kde-{name}")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, f"{target}/usr",
        extra_args=[f"-DCMAKE_PREFIX_PATH={target}/usr",
                    "-DBUILD_TESTING=OFF", "-DBUILD_QCH=OFF"],
        env=env,
        build_dir=os.path.join(BUILD_TMP, f"kde-{name}-build"))
    log(f"{name} {version} done.", color=GREEN)

def phase_kde(target):
    log_phase("kde", f"Compile KDE Frameworks {KF6_VER} + Plasma {PLASMA_VER}")
    env = build_env(target)

    kf6 = [
        "extra-cmake-modules", "kconfig", "kguiaddons", "ki18n",
        "kitemviews", "sonnet", "kwidgetsaddons", "kcompletion",
        "kdbusaddons", "karchive", "kcoreaddons", "kjobwidgets",
        "kcrash", "kfilemetadata", "kglobalaccel", "kxmlgui",
        "kbookmarks", "kio", "knotifications", "kparts",
        "ktextwidgets", "kwindowsystem", "solid", "kdeclarative",
        "kiconthemes", "knewstuff", "kpackage", "kservice",
        "kwallet", "plasma-framework",
    ]
    for mod in kf6:
        _kde_pkg(mod, KF6_VER, KF6_URL, target, env)

    plasma = [
        "kwin", "plasma-workspace", "plasma-desktop",
        "plasma-nm", "plasma-pa", "powerdevil", "breeze",
        "systemsettings", "plasma-integration", "kdeplasma-addons",
        "ksystemstats", "kscreen", "libkscreen",
    ]
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
    env    = build_env(target)
    prefix = f"{target}/usr"

    # AppStream
    url     = f"https://github.com/ximion/appstream/releases/download/v{APPSTREAM_VER}/appstream-{APPSTREAM_VER}.tar.gz"
    tarball = os.path.join(src, f"appstream-{APPSTREAM_VER}.tar.gz")
    download(url, tarball)
    bd = os.path.join(BUILD_TMP, "appstream")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=["-DAPX_INSTALL_DOCS=OFF", "-DAPX_INSTALL_TESTS=OFF",
                    f"-DCMAKE_PREFIX_PATH={prefix}"],
        env=env, build_dir=os.path.join(BUILD_TMP, "appstream-build"))

    # PackageKit
    url     = f"https://www.freedesktop.org/software/PackageKit/releases/PackageKit-{PACKAGEKIT_VER}.tar.xz"
    tarball = os.path.join(src, f"PackageKit-{PACKAGEKIT_VER}.tar.xz")
    download(url, tarball)
    bd = os.path.join(BUILD_TMP, "packagekit")
    shutil.rmtree(bd, ignore_errors=True)
    extract(tarball, bd)
    cmake_install(bd, prefix,
        extra_args=["-DPK_BUILD_APTCC=OFF", "-DPK_BUILD_TESTS=OFF",
                    f"-DCMAKE_PREFIX_PATH={prefix}"],
        env=env, build_dir=os.path.join(BUILD_TMP, "packagekit-build"))

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
                    "-DWITH_FLATPAK=OFF", "-DWITH_FWUPD=OFF",
                    "-DWITH_PACKAGEKIT=ON",
                    f"-DCMAKE_PREFIX_PATH={prefix}"],
        env=env, build_dir=os.path.join(BUILD_TMP, "discover-build"))
    log("Plasma Discover + PackageKit + SPK backend installed.", color=GREEN)

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

def phase_systemd(target):
    """Install systemd {SYSTEMD_VER} by extracting Debian packages into the target."""
    log_phase("systemd", f"Install systemd {SYSTEMD_VER} from Debian packages")
    src = sources(target)
    ensure(target)
    deb_base = "https://ftp.debian.org/debian/pool/main/s/systemd"
    pkgs = [
        f"libsystemd0_{SYSTEMD_VER}-1_amd64.deb",
        f"libudev1_{SYSTEMD_VER}-1_amd64.deb",
        f"udev_{SYSTEMD_VER}-1_amd64.deb",
        f"systemd_{SYSTEMD_VER}-1_amd64.deb",
        f"systemd-sysv_{SYSTEMD_VER}-1_amd64.deb",
    ]
    for deb_name in pkgs:
        url = f"{deb_base}/{deb_name}"
        deb = os.path.join(src, deb_name)
        download(url, deb)
        log(f"Extracting {deb_name}...")
        _extract_deb(deb, target)
        log(f"{deb_name} extracted.", color=GREEN)

    # Enable NetworkManager if present
    wants = os.path.join(target, "etc", "systemd", "system", "multi-user.target.wants")
    ensure(wants)
    nm_service = os.path.join(target, "lib", "systemd", "system", "NetworkManager.service")
    nm_link    = os.path.join(wants, "NetworkManager.service")
    if os.path.exists(nm_service) and not os.path.lexists(nm_link):
        os.symlink(nm_service, nm_link)
    log("systemd installed.", color=GREEN)

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
    ecm_ver = "6.10.0"
    ecm_url = f"https://download.kde.org/stable/frameworks/6.10/extra-cmake-modules-{ecm_ver}.tar.xz"
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
    with open(os.path.join(bd, ".config"), "a") as f:
        f.write("\nCONFIG_STATIC=y\n")
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
    run([mkrescue, "-o", iso_path, f"--volume-id={label}", work_dir])

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
    ("kde",              phase_kde,                 "Compile KDE Frameworks + Plasma"),
    ("plasma-configure", phase_plasma_configure,    "Configure Plasma/SDDM session"),
    ("kwin-deps",        phase_kwin_deps,           "Copy KWin dependencies"),
    ("qt6uitools",       phase_qt6uitools,          "Ensure Qt6UITools present"),
    ("kernel",           phase_kernel,              "Compile Linux 6.12.16"),
    ("patch-metadata",   phase_patch_metadata,      "Patch metadata for SmechOS branding"),
    ("discover",         phase_plasma_discover,     "Compile Plasma Discover + PackageKit"),
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
    ("kde",             phase_kde,                       "Compile KDE Frameworks + Plasma"),
    ("plasma-configure",phase_plasma_configure,          "Configure Plasma/SDDM session"),
    ("kwin-deps",       phase_kwin_deps,                 "Copy KWin dependencies"),
    ("qt6uitools",      phase_qt6uitools,                "Ensure Qt6UITools present"),
    ("kernel",          phase_kernel,                    "Compile Linux 6.12.16"),
    ("patch-metadata",  phase_patch_metadata,            "Patch metadata for SmechOS branding"),
    ("discover",        phase_plasma_discover,           "Compile Plasma Discover + PackageKit"),
    ("calamares",       phase_calamares,                 "Build Calamares graphical installer"),
    ("chrome",          phase_google_chrome,             "Install Google Chrome stable"),
    ("live-initramfs",  phase_live_initramfs,            "Build busybox live initramfs"),
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
    print(f"{MAGENTA}{BOLD}{'='*64}{R}\n")

    for name, fn, desc in phases:
        t0 = time.time()
        fn(target)
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
