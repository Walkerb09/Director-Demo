#!/usr/bin/env python3
"""Put the PUBLIC 24 GB demo bundle in a PUBLIC Hugging Face repo, for the public Vast template to fetch.
   /venv/main/bin/python upload_demo_to_hf.py yourname/director-demo /workspace/Director_Demo24_Public_V0970.zip
Uses your saved token (or HF_TOKEN) with WRITE access; run it on YOUR machine, the token never goes in the template.
It refuses any bundle that is not the public demo, or that holds a photo or a video. It uploads the bundle, latest.json
and director_demo_provision.sh (the PROVISIONING_SCRIPT link is printed at the end)."""
import hashlib, io, json, os, re, sys, tempfile, zipfile
from pathlib import Path
os.environ.setdefault('HF_HOME', '/workspace/.cache/huggingface')
from huggingface_hub import HfApi

MEDIA = ('.mp4', '.mov', '.webm', '.mkv', '.png', '.jpg', '.jpeg', '.webp', '.wav', '.mp3', '.flac', '.gif')

def check(data, where=''):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for i in z.infolist():
            n = i.filename
            if n.lower().endswith(MEDIA): sys.exit(f'REFUSED: {where}{n} is a photo/video. The public bundle must hold none.')
            if n.endswith('.zip'): check(z.read(i), where + n + ' > ')
            elif not n.endswith('.onnx') and re.search(rb'hf_[A-Za-z0-9]{30,}', z.read(i)):
                sys.exit(f'REFUSED: {where}{n} looks like it holds a Hugging Face token.')

def main():
    if len(sys.argv) != 3: sys.exit(__doc__)
    repo, zp = sys.argv[1], Path(sys.argv[2])
    if not (zp.is_file() and zp.name.startswith('Director_Demo24_Public_') and zp.suffix == '.zip'):
        sys.exit('Expected a Director_Demo24_Public_*.zip file: ' + str(zp))
    data = zp.read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if 'PUBLIC_DEMO' not in z.namelist(): sys.exit('REFUSED: not the public demo bundle (no PUBLIC_DEMO marker).')
        script = z.read('vast_template/director_demo_provision.sh')
    check(data)
    api = HfApi()
    api.create_repo(repo, private=False, exist_ok=True)
    if api.repo_info(repo).private: print(f'Note: {repo} is private; make it public on huggingface.co so renters can fetch it.')
    digest = hashlib.sha256(data).hexdigest()
    api.upload_file(path_or_fileobj=str(zp), path_in_repo=zp.name, repo_id=repo, commit_message='Add ' + zp.name)
    api.upload_file(path_or_fileobj=script, path_in_repo='director_demo_provision.sh', repo_id=repo,
                    commit_message='Demo provisioning script')
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as t:
        json.dump({'file': zp.name, 'sha256': digest, 'version': 'V0.9.70-demo24'}, t)
    api.upload_file(path_or_fileobj=t.name, path_in_repo='latest.json', repo_id=repo, commit_message='Latest: ' + zp.name)
    os.unlink(t.name)
    print(f'UPLOADED {zp.name} to {repo} (sha256 {digest[:12]}...)')
    print(f'PROVISIONING_SCRIPT = https://huggingface.co/{repo}/resolve/main/director_demo_provision.sh')

if __name__ == '__main__': main()
