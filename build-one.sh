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
#
# Plasma packages live under stable/plasma/<ver>/. KDE Gear applications
# (Konsole, Dolphin, ...) live under stable/release-service/<ver>/src/ and
# use a completely different version series -- pass --gear for those.
set -euo pipefail

PKG="${1:-}"
VER="${2:-}"
ROOT="${3:-/home/smech/smechos-work/root}"
TRACK="plasma"
for a in "$@"; do [ "$a" = "--gear" ] && TRACK="gear"; done

if [ -z "$PKG" ] || [ -z "$VER" ]; then
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
fi

if [ "$TRACK" = "gear" ]; then
    URL="https://download.kde.org/stable/release-service/$VER/src/$PKG-$VER.tar.xz"
else
    URL="https://download.kde.org/stable/plasma/$VER/$PKG-$VER.tar.xz"
fi

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

cat > "$WORK/one-build-inner.sh" <<'INNER'
set -euo pipefail
PKG="$1"; ROOT="$2"; SRC="$3"; BD="$4"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    build-essential cmake ninja-build pkg-config patchelf \
    libboost-dev libx11-dev libvulkan-dev libxkbcommon-dev libwayland-dev >/dev/null

# The rootfs is the same glibc as this container, so pointing the loader at
# it is safe here (and is what lets its qtpaths/moc/msgfmt run natively).
export LD_LIBRARY_PATH="$ROOT/usr/lib/x86_64-linux-gnu:$ROOT/usr/lib"
# Rootfs bin goes LAST. Putting it first makes the rootfs's own cmake win
# over the container's, and it then looks for its modules under a version
# directory that does not exist there:
#   Modules directory not found in <rootfs>/usr/share/cmake-3.28
# cmake locates the rootfs's qtpaths/moc/msgfmt through CMAKE_PREFIX_PATH by
# absolute path regardless, so nothing is lost by de-prioritising it here.
export PATH="$PATH:$ROOT/usr/bin"

echo "=== sanity: rootfs tools run natively ==="
"$ROOT/usr/bin/qtpaths" --query QT_INSTALL_PREFIX

rm -rf "$BD"; mkdir -p "$BD"; cd "$BD"
/usr/bin/cmake "$SRC" -G Ninja \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_PREFIX_PATH="$ROOT/usr" \
    -DCMAKE_INSTALL_RPATH_USE_LINK_PATH=FALSE \
    -DCMAKE_INSTALL_RPATH=/usr/lib/x86_64-linux-gnu \
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

echo "=== building in ubuntu:24.04 ==="
sudo podman run --rm --security-opt label=disable \
    -v "$ROOT:$ROOT" -v "$WORK:$WORK" \
    ubuntu:24.04 bash "$WORK/one-build-inner.sh" "$PKG" "$ROOT" "$SRC" "$BD"

echo
echo "Done. $PKG-$VER installed into $ROOT"
echo "Next: repack and boot-test --"
echo "  sudo mksquashfs $ROOT <iso-build>/live/filesystem.squashfs -comp xz -noappend"
echo "  grub2-mkrescue -o smechos.iso <iso-build> -- -volid SMECHOS_LIVE"
echo "  qemu-system-x86_64 -enable-kvm -m 4096 -smp 4 -cdrom smechos.iso \\"
echo "      -device virtio-vga-gl -display egl-headless,gl=on -vnc :1"
