# SABI — SmechOS Application Binary Interface

This document is the declared contract `.spk` packages and third-party
binaries can rely on across SmechOS upgrades. Before this document existed,
these behaviors were real but undeclared — accidents of how the build
pipeline happened to be structured, not commitments. Several of them
already caused real bugs when a component (PLM, sourced from a Fedora SRPM)
made different assumptions. This document exists so that stops happening
silently.

## 1. libc and toolchain floor

- **Still shipping today:** the `smechos-plasma-live` profile uses the build
  container's own glibc (Ubuntu 24.04, glibc 2.39) as-is — copied into the
  target image, not compiled by SmechOS. This is a deliberate, stated
  decision (see `spk-compile.py`'s `_bootstrap_glibc_runtime` docstring),
  not an oversight. **Third-party packages should keep building against
  glibc 2.39 as the real floor until the item below actually lands in the
  shipping pipeline** — don't jump the gun on the strength of the toolchain
  work alone.
- **Toolchain bring-up is real and done, separately from the shipping
  pipeline:** a genuine from-source SmechOS glibc — **2.41**, built via
  crosstool-ng — exists for a new, SmechOS-owned target triplet,
  `x86_64-smechos-linux-gnu`. This is not aspirational: it was verified
  directly (`libc.so.6` is a real ELF built for that triplet, its version
  string reads `GNU C Library (crosstool-NG 1.27.0) stable release version
  2.41`). That toolchain has since proven itself on the kernel, systemd, the
  full Wayland/X11 stack, Mesa (with real `libGL`/`libEGL`/gallium DRI
  drivers), and a growing set of Qt6 modules (`qtbase`, `qtshadertools`,
  `qtdeclarative` in progress) — all built and installed against a
  from-scratch sysroot, not the container's.
- **Update (2026-10-07): integration is genuinely in progress, not just
  planned.** `phase_kernel`, `phase_wayland`, `phase_wayland_protocols`,
  and `phase_libinput` already build against the real cross-toolchain in
  the actual production pipeline (`spk-compile.py`'s own
  `SMECHOS_PLASMA_LIVE_PHASES` list), via `CROSS_TRIPLET`/
  `CROSS_TOOLCHAIN_BIN`/`_meson_cross_file()`. Mesa and Qt6/KDE conversion
  is the remaining, harder work — same incremental approach, hardest
  phases last, exactly per the original plan's own reasoning (host-tool/
  target-tool split for things like `moc`/`uic`/`rcc`). Don't describe
  SmechOS as "running its own glibc" until Mesa/Qt6/KDE land too — the
  shipping `smechos-plasma-live` profile still uses the container's glibc
  for those phases today; describe it as "the kernel and part of the
  Wayland stack already build against the real toolchain in production;
  the desktop stack conversion is still in progress."
- **The toolchain itself is now a real, published artifact**, not just a
  local build: `Smech-Labs/smechos-toolchain` on GitHub ships the compiler
  (binutils/GCC/glibc, no sysroot — everything the sysroot would have held
  is built from source by `spk-compile.py` itself, same as the kernel and
  systemd always have been). `CROSS_TOOLCHAIN_PREFIX` in `spk-compile.py`
  is env-var overridable (`SMECHOS_TOOLCHAIN_PREFIX`) precisely so anyone
  else can download that release and skip crosstool-ng's multi-hour build
  entirely, rather than being silently stuck with a path that only exists
  on one machine.
- **`x86_64-smechos-linux-gnu` (the compiler/target triplet) is a different
  thing from `MULTIARCH_TRIPLET` (currently `x86_64-linux-gnu`, the
  Debian-style library *path* convention in §2).** The former identifies
  what the cross-toolchain builds *for*; the latter identifies where
  libraries are *found* on disk at runtime. Whether `MULTIARCH_TRIPLET`
  itself changes once this toolchain actually ships is an open question,
  not yet decided — don't assume one implies the other.
- Whichever glibc is in effect, its **symbol-version floor is the contract**
  — a package built against a newer glibc's symbol versions than the
  shipping image provides will fail to load, not just misbehave.

## 2. Multiarch library path is a declared contract, not an accident

- Libraries live under `/usr/lib/<MULTIARCH_TRIPLET>/`, where
  `MULTIARCH_TRIPLET` is currently `x86_64-linux-gnu` — Debian/Ubuntu's
  convention, inherited from the build container. This is **not** upstream
  glibc's own default, and **not** what Fedora (`/usr/lib64`) or Arch (flat
  `/usr/lib`) use.
- `spk-compile.py` centralizes this as the `MULTIARCH_TRIPLET` constant.
  Any code (in this pipeline or in third-party `.spk` packages) that needs
  the multiarch path should reference that constant, not hardcode the
  literal string — this is what makes a future triplet change (e.g. once a
  custom glibc/toolchain lands) a one-line change instead of a many-site
  hunt.
- `/etc/ld.so.conf.d/<MULTIARCH_TRIPLET>.conf` is written declaring this
  path; packages must not assume any other search path is populated.

## 3. RPATH / RUNPATH policy

- **Mandate `$ORIGIN`-relative RPATH over absolute paths.** The build
  pipeline runs un-chrooted (the container's own dynamic linker executes
  target-installed binaries directly, pointed at the staging tree via
  `LD_LIBRARY_PATH`) — an absolute RPATH baked in at build time
  (`/usr/lib/x86_64-linux-gnu/...`) is fragile the moment the binary runs
  in a different root than it was built in.
- Verified pattern already in use: CMake/KDE builds set
  `CMAKE_INSTALL_RPATH=/usr/lib/<MULTIARCH_TRIPLET>` as a *secondary* entry —
  confirmed via `readelf` that when the absolute entry doesn't resolve
  (un-chrooted execution), the linker falls through to the `$ORIGIN`-relative
  entry that follows it, which does. New packages should follow this same
  pattern: an `$ORIGIN`-relative RPATH entry must always be present and
  must always resolve on its own, independent of whether an absolute entry
  also happens to be there.
- No `patchelf`-style post-hoc RPATH rewriting is used anywhere in this
  pipeline — RPATH correctness is achieved via build-system flags at build
  time. Third-party packages should do the same rather than relying on a
  post-install patch step.

## 4. SONAME transition policy

- A library's SONAME can and does change between point releases within the
  same pinned line (real precedent: `libPlasma.so.6` → `.so.7` between
  Plasma 6.6.x point releases). `.spk` packages must declare the SONAME they
  actually link against, not just a package version — a package built
  against `libPlasma.so.6` will not load against a `.so.7`-only image.
- The pinned floor versions third-party packages should compile against
  (see `spk-compile.py`'s version constants): **Qt 6.10.3**, **KDE
  Frameworks (KF6) 6.24.0**, **Plasma 6.6.6**, tracking the "Bullet-Proof
  KDE" LTS line (independently confirmed as LTS by endoflife.date as of
  2026-09-09, critical bug fixes through 31 Aug 2029). SmechOS deliberately
  does not track latest Plasma releases — see project memory
  `smechos-plasma66-lts-pivot` for the full reasoning if this needs
  re-justifying.

## 5. PAM stack convention: Debian, not Fedora/authselect

- SmechOS's rootfs is Debian/Ubuntu ABI throughout. PAM service files must
  follow Debian's `common-auth`/`common-account`/`common-*` indirection
  convention, **not** Fedora's `authselect`-generated `system-auth`/
  `postlogin` stack.
- This is not hypothetical: PLM ("plasma-login-manager", SmechOS's actual
  shipping login manager) ships PAM templates sourced from a Fedora SRPM,
  assuming exactly the wrong convention. `phase_plasma_configure` patches
  this at build time via `_pam_ensure_line()`/`_pam_make_optional()`
  (idempotent line-ensure/soft-require helpers). Any future component
  sourced from an RPM-based distro must go through the same
  reconciliation — don't assume an upstream RPM package's PAM config works
  as-is on SmechOS.
- **Current audit status across PLM's five shipped PAM service files**
  (as of the RC4/SABI-SAPI formalization pass): `postlogin`, `system-auth`,
  and `password-auth` are stubbed as comment-only files so their
  `include` directives stop hard-aborting the stack; `plasmalogin-autologin`
  (the only service file exercised by a real boot so far, since SmechOS
  currently autologins straight to desktop) is actively patched with
  `pam_systemd.so` and a real account-phase line; `plasmalogin-greeter`
  was audited and needs no fix (its account phase already has a real
  line). **`plasmalogin` itself — plain, interactive/password-based
  login — has never been verified via a real boot in this project.**
  Treat it as an open verification gap, not a confirmed-working path,
  until someone actually boot-tests a non-autologin login (e.g. after
  Calamares commits a real password-based user account).
- PAM **modules** (`.so` files, not config) are copied from the container's
  multiarch security dir (`/usr/lib/<MULTIARCH_TRIPLET>/security`) into the
  target's `/usr/lib/security`, a second legacy-style module search path
  PLM's PAM implementation also checks. `pam_selinux_permit.so` is
  deliberately excluded — it's Fedora/authselect-only and not shipped by
  Debian.

## 6. What's genuinely independent already (not Debian/Ubuntu-inherited)

For clarity on what this contract does *not* need to hedge about:

- **Kernel** — compiled from source in-pipeline.
- **systemd** (currently 261) — compiled from source from systemd's own
  upstream tarball, not Debian's patched package.
- **KDE Plasma / KF6 core** (kwin, plasma-workspace, frameworks) — compiled
  from source in-pipeline.
- **spk** — SmechOS's own package manager; the shipped system does not run
  on apt/dpkg.

**Verified in the isolated toolchain workdir, not yet shipping (see §1):**
the `x86_64-smechos-linux-gnu` cross-toolchain itself (crosstool-ng,
glibc 2.41 from source), Mesa (real `libGL`/`libEGL`/gallium drivers), and
Qt6's `qtbase`+`qtshadertools` (`qtdeclarative` in progress). These are
real, independently-confirmed builds — not configured-but-unbuilt — but
they live outside `spk-compile.py` until the pipeline conversion lands.
Don't list these alongside the items above as "already independent in the
shipping system" until that's actually true.

## How to apply this document

- New `.spk` packages: build against the floor versions in §4, link against
  the multiarch path via the declared convention in §2, and use
  `$ORIGIN`-relative RPATH per §3.
- New components sourced from another distro's packaging (RPM, AUR, etc.):
  audit PAM, path, and RPATH assumptions against this document before
  integrating — don't assume upstream packaging conventions transfer.
- When SmechOS's own glibc/toolchain work (targeting RC4) actually lands in
  `spk-compile.py` itself — not just the isolated verification workdir —
  §1 gets updated in place to drop the "not yet shipping" caveat; §§2-5 are
  expected to remain stable across that transition, since they were
  designed as declared contracts independent of which specific glibc is
  underneath.
