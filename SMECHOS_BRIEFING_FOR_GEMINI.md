# SmechOS — full briefing

## What it is

A Linux distribution built genuinely from source, not repackaged from an
existing distro — Ubuntu, Fedora, Debian, none of them. The build pipeline
(`spk-compile.py`) downloads upstream sources and compiles a systemd/glibc
base, a custom kernel, KDE Plasma as the desktop, and `spk` (its own
package manager) directly, rather than starting from someone else's
prebuilt rootfs.

## Why it exists

Not a resume project. Two real, specific motivations: gaps in Plasma
Discover's package-management support that frustrated the founder
directly, and a long-standing personal goal of building an actual OS from
scratch. Linux experience started 2022/23 on Ubuntu 22.04 — this isn't
someone who's been doing OS development for a decade, it's someone who
decided to actually build the thing they wanted instead of waiting for it.

## Release history and naming

Two release "families," named deliberately:

- **Peritos family** (the whole pre-RC4 history, est. 2026-06-25): every
  pre-RC build through RC3, plus the Founder Anniversary Edition (FAE) —
  explicitly prototype-only, built against the build container's
  inherited glibc/toolchain rather than SmechOS's own. RC3 shipped
  2026-09-30 after root-causing a real 7-bug boot-chain failure chain
  (missing Mesa/GBM payload, un-regenerated ld.so.cache, missing systemd
  user D-Bus units, unpropagated QT_PLUGIN_PATH, missing xrdb, missing
  libdecor blocking Xwayland, absent fontconfig). FAE shipped 2026-10-06 —
  the founder's actual birthday — as a cosmetic-only send-off for the
  family: new wallpaper, GRUB theme, hidden easter eggs, zero code
  changes.
- **Lesobinaska family** opens with **RC4, "Founder Name Day Edition"
  (FNDE)**, target 2026-11-30. Real new scope: a genuine cross-toolchain
  (crosstool-ng, triplet `x86_64-smechos-linux-gnu`) with SmechOS's own
  from-source glibc, replacing the old container-glibc-matching trick for
  the first time. Formalized `SABI.md`/`SAPI.md` ABI/API contracts.
  Progress toward `spk`/APT feature parity (still unscoped).

## RC4 status as of this writing

Cross-compiling cleanly: the Mesa/Qt6 dependency chain, all 10 Qt6 modules
(two real bugs found and fixed to get there — a pcre2 UTF-16 codepoint gap,
and a CMake GLVND/legacy OpenGL conflict), and the kernel (which also
picked up three real config fixes this cycle: `CONFIG_BLK_DEV_NVME`,
`CONFIG_DM_CRYPT`, and `CONFIG_DM_THIN_PROVISIONING` were all completely
absent — meaning installs would never boot on most modern hardware or
through Calamares' encryption/LVM-thin paths). KDE Frameworks + Plasma
cross-compilation is not yet done.

**One real open bug, found this cycle, affecting the already-shipped RC3/
FAE**, not just RC4: after the first-boot setup wizard hands off to the
real desktop session, the screen goes solid black and stays that way —
reproduced identically on QEMU and real AMD Vega hardware, which points at
a session-handoff logic bug rather than a GPU driver issue. Root cause not
yet found; real diagnostic evidence gathered.

## The broader philosophy

Deliberately pinned to the Plasma 6.6/KDE Frameworks 6.24 LTS line (the
"Bullet-Proof KDE Initiative," a real, funded, whole-stack LTS effort —
not chasing the newest Plasma release, which was an explicit, reasoned
decision to trade bleeding-edge features for not re-fighting the same
build-breakage class of bugs every few months) — a deliberate stability-
first phase, not a permanent identity; the stated long-term plan is to
move to a rolling-release model once the foundation stops producing
boot-critical bugs at the current rate.

Sovereignty-focused across the board, not just as marketing language:
anonymous founder by design, a standing "no cloud AI" principle for every
Smech Labs product (local inference only), and infrastructure choices
(Hetzner over cheaper alternatives, self-hosted GitLab in progress,
Authentik over relying solely on Google for auth) that consistently
trade convenience for not depending on centralized platforms.

## Where it's headed

RC4/FNDE is deliberately meant to be the point where SmechOS opens to
outside contributors for real — not just a technical milestone. A
co-maintainer call went out this week (concrete areas: PLM build
integration, `spk`/APT parity, real-hardware boot-testing, more), offering
full ownership of whatever a contributor personally builds rather than a
diluted equity split, specifically so it scales to more than one person
joining. An honest status checkpoint against the full RC4 scope is planned
before the Nov 30 date gets treated as fixed.
