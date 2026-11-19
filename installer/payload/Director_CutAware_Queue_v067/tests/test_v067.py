from __future__ import annotations
import json,sys,subprocess,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cut_audit,drain_jobs,install_v067

class T(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
    self.root=Path(self.tmp.name);self.out=self.root/'output';self.out.mkdir()
    self.video=self.out/'test.mp4'
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=red:s=64x96:r=24:d=1',
         '-f','lavfi','-i','color=c=blue:s=64x96:r=24:d=1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
         '-map','[v]','-c:v','mpeg4','-q:v','2',str(self.video)]
    subprocess.run(cmd,check=True,capture_output=True,timeout=35)
    self.report=self.root/'obs.json'
    data={'version':'director-geometry-semantic-observer-0.6.5',
         'source_path':str(self.video),'source_sha256':cut_audit.sha256(self.video),
         'video_meta':{'frames':48,'width':64,'height':96,'fps':24.},
         'sampled_frames':[0,10,20,30,40,47],
         'observations':{str(f):{'parsed':{'valid':f!=30}} for f in [0,10,20,30,40,47]},
         'preprocess_only':False,'h3_visual_conditioning':False,
         'automatic_editing_authorized':False,'audit':{'continuity_handoff_authorized':False}}
    self.report.write_text(json.dumps(data))
 def test_cut_candidates(self):
    cuts=cut_audit.scene_candidates(self.video,24,48)
    # Synthetic full-screen color cut is detected by conservative ffmpeg detector.
    self.assertEqual([x['frame_estimate'] for x in cuts],[24])
 def test_cut_audit(self):
    p=cut_audit.audit_report(self.report,output_root=self.out)
    o=json.loads(p.read_text());self.assertEqual(o['candidate_cuts'][0]['frame_estimate'],24)
    self.assertEqual(o['invalid_qwen_sample_frames'],[30])
    self.assertEqual(o['sample_intervals_crossing_candidate_cuts'][0]['sampled_from'],20)
    self.assertFalse(o['continuity_handoff_authorized']);self.assertFalse(o['automatic_editing_authorized'])
    self.assertEqual(p,cut_audit.audit_report(self.report,output_root=self.out))
 def test_no_cut_single_color(self):
    clip=self.out/'flat.mp4'
    subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi',
                    '-i','color=c=green:s=64x96:r=24:d=1','-c:v','mpeg4',str(clip)],check=True,timeout=30)
    self.assertEqual(cut_audit.scene_candidates(clip,24,24),[])
 def test_refuse_hash_mismatch(self):
    o=json.loads(self.report.read_text());o['source_sha256']='0'*64;self.report.write_text(json.dumps(o))
    with self.assertRaisesRegex(ValueError,'hash'):cut_audit.audit_report(self.report,output_root=self.out)
 def test_refuse_outside_output(self):
    other=self.root/'elsewhere';other.mkdir()
    with self.assertRaisesRegex(ValueError,'outside'):cut_audit.audit_report(self.report,output_root=other)
 def test_no_overwrite_report(self):
    with self.assertRaises(ValueError):cut_audit.audit_report(self.report,output_root=self.out,output_path=self.report)
 def test_no_overwrite_conflict(self):
    target=self.root/'cut_evidence.json';target.write_text('conflict')
    with self.assertRaises(FileExistsError):cut_audit.audit_report(self.report,output_root=self.out,output_path=target)
 def test_resource_enough_does_not_unload(self):
    with patch.object(drain_jobs,'require_idle'),patch.object(drain_jobs,'gpu_free_mib',return_value=60000),patch.object(drain_jobs,'system_available_mib',return_value=90000),patch.object(drain_jobs.urllib.request,'urlopen',side_effect=AssertionError('should not free')):
        drain_jobs.ensure_qwen_resources(sleep=lambda x:None)
 def test_resource_unload_after_low_memory(self):
    class Resp:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
    with patch.object(drain_jobs,'require_idle'),patch.object(drain_jobs,'gpu_free_mib',side_effect=[1000,40000]),patch.object(drain_jobs,'system_available_mib',return_value=90000),patch.object(drain_jobs.urllib.request,'urlopen',return_value=Resp()) as url:
        drain_jobs.ensure_qwen_resources(sleep=lambda x:None)
        self.assertEqual(url.call_count,1)
 def test_resource_remains_low_fails(self):
    class Resp:
        status=200
        def __enter__(self):return self
        def __exit__(self,*args):pass
    with patch.object(drain_jobs,'require_idle'),patch.object(drain_jobs,'gpu_free_mib',return_value=1000),patch.object(drain_jobs,'system_available_mib',return_value=90000),patch.object(drain_jobs.urllib.request,'urlopen',return_value=Resp()):
        with self.assertRaisesRegex(RuntimeError,'Insufficient'):drain_jobs.ensure_qwen_resources(sleep=lambda x:None)
 def test_resource_busy_refuses_unload(self):
    with patch.object(drain_jobs,'require_idle',side_effect=RuntimeError('busy')),patch.object(drain_jobs.urllib.request,'urlopen',side_effect=AssertionError('must not unload')):
        with self.assertRaisesRegex(RuntimeError,'busy'):drain_jobs.ensure_qwen_resources()
 def test_archive_log(self):
    d=self.root/'results'/'jobabc';d.mkdir(parents=True);(d/'request.json').write_text('x');(d/'worker.log').write_text('error')
    moved=drain_jobs.archive_partial_result(d,'jobabc')
    self.assertFalse(d.exists());self.assertTrue((moved/'worker.log').exists());self.assertIsNone(drain_jobs.archive_partial_result(d,'jobabc'))
 def test_archive_refuses_valid_report(self):
    d=self.root/'results'/'jobabc';d.mkdir(parents=True);(d/'semantic_observation.json').write_text('{}')
    with self.assertRaises(FileExistsError):drain_jobs.archive_partial_result(d,'jobabc')
 def test_archive_refuses_unexpected_file(self):
    d=self.root/'results'/'jobabc';d.mkdir(parents=True);(d/'weird.txt').write_text('x')
    with self.assertRaises(RuntimeError):drain_jobs.archive_partial_result(d,'jobabc')
 def test_install_guard_backup_rollback(self):
    target=self.root/'worker';target.mkdir()
    base=target/'drain_jobs.py';base.write_bytes((ROOT/'test_fixtures/drain_jobs_v066_original.py').read_bytes())
    self.assertEqual(install_v067.install(target=target,apply=False),'dry_run')
    self.assertEqual(install_v067.install(target=target,apply=True),'installed')
    self.assertEqual(install_v067.install(target=target,apply=True),'current')
    self.assertEqual(install_v067.rollback(target=target),'rolled_back')
    self.assertEqual(install_v067.install(target=target,apply=False),'dry_run')
 def test_install_refuses_modified(self):
    target=self.root/'worker';target.mkdir();(target/'drain_jobs.py').write_text('other')
    with self.assertRaisesRegex(RuntimeError,'Unknown installed'):install_v067.install(target=target,apply=True)
 def test_end_to_end_mock_queue_to_sidecar(self):
    addon=ROOT/'test_fixtures'
    import importlib.util
    spec=importlib.util.spec_from_file_location('cap_v0661',str(addon/'shot_capture_v0661.py'))
    cap=importlib.util.module_from_spec(spec);spec.loader.exec_module(cap)
    jobs=self.root/'jobs';done=self.root/'completed';result_root=self.root/'results'
    cap.capture(str(self.video),output_root=self.out,job_root=jobs)
    template=json.loads(self.report.read_text())
    class FakeObserver:
        sha256=staticmethod(cap.sha256_file)
        media_meta=staticmethod(cap.video_meta)
        def observe_video(self,src,dst,samples,source_class,require_idle,allow_free):
            assert require_idle and not allow_free
            dst.mkdir(parents=True)
            p=dst/'semantic_observation.json'
            template['source_path']=str(src)
            template['sampled_frames']=[0,7,14,21,28,35,42,47]
            template['observations']={str(f):{'parsed':{'valid':True}} for f in template['sampled_frames']}
            p.write_text(json.dumps(template));return p
    with patch.object(drain_jobs,'require_idle'),patch.object(drain_jobs,'observer_module',return_value=FakeObserver()):
        completed=drain_jobs.run(jobs=jobs,results=result_root,done=done,video_root=self.out,
                                  manage_memory=False)
    self.assertEqual(len(completed),1)
    sidecar=Path(completed[0]).parent/'cut_evidence.json'
    self.assertTrue(sidecar.exists())
    assert json.loads(sidecar.read_text())['candidate_cuts'][0]['frame_estimate']==24
    self.assertEqual(len(list(done.glob('*.json'))),1)
    self.assertEqual(json.loads(list(done.glob('*.json'))[0].read_text())['cut_evidence_report'],str(sidecar))
 def test_disable_future_authority_claims(self):
    d=json.loads(self.report.read_text());d['automatic_editing_authorized']=True
    self.report.write_text(json.dumps(d))
    with self.assertRaises(ValueError):cut_audit.audit_report(self.report,output_root=self.out)
 def test_clean_chain_is_shadow_only(self):
    t=(ROOT/'cut_audit.py').read_text();self.assertIn("'h3_visual_conditioning':False",t)
    self.assertNotIn('H3ReferenceToVideo',t)

if __name__=='__main__':unittest.main()
