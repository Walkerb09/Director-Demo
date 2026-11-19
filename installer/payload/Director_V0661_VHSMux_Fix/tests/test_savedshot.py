from __future__ import annotations
import copy,importlib.util,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
ADDON=ROOT/'addon/ComfyUI-Director-SavedShotQueue-V066'
sys.path.insert(0,str(ADDON))
sys.path.insert(0,str(ROOT))
import shot_capture as cap
import patch_workflow as patch
import drain_jobs as drain
import install_v066 as ins

class T(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.out=self.root/'output';self.out.mkdir()
  self.jobs=self.root/'jobs';self.done=self.root/'done'
  self.video=self.out/'test.mp4'
  cmd=['ffmpeg','-y','-hide_banner','-loglevel','error','-f','lavfi',
       '-i','color=c=blue:s=64x96:r=24','-frames:v','12','-c:v','mpeg4',str(self.video)]
  subprocess.run(cmd,check=True,capture_output=True,timeout=20)
 def test_meta(self):
  m=cap.video_meta(self.video)
  self.assertEqual(m['frames'],12);self.assertEqual(m['fps'],24)
 def test_resolve_string(self):
  self.assertEqual(cap.resolve_vhs_video((False,[str(self.video)]),self.out),self.video)
 def test_resolve_output_relative(self):
  self.assertEqual(cap.resolve_vhs_video(['output/test.mp4'],self.out),self.video)
 def test_reject_outside(self):
  a=self.root/'outside.mp4';a.write_bytes(self.video.read_bytes())
  with self.assertRaises(ValueError):cap.resolve_vhs_video(str(a),self.out)
 def test_reject_symlink_outside(self):
  a=self.root/'outside.mp4';a.write_bytes(self.video.read_bytes())
  (self.out/'bad.mp4').symlink_to(a)
  with self.assertRaises(ValueError):cap.resolve_vhs_video('bad.mp4',self.out)
 def test_reject_missing(self):
  with self.assertRaises(ValueError):cap.resolve_vhs_video('missing.mp4',self.out)
 def test_reject_two_videos(self):
  b=self.out/'copy.mp4';b.write_bytes(self.video.read_bytes())
  with self.assertRaises(ValueError):cap.resolve_vhs_video(['test.mp4','copy.mp4'],self.out)
 def test_capture(self):
  dest,rec,created=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  self.assertTrue(created);self.assertEqual(rec['source_sha256'],cap.sha256_file(self.video))
  self.assertEqual(rec['video_meta']['frames'],12)
  self.assertFalse(rec['automatic_editing_authorized'])
  self.assertEqual(json.loads(dest.read_text())['job_id'],rec['job_id'])
 def test_capture_idempotent(self):
  d,a,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  d2,b,created=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  self.assertEqual(d,d2);self.assertEqual(a,b);self.assertFalse(created)
 def test_capture_new_content_new_job(self):
  a,_,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  with self.video.open('ab') as f:f.write(b'append')
  b,_,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  self.assertNotEqual(a,b)
 def test_capture_different_samples(self):
  a,_,_=cap.capture(str(self.video),samples=4,output_root=self.out,job_root=self.jobs)
  b,_,_=cap.capture(str(self.video),samples=8,output_root=self.out,job_root=self.jobs)
  self.assertNotEqual(a,b)
 def test_capture_guard(self):
  for samples in [1,25,'8']:
   with self.assertRaises(ValueError):cap.capture(str(self.video),samples=samples,output_root=self.out,job_root=self.jobs)
 def test_job_guard(self):
  d,r,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  obj,src=drain.job_record(d,video_root=self.out)
  self.assertEqual(src,self.video)
  data=json.loads(d.read_text());data['automatic_editing_authorized']=True
  d.write_text(json.dumps(data))
  with self.assertRaises(ValueError):drain.job_record(d,video_root=self.out)
  data['continuity_handoff_authorized']=True;d.write_text(json.dumps(data))
  with self.assertRaises(ValueError):drain.job_record(d,video_root=self.out)
 def test_source_identity(self):
  d,r,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  data=json.loads(d.read_text());data['source_sha256']='0'*64;d.write_text(json.dumps(data))
  with self.assertRaises(ValueError):drain.job_record(d,video_root=self.out)
 def test_drain_requires_idle_before_gpu(self):
  p,r,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  old=drain.require_idle
  drain.require_idle=lambda:(_ for _ in ()).throw(RuntimeError('busy'))
  try:
   with self.assertRaisesRegex(RuntimeError,'busy'):
    drain.run(jobs=self.jobs,done=self.done,results=self.root/'results')
  finally:drain.require_idle=old
 def test_dry_run(self):
  p,r,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  old=drain.require_idle;drain.require_idle=lambda:None
  try:self.assertEqual(drain.run(jobs=self.jobs,done=self.done,results=self.root/'results',dry_run=True,video_root=self.out),[])
  finally:drain.require_idle=old
 def test_install_dry_run(self):
  target=self.root/'addon';self.assertEqual(ins.install(source=ADDON,target=target), 'dry_run');self.assertFalse(target.exists())
 def test_install_idempotent(self):
  target=self.root/'addon';q=lambda:None
  self.assertEqual(ins.install(source=ADDON,target=target,dry_run=False,queue_checker=q),'installed')
  self.assertEqual(ins.install(source=ADDON,target=target,dry_run=False,queue_checker=q),'identical')
  (target/'nodes.py').write_text('tampered')
  with self.assertRaises(FileExistsError):ins.install(source=ADDON,target=target,dry_run=False,queue_checker=q)
 def test_install_queue_guard(self):
  def busy():raise RuntimeError('busy')
  with self.assertRaisesRegex(RuntimeError,'busy'):
   ins.install(source=ADDON,target=self.root/'add2',dry_run=False,queue_checker=busy)
 def test_capture_node_interface(self):
  txt=(ADDON/'nodes.py').read_text()
  self.assertIn("'saved_video':('VHS_FILENAMES'",txt)
  self.assertIn('OUTPUT_NODE=True',txt)
  self.assertNotIn('H3_prompt',txt)
 def test_capture_node_disable(self):
  s=(ADDON/'nodes.py').read_text();ns={}
  exec(s.replace('from .shot_capture import capture','from shot_capture import capture'),ns)
  val=ns['DirectorV066SavedShotShadowQueue']().run(None,capture_enabled=False)
  self.assertIn('disabled',val['result'][1]);self.assertEqual(val['result'][0],'{}')
 def fixture(self):
  return {'nodes':[{'id':1,'type':'VHS_VideoCombine','pos':[100,100],
                 'outputs':[{'name':'Filenames','type':'VHS_FILENAMES','links':[10]}],
                 'order':4},{'id':2,'type':'ExistingReview','inputs':[{'name':'shot','type':'VHS_FILENAMES','link':10}],
                   'outputs':[],'order':5}],
          'links':[[10,1,0,2,0,'VHS_FILENAMES']], 'last_node_id':2,'last_link_id':10}
 def test_shadow_drain_end_to_end_mock(self):
  p,r,_=cap.capture(str(self.video),output_root=self.out,job_root=self.jobs)
  class FakeObserver:
   sha256=staticmethod(cap.sha256_file)
   media_meta=staticmethod(cap.video_meta)
   def observe_video(self,src,dst,samples,source_class,require_idle,allow_free):
    self.test_assert(require_idle and not allow_free)
    dst.mkdir(parents=True)
    f=dst/'semantic_observation.json'
    f.write_text(json.dumps({'source_sha256':cap.sha256_file(src),'preprocess_only':False,
                             'automatic_editing_authorized':False}))
    return f
   test_assert=staticmethod(lambda ok:None if ok else (_ for _ in ()).throw(AssertionError()))
  old_idle=drain.require_idle;old_observer=drain.observer_module
  drain.require_idle=lambda:None
  drain.observer_module=lambda p:FakeObserver()
  try:
   completed=drain.run(jobs=self.jobs,done=self.done,results=self.root/'results',
                       video_root=self.out,observer_path=self.root/'fake_observer.py')
   self.assertEqual(len(completed),1)
   self.assertEqual(len(list(self.done.glob('*.json'))),1)
   self.assertEqual(drain.run(jobs=self.jobs,done=self.done,results=self.root/'results',
                       video_root=self.out,observer_path=self.root/'fake_observer.py'),[])
  finally:drain.require_idle=old_idle;drain.observer_module=old_observer
 def test_null_vhs_output_links(self):
  graph=self.fixture();graph['nodes'][0]['outputs'][0]['links']=None
  result=patch.patch(graph)
  self.assertEqual(result['nodes'][0]['outputs'][0]['links'],[11])
  self.assertIsNone(graph['nodes'][0]['outputs'][0]['links'])
 def test_workflow_patch_links_preserved(self):
  f=self.fixture();g=patch.patch(f)
  self.assertEqual(f['links'],[[10,1,0,2,0,'VHS_FILENAMES']])
  self.assertEqual(g['links'][0],f['links'][0]);self.assertEqual(g['links'][1][1],1)
  self.assertEqual(g['links'][1][3],3)
  self.assertEqual(g['nodes'][-1]['type'],patch.NODE)
  self.assertTrue(g['nodes'][-1]['widgets_values'][0])
 def test_workflow_patch_no_double(self):
  g=patch.patch(self.fixture())
  with self.assertRaises(ValueError):patch.patch(g)
 def test_workflow_patch_multi_vhs(self):
  g=self.fixture();b=copy.deepcopy(g['nodes'][0]);b['id']=3;g['nodes'].append(b)
  with self.assertRaises(ValueError):patch.patch(g)
  out=patch.patch(g,video_node_id=1);self.assertEqual(out['nodes'][-1]['type'],patch.NODE)
 def test_workflow_unknown_source(self):
  g=self.fixture();g['nodes'][0]['outputs'][0]['type']='VIDEO'
  with self.assertRaises(ValueError):patch.patch(g)
 def test_workflow_input_not_mutated(self):
  f=self.fixture();before=json.dumps(f,sort_keys=True);patch.patch(f)
  self.assertEqual(json.dumps(f,sort_keys=True),before)
 def test_clean_chain_no_media_output(self):
  txt=(ADDON/'nodes.py').read_text()
  self.assertIn("RETURN_TYPES=('STRING','STRING')",txt)
  self.assertNotIn("'IMAGE'",txt);self.assertNotIn("'VIDEO'",txt)
 def test_no_worker_in_capture(self):
  t=(ADDON/'shot_capture.py').read_text();self.assertNotIn('transformers',t)
  self.assertNotIn('Qwen3VL',t)

if __name__=='__main__':unittest.main()
