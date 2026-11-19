import importlib.util, os, subprocess, sys, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ADDON=ROOT/'addon/ComfyUI-Director-SavedShotQueue-V066'
sys.path.insert(0,str(ADDON))
sys.path.insert(0,str(ROOT))
import shot_capture as cap
import install_v0661 as installer
import recover_saved as recovery

class MuxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t=tempfile.TemporaryDirectory();cls.root=Path(cls.t.name)
        cls.out=cls.root/'output';cls.out.mkdir()
        cls.base=cls.out/'H3_Shot_00001.mp4'
        cls.audio=cls.out/'H3_Shot_00001-audio.mp4'
        cls.other=cls.out/'Other_Shot.mp4'
        cp=subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','lavfi','-i',
            'color=c=blue:s=64x96:r=24','-frames:v','12','-c:v','mpeg4',str(cls.base)],capture_output=True,text=True,timeout=30)
        if cp.returncode:raise RuntimeError(cp.stderr)
        cp=subprocess.run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(cls.base),
          '-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=1',
          '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-shortest',str(cls.audio)],capture_output=True,text=True,timeout=30)
        if cp.returncode:raise RuntimeError(cp.stderr)
        cls.other.write_bytes(cls.base.read_bytes())
    @classmethod
    def tearDownClass(cls):cls.t.cleanup()
    def test_prefers_audio_exact_pair(self):
        self.assertEqual(cap.resolve_vhs_video([str(self.base),str(self.audio)],self.out),self.audio)
    def test_reversed_order_same(self):
        self.assertEqual(cap.resolve_vhs_video([str(self.audio),str(self.base)],self.out),self.audio)
    def test_vhs_nested_tuple(self):
        self.assertEqual(cap.resolve_vhs_video((False,[str(self.base),str(self.audio)]),self.out),self.audio)
    def test_audio_only(self):
        self.assertEqual(cap.resolve_vhs_video([str(self.audio)],self.out),self.audio)
    def test_single_no_audio(self):
        self.assertEqual(cap.resolve_vhs_video([str(self.base)],self.out),self.base)
    def test_relative_paths(self):
        self.assertEqual(cap.resolve_vhs_video(['output/'+self.base.name,'output/'+self.audio.name],self.out),self.audio)
    def test_duplicate_entries(self):
        self.assertEqual(cap.resolve_vhs_video([str(self.base),str(self.audio),str(self.audio)],self.out),self.audio)
    def test_unrelated_two_videos_rejected(self):
        with self.assertRaises(ValueError):cap.resolve_vhs_video([str(self.base),str(self.other)],self.out)
    def test_three_videos_rejected(self):
        with self.assertRaises(ValueError):cap.resolve_vhs_video([str(self.base),str(self.audio),str(self.other)],self.out)
    def test_audio_label_without_audio_stream_rejected(self):
        fake=self.out/'Fake-audio.mp4';base=self.out/'Fake.mp4'
        fake.write_bytes(self.base.read_bytes());base.write_bytes(self.base.read_bytes())
        with self.assertRaisesRegex(ValueError,'lacks an audio'):
            cap.resolve_vhs_video([str(fake),str(base)],self.out)
    def test_cross_folder_same_stem_rejected(self):
        other_dir=self.out/'nested';other_dir.mkdir(exist_ok=True)
        (other_dir/self.audio.name).write_bytes(self.audio.read_bytes())
        with self.assertRaises(ValueError):
            cap.resolve_vhs_video([str(self.base),str(other_dir/self.audio.name)],self.out)
    def test_escape_symlink_rejected(self):
        outside=self.root/'external.mp4';outside.write_bytes(self.base.read_bytes())
        bad=self.out/'bad-audio.mp4'
        if not bad.exists():bad.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'escapes'):
            cap.resolve_vhs_video([str(self.base),str(bad)],self.out)
    def test_capture_selects_muxed_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            dest,rec,created=cap.capture([str(self.base),str(self.audio)],output_root=self.out,job_root=Path(d))
            self.assertTrue(created)
            self.assertEqual(rec['source_video_path'],str(self.audio))
            self.assertEqual(rec['source_sha256'],cap.sha256_file(self.audio))
            self.assertFalse(rec['automatic_editing_authorized'])
            self.assertFalse(rec['generated_footage_for_H3_visual_conditioning'])
            dest2,_,created2=cap.capture([str(self.base),str(self.audio)],output_root=self.out,job_root=Path(d))
            self.assertEqual(dest,dest2);self.assertFalse(created2)
    def test_install_rejects_modified_version(self):
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);target=t/'shot_capture.py';node=t/'nodes.py';init=t/'__init__.py'
            target.write_text('unexpected patched text');node.write_bytes((ADDON/'nodes.py').read_bytes());init.write_bytes((ADDON/'__init__.py').read_bytes())
            with self.assertRaisesRegex(ValueError,'unexpected modifications'):
                installer.install(install=False,target=target,node=node,init=init,backup_dir=t/'backup')
    def test_install_creates_backup_and_idempotent(self):
        from pathlib import Path
        # Test must not depend on local developer path; use known V0.6.6 baseline fixture.
        original=ROOT/'fixtures'/'shot_capture_v066_original.py'
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);target=t/'shot_capture.py';node=t/'nodes.py';init=t/'__init__.py'
            target.write_bytes(original.read_bytes());node.write_bytes((ADDON/'nodes.py').read_bytes());init.write_bytes((ADDON/'__init__.py').read_bytes())
            self.assertEqual(installer.install(install=False,target=target,node=node,init=init,backup_dir=t/'backup',check_idle=lambda:None),'dry_run')
            self.assertEqual(installer.install(install=True,target=target,node=node,init=init,backup_dir=t/'backup',check_idle=lambda:None),'installed')
            self.assertEqual(target.read_bytes(),(ROOT/'patch'/'shot_capture.py').read_bytes())
            backups=list((t/'backup').glob('*.py'));self.assertEqual(len(backups),1)
            self.assertEqual(backups[0].read_bytes(),original.read_bytes())
            self.assertEqual(installer.install(install=True,target=target,node=node,init=init,backup_dir=t/'backup',check_idle=lambda:None),'current')
            installer.rollback(target=target,backup_dir=t/'backup',check_idle=lambda:None)
            self.assertEqual(target.read_bytes(),original.read_bytes())
    def test_install_busy_queue_guard(self):
        original=ROOT/'fixtures'/'shot_capture_v066_original.py'
        with tempfile.TemporaryDirectory() as d:
            t=Path(d);target=t/'shot_capture.py';node=t/'nodes.py';init=t/'__init__.py'
            target.write_bytes(original.read_bytes());node.write_bytes((ADDON/'nodes.py').read_bytes());init.write_bytes((ADDON/'__init__.py').read_bytes())
            def busy():raise RuntimeError('busy queue')
            with self.assertRaisesRegex(RuntimeError,'busy'):
                installer.install(install=True,target=target,node=node,init=init,backup_dir=t/'backup',check_idle=busy)
            self.assertEqual(target.read_bytes(),original.read_bytes())
    def test_production_node_interface_unchanged(self):
        self.assertEqual((ADDON/'nodes.py').read_bytes(),(ROOT/'fixtures'/'nodes_v066_original.py').read_bytes())
    def test_recover_pair_with_override(self):
        with tempfile.TemporaryDirectory() as d:
            # Capture defaults to /workspace jobs; temporarily replace its job root in
            # copy used by this isolated test; no production path is touched.
            copied=Path(d)/'capture.py'
            copied.write_text((ROOT/'patch'/'shot_capture.py').read_text().replace(
                "JOB_ROOT=Path('/workspace/DirectorSavedShotQueueV066/jobs')", f"JOB_ROOT=Path({str(Path(d)/'jobs')!r})"))
            # Example filenames are realistic, but recovery path/stem overrides must match.
            target_stem=self.base.stem
            path=recovery.recover(target_stem,capture_module=copied,output_root=self.out)
            self.assertTrue(path.is_file())

if __name__=='__main__': unittest.main()
