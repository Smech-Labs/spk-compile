# Lesobinaska FNDE assets — Founder Name Day Edition (Nov 30, 2026)

Branding/easter-egg assets for RC4, the first release of the Lesobinaska
family. Theme: St. Andrew's Eve Romanian folklore (moon, treeline, wolf
silhouette, garlic braid) — deliberately different mood from the Oct 6
Peritos finale's party theme, since this is a real technical milestone,
not a cosmetic patch. See `RELEASE_NOTES_DRAFT.md` for the public copy.

These are **manual, one-time seasonal easter eggs**, same as the Peritos
set — deliberately NOT wired into `spk-compile.py`. Applied by hand to the
RC4 rootfs once, not part of the automated build pipeline. Manual install
reference:

- **`wallpaper.png`** (3840×2160) — default desktop wallpaper.
- **`fastfetch-lesobinaska-logo.txt`** — ANSI logo for fastfetch, cooler
  moonlit palette instead of Peritos's warm gold.
- **`lesobinaska`** — standalone script, not advertised anywhere. Install
  to `/usr/local/bin/lesobinaska`, executable. Carries the real toolchain
  war story (LLVM → Clang → RTTI cascade), not a joke like `peritos`'s.
- **`grub/background.png`** + **`grub/theme.txt`** — GRUB boot theme,
  same install pattern as the Peritos one
  (`/boot/grub/themes/lesobinaska/`, `GRUB_THEME` in
  `/etc/default/grub`). Menu box position assumes the panel baked into
  `background.png`.

Note on the family name: "Lesobinaska" is a near-phonetic match for the
Russian word for "lesbian" (лесбиянка) — flagged to the user, who chose
to keep it anyway with full knowledge of the collision. Not an oversight;
don't "fix" it unprompted.
