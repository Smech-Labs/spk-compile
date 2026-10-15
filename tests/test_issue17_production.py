"""Builder integration checks. External Linux commands are mocked, not boot proof."""
import hashlib
import inspect
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from test_display_manager_groups import build

FIXTURE = Path(__file__).parent / 'fixtures/Polkit1Backend.cpp'

class KAuthIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'src/backends/polkit-1/Polkit1Backend.cpp'
        self.source.parent.mkdir(parents=True)
        shutil.copyfile(FIXTURE, self.source)

    def test_real_upstream_source_patch_is_idempotent_and_retains_policy(self):
        self.assertTrue(build._patch_kauth_no_glib(self.root))
        data = self.source.read_bytes()
        self.assertFalse(build._patch_kauth_no_glib(self.root))
        self.assertEqual(data, self.source.read_bytes())
        text = self.source.read_text()
        self.assertIn('#if !QT_CONFIG(glib)', text)
        self.assertIn('checkAuthorizationSyncWithDetails(action, subject,', text)
        self.assertIn('PolkitQt1::Authority::AllowUserInteraction, polkit1Details)', text)
        self.assertIn('authority->clearError();\n        return false;', text)
        self.assertIn('case PolkitQt1::Authority::Yes:\n        return true;\n    default:\n        return false;', text)

    def test_unrecognized_source_fails_without_writing(self):
        self.source.write_text('unrecognized upstream source')
        before = self.source.read_bytes()
        with self.assertRaises(RuntimeError):
            build._patch_kauth_no_glib(self.root)
        self.assertEqual(before, self.source.read_bytes())

    def test_kde_package_rebuilds_old_stamp_and_patches_before_cmake(self):
        calls = []
        def extract(_archive, destination):
            destination = Path(destination) / 'src/backends/polkit-1/Polkit1Backend.cpp'
            destination.parent.mkdir(parents=True)
            shutil.copyfile(FIXTURE, destination)
        def cmake(source, prefix, **kwargs):
            calls.append('cmake')
            text=(Path(source)/'src/backends/polkit-1/Polkit1Backend.cpp').read_text()
            self.assertIn('checkAuthorizationSyncWithDetails', text)
            self.assertIn('-DCMAKE_REQUIRE_FIND_PACKAGE_PolkitQt6-1=ON', kwargs['extra_args'])
        with patch.object(build,'BUILD_TMP',str(self.root/'build')), \
             patch.object(build,'sources',return_value=str(self.root)), \
             patch.object(build,'_phase_done',side_effect=lambda profile,name: name=='kde-pkg-kauth'), \
             patch.object(build,'download'), patch.object(build,'extract',side_effect=extract), \
             patch.object(build,'cmake_install',side_effect=cmake), patch.object(build,'_mark_done') as mark:
            build._kde_pkg('kauth','6.24.0','unused',str(self.root/'target'),{})
        self.assertEqual(calls,['cmake'])
        mark.assert_called_once_with('smechos-plasma-live','kde-pkg-kauth-issue17-polkit-sync-v1')

    def test_current_stamp_skips_package(self):
        with patch.object(build,'_phase_done',return_value=True), patch.object(build,'download') as download:
            build._kde_pkg('kauth','6.24.0','unused',str(self.root),{})
        download.assert_not_called()

    def test_resume_reenters_old_completed_kde_phase(self):
        with patch.object(build,'PROFILES',{'smechos-plasma-live':[('kde',lambda target: self.calls.append(target),'KDE')]}), \
             patch.object(build,'_resolve_kde_versions',return_value=('6.6.6','6.24','6.24.0')), \
             patch.object(build,'_phase_done',side_effect=lambda profile,name:name=='kde'), \
             patch.object(build,'ensure'), patch.object(build,'_mark_done'):
            self.calls=[]
            build.cmd_build('smechos-plasma-live',str(self.root))
        self.assertEqual(self.calls,[str(self.root)])

    def test_resume_refreshes_both_distribution_formats_once(self):
        done={'kde','bundle','bundle-spkg','kde-pkg-kauth-issue17-polkit-sync-v1'}
        calls=[]
        phases=[(name,lambda target,n=name:calls.append(n),name) for name in ('kde','bundle','bundle-spkg')]
        with patch.object(build,'PROFILES',{'smechos-plasma-live':phases}), \
             patch.object(build,'_resolve_kde_versions',return_value=('6.6.6','6.24','6.24.0')), \
             patch.object(build,'_phase_done',side_effect=lambda profile,name:name in done), \
             patch.object(build,'ensure'), patch.object(build,'_mark_done',side_effect=lambda profile,name:done.add(name)):
            build.cmd_build('smechos-plasma-live',str(self.root))
            self.assertEqual(calls,['bundle','bundle-spkg'])
            calls.clear()
            build.cmd_build('smechos-plasma-live',str(self.root))
            self.assertEqual(calls,[])

    def test_polkit_dependency_precedes_frameworks(self):
        source=inspect.getsource(build.phase_kde)
        self.assertLess(source.index('_pqt_stamp ='),source.index('for mod in kf6:'))

class FinalPackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'target'
        self.bin=self.root/'usr/bin'
        self.bin.mkdir(parents=True)
        for name in build._ISSUE17_NATIVE:
            (self.bin/name).write_bytes(b'\x7fELFsynthetic-test-fixture')

    def test_fresh_native_build_is_unchanged(self):
        before={p.name:p.read_bytes() for p in self.bin.iterdir()}
        self.assertEqual(build._restore_issue17_native(self.root),0)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.bin.iterdir()})

    def test_unknown_wrapper_aborts_before_packaging(self):
        (self.bin/'plasmashell').write_text('#!/bin/sh\necho unknown')
        (self.bin/'plasmashell.real').write_bytes(b'\x7fELFunknown')
        seen=[]
        with patch.object(build,'BUILD_TMP',str(self.root.parent/'build')), \
             patch.object(build,'run',side_effect=lambda argv,**kw:seen.append(argv)), \
             patch.object(build.shutil,'which',side_effect=lambda name:name):
            with self.assertRaisesRegex(RuntimeError,'Unverified'):
                build.phase_iso_live_smechos(str(self.root))
        self.assertFalse(any(argv[0]=='mksquashfs' for argv in seen))
        self.assertTrue((self.bin/'plasmashell').read_bytes().startswith(b'#!'))

    def test_installed_plm_without_group_declaration_aborts(self):
        (self.bin/'plasmalogin').touch()
        with patch.object(build,'BUILD_TMP',str(self.root.parent/'build')), \
             patch.object(build,'run') as command, \
             patch.object(build.shutil,'which',side_effect=lambda name:name):
            with self.assertRaisesRegex(RuntimeError,'PLM installed without'):
                build.phase_iso_live_smechos(str(self.root))
        self.assertFalse(any(c.args[0][0]=='mksquashfs' for c in command.call_args_list))

    def test_native_rename_readback_and_backup_with_synthetic_metadata(self):
        # Exercises rename/readback; mocked Linux metadata is NOT a Linux proof.
        expected={}
        wrapper=b'#!/bin/sh\nexec fixture'
        native=b'\x7fELFfixture'
        for name in build._ISSUE17_NATIVE:
            (self.bin/name).write_bytes(wrapper)
            (self.bin/(name+'.real')).write_bytes(native)
            expected[name]=(hashlib.sha256(wrapper).hexdigest(),hashlib.sha256(native).hexdigest())
        real_stat=Path.stat
        def synthetic_stat(path,*args,**kwargs):
            result=real_stat(path,*args,**kwargs)
            if str(path).endswith('.real'):
                fields=list(result); fields[0]=0o100755; fields[4]=0; fields[5]=0
                return build.os.stat_result(fields)
            return result
        with patch.object(build,'_ISSUE17_NATIVE',expected), \
             patch.object(build,'os',wraps=build.os) as platform, \
             patch.object(Path,'stat',synthetic_stat):
            platform.name='posix'
            self.assertEqual(build._restore_issue17_native(self.root),2)
        for name in expected:
            self.assertEqual((self.bin/name).read_bytes(),native)
            self.assertEqual((self.bin/(name+'.issue17-wrapper-backup')).read_bytes(),wrapper)
            self.assertFalse((self.bin/(name+'.real')).exists())
        self.assertEqual(build._restore_issue17_native(self.root),0)

    def test_missing_native_executable_is_rejected(self):
        (self.bin/'plasmashell').unlink()
        with self.assertRaises(FileNotFoundError):
            build._restore_issue17_native(self.root)

    def test_known_pair_still_refuses_non_linux_mutation(self):
        if build.os.name=='posix':
            self.skipTest('Windows refusal only; Linux metadata separately validated at runtime')
        expected={}
        for name in build._ISSUE17_NATIVE:
            wrapper=b'#!/bin/sh\nexec fixture'
            native=b'\x7fELFfixture'
            (self.bin/name).write_bytes(wrapper)
            (self.bin/(name+'.real')).write_bytes(native)
            expected[name]=(hashlib.sha256(wrapper).hexdigest(),hashlib.sha256(native).hexdigest())
        with patch.object(build,'_ISSUE17_NATIVE',expected):
            with self.assertRaisesRegex(RuntimeError,'Linux root-owned'):
                build._restore_issue17_native(self.root)
        self.assertFalse(list(self.bin.glob('*.issue17-wrapper-backup')))

    def test_final_boundary_reads_groups_and_native_before_squashfs(self):
        conf=self.root/'usr/lib/sysusers.d/plasmalogin.conf'
        conf.parent.mkdir(parents=True)
        conf.write_text('u plasmalogin - "Greeter" /var/lib/plasmalogin -\n')
        calls=[]
        def command(argv,**kwargs):
            calls.append(argv)
            if argv[0]=='mksquashfs':
                self.assertIn('m plasmalogin video\n',conf.read_text())
                self.assertIn('m plasmalogin render\n',conf.read_text())
                for name in build._ISSUE17_NATIVE:
                    self.assertTrue((self.bin/name).read_bytes().startswith(b'\x7fELF'))
        with patch.object(build,'BUILD_TMP',str(self.root.parent/'build')), \
             patch.object(build,'run',side_effect=command), \
             patch.object(build.shutil,'which',side_effect=lambda name:name), \
             patch.object(build.shutil,'copy2'), patch.object(build,'_grub_mkrescue'), patch.object(build,'_ensure_issue17_screenlock_auth') as auth:
            build.phase_iso_live_smechos(str(self.root))
        self.assertEqual(sum(argv[0]=='mksquashfs' for argv in calls),1)

if __name__=='__main__':
    unittest.main()
