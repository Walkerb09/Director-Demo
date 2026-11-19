# Director V0.7.2.2 — Token-constrained single-pass observer (shadow experiment)

This is an **isolated A/B experiment** on existing H3 saved shot `00002`, NOT an installation into ComfyUI.

Why: V0.7.2.1's raw outputs used unlabeled positional sequences (`YES|NO|...`) despite repeatedly requesting key/value syntax. The new worker constrains generation at the token level to an enumerable family of profile-dependent key/value responses (six fields for the two-role hallway test). It does **not** "repair" old reports or normalize arbitrary malformed strings.

Changes:
- One Qwen model load for all eight frames.
- Token-level output whitelist using `prefix_allowed_tokens_fn`. This constrains *format*, not which scene-label combination is true.
- Role-by-role `YES/NO/UNCERTAIN`, independent microphone, other-person and framing fields.
- `Q=HIGH` is an overall subjective model claim, never evidence that a particular `UNCERTAIN` role is actually absent. Uncertain fields stay uncertain and block complete cast inference.
- Cut-aware segmentation and exact two-frame endpoints preserved.
- New `scene_review_v0722.json`, `scene_review_v0722.jpg`, and `scene_review_v0722_bundle.zip` alongside prior results. Does not modify V0.7.2.1, V0.7.1.1, Director memory, ComfyUI nodes, source video or H3 inputs.
- Run only when the ComfyUI queue is idle. The existing resource handoff may unload cached H3 weights to free GPU memory.

**Install/test:** Extract the ZIP into `/workspace/DirectorProfiledV0722`, run `bash /workspace/DirectorProfiledV0722/run_v0722.sh --preflight`, and if preflight succeeds `bash /workspace/DirectorProfiledV0722/run_v0722.sh --run`.

**Upload:** `/workspace/DirectorSavedShotQueueV066/results/7a3275aa22778e5b32b50c94/scene_review_v0722_bundle.zip`.

Limitations: The grammar may bias model token probabilities, so *live Qwen results require separate visual review.* Output is model claim only, not guaranteed identity, camera motion, cut intent, continuity approval or permission for editing. No generated footage is ever fed into H3 as visual conditioning.
