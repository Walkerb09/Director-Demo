"""Portable offline test fixture for FFmpeg decoding; NOT a production observer."""
import json,subprocess
from pathlib import Path

def media_meta(video):
 cp=subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
   '-show_entries','stream=width,height,nb_read_frames,avg_frame_rate','-of','json',str(video)],
   text=True,capture_output=True,timeout=60)
 if cp.returncode:raise RuntimeError('ffprobe failed')
 s=json.loads(cp.stdout)['streams'][0]
 n,d=map(int,s['avg_frame_rate'].split('/'))
 return {'frames':int(s['nb_read_frames']),'width':int(s['width']),
         'height':int(s['height']),'fps':n/d}

def decode_selected(path,meta,idx):
 import numpy as np
 filt='+'.join('eq(n\\,%d)'%i for i in idx)
 cp=subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-threads','2',
  '-i',str(path),'-map','0:v:0','-vsync','0','-vf','select='+filt,
  '-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,timeout=60)
 if cp.returncode:raise RuntimeError('ffmpeg frame decode failed')
 expected=meta['width']*meta['height']*3*len(idx)
 if len(cp.stdout)!=expected:raise RuntimeError('Frame output byte mismatch')
 arrays=np.frombuffer(cp.stdout,dtype=np.uint8).reshape(len(idx),meta['height'],meta['width'],3)
 return {f:arrays[n].copy() for n,f in enumerate(idx)}
