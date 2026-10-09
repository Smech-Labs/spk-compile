# SmechOS is looking for co-maintainers

SmechOS is a Linux distro built from source — not repackaged from an
existing distro. KDE Plasma desktop, its own `spk` package manager, and
as of its next release a real cross-compiled toolchain instead of
inheriting the build container's glibc. One person has built this solo
so far. Looking to change that.

**This is unpaid, volunteer work. No employment relationship, anonymous
founder, and that's by design — see below.**

## What you get

- **You own 100% of what you build.** Your code, your design work, your
  docs — yours, full stop. It ships as part of SmechOS under the project's
  name, and you're credited as a co-maintainer. No equity-splitting, no
  dilution math as more people join.
- Real ownership over a real area of the project — not "go find something
  to do," an actual scoped piece that's yours.
- Terms are written down and signed, not a handshake you have to just trust
  (template: `COMAINTAINER_AGREEMENT_TEMPLATE.md` in the repo).
- You can stay pseudonymous. The founder is anonymous too.

## Open areas right now (pick one, or propose your own)

1. **PLM (Plasma Login Manager) build integration** — currently sourced
   out-of-band, no build phase in the pipeline yet.
2. **`spk` ↔ APT feature parity** — SmechOS's package manager vs. what
   Debian/Ubuntu users expect. Unscoped; needs real investigation into
   what parity actually requires.
3. **Interactive (non-autologin) session support** — never boot-verified.
   Real QA + fix work.
4. **Hardware/driver support audits** — found a kernel with zero NVMe
   support the hard way this week. Probably not the only gap (WiFi drivers
   look thin too). Needs someone who'll go through the kernel config
   methodically against real hardware.
5. **Real-hardware boot testing** — if you've got spare/weird hardware
   lying around, this is directly useful. QEMU testing only gets you so
   far; a real black-screen bug just got confirmed on real AMD Vega
   hardware this week that QEMU alone wouldn't have caught cleanly.
6. **KDE/Plasma theming & branding** — design-minded, lower technical bar
   to start.
7. **Documentation** — `SABI.md`/`SAPI.md` exist but are thin; install
   docs, contributor onboarding, all need real work.
8. **Website** (`smechos-site`) — maintenance, design, content.
9. **Translations / i18n.**
10. **Community space moderation** — see Community below.

## Community

**Official spaces** (run by the project): Matrix, a mailing list,
Discourse-based chat, YouTube.

**Unofficial spaces**: Discord, Telegram, Stoat Chat. These are set up and
run by fans/community members, not Smech Labs — treated as genuinely
unofficial, not a soft-launched official channel. (Discord specifically:
its age-verification push is a real reason to prefer the official spaces
above if that's a concern for you — use Discord with that in mind.)

Matrix/mailing list/Discourse aren't stood up yet — coming once there's
enough here to justify it.

## How to reach out

Reply on this issue. That's the one real channel right now.

---

*Repo: github.com/Smech-Labs — downloads at the smechos-site, ISOs via
smechlabs-iso-files.*
