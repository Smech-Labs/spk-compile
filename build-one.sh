#!/bin/bash
# build-one.sh -- build a single KDE/Plasma package against an existing
# SmechOS rootfs, without rebuilding the whole distribution.
#
# Most open issues on the tracker are "package X was never added to the
# component list in spk-compile.py". The code change is one line; the
# expensive part is verifying it. This script is the cheap path: it builds
# just that package and installs it into a rootfs you already have.
#
# WHY A CONTAINER
#
# The SmechOS rootfs ships no libc headers (no /usr/include/bits), so it
# cannot be used as a build sysroot. Building on a modern host instead means
# host glibc headers against target glibc libs, and the target's own build
# tools (qtpaths, moc, msgfmt) cannot run there either -- the target's
# libc.so.6 loaded by a newer host ld.so fails on __nptl_change_stack_perm.
#
# The rootfs is Ubuntu glibc 2.39, so ubuntu:24.04 matches it exactly.
# Inside that container everything is native: the rootfs's own tools run
# directly, headers and libs agree, and the output is ABI-correct by
# construction. No --sysroot, no ld.so wrappers, no bind-mounted shims.
#
# USAGE
#   ./build-one.sh <package> <version> [rootfs] [--gear]
#
#   ./build-one.sh spectacle 6.7.2 /path/to/rootfs
#   ./build-one.sh konsole 25.08.3 /path/to/rootfs --gear
#   ./build-one.sh kpty 6.27.0 /path/to/rootfs --kf6
#
# Plasma packages live under stable/plasma/<ver>/. KDE Gear applications
# (Konsole, Dolphin, ...) live under stable/release-service/<ver>/src/ and
# use a completely different version series -- pass --gear for those.
set -euo pipefail

PKG="${1:-}"
VER="${2:-}"
ROOT="${3:-/home/smech/smechos-work/root}"
TRACK="plasma"
for a in "$@"; do
    [ "$a" = "--gear" ] && TRACK="gear"
    [ "$a" = "--kf6" ] && TRACK="kf6"
done

if [ -z "$PKG" ] || [ -z "$VER" ]; then
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

case "$TRACK" in
    gear)
        URL="https://download.kde.org/stable/release-service/$VER/src/$PKG-$VER.tar.xz"
        ;;
    kf6)
        # Frameworks nest under a major.minor directory while the tarball
        # carries the full major.minor.patch -- 6.27.0 lives in 6.27/.
        KF6_DIR="${VER%.*}"
        URL="https://download.kde.org/stable/frameworks/$KF6_DIR/$PKG-$VER.tar.xz"
        ;;
    *)
        URL="https://download.kde.org/stable/plasma/$VER/$PKG-$VER.tar.xz"
        ;;
esac

WORK="${SMECH_BUILD_TMP:-/mnt/smechos_build_tmp}"
SRC="$WORK/one-$PKG"
BD="$WORK/one-$PKG-build"

echo "package : $PKG-$VER  (track: $TRACK)"
echo "rootfs  : $ROOT"
echo "url     : $URL"

# Check via sudo: an extracted rootfs is normally root-owned drwxr-x---, so
# an unprivileged test cannot traverse it and would wrongly report a valid
# rootfs as invalid. unsquashfs run under sudo produces exactly that mode,
# which is the usual way to obtain one.
sudo test -d "$ROOT/usr/lib/x86_64-linux-gnu" \
    || { echo "ERROR: '$ROOT' does not look like a SmechOS rootfs"; exit 1; }
command -v podman >/dev/null || { echo "ERROR: podman is required"; exit 1; }

# kdoctools ships its own DocBook catalog (entity declarations for author
# names, GPL/FDL boilerplate, per-language strings) at
# usr/share/kf6/kdoctools/customization/catalog.xml, but the system catalog
# at etc/xml/catalog never delegates to it -- so any package that builds a
# handbook fails xmllint validation with "Entity 'underFDL' not defined" and
# similar, even though the entities are present on disk. One-time, idempotent
# fix, applied to the rootfs itself so it only needs doing once ever.
KDOCTOOLS_CATALOG_REL="../../usr/share/kf6/kdoctools/customization/catalog.xml"
if [ -f "$ROOT/usr/share/kf6/kdoctools/customization/catalog.xml" ] \
   && [ -f "$ROOT/etc/xml/catalog" ] \
   && ! sudo grep -q "kdoctools/customization/catalog.xml" "$ROOT/etc/xml/catalog"; then
    echo "=== wiring kdoctools DocBook catalog into $ROOT/etc/xml/catalog ==="
    sudo sed -i "s#</catalog>#  <nextCatalog catalog=\"$KDOCTOOLS_CATALOG_REL\"/>\n</catalog>#" "$ROOT/etc/xml/catalog"
fi

sudo mkdir -p "$WORK"
TARBALL="$WORK/$PKG-$VER.tar.xz"
if [ ! -f "$TARBALL" ]; then
    echo "=== downloading ==="
    sudo curl -fL --progress-bar -o "$TARBALL" "$URL" \
        || { echo "ERROR: download failed. Wrong version, or a Gear package without --gear?"; exit 1; }
fi

sudo rm -rf "$SRC" "$BD"
sudo mkdir -p "$SRC"
sudo tar --strip-components 1 -xf "$TARBALL" -C "$SRC"

# xdg-desktop-portal-kde's top-level CMakeLists.txt does
# add_subdirectory(autotests) with no BUILD_TESTING guard -- an upstream
# oversight -- so -DBUILD_TESTING=OFF does not stop it configuring tests
# that link Qt::Test, which was never find_package()'d because nothing else
# in the project needs it. Fails with "target Qt::Test ... was not found"
# even though Qt6Test itself is present in the rootfs.
if [ "$PKG" = "xdg-desktop-portal-kde" ] && [ -f "$SRC/CMakeLists.txt" ]; then
    sudo sed -i 's/^add_subdirectory(autotests)$/if(BUILD_TESTING)\n  add_subdirectory(autotests)\nendif()/' "$SRC/CMakeLists.txt"
fi

cat > "$WORK/one-build-inner.sh" <<'INNER'
set -euo pipefail
PKG="$1"; ROOT="$2"; SRC="$3"; BD="$4"
# The published image already has the toolchain, so skip the apt step --
# that download is otherwise the slowest part of a run and repeats every
# time. Falls back to installing when running on a bare ubuntu:24.04.
if [ -f /etc/smechos-build-image ]; then
    echo "=== prepared build image, toolchain present ==="
else
    echo "=== bare base image, installing toolchain ==="
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends \
        build-essential cmake ninja-build pkg-config patchelf gettext \
        libboost-dev libx11-dev libvulkan-dev libxkbcommon-dev libwayland-dev \
        libicu-dev libcups2-dev zlib1g-dev liblmdb-dev libdrm-dev docbook-xml docbook-xsl python3-pip libpam0g-dev libgcrypt20-dev >/dev/null
fi

# The rootfs is the same glibc as this container, so pointing the loader at
# it is safe here (and is what lets its qtpaths/moc/msgfmt run natively).
# The pulseaudio/ subdir holds libpulsecommon-*.so, a private versioned
# library libpulse.so.0 needs at link time (DT_NEEDED, not just dlopen) --
# without it, anything pulling in libpulse (Konsole, via KNotifications
# sound playback) fails with "undefined reference to pa_*" even though
# libpulse.so.0 itself resolves fine.
export LD_LIBRARY_PATH="$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/usr/lib:$ROOT/usr/lib/x86_64-linux-gnu/pulseaudio"
# Rootfs bin goes LAST. Putting it first makes the rootfs's own cmake win
# over the container's, and it then looks for its modules under a version
# directory that does not exist there:
#   Modules directory not found in <rootfs>/usr/share/cmake-3.28
# cmake locates the rootfs's qtpaths/moc/msgfmt through CMAKE_PREFIX_PATH by
# absolute path regardless, so nothing is lost by de-prioritising it here.
export PATH="$PATH:$ROOT/usr/bin"
# Rootfs tools that look up data files by XDG path rather than through
# CMAKE_PREFIX_PATH need this, otherwise they search their compiled-in
# prefix -- which is empty inside the container. kdoctools is the usual
# casualty: "kf.doctools.core: Error: Could not find kdoctools catalogs",
# even though the catalogs are present in the rootfs.
export XDG_DATA_DIRS="$ROOT/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"
# DocBook entity resolution for packages that build handbooks.
[ -f "$ROOT/etc/xml/catalog" ] && export XML_CATALOG_FILES="$ROOT/etc/xml/catalog"

# CMake Find modules that go through pkg-config (e.g. the WaylandProtocols
# lookup used by kwin/kwayland/xdg-desktop-portal-kde) resolve data-file
# paths using the .pc file's own hardcoded "prefix=/usr", producing an
# absolute container path like /usr/share/wayland-protocols/... rather than
# anything under $ROOT -- even though pkg-config correctly found the
# rootfs's .pc file in the first place. That path only actually exists in
# the rootfs's own build. Make the container's view of it agree.
if [ -d "$ROOT/usr/share/wayland-protocols" ] && [ ! -e /usr/share/wayland-protocols ]; then
    ln -s "$ROOT/usr/share/wayland-protocols" /usr/share/wayland-protocols
fi

echo "=== sanity: rootfs tools run natively ==="
"$ROOT/usr/bin/qtpaths" --query QT_INSTALL_PREFIX

rm -rf "$BD"; mkdir -p "$BD"; cd "$BD"
# -DKDE_INSTALL_PLUGINDIR=/usr/plugins: this rootfs's Qt6 was built with
# QT_INSTALL_PLUGINS=/usr/plugins (non-multiarch), not the lib/<triplet>/
# plugins path KDE's ECM/KDECMakeSettings computes by default. Without this,
# a package's Qt plugins (KCMs, kpart, applets, ...) build and install fine
# but land somewhere Qt's plugin loader never scans -- e.g. a KCM that
# builds cleanly and simply never appears in System Settings.
/usr/bin/cmake "$SRC" -G Ninja \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$ROOT/usr" \
    -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE \
    -DCMAKE_INSTALL_RPATH=/usr/lib/x86_64-linux-gnu \
    -DKDE_INSTALL_PLUGINDIR=/usr/plugins \
    -DBUILD_TESTING=OFF -DBUILD_QCH=OFF -DBUILD_PYTHON_BINDINGS=OFF

/usr/bin/ninja -j"$(nproc)"
DESTDIR="$ROOT" /usr/bin/cmake --install "$BD" | tee /tmp/install.log

# cmake bakes the staging path into RUNPATH even with
# CMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE. Left alone that ships the
# builder's home directory inside the ISO, so strip it.
echo "=== cleaning RUNPATH ==="
grep -oP '(?<=^-- Installing: )\S+' /tmp/install.log | while read -r f; do
    [ -f "$f" ] || continue
    head -c4 "$f" | grep -q ELF || continue
    if patchelf --print-rpath "$f" 2>/dev/null | grep -q "$ROOT"; then
        patchelf --set-rpath /usr/lib/x86_64-linux-gnu "$f"
        echo "  fixed: ${f#$ROOT}"
    fi
done
echo "INSTALL_OK"
INNER

# Prefer the prepared image (toolchain baked in). If it cannot be obtained
# -- offline, or before it has been published -- fall back to a bare
# ubuntu:24.04 and let the inner script install the toolchain itself. The
# base must stay 24.04 either way: it is glibc 2.39 like the rootfs, which
# is the whole reason this builds correctly.
IMAGE="${SMECH_BUILD_IMAGE:-ghcr.io/smech-labs/smechos-build:latest}"
if ! sudo podman image exists "$IMAGE" 2>/dev/null; then
    if ! sudo podman pull -q "$IMAGE" >/dev/null 2>&1; then
        echo "note: $IMAGE unavailable, falling back to ubuntu:24.04"
        IMAGE="ubuntu:24.04"
    fi
fi

echo "=== building in $IMAGE ==="
sudo podman run --rm --security-opt label=disable \
    -v "$ROOT:$ROOT" -v "$WORK:$WORK" \
    "$IMAGE" bash "$WORK/one-build-inner.sh" "$PKG" "$ROOT" "$SRC" "$BD"

echo
echo "Done. $PKG-$VER installed into $ROOT"
echo "Next: repack and boot-test --"
echo "  sudo mksquashfs $ROOT <iso-build>/live/filesystem.squashfs -comp xz -noappend"
echo "  grub2-mkrescue -o smechos.iso <iso-build> -- -volid SMECHOS_LIVE"
echo "  qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -cdrom smechos.iso \\"
echo "      -device virtio-vga-gl -display egl-headless,gl=on -vnc :1"
