"""Optional post-save capture from VHS_FILENAMES. Never runs the vision model."""
from __future__ import annotations
import json
from .shot_capture import capture

class DirectorV066SavedShotShadowQueue:
    OUTPUT_NODE=True
    CATEGORY='Director/CleanChain'
    FUNCTION='run'
    RETURN_TYPES=('STRING','STRING')
    RETURN_NAMES=('shadow_job_JSON','summary')
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{
            'saved_video':('VHS_FILENAMES',),
            'capture_enabled':('BOOLEAN',{'default':True}),
            'samples':('INT',{'default':8,'min':2,'max':24}),
            'source_class':(['H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY','UNKNOWN_ORIGIN'],),
            'run_nonce':('INT',{'default':0,'min':0,'max':999999999}),
        }}
    def run(self,saved_video,capture_enabled=True,samples=8,source_class='H3_GENERATED_OBSERVATION_ONLY',run_nonce=0):
        if not capture_enabled:
            msg='V0.6.6 SHADOW: capture disabled; no queued observation'
            return {'ui':{'text':[msg]},'result':('{}',msg)}
        dest,record,created=capture(saved_video,samples,source_class)
        msg=('V0.6.6 SHADOW CAPTURE '+('QUEUED' if created else 'ALREADY QUEUED')+
             ' | '+str(record['video_meta']['frames'])+' frames | job '+record['job_id']+
             ' | no Qwen until queue-idle drain; no edit authority')
        return {'ui':{'text':[msg]},'result':(json.dumps(record,sort_keys=True),msg)}
