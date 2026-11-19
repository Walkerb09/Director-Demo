#!/usr/bin/env python3
"""One-command V0.7.36 Director entry point. 'Continue the interview' needs no new brief.

Requires real Vast capture JSON and pristine original stills on first project init.
Nothing here queues H3, launches Qwen, or edits the operational Director.
"""
from pathlib import Path
import argparse
import json
import os
import sys
import director_project_v0735 as project
import director_project_progress_v0736 as lifecycle

HERE=Path(__file__).resolve().parent

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('instruction',help='For this version: Continue the interview')
    ap.add_argument('--workspace',type=Path,default=Path('/workspace'))
    ap.add_argument('--project',type=Path)
    ap.add_argument('--no-rebuild',action='store_true',help='Skip rebuilding the approved F36 working film')
    a=ap.parse_args()
    ws=a.workspace
    if ws.is_symlink():raise project.MemoryErrorSafety('LINKED_WORKSPACE')
    root=a.project or ws/'DirectorProjects'/'hallway_interview_v1'
    seed=HERE/'scene_seed_v0735.json'
    if not root.exists():
        state=project.init_project(root,seed,HERE/'autoedit_case.json',HERE/'live_f36_decision.json',
             ws/'ComfyUI'/'input',ws/'DirectorSavedShotQueueV066',ws/'ComfyUI'/'output')
        print('DIRECTOR_PROJECT_INITIALIZED_FROM_REAL_CAPTURE_RECORDS',state['project_id'])
    else:
        state=project.status(root)
        if state['project_id']!='hallway-interview-v1':raise project.MemoryErrorSafety('OTHER_PROJECT_WOULD_BE_USED')
        print('DIRECTOR_PROJECT_REOPENED_FROM_PERSISTENT_MEMORY',state['project_id'])
    result=lifecycle.continue_project(root,ws/'ComfyUI'/'input',HERE/'workflow_template_verified.json',a.instruction)
    print('DIRECTOR_LIFECYCLE_ACTION',result['action'])
    print('ACTIVE_F36_WORKING_EDIT',state['working_edit']['timeline_frames'],'frames (unchanged)')
    if result.get('plan_path'):
        print('NEXT_PLAN',result['plan_path'])
    if result.get('workflow_path'):
        print('H3_WORKFLOW_PREPARED_NOT_QUEUED',result['workflow_path'])
    if result['action']=='HUMAN_REVIEW_EXISTING_CAPTURE':
        print('CAPTURE_FOUND_BUT_HUMAN_REVIEW_REQUIRED_NO_AUTO_ADVANCE')
    if not a.no_rebuild:
        edited=project.rebuild_working(root,ws/'DirectorSavedShotQueueV066',ws/'ComfyUI'/'output')
        print('WORKING_FILM_REBUILT_AND_SOURCE_CHECKED',edited['render_path'])
    print('NO_NEW_CONTEXT_REQUIRED_NO_H3_GENERATION_NO_QWEN')
    print('SAVE_PROJECT_BACKUP_BEFORE_TERMINATING_VAST_INSTANCE')

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('DIRECTOR_PROJECT_REQUIRES_ATTENTION:',type(e).__name__,str(e),file=sys.stderr)
        sys.exit(1)
