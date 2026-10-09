# SmechOS / Smech Labs Co-Maintainer Agreement

**Status:** Template/draft — fill in and both parties sign before it's final.
**Nature of this document:** A written, cryptographically-signed record of mutual
intent and terms. It is **not a legal contract** and is not legally enforceable
on its own — see the Enforcement section below for what it actually protects
against and what it doesn't.

---

## Parties

- **Smech Labs**, represented by: `<Smech's signing key fingerprint>`
- **Co-Maintainer**, identified by: `<co-maintainer's signing key fingerprint>`

(Real-world identity disclosure between the parties is a separate decision —
see "On Anonymity" below. This agreement works whether or not either party
discloses a legal name to the other, but that choice has consequences spelled
out below.)

## What This Covers

This agreement applies to SmechOS and the broader Smech Labs project
ecosystem as it exists on `<date>` and as it grows going forward, unless a
specific contribution is explicitly scoped out in writing by both parties
before work begins on it.

## Compensation

**There is no employment relationship.** This is unpaid, voluntary work by
both parties. Neither party owes the other wages, benefits, or any ongoing
commitment of time.

## Ownership Split

- The Co-Maintainer owns **100% of the creative/technical work they
  personally build** for this project (their own code, designs, docs, etc.).
  Smech Labs does not claim a stake in it.
- In exchange, the Co-Maintainer's work ships as part of SmechOS/Smech Labs
  under the project's branding, and they're credited as a co-maintainer
  (see Credit below).
- This scales cleanly to any number of co-maintainers: each person owns what
  they build, full stop — no splitting a fixed pool of equity, no dilution
  math required when a new co-maintainer joins.
- If a co-maintainer builds directly on another co-maintainer's prior work
  (a fork, an extension, a derivative), ownership of that specific
  contribution is between those two people to work out — not assumed to
  default to either side.

## Credit

The Co-Maintainer is credited as a co-maintainer of SmechOS/Smech Labs in
project documentation, release notes, and public-facing materials, unless
they request otherwise (e.g., to preserve anonymity).

## Infrastructure Pooling

If the Co-Maintainer contributes infrastructure (VPS, hosting, hardware,
etc.) for the project's use:

- It remains the Co-Maintainer's property. Smech Labs has no ownership claim
  over it.
- The Co-Maintainer may withdraw it at any time with `<X days>` notice.
- Smech Labs will maintain the project's ability to operate without that
  infrastructure where reasonably possible (i.e., avoid hard dependencies on
  a single person's personal hardware for anything critical) — see your own
  Hetzner-over-Contabo infra-reliability stance for why this matters.

## On Anonymity

Both parties may remain pseudonymous to each other and to the public. This
is explicitly permitted and does not void this agreement. **However:** if
either party later wants to pursue real legal recourse over a dispute
(ownership, infra, credit), both real identity and a jurisdiction willing to
recognize this document would be required at that time — this agreement by
itself does not provide that path. Decide now, not during a dispute, whether
that tradeoff is acceptable to both of you.

## Enforcement

This document is enforced by:
1. **Reputation** — a public, signed record of what was agreed, visible to
   the community if a dispute arises.
2. **Access control** — Smech Labs can revoke repository/infra access
   granted under this agreement at any time; this does not retroactively
   change the ownership split for work already done.
3. **Good faith** — this only works if both parties intend to honor it.

This document is **not**:
- A substitute for a lawyer-drafted contract.
- Guaranteed to be recognized by any specific court or jurisdiction.
- Enforceable against an anonymous party with no real-world identity on
  record.

## Exit

Either party may end this arrangement at any time. Work already contributed
keeps its ownership split as defined above. Access to infra/repos is revoked
on exit. No further credit is required for a maintainer no longer active,
though past credit in existing release notes/history is not retroactively
removed.

## Signatures

```
Smech Labs:
  Key fingerprint: <fingerprint>
  Signature:       <detached signature of this file's hash>
  Date:            <date>

Co-Maintainer:
  Key fingerprint: <fingerprint>
  Signature:       <detached signature of this file's hash>
  Date:            <date>
```

To sign: `gpg --detach-sign --armor COMAINTAINER_AGREEMENT_<name>.md`, both
parties publish their signature + the exact signed file hash somewhere
durable (a pinned GitHub Gist, the repo itself, etc.).
