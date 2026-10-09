# Peritos Finale assets — Founder Anniversary Edition (Oct 6, 2026)

Cosmetic send-off assets for the Peritos family's last release. Scope:
wallpaper + hidden surprises only — not a new RC, not related to RC4's
cross-toolchain work. See `RELEASE_NOTES_DRAFT.md` for the public copy.

These are **manual, one-time seasonal easter eggs** — deliberately NOT
wired into `spk-compile.py` and not meant to be. They get applied by hand
to the Oct 6 patch's rootfs once, not baked into the automated build
pipeline for every future build. Manual install reference:

- **`wallpaper.png`** (3840×2160) — default desktop wallpaper. Drop into
  whatever phase currently installs the default Plasma wallpaper.
- **`fastfetch-peritos-logo.txt`** — ANSI logo for fastfetch. Point
  fastfetch's config at it via `--logo-type file --logo-source <path>`, or
  a `logo: {type: "file", source: "..."}` block in its `.jsonc` config —
  for this release only, not a permanent fastfetch default.
- **`peritos`** — standalone script, not advertised anywhere (no man page,
  no `--help` mention). Install to `/usr/local/bin/peritos`, executable.
- **`issue-flourish.txt`** — one line to append to `/etc/issue`.
- **`grub/background.png`** + **`grub/theme.txt`** — GRUB boot theme.
  Install both under `/boot/grub/themes/peritos-finale/` and point
  `GRUB_THEME` at `theme.txt` in `/etc/default/grub`, then
  `update-grub`/`grub-mkconfig`. Menu box position assumes the panel
  baked into `background.png` — don't regenerate one without the other.

Dates used throughout (verified against real `SmechDeploy` git tags, not
guessed): **est. 2026-06-25** (`v0.1.0-beta-netinst`, the repo's first
tagged ISO release) **— 2026-10-06** (this patch's ship date).
