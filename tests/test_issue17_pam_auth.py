"""Issue #17 PAM screen-lock staging tests. No host PAM files are modified."""
import ast
import hashlib
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "spk-compile.py"

def load_helper():
    doc = ast.parse(SOURCE.read_text(encoding="utf-8"))
    node = next(x for x in doc.body if isinstance(x, ast.FunctionDef)
                and x.name == "_ensure_issue17_screenlock_auth")
    ctx = {"os": os, "Path": Path, "hashlib": hashlib, "shutil": shutil,
           "stat": stat, "MULTIARCH_TRIPLET": "x86_64-linux-gnu",
           "log": lambda *args, **kw: None, "GREEN": ""}
    code = compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), "exec")
    exec(code, ctx)
    return ctx["_ensure_issue17_screenlock_auth"]

@unittest.skipUnless(os.name == "posix" and os.geteuid() == 0,
                     "Requires isolated Linux root-runner")
class Issue17PamStaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configure = staticmethod(load_helper())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="issue17-pam-", dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "target"
        for path in ("etc/pam.d", "usr/lib/security", "usr/sbin"):
            (self.root / path).mkdir(parents=True, exist_ok=True)
        (self.root / "etc/group").write_text("root:x:0:\nvideo:x:14:\n", encoding="utf-8")
        (self.root / "etc/shadow").write_text("root:!:0:0:99999:7:::\n", encoding="utf-8")
        os.chmod(self.root / "etc/shadow", 0o600)
        self.host_module = Path("/usr/lib/x86_64-linux-gnu/security/pam_unix.so")
        self.host_helper = Path("/usr/sbin/unix_chkpwd")
        if not self.host_module.is_file() or not self.host_helper.is_file():
            self.skipTest("Linux-PAM reference package unavailable")
        shutil.copy2(self.host_module, self.root / "usr/lib/security/pam_unix.so")
        self.common = ("common-auth", "common-account", "common-password", "common-session")
        for name in self.common:
            (self.root / "etc/pam.d" / name).write_text(
                "auth required pam_unix.so\n" if name == "common-auth"
                else "account required pam_unix.so\n", encoding="utf-8")

    def check_hash(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_repairs_missing_kde_helper_group_and_shadow_permissions(self):
        gid = self.configure(self.root)
        self.assertEqual(gid, 42)
        kde = self.root / "etc/pam.d/kde"
        helper = self.root / "usr/sbin/unix_chkpwd"
        shadow = self.root / "etc/shadow"
        self.assertIn("@include common-auth", kde.read_text())
        self.assertNotIn("pam_permit.so", kde.read_text())
        self.assertIn("shadow:x:42:\n", (self.root / "etc/group").read_text())
        self.assertEqual(self.check_hash(helper), self.check_hash(self.host_helper))
        self.assertEqual((helper.stat().st_uid, helper.stat().st_gid, stat.S_IMODE(helper.stat().st_mode)), (0,42,0o2755))
        self.assertEqual((shadow.stat().st_uid, shadow.stat().st_gid,stat.S_IMODE(shadow.stat().st_mode)), (0,42,0o640))
        self.assertEqual(stat.S_IMODE(kde.stat().st_mode),0o644)

    def test_repeated_finalize_is_idempotent(self):
        self.configure(self.root)
        group = (self.root / "etc/group").read_bytes()
        helper = self.check_hash(self.root / "usr/sbin/unix_chkpwd")
        self.configure(self.root)
        self.assertEqual(group,(self.root / "etc/group").read_bytes())
        self.assertEqual(helper,self.check_hash(self.root / "usr/sbin/unix_chkpwd"))
        self.assertEqual((self.root / "etc/group").read_text().count("shadow:x:42:"),1)

    def test_no_common_policy_uses_nonpermissive_unix_stack(self):
        for name in self.common: (self.root / "etc/pam.d" / name).unlink()
        self.configure(self.root)
        content=(self.root / "etc/pam.d/kde").read_text()
        self.assertIn("auth required pam_unix.so",content)
        self.assertIn("account required pam_unix.so",content)
        self.assertNotIn("pam_permit",content)

    def test_partial_common_stack_stops_before_mutation(self):
        (self.root / "etc/pam.d/common-password").unlink()
        with self.assertRaisesRegex(RuntimeError,"Incomplete common"):
            self.configure(self.root)
        self.assertFalse((self.root / "usr/sbin/unix_chkpwd").exists())
        self.assertFalse((self.root / "etc/pam.d/kde").exists())

    def test_cross_abi_host_fallback_refuses_mismatched_module(self):
        module=self.root / "usr/lib/security/pam_unix.so"
        module.write_bytes(b"\x7fELF"+b"x"*100)
        with self.assertRaisesRegex(RuntimeError,"Cross-ABI"):
            self.configure(self.root)
        self.assertFalse((self.root / "usr/sbin/unix_chkpwd").exists())
        self.assertNotIn("shadow:",(self.root / "etc/group").read_text())

    def test_gid_collision_is_rejected(self):
        with (self.root / "etc/group").open("a") as f: f.write("occupied:x:42:\n")
        with self.assertRaisesRegex(RuntimeError,"occupied"):
            self.configure(self.root)

    def test_existing_permit_only_kde_stack_is_rejected(self):
        (self.root / "etc/pam.d/kde").write_text("auth sufficient pam_permit.so\n")
        with self.assertRaisesRegex(RuntimeError,"Unsafe permissive|lacks verified"):
            self.configure(self.root)

    def test_symlinked_pam_policy_is_rejected(self):
        (self.root / "etc/pam.d/kde").symlink_to(self.root / "etc/group")
        with self.assertRaisesRegex(RuntimeError,"symlinked KDE"):
            self.configure(self.root)

    def test_unverified_helper_binary_is_rejected(self):
        helper=self.root / "usr/sbin/unix_chkpwd"
        helper.write_text("#!/bin/sh\nexit 0\n")
        with self.assertRaisesRegex(RuntimeError,"not ELF"):
            self.configure(self.root)

    def test_missing_shadow_is_rejected(self):
        (self.root / "etc/shadow").unlink()
        with self.assertRaisesRegex(RuntimeError,"non-regular"):
            self.configure(self.root)

    def test_packaging_calls_auth_repair_after_native_before_squashfs(self):
        src = SOURCE.read_text(encoding="utf-8")
        block = src[src.index("def phase_iso_live_smechos(target):"):]
        a=block.index("_restore_issue17_native(target)")
        b=block.index("_ensure_issue17_screenlock_auth(target)")
        c=block.index("    run([mksquashfs, target, squashfs_path,")
        self.assertLess(a,b)
        self.assertLess(b,c)


    def test_existing_permit_before_unix_is_rejected(self):
        (self.root / "etc/pam.d/kde").write_text(
            "auth sufficient pam_permit.so\n"
            "auth required pam_unix.so\n"
            "account required pam_unix.so\n")
        with self.assertRaisesRegex(RuntimeError, "Unsafe permissive"):
            self.configure(self.root)

    def test_common_auth_permit_before_unix_is_rejected(self):
        (self.root / "etc/pam.d/common-auth").write_text(
            "auth sufficient pam_permit.so\n"
            "auth required pam_unix.so\n")
        with self.assertRaisesRegex(RuntimeError, "Unsafe permissive"):
            self.configure(self.root)

    def test_shadow_group_shared_with_users_is_rejected(self):
        with (self.root / "etc/group").open("a") as f:
            f.write("users:x:1000:alice\nshadow:x:1000:\n")
        with self.assertRaisesRegex(RuntimeError, "shared/occupied"):
            self.configure(self.root)

    def test_gid_leading_zero_collision_is_rejected(self):
        with (self.root / "etc/group").open("a") as f:
            f.write("occupied:x:042:\n")
        with self.assertRaisesRegex(RuntimeError, "shared/occupied"):
            self.configure(self.root)

    def test_hardlinked_shadow_is_rejected(self):
        shadow = self.root / "etc/shadow"
        os.link(shadow, self.root / "etc/shadow-link")
        with self.assertRaisesRegex(RuntimeError, "Hardlinked"):
            self.configure(self.root)


    def test_absolute_path_permit_bypass_rejected(self):
        (self.root / "etc/pam.d/kde").write_text(
            "auth sufficient /usr/lib/security/pam_permit.so\n"
            "auth required pam_unix.so\n")
        with self.assertRaisesRegex(RuntimeError, "Unsafe permissive"):
            self.configure(self.root)

    def test_other_sufficient_auth_module_rejected(self):
        (self.root / "etc/pam.d/kde").write_text(
            "auth sufficient pam_rootok.so\n"
            "auth required pam_unix.so\n")
        with self.assertRaisesRegex(RuntimeError, "Unsafe permissive"):
            self.configure(self.root)

    def test_common_auth_bracketed_short_circuit_rejected(self):
        (self.root / "etc/pam.d/common-auth").write_text(
            "auth [success=done default=ignore] pam_unix.so\n"
            "auth required pam_unix.so\n")
        with self.assertRaisesRegex(RuntimeError, "Unsafe permissive"):
            self.configure(self.root)

    def test_common_auth_unverified_include_rejected(self):
        (self.root / "etc/pam.d/common-auth").write_text(
            "@include attacker-auth\n"
            "auth required pam_unix.so\n")
        with self.assertRaisesRegex(RuntimeError, "Unsafe permissive"):
            self.configure(self.root)

if __name__ == "__main__":
    unittest.main()
