# SmechOS Governance

This document defines how decisions get made, who holds authority, and what
safeguards exist so that authority can't be silently abused. It exists
because informal, undocumented governance is itself a risk — if the rules
only live in one person's head, they aren't rules, they're mood.

## 1. Roles

- **Founder.** Holds permanent top-tier veto authority (see §2) while
  active in the project. There is exactly one Founder at a time.
- **Pseudo-Founder (pool).** A standing pool of 6-8 Trusted Maintainers
  the Founder designates as succession-eligible (see §3). Each holds
  ordinary Trusted Maintainer veto weight (40) — membership in the pool
  is an eligibility to be *elected* Founder after a succession trigger,
  not a power in itself, and confers nothing until that vote happens.
- **Trusted Maintainer.** A contributor the Founder has explicitly granted
  veto authority to (see §2, §5). Not an elected position, not inherited,
  not permanent by default. The Pseudo-Founder pool is drawn from Trusted
  Maintainers (§3).
- **Contributor.** Anyone submitting code, packages, documentation, or
  other project work. No tier of contribution, including Trusted
  Maintainer or Pseudo-Founder, requires identity disclosure (see §4).

## 2. Veto Authority

SmechOS uses a weighted-veto model, not a one-person/one-vote model, for
policy decisions:

- **Founder veto weight: 50.** Overriding a Founder veto requires opposing
  votes totaling more than 50 in weighted value.
- **Trusted Maintainer veto weight: 40** — including everyone in the
  Pseudo-Founder pool, unless and until they are actually elected Founder
  under §3. Real and substantial, but deliberately set below the
  Founder's weight so that no single assigned vetoer — or even several
  acting together — can override the Founder alone at these two weights.
- At all times, exactly one person holds the 50-weight tier: the Founder,
  or after succession, whoever the Pseudo-Founder pool elected under §3.
  The 50-weight tier is never split or duplicated.
- Veto weight applies to **policy decisions** (this document, project
  direction, role assignment). It is separate from the technical
  release-safeguard mechanisms in §6, which apply regardless of anyone's
  veto weight or role.

## 3. Succession

- The Founder maintains a standing pool of **6-8 Trusted Maintainers**
  designated as Pseudo-Founders — eligible to be elected Founder after a
  succession trigger, and nothing more until then. Pool membership is
  revocable and reassignable at the Founder's discretion, the same as any
  other Trusted Maintainer status, and pool members hold ordinary
  Trusted Maintainer veto weight (40) for as long as the Founder remains
  active.
- **Succession triggers on either of two events:** the Founder's death,
  or the Founder's departure from the project. Departure means an
  explicit, unambiguous statement of resignation from the Founder role —
  not mere inactivity or an extended absence, so a temporarily quiet
  Founder is never mistaken for a departed one.
- Once a trigger event occurs and is announced, a governance meeting is
  held in which the current Trusted Maintainers vote to elect the new
  Founder from among the Pseudo-Founder pool. The candidate receiving the
  most support is elected and immediately receives the Founder role and
  its 50-weight veto.
- Only members of the standing Pseudo-Founder pool are eligible
  candidates in this vote — the pool exists specifically so that everyone
  voting already knows, ahead of any crisis, who the realistic candidates
  are.
- Immediately upon taking office, the new Founder should reconstitute a
  full 6-8-member Pseudo-Founder pool (retaining, replacing, or adding
  members as they judge fit), so the project is never left without a
  defined slate of successors.
- If the Pseudo-Founder pool is empty at the time a trigger event occurs,
  the governance meeting instead nominates and elects a Founder from the
  full body of Trusted Maintainers.

## 4. Contributor Identity & Privacy

**SmechOS will not require government-issued identity verification for
any tier of contribution or maintainership, at any project scale.**

This is a deliberate, permanent policy position, not a temporary
convenience for a small project:

- Pseudonymous and anonymous contribution is welcome and fully supported
  at every level, including Trusted Maintainer.
- The rationale: identity verification imposes a certain, asymmetric cost
  on exactly the contributors most likely to need pseudonymity for real
  personal-safety reasons (activists, journalists, contributors under
  authoritarian or restrictive political regimes), in exchange for
  deterrence-based protection that is real but inherently weaker than
  structural safeguards — see §6.
- This policy is explicitly informed by the 2024 xz-utils backdoor: the
  failure that mattered there was a single maintainer becoming the sole,
  unchecked gatekeeper for releases, not a lack of identity verification.
  Structural controls address that failure mode directly; identity
  verification would not have.

## 5. Trusted Maintainer Assignment

- Granted solely at the Founder's discretion. This includes the
  Pseudo-Founder designation (§3), which is a Trusted Maintainer status
  plus a standing succession designation, not a separate grant process.
- Revocable solely at the Founder's discretion.
- No identity disclosure is required to receive or hold this role (§4).
- The Founder should document *why* veto authority was granted when
  assigning it, so the reasoning isn't lost if the Founder is later
  unavailable to explain it.

## 6. Release & Code Safeguards (Structural, Identity-Independent)

Because SmechOS does not use identity verification as a security control,
security instead comes from architecture that does not depend on knowing
or trusting any single person, verified or not:

- **Mandatory multi-signature thresholds.** Changes to core/critical
  packages (the base system, `spk`, the installer, anything in the boot
  path) require sign-off from at least two independent, cryptographically
  signed keys before merging — no single key, regardless of whose it is
  or how long they've contributed, can push a critical change alone.
- **Separation of duties.** No single maintainer may both author a
  critical change and be the one who merges/releases it. The two roles
  must be held by different keys for that specific change.
- These safeguards apply uniformly — they do not scale down for Trusted
  Maintainers or up for the Founder. Nobody is exempt.

## 7. Day-to-Day Decisions

Most contributions (bug fixes, packaging, non-critical features) do not
require a vote at all — normal maintainer review and merge, subject to
the safeguards in §6. The veto structure in §2 exists for policy-level
disagreements, not routine development.

## 8. Amending This Document

Changes to this document are themselves policy decisions and subject to
the veto weights in §2.
