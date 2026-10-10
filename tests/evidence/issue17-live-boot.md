# Issue #17 — real FAE first-boot handoff proof (2026-10-09)

This evidence is for [SmechOS issue #17](https://github.com/Smech-Labs/smechos-issues/issues/17), not a claim that a full RC4 release was rebuilt.

## Reproduction and result

- Input: a copy of the already booted RC3/Founder Anniversary Edition SmechOS root filesystem. The original ISO/rootfs were retained unchanged.
- The checked-in `spk-compile.py` production finalizers were applied to the isolated Linux staging root: native KDE executable readback, KDE PAM `kde` service, `shadow` group/file permissions and compatible `unix_chkpwd` helper.
- Those staged bytes were packed as XZ SquashFS, inserted into the existing GRUB/BIOS bootable ISO structure, and independently compared against the embedded SquashFS byte-for-byte.
- Boot test on Windows 11 host with QEMU 11.1/WHPX, two virtual CPUs, 3072 MiB RAM, `virtio-vga`, GTK display, virtual USB tablet and keyboard, no networking, no persistent VM disk.
- **Observed:** the plasma-setup wizard completed its Language, Keyboard, Theme, About You, Hostname and Finish steps. After Finish, the SmechOS login greeter appeared for the created disposable `smechtest` account. Password login reached a **visible Plasma Wayland user desktop with KDE panel and Welcome Center**, remaining visible at a later capture. No indefinite black handoff.
- This is a genuine GUI boot and login result. It is not a full RC4 cross-toolchain recompilation or a test of PackageKit/Discover.

## Integrity and source linkage

| Artifact | SHA-256 |
| --- | --- |
| Post-review local Windows checkout `spk-compile.py` (CRLF working copy) | `a84ee0f001c27df3e981fbf6f57a204e42d61e2b9f94cbad92b05a8cfbf26067` |
| Isolated FAE rootfs SquashFS after builder staging | `5edfc83ffb7685606324a244127dfe077b99a613d3dbbc0d717cf63b3c12fafa` |
| **Byte-verified and booted ISO** | `4d652736bdfa5a6e940697124b37aef23525cd9e4f885e0cfb701cd4cd283f16` |

The independent ISO verifier reported `SOURCE_LINKED_ISO_BYTE_EXACT_PASS` and verified all 2,049,617,920 bytes of the embedded SquashFS. The hardened finalizer was reapplied on the stage with successful readback and no file-hash changes.

The final fail-closed PAM-policy and filesystem-path checks were replayed against the staged FAE filesystem after this ISO was built. The [current-source compatibility receipt](issue17-source-artifact-compatibility.json) confirms the same six relevant runtime file hashes and an idempotent finalizer (PASS). This proves the hardening did not change those staged runtime bytes; it does not substitute for a full RC4 toolchain rebuild. The source SHA-256 above is for the local Windows checkout; the Git commit identifies the portable source revision.

## Visible evidence

- [Post-Finish login greeter for the temporary test user](issue17-greeter-test-account.jpg)
- [Plasma desktop after the new user's login, with KDE bottom panel and Welcome Center](issue17-plasma-after-login.jpg)

Both screenshots came from the above QEMU run using the verified ISO, not from the host desktop.

## Automated regression tests

On an isolated Linux tool image, `python3 -B -m unittest discover -s tests -p 'test_*.py'` reported **43 tests, 42 passed, one Windows-only test skipped, zero failures**. The suite covers KAuth change placement/idempotence, native executable restoration, greeter `video`/`render` group membership, PAM-service safety, cross-ABI helper fallback rejection, GID collision, hardlink/symlink defense, and final ISO packaging call ordering.

No payment or maintainer acceptance is asserted here. The intended production deliverable is the source patch, not the local ISO binary.
