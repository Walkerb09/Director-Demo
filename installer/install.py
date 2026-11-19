#!/usr/bin/env python3
"""PUBLIC 24 GB DEMO install (V0.9.70): the lite24 H3 set, Director Studio, Qwen 8B observer chain. No personal media.
Fails closed, additive, resumable; never runs H3 or Qwen, never restarts ComfyUI."""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, time, urllib.request, zipfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
WS=Path('/workspace')
COMFY=WS/'ComfyUI'
PIP=Path('/venv/main/bin/python')
UPDATE_COMFY=False
MODEL_FILES=[
 ('diffusion_models/minimax_h3_ref2va_bf16.safetensors', 'e32c54c1a7b4f5f397f195cea267ccb18806303bb665678c4bee60953bdf3026', 'H3 diffusion BF16'),
 ('text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors','600d567f6a9629c8574e8e7041b199bdd9c59a986afa7906910a81919610607d','H3 text encoder BF16'),
 ('vae/minimax_h3_video_vae_fp16.safetensors','7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522','H3 video VAE'),
 ('vae/minimax_h3_audio_vae_fp32.safetensors','8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48','H3 audio VAE'),
]
# V0.9.70 the 24 GB DEMO set (--lite24): Comfy-Org's own smaller H3 files, loaded by stock ComfyUI. Their SHA-256 is read
# live from Hugging Face's file index (what Hugging Face itself stores for the file), then checked after the download.
LITE_FILES=[
 ('diffusion_models/minimax_h3_ref2va_pruned_w6a8.safetensors',None,'H3 diffusion, pruned w6a8 (24 GB demo)',16.0e9),
 ('text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors',None,'H3 text encoder nvfp4 (24 GB demo)',15.7e9),
 ('vae/minimax_h3_video_vae_int8_convrot.safetensors',None,'H3 video VAE int8 (24 GB demo)',2.81e9),
 ('vae/minimax_h3_audio_vae_fp32.safetensors','8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48','H3 audio VAE',.605e9),
]
LITE=True   # the public demo bundle is always the 24 GB set
PAYLOAD={
 'savedshot':'Director_V0661_VHSMux_Fix.zip',
 'cutaware':'Director_CutAware_Queue_v067.zip',
 'observer':'Director_SemanticObserver_v065.zip',
 'profile':'Director_ProfiledObserver_v0722.zip',
 'context':'Director_SceneContext_v073.zip',
 'postshot':'Director_UnifiedPostshot_v074.zip',
 'dispatcher':'Director_IdleDispatcher_v075.zip',
 'workflow':'Director_H3_CleanChain_V066_SavedShotCapture_TEST_TEMPLATE.json',
 'studio':'ComfyUI-DirectorStudio_v0970_demo.zip',
}

def sha(path):
 h=hashlib.sha256()
 with open(path,'rb') as f:
  for part in iter(lambda:f.read(8*1024*1024),b''): h.update(part)
 return h.hexdigest()

def shell(*cmd,env=None):
 print('\nRUN', ' '.join(map(str,cmd)),flush=True)
 subprocess.run(list(map(str,cmd)),check=True,env=env)

def log(message):print('\n>>> '+message,flush=True)

def verify_bundle():
 manifest=json.loads((HERE/'payload_manifest.json').read_text())
 assert set(manifest)==set(PAYLOAD.values()),'Unexpected bundle manifest'
 for file,digest in manifest.items():
  target=HERE/'payload'/file
  if not target.is_file() or sha(target)!=digest:raise RuntimeError('Corrupt package: '+file)
  if file.endswith('.zip'):
   with zipfile.ZipFile(target) as z:
    if z.testzip():raise RuntimeError('Corrupt ZIP: '+file)
    for item in z.infolist():
     p=Path(item.filename)
     if p.is_absolute() or '..' in p.parts or (item.external_attr >> 16) & 0o170000 == 0o120000:
      raise RuntimeError('Unsafe ZIP entry '+item.filename)
 log('Payload signatures and archive members verified')

def copy_exact(source,dest):
 source=Path(source); dest=Path(dest)
 if dest.is_symlink():raise RuntimeError('Refusing symlink: '+str(dest))
 if dest.exists():
  if not dest.is_file() or sha(source)!=sha(dest):raise RuntimeError('Refusing to overwrite changed file: '+str(dest))
  return
 dest.parent.mkdir(parents=True,exist_ok=True)
 tmp=dest.with_name(dest.name+'.bootstrap-part')
 if tmp.exists():
  if tmp.is_file() and sha(source)==sha(tmp):pass
  else:raise RuntimeError('Unexpected staged file: '+str(tmp))
 else:shutil.copy2(source,tmp)
 tmp.rename(dest)

def unpack_package(key,target):
 if target.is_symlink():raise RuntimeError('Refusing symlink directory: '+str(target))
 with zipfile.ZipFile(HERE/'payload'/PAYLOAD[key]) as z:
  for info in z.infolist():
   name=Path(info.filename)
   if info.is_dir():continue
   path=target/name
   if path.is_symlink():raise RuntimeError('Symlink destination '+str(path))
   path.parent.mkdir(parents=True,exist_ok=True)
   if path.exists():
    expected=hashlib.sha256(z.read(info)).hexdigest()
    if key=='savedshot' and str(name)=='drain_jobs.py':
     # A repeat install may already have the signed V0.6.7 upgrade in place.
     with zipfile.ZipFile(HERE/'payload'/PAYLOAD['cutaware']) as newer:
      accepted=hashlib.sha256(newer.read('drain_jobs.py')).hexdigest()
     if path.is_file() and sha(path) in (expected,accepted):continue
    if not path.is_file() or expected!=sha(path):
     raise RuntimeError('Refusing to overwrite existing file: '+str(path))
   else:
    with z.open(info) as src:
     data=src.read()
    stage=path.with_name(path.name+'.bootstrap-part')
    if stage.exists():raise RuntimeError('Unfinished stage: '+str(stage))
    stage.write_bytes(data);stage.rename(path)
 log('Extracted '+key+' to '+str(target))

def patch_additive_from_package(src_dir,subdir,dest):
 root=src_dir/subdir
 if not root.is_dir():raise RuntimeError('Missing packaged node: '+str(root))
 for f in sorted(root.rglob('*')):
  if f.is_file():copy_exact(f,dest/f.relative_to(root))

def comfy_port():
 for port in (os.environ.get('COMFYUI_PORT'),'18188','8188'):
  if not port:continue
  try:
   with urllib.request.urlopen(f'http://127.0.0.1:{port}/queue',timeout=3):return port
  except Exception:pass
 return None

def queue_preflight():
 port=comfy_port()
 if port is None:
  log('ComfyUI API not reachable (tried 18188/8188); no queue checks possible. Only proceed if nothing is generating');return
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{port}/queue',timeout=4) as response:
   q=json.load(response)
  if not isinstance(q.get('queue_running'),list) or not isinstance(q.get('queue_pending'),list):
   raise RuntimeError('Unknown ComfyUI queue format; refusing install')
  if q['queue_running'] or q['queue_pending']:
   raise RuntimeError('ComfyUI is generating; wait until idle')
  log(f'ComfyUI at port {port} is idle')
 except (OSError,TimeoutError) as e:
  log('ComfyUI API not reachable on port 18188; no queue checks possible. Only proceed if nothing is generating: '+str(e))

def python_setup():
 if not (COMFY/'main.py').is_file():raise RuntimeError('Expected preinstalled /workspace/ComfyUI/main.py; ask for template paths')
 if not PIP.is_file():raise RuntimeError('Expected preinstalled /venv/main/bin/python, required by archived Director runners. Check template.')
 if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
  raise RuntimeError('ffmpeg and ffprobe must be installed first on this ComfyUI template')
 if not shutil.which('git'):raise RuntimeError('git is missing')
 # Avoid hundreds of gigabytes of downloads if the installed ComfyUI predates native H3.
 has_h3=False
 for base in (COMFY/'comfy_extras',COMFY/'nodes.py'):
  files=base.rglob('*.py') if base.is_dir() else ([base] if base.is_file() else [])
  for src in files:
   if 'MiniMaxH3ReferenceToVideo' in src.read_text(errors='ignore'):
    has_h3=True;break
  if has_h3:break
 if not has_h3:
  if not UPDATE_COMFY:
   raise RuntimeError('This ComfyUI is too old for MiniMax H3 (no MiniMaxH3ReferenceToVideo). Re-run with --update-comfyui to update it '
                      '(git pull --ff-only + requirements), or pick a newer ComfyUI template. Nothing was downloaded.')
  if subprocess.run(['git','-C',str(COMFY),'status','--porcelain','--untracked-files=no'],capture_output=True,text=True).stdout.strip():
   raise RuntimeError('ComfyUI has local code changes; not updating it automatically. Update it yourself, then re-run.')
  shell('git','-C',COMFY,'pull','--ff-only')
  shell(PIP,'-m','pip','install','--disable-pip-version-check','-r',COMFY/'requirements.txt')
  return python_setup()
 shell(PIP,'-c','import torch; print("COMFY_TORCH",torch.__version__,"CUDA",torch.cuda.is_available())')
 shell(PIP,'-m','pip','install','--disable-pip-version-check',
       'imageio-ffmpeg','scikit-image','pilgram','PyYAML','aiohttp',
       'huggingface_hub>=0.35,<2','hf_xet')
 # Director Studio needs OpenCV with the DNN face models (cv2.FaceDetectorYN, OpenCV >= 4.8). Use the OpenCV ComfyUI
 # already has; install the headless build only if there is none (two OpenCV builds side by side break `import cv2`).
 probe='import cv2,sys; ok=hasattr(cv2,"FaceDetectorYN") and hasattr(cv2,"FaceRecognizerSF"); print("OPENCV",cv2.__version__,ok); sys.exit(0 if ok else 3)'
 r=subprocess.run([str(PIP),'-c',probe])
 if r.returncode!=0:
  have=subprocess.run([str(PIP),'-m','pip','list','--format=freeze'],capture_output=True,text=True).stdout.lower()
  if 'opencv' in have and r.returncode==3:
   shell(PIP,'-m','pip','install','--disable-pip-version-check','--upgrade',
         next((n for n in ('opencv-contrib-python-headless','opencv-contrib-python','opencv-python-headless','opencv-python') if n+'==' in have),'opencv-python-headless'))
  else:
   shell(PIP,'-m','pip','install','--disable-pip-version-check','opencv-python-headless>=4.8')
  shell(PIP,'-c',probe)
 # Qwen environment shares preinstalled torch, but keeps its newer transformers independent.
 qwen=WS/'h3-qwen-venv'
 if not (qwen/'bin/python').is_file():shell(PIP,'-m','venv','--system-site-packages',qwen)
 shell(qwen/'bin/python','-m','pip','install','--disable-pip-version-check',
       'transformers>=4.57.1,<5','accelerate>=1,<2','huggingface_hub>=0.35,<2',
       'safetensors','pillow','sentencepiece')
 shell(qwen/'bin/python','-c',
       'import torch; from transformers import Qwen3VLForConditionalGeneration,AutoProcessor; print("QWEN_IMPORT_READY",torch.__version__)')

def clone_node(folder,url):
 dest=COMFY/'custom_nodes'/folder
 if dest.exists():
  if not (dest/'.git').exists():raise RuntimeError('Existing non-git node folder: '+str(dest))
  log('Keeping existing '+folder+' checkout unchanged')
 else:shell('git','clone','--depth','1',url,dest)

def install_code():
 packages={
  'savedshot':'DirectorSavedShotQueueV066',
  'cutaware':'DirectorCutAwareV067_ARCHIVE',
  'observer':'DirectorSemanticObserverV065',
  'profile':'DirectorProfiledV0722',
  'context':'DirectorSceneContextV073',
  'postshot':'DirectorUnifiedV074',
  'dispatcher':'DirectorIdleDispatcherV075',
 }
 for key,name in packages.items():unpack_package(key,WS/name)
 patch_additive_from_package(WS/'DirectorSavedShotQueueV066',
   'addon/ComfyUI-Director-SavedShotQueue-V066',
   COMFY/'custom_nodes/ComfyUI-Director-SavedShotQueue-V066')
 patch_additive_from_package(WS/'DirectorSemanticObserverV065',
   'addon/ComfyUI-Director-SemanticObserver-V063',
   COMFY/'custom_nodes/ComfyUI-Director-SemanticObserver-V063')
 # Apply verified stand-alone V0.6.7 cut-aware drain implementation; preserve base archive.
 for file in ('drain_jobs.py','cut_audit.py'):
  dest=WS/'DirectorSavedShotQueueV066'/file
  src=WS/'DirectorCutAwareV067_ARCHIVE'/file
  if file=='drain_jobs.py':
   # Exact old file bytes are in the signed V0.6.6.1 payload ZIP, not inferred from filesystem.
   with zipfile.ZipFile(HERE/'payload'/PAYLOAD['savedshot']) as archive:
    old_bytes=archive.read(file)
   oldhash=hashlib.sha256(old_bytes).hexdigest()
   if sha(dest)!=sha(src):
    if sha(dest)!=oldhash:raise RuntimeError('Unknown previous drain worker; refusing patch')
    backup=dest.with_name('drain_jobs.py.pre_v067_backup')
    copy_exact(dest,backup)
    stage=dest.with_name('drain_jobs.py.v067-part')
    if stage.exists():raise RuntimeError('Unfinished V0.6.7 stage')
    shutil.copy2(src,stage)
    if sha(stage)!=sha(src):raise RuntimeError('Worker copy verification failed')
    stage.replace(dest)
  else:copy_exact(src,dest)
 workflow=PAYLOAD['workflow']
 copy_exact(HERE/'payload'/workflow,WS/'DirectorWorkflows'/workflow)
 # ComfyUI's workflow library locations vary; the original JSON is always available at DirectorWorkflows.
 for name in ('DirectorIdleDispatcherV075','DirectorUnifiedV074','DirectorProfiledV0722','DirectorSceneContextV073'):
  root=WS/name
  script=root/'verify_package.py'
  if script.is_file():shell(PIP,script)
 log('Director operational components ready (public demo: no sample media)')

COMFY_FLAGS=('--disable-dynamic-vram','--disable-pinned-memory')
THREAD_MARK='# Director: CPU threads = this container\'s real share'
THREAD_LINE=(r"""_dq=$(awk '$1!="max"&&$2>0{print int($1/$2)}' /sys/fs/cgroup/cpu.max 2>/dev/null); """
 r"""[ -z "$_dq" ] && _dq=$(awk 'NR==1{q=$1} NR==2{p=$1} END{if(q>0&&p>0)print int(q/p)}' /sys/fs/cgroup/cpu/cpu.cfs_quota_us /sys/fs/cgroup/cpu/cpu.cfs_period_us 2>/dev/null); """
 r"""if [ -n "$_dq" ] && [ "$_dq" -gt 0 ] && [ -z "$OMP_NUM_THREADS" ]; then _dt=$(( _dq > 8 ? _dq - 2 : _dq )); """
 r"""export OMP_NUM_THREADS=$_dt MKL_NUM_THREADS=$_dt OPENBLAS_NUM_THREADS=$_dt NUMEXPR_NUM_THREADS=$_dt; fi  """)+THREAD_MARK
def comfy_memory_flags(script=Path('/opt/supervisor-scripts/comfyui.sh')):
 """Full BF16 H3 is ~118 GB of weights. ComfyUI's dynamic VRAM (on by default since ~0.37) stages the text encoder AND
 the video model in system RAM at once, which kills ComfyUI on a 123 GB instance. These flags restore the classic
 loading. Added to Vast's ComfyUI launch line (once, with a .bak copy); takes effect at the next ComfyUI restart."""
 if not script.is_file():
  log('Could not find '+str(script)+': add '+' '.join(COMFY_FLAGS)+' to ComfyUI\'s launch options yourself');return
 lines=script.read_text().splitlines(keepends=True); hit=None
 for i,l in enumerate(lines):
  if '${COMFYUI_ARGS}' in l and 'COMFYUI_ARGS=' not in l:hit=i        # the launch line (the instance sets its own COMFYUI_ARGS)
 if hit is None:
  log('Unknown ComfyUI launch script: add '+' '.join(COMFY_FLAGS)+' to ComfyUI\'s launch options yourself');return
 if not any(THREAD_MARK in l for l in lines):          # Vast shows every host core (e.g. 384) but a rental gets a share (e.g. 46)
  lines.insert(1 if lines and lines[0].startswith('#!') else 0, THREAD_LINE+'\n'); hit+=1
  log('ComfyUI CPU threads capped to the container\'s real share (Vast hosts show every core; too many threads made the CPU steps crawl)')
 if all(f in lines[hit] for f in COMFY_FLAGS):
  bak=script.with_name(script.name+'.before_director')
  if not bak.exists():shutil.copy2(script,bak)
  tmp=script.with_name(script.name+'.director-part'); tmp.write_text(''.join(lines)); shutil.copymode(script,tmp); tmp.replace(script)
  log('ComfyUI memory flags already set ('+' '.join(COMFY_FLAGS)+')');return
 missing=' '.join(f for f in COMFY_FLAGS if f not in lines[hit])
 lines[hit]=lines[hit].replace('${COMFYUI_ARGS}','${COMFYUI_ARGS} '+missing,1)
 bak=script.with_name(script.name+'.before_director')
 if not bak.exists():shutil.copy2(script,bak)
 tmp=script.with_name(script.name+'.director-part'); tmp.write_text(''.join(lines)); shutil.copymode(script,tmp); tmp.replace(script)
 log('ComfyUI launch flags added for full BF16 H3: '+missing+' (active after the ComfyUI restart; original kept as '+bak.name+')')

def install_studio():
 """Director Studio V0.9.29 into ComfyUI/custom_nodes (the web UI at /director). An older/different copy is moved
 to /workspace/DirectorBackups first, never deleted."""
 dest=COMFY/'custom_nodes'/'ComfyUI-DirectorStudio'
 stage=WS/'.director_studio_stage'
 if stage.exists():shutil.rmtree(stage)
 stage.mkdir(parents=True)
 with zipfile.ZipFile(HERE/'payload'/PAYLOAD['studio']) as z:z.extractall(stage)
 new=stage/'ComfyUI-DirectorStudio'
 shell(PIP,new/'verify_package.py')
 if dest.exists():
  same=(dest/'package_manifest.json').is_file() and (dest/'package_manifest.json').read_bytes()==(new/'package_manifest.json').read_bytes()
  if same:
   log('Director Studio V0.9.29 already installed (identical)');shutil.rmtree(stage);return
  keep=WS/'DirectorBackups'/('ComfyUI-DirectorStudio_before_v0970_'+time.strftime('%Y%m%d_%H%M%S'))
  keep.parent.mkdir(parents=True,exist_ok=True); shutil.move(str(dest),str(keep))
  log('Previous Director Studio moved to '+str(keep))
 shutil.move(str(new),str(dest)); shutil.rmtree(stage)
 (WS/'DirectorStudio').mkdir(exist_ok=True)
 log('Director Studio V0.9.29 installed: open your ComfyUI link with /director at the end after restarting ComfyUI')

POSE_FILES=[('Comfy-Org/MiniMax-H3','model_patches/minimax_h3_fun_controlnet_union_2.0_pruned_bf16.safetensors'),
            ('Comfy-Org/SDPose','checkpoints/sdpose_wholebody_fp16.safetensors'),
            ('Comfy-Org/SDPose','diffusion_models/rt_detr_v4-x-hgnet_fp16.safetensors')]
POSE_BUILT='model_patches/minimax_h3_fun_controlnet_union_2.0_full_adaln_bf16.safetensors'
def pose_setup(env):
 """🎭 Movement from your video (V0.9.29): SDPose + RT-DETR + a pose ControlNet BUILT for the full BF16 H3.
 Comfy-Org's pruned ControlNet uses the "curve basis" AdaLN of the pruned base models, which ComfyUI refuses with the
 full minimax_h3_ref2va_bf16 ("adaln width 8 ... 2688"); the Director's builder swaps in the full AdaLN layers from
 Alibaba's original ControlNet-Union-2.0 (only those ~5 GB are fetched) after checking both files match."""
 if shutil.disk_usage(WS).free<30e9:raise RuntimeError('Not enough free /workspace disk for the pose models (~24 GB); increase storage')
 for repo,rel in POSE_FILES:
  if (COMFY/'models'/rel).is_file():print('POSE_MODEL_PRESENT',rel,flush=True);continue
  log('Downloading pose model '+rel+' from '+repo)
  shell(PIP,'-c','from huggingface_hub import hf_hub_download; import sys; print(hf_hub_download(repo_id=sys.argv[1], filename=sys.argv[2], local_dir=sys.argv[3]))',
        repo,rel,COMFY/'models',env=env)
  if not (COMFY/'models'/rel).is_file():raise RuntimeError('Pose model did not arrive: '+rel)
 if LITE:
  log('Pose models ready (🎭 Movement; the 24 GB demo set uses Comfy-Org\'s pruned ControlNet as it is): restart ComfyUI so it lists them');return
 if (COMFY/'models'/POSE_BUILT).is_file():print('POSE_MODEL_PRESENT',POSE_BUILT,flush=True)
 else:
  log('Building the pose ControlNet for the full BF16 H3 (reads ~5 GB of Alibaba\'s original)')
  shell(PIP,COMFY/'custom_nodes'/'ComfyUI-DirectorStudio'/'studio_posebuild_v0926.py',COMFY/'models'/POSE_FILES[0][1],COMFY/'models'/POSE_BUILT,env=env)
  if not (COMFY/'models'/POSE_BUILT).is_file():raise RuntimeError('Pose ControlNet was not built')
 log('Pose models ready (🎭 Movement): restart ComfyUI so it lists them')

def remote_sha(rel,env):
 """The SHA-256 Hugging Face stores for a Comfy-Org/MiniMax-H3 file (its LFS index), read live."""
 r=subprocess.run([str(PIP),'-c','import sys; from huggingface_hub import HfApi; i=HfApi().get_paths_info("Comfy-Org/MiniMax-H3",[sys.argv[1]],expand=True); print(i[0].lfs.sha256)',rel],
                  capture_output=True,text=True,env=env,timeout=120)
 h=(r.stdout or '').strip().splitlines()[-1:] or ['']
 if r.returncode!=0 or len(h[0])!=64 or any(c not in '0123456789abcdef' for c in h[0]):
  raise RuntimeError('Could not read the official SHA-256 of '+rel+' from Hugging Face: '+(r.stderr or '').strip()[-200:])
 return h[0]

def model_setup(qwen32b=False,pose=False,think=False,qwen36=False):
 env=os.environ.copy()
 env['HF_HOME']=str(WS/'.cache/huggingface')
 env['HF_HUB_DISABLE_TELEMETRY']='1'
 env['HF_XET_HIGH_PERFORMANCE']='1'
 tokf=WS/'.cache/huggingface'/'token'
 print('HF_TOKEN','found (environment)' if env.get('HF_TOKEN') else 'found (saved file)' if tokf.is_file() and tokf.stat().st_size else
       'NOT SET: downloads are unauthenticated (slower, rate-limited); run install.sh again in a bash terminal to enter it',flush=True)
 (WS/'.cache/huggingface').mkdir(parents=True,exist_ok=True)
 remaining=shutil.disk_usage(WS).free
 print('AVAILABLE_DISK_GiB',round(remaining/2**30,1),flush=True)
 # Before huge downloads, check if models all already match their pinned source hashes.
 missing=[]
 files=[(r,e or remote_sha(r,env),l) for r,e,l,_ in LITE_FILES] if LITE else MODEL_FILES
 for rel,expected,label in files:
  p=COMFY/'models'/rel
  if p.is_file() and sha(p)==expected:
   print('VERIFIED_EXISTING_MODEL',rel,flush=True)
  else:
   if p.exists():raise RuntimeError('Existing model has wrong SHA, refusing replacement: '+str(p))
   missing.append((rel,expected,label))
 if missing:
  approx={
   'diffusion_models/minimax_h3_ref2va_bf16.safetensors':66.3e9,
   'text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors':51.5e9,
   'vae/minimax_h3_video_vae_fp16.safetensors':5.21e9,
   'vae/minimax_h3_audio_vae_fp32.safetensors':.605e9,
   **{r:g for r,_,_,g in LITE_FILES},
  }
  # + Qwen weights (~17.5GB) and a safety margin; supports partially completed resumes.
  estimated=sum(approx[rel] for rel,_,_ in missing) + 17.5e9 + 12e9 + (66e9 if qwen32b else 0) + (66e9 if think else 0) + (56e9 if qwen36 else 0)
  print('ESTIMATED_REQUIRED_FREE_GiB',round(estimated/2**30,1),flush=True)
  if remaining<estimated:raise RuntimeError('Not enough free /workspace disk for remaining downloads; increase storage')
 # Install H3 and Qwen in the same shared HF cache on /workspace.
 for rel,expected,label in missing:
  log('Downloading '+label+' from official Comfy-Org/MiniMax-H3; this can take time')
  dest=COMFY/'models'/rel
  shell(PIP,'-c',
        'from huggingface_hub import hf_hub_download; import sys; print(hf_hub_download(repo_id="Comfy-Org/MiniMax-H3", filename=sys.argv[1], local_dir=sys.argv[2]))',
        rel,COMFY/'models',env=env)
  if not dest.is_file() or sha(dest)!=expected:
   raise RuntimeError('Downloaded model does not match pinned SHA-256: '+rel)
  print('SHA256_OK',rel,flush=True)
 log('Downloading dedicated Qwen3-VL-8B-Instruct observer into /workspace HF cache')
 shell(WS/'h3-qwen-venv/bin/python','-c',
       'from huggingface_hub import snapshot_download; print(snapshot_download(repo_id="Qwen/Qwen3-VL-8B-Instruct", allow_patterns=["*.safetensors","*.json","*.txt","*.model","merges.txt","vocab.json"]))',env=env)
 if qwen32b:            # the sharper rewriter / take checker (Settings > Qwen model > 32B)
  if shutil.disk_usage(WS).free<70e9:raise RuntimeError('Not enough free /workspace disk for Qwen3-VL-32B Abliterated (~66 GB); increase storage')
  log('Downloading Qwen3-VL-32B-Instruct Abliterated (huihui-ai, ~66 GB) for the rewriter, the film planner and the take check')
  shell(WS/'h3-qwen-venv/bin/python','-c',
        'from huggingface_hub import snapshot_download; print(snapshot_download(repo_id="huihui-ai/Huihui-Qwen3-VL-32B-Instruct-abliterated", allow_patterns=["*.safetensors","*.json","*.txt","*.model","*.jinja","merges.txt","vocab.json"]))',env=env)
 if think:             # V0.9.40 🧠 the film writer that thinks first (Settings > 🎬 Film writer)
  if shutil.disk_usage(WS).free<70e9:raise RuntimeError('Not enough free /workspace disk for Qwen3-VL-32B Thinking Abliterated (~66 GB); increase storage')
  log('Downloading Qwen3-VL-32B-Thinking Abliterated (huihui-ai, ~66 GB) for the 🧠 film writer')
  shell(WS/'h3-qwen-venv/bin/python','-c',
        'from huggingface_hub import snapshot_download; print(snapshot_download(repo_id="huihui-ai/Huihui-Qwen3-VL-32B-Thinking-abliterated", allow_patterns=["*.safetensors","*.json","*.txt","*.model","*.jinja","merges.txt","vocab.json"]))',env=env)
 if qwen36:            # V0.9.55 Qwen3.6-27B Abliterated: newest; its own environment (transformers 5), shares the machine's torch
  if shutil.disk_usage(WS).free<62e9:raise RuntimeError('Not enough free /workspace disk for Qwen3.6-27B Abliterated (~56 GB); increase storage')
  q36=WS/'h3-qwen36-venv'
  if not (q36/'bin/python').is_file():shell(PIP,'-m','venv','--system-site-packages',q36)
  shell(q36/'bin/python','-m','pip','install','--disable-pip-version-check','transformers>=5.2,<6','accelerate>=1,<2','huggingface_hub>=0.35,<2','safetensors','pillow','sentencepiece')
  shell(q36/'bin/python','-c','import torch, transformers; from transformers import AutoProcessor; print("QWEN36_IMPORT_READY",torch.__version__,transformers.__version__)')
  log('Downloading Qwen3.6-27B Abliterated (huihui-ai, ~56 GB) for the rewriter, the film planner and the take check')
  shell(q36/'bin/python','-c',
        'from huggingface_hub import snapshot_download; print(snapshot_download(repo_id="huihui-ai/Huihui-Qwen3.6-27B-abliterated", allow_patterns=["*.safetensors","*.json","*.txt","*.model","*.jinja","merges.txt","vocab.json"]))',env=env)
 if pose:pose_setup(env)
 print('QWEN_HF_HOME',env['HF_HOME'],flush=True)
 # Keep default HF_HOME for Director command-line runners on future interactive terminals.
 bashrc=Path.home()/'.bashrc'
 line='export HF_HOME=/workspace/.cache/huggingface'
 if bashrc.is_file():
  content=bashrc.read_text(errors='replace')
  if line not in content:
   with bashrc.open('a') as f:f.write('\n# Director Qwen model cache\n'+line+'\n')
 log('All four official H3 files hash-verified and Qwen model downloaded')


def main():
 p=argparse.ArgumentParser()
 p.add_argument('--verify-bundle-only',action='store_true')
 p.add_argument('--skip-models',action='store_true',help='for local/development validation')
 p.add_argument('--skip-python-deps',action='store_true',help='for local/development validation')
 p.add_argument('--update-comfyui',action='store_true',help='git pull ComfyUI if it is too old for MiniMax H3')
 p.add_argument('--qwen32b',action='store_true',help='also download Qwen3-VL-32B Abliterated (~66 GB) for the rewriter, the film planner and the take check')
 p.add_argument('--think',action='store_true',help='also download Qwen3-VL-32B Thinking Abliterated (~66 GB) for the 🧠 film writer (Settings > Film writer)')
 p.add_argument('--qwen36',action='store_true',help='also set up Qwen3.6-27B Abliterated (~56 GB, its own environment) for the rewriter, the film planner and the take check (Settings > Qwen model > 27B)')
 p.add_argument('--pose',action='store_true',help='also download the pose models (~9 GB) for 🎭 Movement from your video')
 p.add_argument('--lite24',action='store_true',help='the 24 GB DEMO set instead of the full BF16 H3: pruned w6a8 H3 (16 GB), nvfp4 text encoder, int8 VAE, Qwen 8B only (~53 GB in all)')
 a=p.parse_args()
 global UPDATE_COMFY, LITE, COMFY_FLAGS; UPDATE_COMFY=a.update_comfyui
 LITE=True                        # public demo: always the 24 GB set (--lite24 accepted, never needed)
 if a.qwen32b or a.think or a.qwen36:raise RuntimeError('The public 24 GB demo runs Qwen 8B only: leave out --qwen32b / --think / --qwen36')
 if LITE: COMFY_FLAGS=[]          # a 24 GB card NEEDS ComfyUI's dynamic VRAM / offloading: none of the full-BF16 flags
 verify_bundle()
 if a.verify_bundle_only:return
 if not WS.is_dir():raise RuntimeError('/workspace missing')
 queue_preflight()
 if not a.skip_python_deps:python_setup()
 clone_node('ComfyUI-VideoHelperSuite','https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git')
 clone_node('ComfyUI_Eclipse','https://github.com/r-vage/ComfyUI_Eclipse.git')
 install_code()
 install_studio()
 comfy_memory_flags()
 if not a.skip_models:model_setup(qwen32b=a.qwen32b,pose=a.pose,think=a.think,qwen36=a.qwen36)
 prof=WS/'DirectorStudio'/'_profile.json'                 # V0.9.70 which model set the Director runs
 prof.parent.mkdir(parents=True,exist_ok=True); prof.write_text(json.dumps({'profile':'lite24' if LITE else 'full'}))
 log('Model profile: '+('24 GB DEMO set (pruned w6a8 H3, nvfp4 text encoder, Qwen 8B)' if LITE else 'full BF16 H3'))
 print('\n=== DEMO INSTALL COMPLETED WITHOUT GENERATION ===',flush=True)
 print('Next: restart ComfyUI (when idle), then:  /venv/main/bin/python check_ready.py',flush=True)
 print('Then open your ComfyUI link with /director at the end.',flush=True)
 print('Workflow:',WS/'DirectorWorkflows'/PAYLOAD['workflow'],flush=True)
 print('Director preview after ComfyUI restart: bash /workspace/DirectorIdleDispatcherV075/run_v075.sh',flush=True)

if __name__=='__main__':
 try:main()
 except Exception as e:
  print('BOOTSTRAP_STOPPED_SAFELY:',type(e).__name__+':',e,file=sys.stderr,flush=True)
  raise SystemExit(1)
