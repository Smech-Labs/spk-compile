#!/bin/bash
# kexec_test_smechos.sh -- boot the finished SmechOS stack on real hardware
# via kexec, skipping firmware/bootloader entirely. Run this ON THE TEST
# LAPTOP, not the build machine -- kexec jumps the machine it runs on.
#
# WHY THIS IS SAFE: kexec loads a new kernel straight from RAM and jumps to
# it. It never touches the laptop's partition table, EFI System Partition,
# or bootloader config. If the new kernel/userspace hangs or crashes, a hard
# power-cycle returns you to exactly the working system you had before --
# nothing on disk was ever modified by this script.
#
# WHAT THIS REUSES, NOT REINVENTS: the kernel, initramfs, and boot
# cmdlines here are the exact same ones phase_iso_live_smechos's own
# grub.cfg already uses -- the live-boot mechanism (busybox init ->
# squashfs -> overlay -> switch_root) that was already real-boot-tested
# and debugged this project (see phase_live_initramfs's init script and
# phase_kernel's own comments on bugs found that way). This script's only
# job is to reach that same boot path via kexec instead of a reboot +
# GRUB menu selection, saving the firmware/POST cycle on every iteration.
#
# PREREQUISITE, done manually, NOT by this script: a USB stick with the
# real SmechOS live ISO written to it, plugged into the laptop. The
# busybox init script (phase_live_initramfs) scans /dev/sr0, /dev/sda,
# /dev/sdb, /dev/sdc for an iso9660 filesystem with live/filesystem.squashfs
# on it -- this script does not touch that device at all, kexec only
# supplies the kernel + initramfs, the init script finds the squashfs
# itself exactly as it would on a normal boot. Writing the ISO is a real,
# genuinely destructive dd to a block device -- deliberately NOT automated
# here. Reference command (confirm the device is really the USB stick,
# never run this blind):
#   sudo dd if=/path/to/smechos-plasma-live.iso of=/dev/sdX bs=4M status=progress conv=fsync
#
# STATUS: written, never executed. No SmechOS kernel/initramfs has
# actually been built yet as of this writing (phase_kernel /
# phase_live_initramfs haven't run in tonight's test chain) -- this script
# is real and correct against what those phases produce, but genuinely
# untested end to end. Don't treat "wired" as "verified."
#
# USAGE:
#   ./kexec_test_smechos.sh --mode normal|nomodeset|debug --load   # load only, inert
#   ./kexec_test_smechos.sh --mode normal --go                     # load AND jump -- ACTUALLY BOOTS INTO IT

set -euo pipefail

TARGET="${SMECHOS_TARGET:-/home/smech/smechos-work/spk-integration-test/target}"
VMLINUZ="$TARGET/boot/vmlinuz"
INITRD="$TARGET/boot/live-initrd.img"

MODE="normal"
ACTION=""

while [ $# -gt 0 ]; do
    case "$1" in
        --mode)   MODE="$2"; shift 2 ;;
        --load)   ACTION="load"; shift ;;
        --go)     ACTION="go"; shift ;;
        --target) TARGET="$2"; VMLINUZ="$TARGET/boot/vmlinuz"; INITRD="$TARGET/boot/live-initrd.img"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$ACTION" ]; then
    echo "Must pass --load (prepare only, inert) or --go (prepare AND jump into it)." >&2
    echo "See this script's own header comment before using --go." >&2
    exit 1
fi

# Same three cmdlines phase_iso_live_smechos's grub.cfg already uses --
# not reinvented here, copied verbatim so this reaches the identical,
# already-tested boot path.
case "$MODE" in
    normal)    CMDLINE="boot=live quiet splash loglevel=3" ;;
    nomodeset) CMDLINE="boot=live nomodeset quiet loglevel=3" ;;
    debug)     CMDLINE="boot=live console=ttyS0,115200n8 console=tty0 loglevel=7 systemd.log_level=debug" ;;
    *) echo "Unknown --mode '$MODE' (expected: normal, nomodeset, debug)" >&2; exit 1 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "Must run as root (kexec_load / kexec_boot require CAP_SYS_BOOT)." >&2
    exit 1
fi

if ! command -v kexec >/dev/null 2>&1; then
    echo "kexec-tools not installed on this machine. Install it first:" >&2
    echo "  sudo dnf install kexec-tools   # Fedora" >&2
    echo "  sudo apt install kexec-tools   # Debian/Ubuntu" >&2
    exit 1
fi

for f in "$VMLINUZ" "$INITRD"; do
    if [ ! -f "$f" ]; then
        echo "Missing: $f" >&2
        echo "phase_kernel and phase_live_initramfs need to have actually run" >&2
        echo "and produced real output before this script can load anything." >&2
        exit 1
    fi
done

echo "=== kexec load: $MODE ==="
echo "  kernel:  $VMLINUZ"
echo "  initrd:  $INITRD"
echo "  cmdline: $CMDLINE"
echo ""
echo "Make sure the USB stick with the written SmechOS live ISO is plugged in"
echo "before continuing -- the busybox init script finds it itself at boot,"
echo "this script never touches it."
echo ""

kexec -l "$VMLINUZ" --initrd="$INITRD" --command-line="$CMDLINE"
echo "Kernel loaded into memory. Nothing has happened yet -- the running"
echo "system is completely untouched and this is fully reversible by just"
echo "not running --go."

if [ "$ACTION" = "go" ]; then
    echo ""
    echo "=== JUMPING NOW. This machine is about to boot into SmechOS. ==="
    echo "Sync'ing filesystems first..."
    sync
    kexec -e
fi
