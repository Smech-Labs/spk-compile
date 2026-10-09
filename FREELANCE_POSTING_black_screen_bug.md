# Ready to post — Upwork (or equivalent)

## Title
Linux Wayland/KWin session-handoff bug — black screen after login (real hardware confirmed, diagnostics already done)

## Category / Skills
Linux, Wayland, KDE Plasma/KWin, DRM/KMS, systemd, C/C++, Qt6

## Project type
Hourly, open-ended — no fixed cap, but see "How this is structured" below
for the check-in cadence (keeps this honest for both of us, not a budget
limit).

## Experience level
Expert — this already defeated several hours of real debugging by someone
with direct access to the build pipeline, live reproduction, and full
diagnostic tooling. A generalist Linux dev is unlikely to make fast
progress here; someone with actual Wayland compositor or DRM/KMS internals
experience will.

---

## The job

A Linux distro's first-boot flow runs a setup wizard as one system user
with its own `kwin_wayland` compositor instance. When the wizard finishes,
a second desktop session (different user) is supposed to take over the
display — instead the screen goes solid black and stays that way,
permanently. Confirmed on two completely unrelated GPU backends (QEMU
`virtio-vga` and real AMD Vega/`amdgpu` hardware) hitting the identical
symptom, which points at a logic bug in the session handoff itself, not a
driver issue.

Real diagnostic work already done, so you're not starting cold:
- `kwin_wayland` for the real session is confirmed to actually start
  (process exists, confirmed via `/proc` scan).
- `systemd` has, in one run, logged a clean "Reached target Main User
  Target" for that session — the session's own startup sequence reports
  success.
- Despite both of the above, the screen stays black.
- Leading hypothesis (untested with hard evidence): the first session's
  `kwin_wayland` still holds DRM master on `/dev/dri/card0` when the
  second session's `kwin_wayland` tries to start.

Full write-up, reproduction steps, and exactly what's confirmed vs. still
open: **[BUG_BRIEF_black_screen_handoff.md](https://github.com/Reuteknohontake-Labs/spk-compile/blob/main/BUG_BRIEF_black_screen_handoff.md)**

## Deliverable

A real root cause (not a guess) and a working fix, verified by an actual
boot reaching a visible, functional desktop after the handoff — not
"should work now."

## How this is structured

You'll work against **[this repo](https://github.com/Reuteknohontake-Labs/spk-compile)**
— a full, working mirror of the build system, isolated from the
production codebase. Fork it, submit your fix as a PR against it. I'll
review and diff against the original state before anything goes further —
standard practice, not a reflection on you.

Since this is open-ended hourly: let's check in after roughly every 3–4
hours of billed time with a short progress update (what you've ruled out,
what you're trying next) — not a cap, just keeping both sides honest if
something's not converging, same as I'd want from anyone on a genuinely
open-ended systems bug.

## What I need from you

A sentence or two on real Wayland compositor, DRM/KMS, or systemd
session-management experience before we start — not a generic Linux
resume bullet point.
