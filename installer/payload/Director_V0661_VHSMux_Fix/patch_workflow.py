#!/usr/bin/env python3
"""Patch a COPY of any supported ComfyUI workflow, appending post-VHS shadow capture."""
from __future__ import annotations
import argparse,copy,json
from pathlib import Path
NODE='DirectorV066SavedShotShadowQueue'
VIDEO='VHS_VideoCombine'

def patch(graph,video_node_id=None):
    g=copy.deepcopy(graph)
    if not isinstance(g.get('nodes'),list) or not isinstance(g.get('links'),list):
        raise ValueError('Expected a ComfyUI UI workflow with nodes and links')
    nodes=g['nodes'];links=g['links']
    if any(n.get('type')==NODE for n in nodes):raise ValueError('Capture node is already present')
    videos=[n for n in nodes if n.get('type')==VIDEO and (video_node_id is None or n.get('id')==video_node_id)]
    if len(videos)!=1:
        raise ValueError('Specify --video-node-id when workflow has multiple VHS_VideoCombine nodes; found '+str(len(videos)))
    video=videos[0]
    outs=[(i,o) for i,o in enumerate(video.get('outputs',[])) if o.get('type')=='VHS_FILENAMES']
    if len(outs)!=1:raise ValueError('VHS output does not expose exactly one VHS_FILENAMES port')
    slot,out=outs[0]
    nid=max([int(g.get('last_node_id',0))]+[n['id'] for n in nodes])+1
    lid=max([int(g.get('last_link_id',0))]+[l[0] for l in links])+1
    old_links=copy.deepcopy(links)
    pos=video.get('pos',[0,0]);
    if not isinstance(pos,list) or len(pos)<2 or not all(isinstance(x,(float,int)) for x in pos[:2]):pos=[0,0]
    node={'id':nid,'type':NODE,'pos':[pos[0]+525,pos[1]-100], 'size':[430,230],
          'flags':{},'order':max([n.get('order',0) for n in nodes])+1,'mode':0,
          'inputs':[{'name':'saved_video','type':'VHS_FILENAMES','link':lid}],
          'outputs':[{'name':'shadow_job_JSON','type':'STRING','links':[]},
                     {'name':'summary','type':'STRING','links':[]}],
          'properties':{'Node name for S&R':NODE},
          'widgets_values':[True,8,'H3_GENERATED_OBSERVATION_ONLY',0],
          'title':'V0.6.6 SHADOW | queue saved H3 shot (no Qwen in generation queue)'}
    if out.get('links') is None:out['links']=[]
    if not isinstance(out['links'],list):raise ValueError('Unexpected video output link shape')
    out['links'].append(lid)
    nodes.append(node)
    links.append([lid,video['id'],slot,nid,0,'VHS_FILENAMES'])
    g['last_node_id']=nid;g['last_link_id']=lid
    if links[:-1]!=old_links or len(nodes)!=len(graph['nodes'])+1:
        raise AssertionError('Unexpected changes to existing links')
    return g

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--video-node-id',type=int)
    args=parser.parse_args()
    if args.output.resolve()==args.workflow.resolve() or args.output.exists():
        raise ValueError('Refusing to overwrite original workflow or existing destination')
    g=patch(json.loads(args.workflow.read_text()),args.video_node_id)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(g,indent=2)+'\n')
    print('V066_WORKFLOW_COPY_CREATED',args.output)
    print('Existing H3 and review links preserved; new branch uses ONLY VHS_FILENAMES')

if __name__=='__main__':main()
