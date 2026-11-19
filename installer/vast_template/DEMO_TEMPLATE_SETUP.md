# Director Studio: the PUBLIC 24 GB demo template

Anyone can rent this template and try the Director on an ordinary 24 GB GPU (RTX 4090, 3090, A5000, L4...).
The bundle holds no photos, videos, projects or tokens: renters bring their own reference photos.

## One-time setup

**1. Make a public Hugging Face repo and upload the demo.** On any machine with your write token saved (your own
Vast instance is fine):

    /venv/main/bin/python upload_demo_to_hf.py YOURNAME/director-demo Director_Demo24_Public_V0970.zip

It creates `YOURNAME/director-demo` as public, refuses anything that isn't the public demo bundle or that holds a
photo, video or token, and uploads the bundle, `latest.json` and `director_demo_provision.sh`. At the end it prints the
`PROVISIONING_SCRIPT` link.

**2. Make the template.** Vast > **Templates** > the **ComfyUI** template > **Edit** (this saves your own copy). Environment
variables, and only these:

| Name | Value |
|---|---|
| `PROVISIONING_SCRIPT` | `https://huggingface.co/YOURNAME/director-demo/resolve/main/director_demo_provision.sh` |
| `DIRECTOR_HF_REPO` | `YOURNAME/director-demo` |
| `DIRECTOR_PROFILE` | `lite24` |

Take out `PROVISIONING_COMFYUI_WORKFLOWS` if it is there (a 7 GB model the Director never uses).
Disk **120 GB**. Name it e.g. "Director Studio: 24 GB demo", set it **Public**, and save.

**Never in this template:** `HF_TOKEN`, `DIRECTOR_BACKUP_REPO`, or anything else of yours. Everyone can read a public
template's variables. Your own token stays in **Account > Environment Variables**. Vast adds those only to YOUR instances,
including the ones you start from this template, never to other people's.

## Renting it (what to tell people)

1. Pick a 24 GB GPU with **64 GB or more system RAM** and a fast connection. The first boot downloads ~53 GB.
2. Watch: `tail -f /workspace/director_provision.log`, then `tail -f /workspace/director_install.log`.
3. When it says `DEMO INSTALL COMPLETED`: restart ComfyUI, then
   `/venv/main/bin/python /workspace/DirectorDemoInstall/check_ready.py`
4. Open the instance's ComfyUI link: it goes straight to the Director (ComfyUI runs hidden behind it). The header shows
   "🧪 24 GB demo". ComfyUI's own node page is still there for fixing things: add `/?comfy=1` to the address
   (`/?comfy=0` hides it again).
5. Make a project, add your own people with your own photos, and direct a shot.

☁ Cloud backup stays off unless the renter turns it on with their own Hugging Face repo and token.

## New demo versions

Build a new `Director_Demo24_Public_*.zip` and run step 1 again. Instances started after that get it.
