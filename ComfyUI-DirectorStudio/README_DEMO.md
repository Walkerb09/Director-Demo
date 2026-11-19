# Director Studio: public 24 GB demo (V0.9.70)

A directing studio for MiniMax H3 Ref2VA in ComfyUI. You write what happens and pick your people and their photos. The Director
plans the shot, writes H3's prompt in its official format, places everyone in the frame, runs the take, checks it with
face matching and Qwen, and keeps each project's history so you can undo, retake and cut.

This demo runs on a **24 GB GPU** with Comfy-Org's smaller H3 files (pruned w6a8 H3, nvfp4 text encoder, int8 video VAE)
and Qwen3-VL 8B. Takes look a little less detailed than with the full BF16 set.

**Bring your own photos.** The bundle holds no sample people, photos or videos. The Clean Chain rule applies: only your
original photos (and crops of them) are used as references, never generated clips or frames.

Install on a fresh Vast ComfyUI instance: `bash install.sh` (it always installs the 24 GB set), or use the public
template. Afterwards restart ComfyUI and open its address: it goes straight to the Director. ComfyUI runs hidden
behind it; its own page is at `/?comfy=1`.
