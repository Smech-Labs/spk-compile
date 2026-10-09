"""Regression coverage for the first-boot greeter's DRM group access."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(os.environ.get('SPK_BUILD_SCRIPT', Path(__file__).resolve().parents[1] / 'spk-compile.py'))
spec = importlib.util.spec_from_file_location('spk_compile_test', SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class GreeterGroupsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conf = self.root / 'usr/lib/sysusers.d/plasmalogin.conf'
        self.conf.parent.mkdir(parents=True)
        self.conf.write_text('u plasmalogin - "Greeter" /var/lib/plasmalogin -\n')

    def test_adds_video_and_render(self):
        self.assertTrue(build._configure_plasmalogin_device_groups(self.root))
        self.assertIn('m plasmalogin video\n', self.conf.read_text())
        self.assertIn('m plasmalogin render\n', self.conf.read_text())

    def test_idempotent(self):
        build._configure_plasmalogin_device_groups(self.root)
        before = self.conf.read_bytes()
        self.assertFalse(build._configure_plasmalogin_device_groups(self.root))
        self.assertEqual(before, self.conf.read_bytes())

    def test_preserves_existing_membership_and_missing_newline(self):
        self.conf.write_text(self.conf.read_text() + 'm plasmalogin video')
        build._configure_plasmalogin_device_groups(self.root)
        self.assertEqual(self.conf.read_text().count('m plasmalogin video'), 1)
        self.assertTrue(self.conf.read_text().endswith('video\nm plasmalogin render\n'))

    def test_whitespace_is_not_a_duplicate(self):
        self.conf.write_text(self.conf.read_text() + 'm\t plasmalogin\tvideo\n')
        build._configure_plasmalogin_device_groups(self.root)
        declarations = [line.split() for line in self.conf.read_text().splitlines()]
        self.assertEqual(declarations.count(['m', 'plasmalogin', 'video']), 1)

    def test_comment_is_not_a_membership(self):
        self.conf.write_text(self.conf.read_text() + '# m plasmalogin video\n')
        build._configure_plasmalogin_device_groups(self.root)
        self.assertIn('\nm plasmalogin video\n', self.conf.read_text())

    def test_missing_account_declaration_is_untouched(self):
        self.conf.unlink()
        self.assertFalse(build._configure_plasmalogin_device_groups(self.root))
        self.assertFalse(self.conf.exists())

    def test_phase_configures_greeter(self):
        (self.root / 'etc').mkdir()
        binary = self.root / 'usr/bin/plasmalogin'
        binary.parent.mkdir(parents=True)
        binary.touch()
        with patch.object(build.shutil, 'copy2'), patch.object(build.shutil, 'copystat'):
            build.phase_plasma_configure(str(self.root))
        self.assertIn('m plasmalogin video\n', self.conf.read_text())
        self.assertIn('m plasmalogin render\n', self.conf.read_text())


    def test_final_iso_packaging_handles_late_plm_install(self):
        # The real RC3 image contains PLM but SDDM config from the earlier phase.
        target = self.root / 'target'
        conf = target / 'usr/lib/sysusers.d/plasmalogin.conf'
        conf.parent.mkdir(parents=True)
        conf.write_text('u plasmalogin - "Greeter" /var/lib/plasmalogin -\n')
        (target / 'usr/bin').mkdir(parents=True, exist_ok=True)
        for name in ('kwin_wayland_wrapper', 'plasmashell'):
            (target / 'usr/bin' / name).write_bytes(b'\x7fELF' + b'fixture')
        with patch.object(build, 'BUILD_TMP', str(self.root / 'buildtmp')), \
             patch.object(build, 'run'), \
             patch.object(build.shutil, 'which', return_value='/bin/true'), \
             patch.object(build.shutil, 'copy2'), \
             patch.object(build, '_grub_mkrescue'), patch.object(build, '_ensure_issue17_screenlock_auth') as auth:
            build.phase_iso_live_smechos(str(target))
        self.assertIn('m plasmalogin video\n', conf.read_text())
        self.assertIn('m plasmalogin render\n', conf.read_text())


if __name__ == '__main__':
    unittest.main()
