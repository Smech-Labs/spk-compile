# SAPI — SmechOS API

This document is the declared userspace surface applications can rely on —
what a `.spk` package or a third-party app should call into, and what
SmechOS commits to keeping stable. Companion to `SABI.md` (the binary/ABI
contract); this covers the API/service surface instead.

## 1. Package management: spk's PackageKit backend

- SmechOS exposes package management to desktop software (Discover, GNOME
  Software, any PackageKit client) via a real PackageKit backend, not a
  bespoke SmechOS-only API.
- Mechanism: `/etc/PackageKit/PackageKit.conf` sets `DefaultBackend=spk`.
  `/usr/lib/packagekit-backend/pk-backend-spk.py` is a thin shim
  (generated at build time by `spk-compile.py`) that execs
  `spk packagekit-backend` and forwards stdio — the actual logic lives in
  the `spk` binary itself, not in this shim.
- Applications should talk to PackageKit's standard D-Bus API, not `spk`
  directly, unless they specifically need spk-only functionality PackageKit
  doesn't expose.
- Contract: the `spk` binary must support the `packagekit-backend`
  subcommand. This was not always true — `spk` v2.0.1 had no such
  subcommand at all; it was added later (currently shipping v2.5.0). Any
  future spk version bump must preserve this subcommand's behavior, since
  the desktop's entire package-management UI depends on it.

## 2. Desktop integration: XDG portals, not bespoke D-Bus APIs

- SmechOS adopts standard **XDG desktop portals** for sandboxed/cross-DE
  app integration (file chooser, screen sharing, etc.) rather than
  inventing parallel SmechOS-specific APIs for the same purposes.
- Currently shipped: `xdg-desktop-portal-kde` (the KDE-specific portal
  backend). The generic `xdg-desktop-portal` reference frontend daemon is
  not separately built — the KDE backend is the whole integration.
- **Custom D-Bus namespaces are reserved only for things with no XDG
  equivalent** — e.g. future SmechBoard/`smechctl` integration, which has
  no standard portal concept to map onto. Don't add a custom D-Bus API for
  anything a standard portal already covers.

## 3. OS identity: `os-release`

- Applications that need to detect "is this SmechOS" (and which version)
  should query the standard `/etc/os-release` fields, not a bespoke
  SmechOS-only identity file or API. This keeps SmechOS detectable by
  generic Linux tooling that already knows how to read `os-release`,
  without requiring SmechOS-aware special-casing.

## 4. What's still a known gap (be honest about it, don't paper over it)

- **PLM ("plasma-login-manager") is not built by any phase in the pipeline
  today** — it's built out-of-band from a Fedora SRPM and dropped into the
  target; `phase_plasma_configure` only configures it if it's already
  present. A `plasma-login-manager` build phase (cmake+ECM, Fedora patches
  reapplied for Debian conventions) is still a needed follow-up, tracked
  separately from this document.
- **spk itself is not built from source in this pipeline** — it's pulled in
  as a prebuilt binary release. Bringing spk toward APT-level feature
  parity (real dependency resolution, signed repo metadata, pinning/holds,
  autoremove) is explicitly out of scope for this document and needs its
  own scoping pass against spk's actual source before committing to
  specific features or a timeline.

## How to apply this document

- New desktop-integration features: check §2 first — if a standard XDG
  portal already covers the use case, use it; only reserve a custom D-Bus
  namespace when there's genuinely no portal equivalent.
- New package-management tooling: go through PackageKit (§1) unless
  spk-specific functionality is required.
- Anything claiming to detect "SmechOS-ness": use `os-release` (§3), not a
  new bespoke mechanism.
