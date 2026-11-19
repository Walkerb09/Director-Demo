"""Existing V063 node ID retained; upgraded to V065 holder-vs-target observation."""
import json,uuid
from .semantic_observer import observe_video,RESULTS,MODEL,QWEN

class DirectorV063SemanticTargetShadow:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{
            'saved_video_path':('STRING',{'default':''}),
            'qwen_python':('STRING',{'default':QWEN}),
            'model_id':('STRING',{'default':MODEL}),
            'samples':('INT',{'default':8,'min':2,'max':24}),
            'source_class':(['UNKNOWN_ORIGIN','H3_GENERATED_OBSERVATION_ONLY','REENCODED_REVIEW_ASSEMBLY'],{'default':'UNKNOWN_ORIGIN'}),
            'run_nonce':('INT',{'default':0,'min':0,'max':999999999}),
        }}
    RETURN_TYPES=('STRING','STRING')
    RETURN_NAMES=('shadow_report_JSON','summary')
    CATEGORY='Director/CleanChain'
    FUNCTION='run'
    def run(self,saved_video_path,qwen_python,model_id,samples,source_class,run_nonce):
        if not saved_video_path.strip():raise ValueError('Enter a saved video path for read-only observation')
        dst=RESULTS/('gui_'+uuid.uuid4().hex)
        report=observe_video(saved_video_path,dst,qwen_python,model_id,samples,
                             source_class=source_class,require_idle=False,allow_free=False)
        raw=report.read_text();p=json.loads(raw)
        changes=sum(t['status']=='MODEL_REPORTS_DIFFERENCE' for t in p['audit']['sampled_transitions'])
        issues=len(p['audit']['alerts'])
        status=(f'Director V0.6.5 SHADOW: {len(p["sampled_frames"])} frames, '
                f'{changes} candidate mic-target changes, {issues} uncertainty alerts. '
                'NO edit/continuity authority, NO H3 conditioning.')
        return {'ui':{'text':[status]},'result':(raw,status)}
