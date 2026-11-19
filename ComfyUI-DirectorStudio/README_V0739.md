# H3 Reference Director V0.7.39 — talk to it like a director

One entry point. Plain words. It reads your saved project first, then acts.

```bash
bash run_director.sh "make her react"
bash run_director.sh "closer angle, give it some time"
bash run_director.sh "where are we?"
```

Each instruction that changes the next shot writes a **ComfyUI workflow JSON** (drag it onto the canvas, or add `--stage` to put it in the Workflows sidebar). Nothing is queued unless you add `--generate`.

## Install on Vast (no model downloads, no SSH, no extra web server)

Upload the zip to `/workspace` with Jupyter, then:

```bash
cd /workspace && unzip -n Director_V0739_NaturalLanguage.zip -d DirectorV0739
cd DirectorV0739 && bash run_tests.sh        # optional, ~3 min, offline
bash run_director.sh "where are we?"
```

It reopens your existing project at `/workspace/DirectorProjects/hallway_interview_v1` (V0.7.35/36 memory) and **adds** to it. If no project exists yet it initializes one from your real capture records, exactly like V0.7.35.

## What you can say (examples, not a list of allowed phrases)

| You say | It does |
|---|---|
| `keep going` / `continue` / `what happens next` | Uses the planned next shot (Shot 3 v01 = the same workflow as the JSON delivered earlier) |
| `make her react` / `let grandpa laugh a little` / `he gets choked up` | New variant of the upcoming shot: who reacts, which emotion, how strongly |
| `try another angle` / `close-up on grandpa` / `over the shoulder on her` / `wider` / `slow push in` | Framing and camera move |
| `give it some time` / `pick up the pace` | Pacing |
| `have her ask "what was she like?"` | Directed line (spoken by H3, never treated as a verified transcript) |
| `from now on keep it wider` | Standing preference for every later shot |
| `whatever you think is best` / `your call` | Director's choice, with its reason (e.g. varies coverage after 3 same-framing shots) |
| `another take` | Same beat, new seed |
| `undo` / `start over` | Reverts the last change / returns to the base plan |
| `where are we?` | Status: film, shots, pending decisions, what is still UNKNOWN |
| *(after a take is captured)* `looks good` → `yes` | Accepts it as a WORKING performance (asks to confirm; hedged praise never counts) |
| `redo it, he looks stiff` | Rejects the take (kept as evidence) and plans a retake with your note |
| `add shot 3 to the film` | Cuts the accepted shot in; the Director picks the entry frame technically and renders |
| `go back to my cut` / `use the director's version` / `start shot 2 at frame 40` | Edits the working film; human-directed |
| `tighten the cut a bit` | Routine 6-frame change the Director may make on its own (limit 12 without a number from you) |
| `render the film` | Frame-accurate A/V render, verified against the source clips |

Instructions are combined: notes accumulate on the upcoming shot until it is generated. Your own words also go into the H3 prompt as the filmmaker's note.

Every run first checks for a new capture of the planned shot. If one exists it is ingested (**capture is not approval**) and traced to the variant that produced it through the workflow VHS embeds in the MP4.

## Rules it keeps (from the handoff)

- Never uses generated frames or clips as H3 visual input. Requests like "use the last frame of shot 2" are refused for that part only. If a capture's embedded workflow shows video or audio references were connected, approval is blocked.
- Full BF16 H3, 768×1344, 124 frames at 24fps, six pristine originals only, audio and saved-shot capture wired. Every workflow is validated before it is written.
- Approvals are explicit and confirmed. Accepting a take never edits the film. Nothing is final-film approved.
- F36 stays the base-ledger edit. Overlay edits (F42, trims, new shots) are separate reversible events; `undo` reverts them.
- Dialogue, final pose, microphone holder and motion stay **UNKNOWN** unless you write them in a review.
- `--generate` is the only path that queues H3. It checks the nodes are loaded and the queue is idle first, then sends exactly what the editor would send.

## What changed vs V0.7.36 / V0.7.38

- **Root cause of the V0.7.38 UI failure fixed.** The MP4s from your real Vast runs contain the `/prompt` JSON your live ComfyUI executed. The new converter reproduces it **exactly** for Duo01 and Shot02, keeping `ref_images.ref_image_0` and VHS `crf/pix_fmt/save_metadata/trim_to_audio`, which V0.7.38 wrongly rejected.
- Natural-language Director (`director_intent_v0739.py`, `director_v0739.py`) replaces the three hard-coded phrases.
- Take rejection and retakes (`director_project_progress_v0739.py`). V0.7.36 could never get past a bad take.
- N-shot timeline editor and renderer. Its F36 render matches your real Vast F36 film on all 212 frames (max mean difference 1.04 of 255).
- `save_memory_backup.sh` backs up all three ledgers and issued workflows, each re-verified on export and import.

## Tested vs not tested

- **Tested offline (392 tests):** the converter against live Vast ground truth; interpretation of ~50 phrasings; full plan → capture → review → film → Shot 4 → reject → retake cycles; edits, undo and render; backup and tamper detection. Also the 18 V0.7.36 lifecycle tests on the new module, and the 330 inherited tests.
- **Future takes in tests are synthetic.** They are remuxes of your real Shot 1/2 bytes with embedded workflows, clearly marked synthetic.
- **Not yet run on Vast:** `--generate` (tested against a fake ComfyUI server), and staging into the live sidebar. Nothing here judges acting quality. The entry-frame scorer is technical, not taste.

## Next steps (roadmap)

1. First live check: `bash run_director.sh "where are we?"`, then drag Shot 3 v01 into ComfyUI and generate.
2. Optional observer link: after ingest, queue V0.7.4/V0.7.5 post-shot observation automatically (still UNKNOWN-first, never truth).
3. Last-22-frame structured end-state from *reviewed* shots into the next beat text.
4. Learn editorial taste from your promote/undo history (F42 vs F36 type decisions) to calibrate the routine-edit bounds.
5. Optional thin ComfyUI custom node ("Director prompt box") that calls the same `direct()`, so you never leave the ComfyUI tab.
