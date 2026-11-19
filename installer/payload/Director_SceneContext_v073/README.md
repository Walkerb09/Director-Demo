# Director V0.7.3 — Candidate Scene Context (shadow only)

## Goal

Consolidate **already completed** V0.7.2.2 single-pass scene observer reports into concise, generic, provenance-gated *candidate* context, while keeping all unverified camera framing and motion claims out of scene continuity fields. This does **not** install a ComfyUI plugin, call Qwen, infer audio/dialogue, update persistent Director memory, edit footage, or feed generated footage to H3.

### Input and evidence limits

- Accepts the exact V0.7.2.2 ZIP review bundle schema (`scene_review_v0722.json`, `.jpg`, `bundle_manifest.json`) with SHA-256 verification.
- The supplied sample is *not* guaranteed to show the entire interval. Candidate consensus means **sample agreement only**, never verified truth for the entire segment.
- A `--video` input additionally confirms the original media bytes match the observer source hash. This is required by the supplied Vast runner for both tests.
- Exact segment endpoints must have been sampled. Candidate hard cuts preserve non-continuity barriers. No hard-cut detection doesn't prove seamless motion.
- Any inconsistent field, LOW-Q sample, or invalid prediction quarantines that segment-wide candidate. An invalid last frame also quarantines that field's endpoint candidate; it does not erase independent fields with consistent evidence.
- Camera framing is **quarantined even if Qwen labels agree**: visual review of Shot 7 and Shot `00002` found those labels unreliable.
- Microphone visibility and cast presence are distinct from microphone *holder*, *target*, person identity, speech, motion and edit intention. These remain unobserved or unverified.
- Reference descriptions are operator hints, not independent proof of subject identity. Clean-Chain prohibits generated video as H3 visual conditioning.

## Vast.ai

Upload `Director_SceneContext_v073.zip` to `/workspace` then:

```bash
cd /workspace
unzip -oq Director_SceneContext_v073.zip -d /workspace/DirectorSceneContextV073
bash /workspace/DirectorSceneContextV073/run_v073.sh
```

This reuses two existing results without GPU use. The outputs are:

```
/workspace/DirectorShot7HeldoutV0722/scene_context_v073.json
/workspace/DirectorSavedShotQueueV066/results/7a3275aa22778e5b32b50c94/scene_context_v073.json
```

You may instead use the portable, CPU-only CLI with any **verified V0.7.2.2 report bundle**:

```bash
python scene_context_v073.py --bundle /path/to/scene_review_v0722_bundle.zip \
  --video /path/to/exact/source.mp4 --out /path/to/scene_context_v073.json
```

If `--video` is omitted, the report labels its source-video hash **not reverified**. Existing, different outputs are never overwritten.

## Scope

V0.7.3 is a separate downstream adapter and confidence gate, **not yet installed in the automatic saved-shot worker**. It is intended to replace legacy case-specific JSON summaries after more varied real footage confirms model accuracy. It does not make autonomous editorial choices.
