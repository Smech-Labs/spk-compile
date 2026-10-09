# iso-builder

A small ISO-mastering tool for SmechOS deliverables, structured after
[debian-cd](https://salsa.debian.org/images-team/debian-cd)'s architecture:
a **boot-setup stage** that prepares a `boot$N/` directory and accumulates
`xorriso`/`mkisofs` options into plain-text option/dir-list files, followed
by a separate **image-assembly stage** that reads those files and runs the
single final `xorriso` invocation.

This is not a literal fork of debian-cd's source — that codebase assumes a
Debian package mirror/pool and pre-built debian-installer artifacts (kernel,
initrd, isolinux/GRUB configs already produced by d-i's own build
infrastructure, syslinux/isolinux extracted from `.deb`s via `dpkg
--fsys-tarfile`). SmechOS hand-rolls its own kernel, initramfs, GRUB config,
and Rust installer, and uses spk/Portage rather than `.deb`s, so essentially
none of debian-cd's actual artifact-fetching logic applies. What's preserved
here is the *shape* of the pipeline (`add_mkisofs_opt` accumulation pattern,
boot-setup/image-assembly split, BIOS-then-EFI El Torito layering) — the part
of debian-cd's design that is genuinely reusable independent of Debian's
package-pool model.

debian-cd is copyright 1999 Raphaël Hertzog and 2004-2019 Steve McIntyre,
licensed GPL-2.0-or-later. This tool is released under the same license.
