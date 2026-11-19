# H3 Director Studio V0.9.38 (includes Director V0.7.39 and the Qwen observer bridge)

A simple Director UI inside ComfyUI: open your normal ComfyUI address and add `/director`.
It uses the same port and the same Vast link, so there's no extra server, no SSH and no port 18189.

## Install on Vast

**Instance that already has the Director** (any earlier version): no downloads needed.
```bash
mkdir -p /workspace/DirectorBackups && cd /workspace/ComfyUI/custom_nodes && mv ComfyUI-DirectorStudio /workspace/DirectorBackups/ComfyUI-DirectorStudio_old_$(date +%s)
unzip -o /workspace/ComfyUI-DirectorStudio_v095.zip -d .
```
Restart ComfyUI. The log says `[DirectorStudio] ready at /director`. Open `https://<your ComfyUI address>/director`.
From V0.9.5 on, later versions install from the Director itself: **⬆ Update** (see below).

**Brand-new instance (start from scratch):** first run the bundled bootstrap once. It installs H3 + VideoHelperSuite, the Qwen observer chain and its Python env, and downloads models only if they're missing:
```bash
cd /workspace && unzip -o ComfyUI/custom_nodes/ComfyUI-DirectorStudio/fresh_instance/Director_FreshVast_ComfyUI_FullBootstrap_2026-10-18.zip -d DirectorFreshVastInstaller
cd DirectorFreshVastInstaller && /venv/main/bin/python install.py
```
Then restart ComfyUI. The banner at the top of `/director` checks everything: the H3 node, VideoHelperSuite, all four model files, the video and LoRA nodes, the Qwen chain, `nvidia-smi` and writable folders. For anything missing it tells you what to fix.

## Using it

1. **New project.** Add people (name, she/he/they, nicknames like "the woman", what they wear) and places.
2. **Add a photo or a video** to each one and tick *My own original*. Any phone format works: rotated JPEG/HEIC, PNG with transparency, greyscale, .mov/.mp4/.webm, 4K/60fps, with or without sound.
   - **From a person video** the Director picks 2 faces from different moments, an upper-body and a full-body still, makes **clean cut-outs** (person on neutral grey), and keeps a 73-frame (about 3 s) 24fps reference clip.
   - **From a place video** it picks up to 3 **empty-room plates** from different angles, plus a 73-frame (about 3 s) clip.
   - **Refused, with plain reasons:** blurry, dark, overexposed or tiny faces, photos with several people, generated files (ComfyUI metadata, files in ComfyUI/output, your own takes or renders), and images under 96 px. Small or low-quality photos are accepted with a warning.
3. **Describe the shot:** "The woman walks towards the camera". Or use the buttons: **Who**, **Emotion** (12 shown, 38 under *More emotions*: angry, scared, nervous, confused, relieved, determined, flirty, jealous, guilty, bored, heartbroken, and more) with **intensity**, **Framing**, **Camera**, **Pacing**, and a line of dialogue.
4. **Plan next shot.** You see how it understood you and which references it chose, and why:
   - At most **9 images**: the place plate first (the **same plate as the previous shot** for consistent backgrounds; a different angle if you ask for one, plus a second angle when there's room), then each person's face, full body, upper body and alternate face.
   - **Raw or clean per person:** if someone was filmed in this same place their original stills are used; otherwise their clean cut-outs, and the prompt tells H3 the grey isn't part of the scene.
   - At most **3 reference videos**: each person's original 3-second clip, then the place's. Their voices are optional.
   - **At most two people per shot.** With more, it asks which two.
   - **LoRAs** (optional, max 4): pick from the LoRA files your ComfyUI has, choose a strength, all shots or just one person's, and trigger words.
5. **Generate.** It reads your live ComfyUI node list first. If the video loader nodes are missing it makes a stills-only take automatically and tells you. If a LoRA file is missing it says so instead of failing inside ComfyUI.
6. **Takes arrive by themselves.** A take is only accepted if its embedded prompt, seed, images, clips and LoRAs exactly match the plan. Then it's measured and checked:
   - Hidden cuts.
   - **Wrong number of people.**
   - **Background drift** away from the place reference.
   - Optical-flow motion.

   It's then **cut into the movie automatically**.
7. **Keep / Reject (with a note), nudge cuts ◀6 / 6▶, ✕ remove, Undo, Render movie.** The render evens out colour between shots, gently and within limits.

## Any people, any places, any background

- **Any photos or videos.** Nothing is tied to the hallway, Maya or Grandpa. Any names, nicknames ("the barista", "the customer"), looks and places work.
- **Several people in one photo or video.** It asks *which one is Ava? left / right* (or *middle* with three). It then follows that person through the video, so frames showing only the other person are never used. A reference clip isn't kept from such a video, because it would show both people.
- **Numbered reference mapping** (Settings, on by default). The prompt says which numbered image H3 receives belongs to whom, for example *"images 2-5 = Ava (face, full body, …); images 6-8 = Leo"*. Each person's images are kept together in a fixed order, and the plan card shows the order with #1, #2… Turn it off to compare.
- **Background, per shot** (*Background* menu, or in words):
  - **a place** you added: pick it, or name it ("…on the Rooftop"); people filmed elsewhere are cut out onto it;
  - **the people's own photos/videos** ("keep the background from her video"): the person's original full-body photo becomes the scene anchor;
  - **Auto** keeps the previous shot's choice. With no places at all, it uses the people's own backgrounds.
- **Change characters.** ✎ Edit renames a person or changes their look, nicknames or she/he/they. ✕ on a thumbnail stops using that photo. *Replace the earlier photos/videos* swaps all of a person's references for a new upload. *Remove from project* / *Bring back* takes a person out of new shots.
  - In words: "replace Grandpa with Lucia", "Lucia instead of Maya", "without Grandpa". Earlier direction is rewritten for the new cast.
  - Everything is undoable, and earlier shots keep what they used.

## New in V0.9.70

- 🧪 **A 24 GB demo model set** (for showing the Director on an RTX 4090 / 3090 / A5000 / L4; your full BF16 set stays the
  default and unchanged). All from Comfy-Org/MiniMax-H3 and loaded by stock ComfyUI, no extra custom nodes:
  H3 Ref2VA **pruned w6a8** (16 GB, vs 66 GB), the text encoder **nvfp4_awq** (15.7 GB, vs 51.5 GB; no Blackwell needed),
  the **int8_convrot** video VAE (2.8 GB), the same audio VAE, and Qwen 8B only. ~53 GB in all.
  - Install it with `install.sh --lite24`, or a second Vast template with `DIRECTOR_PROFILE=lite24` (see
    VAST_TEMPLATE_SETUP.md). The installer reads each file's SHA-256 live from Hugging Face's index and checks the download
    against it, leaves ComfyUI's dynamic VRAM / offloading ON (a 24 GB card needs it), and records the set it installed.
  - The Director builds every workflow with the set this machine has (the template variable, else what the installer
    recorded, else what is on disk), and its fail-closed checks accept exactly that set. 🎭 Movement uses Comfy-Org's
    pruned ControlNet as it is (it matches the pruned base; no build). Qwen 8B starts with less free GPU memory.
  - The page says "🧪 24 GB demo" at the top, and 💾 Project > 🩺 This machine names the model set.
  - It is a demo: smaller files lose some detail and identity against the full BF16 H3. Not yet run on a 24 GB GPU.

## New in V0.9.69

📐 **Picture-true positions.** Why placing people depended on the picture a shot opens on, and what changed:

- **The cause.** H3's frame is 9:16 (768 × 1344). A picture of another shape was pinned as it is, and the node fitted it
  with a blind centre crop. In a wide photo, someone standing off-centre was cut out of the first frame (or cut in half),
  while the Director measured the WHOLE photo and told H3 "Maya is on the left". The words and frame 0 disagreed, and it
  got worse the further the picture's shape was from 9:16. Frames the Director builds (🧩 from your dots, 🔗 where the
  shot before ends) had the same problem: the room kept the picture's shape, so the node cropped the finished frame and
  the dots shifted.
- **Fitted.** The picture a shot opens (or ends) on is now cropped to 9:16 **around the people in it** (every face, then
  as much of each body as fits) and sized to the frame, and that is what gets pinned. It is a crop of your original, made
  once and kept next to it; nothing is drawn (Clean-Chain). A 9:16 picture is used as it is. When not everyone fits in
  one 9:16 frame, the plan card says so and whoever is outside comes into the frame during the shot. The plan card shows
  the frame H3 really starts on. Built frames use the same 9:16 view of the room.
- **Read.** Everyone in the shot is found in that fitted frame (faces matched to their original photos): side of the
  frame and how near the camera, in the 📍 dots' own terms. The 📍 setter now starts from this frame too.
- **Told to H3.** The prompt says who is who in the picture: "In that first frame, <Subject 2> (Maya) is the person on the
  left of the frame, in the foreground, close to the camera; <Subject 3> (Grandpa) is the person on the right …" (the same
  for a picture a shot ends on). H3 no longer has to guess which reference belongs to which person in the frame.
- **Held against your dots and your words.** Start dots that agree with the picture take the picture's exact spot, so
  the words match frame 0. Dots that differ can't hold at frame 0 (it IS the picture), so a ⏱ lead-in is added (2 s; your
  own lead-in is kept) and H3 moves everyone from the picture to your dots; the card says who moves where. If they would
  have to cross each other to get there (also when a 🔗 carry-on lead-in leads back to the shot before), it warns: H3
  tends to swap who is who instead. The shot's text is checked too ("Maya on the right" while the picture has her left).
- **📍 Sides fixed when nothing says them.** A two-person shot with no dots, no picture, no shot before it in the scene and
  no side in its words left the sides to chance, so two takes of one prompt could come out mirrored. The Director now
  fixes them (what the shot says is kept; otherwise the first-named person is on the left), the same in every take, and
  says so on the card. Not used when someone comes in during the shot.
- **Every take is measured against the positions asked.** Where each person really is right after the lead-in (or at the
  start) and at the end, recognised by face, against your 📍 dots (or the fixed sides): wrong side, swapped, or far instead
  of near. A misplaced take says so on its card, ranks lower, and in a 🎬 film run counts as not done: another take (up to
  the usual 2), aimed at the positions. People turned away or out of frame aren't judged.
- Settings › three new checkboxes (all on): "📐 Fit and read the picture a shot opens on", "📍 Fix who is on which side
  when the shot doesn't say", "📐 Check every take against the 📍 positions". A/B tests for the first two.

## New in V0.9.68

- ✂ **Split clips and cut pieces out of them.** Every clip in the movie has a **✂ Edit** button. It opens the take with
  a frame counter (← → step one frame, Shift for six) and a strip of its pieces:
  - **✂ Split here** cuts the clip at the frame you're on. Click a piece to remove it (click again to put it back).
    To cut out the middle: split before it and after it, then click the piece between. Removing the last piece
    ends the clip earlier; **⇤ Start here** / **End here ⇥** trim it as before.
  - **▶ Play as cut** plays only the pieces you kept, as the movie will. **↶ Undo** reverses each step.
  - The take never changes: a cut is a decision on top of it (saved in the project's history, in your decisions file
    and in cloud backups). The rendered movie and the ⬇ editor export (EDL / Premiere / Resolve / Final Cut) use the
    pieces you kept, with the sound cut cleanly at each join. A piece in the movie needs at least 1 second.
- ▶ **Takes the browser can't play now play.** Newer VideoHelperSuite versions save takes as 10-bit H.264, which Edge
  and Chrome can't show (the player flashed and stopped). The Director now shows such a take through an 8-bit copy
  made from it once (kept in the project's renders/preview folder). The take itself stays exactly as H3 made it:
  **⬇ Original** in the ✂ editor downloads it, and every measurement, the movie and its colour use it.
- 🩺 **Is this machine as fast as it says?** About a minute after ComfyUI starts, the Director runs a short GPU load
  (~8 s) and a disk read. If the GPU is held back (its clock under load far below its maximum; with "power at the
  limit while the card stays cool" called out) or the disk is slow, a warning says so on the page, with what to do.
  While a take is generating it runs nothing extra and only reads the clock during the take. 💾 Project > 🩺 Check this
  machine runs it any time. (Built after a rented RTX PRO 6000 ran at 600–700 MHz of 3090 at "600 W" and 40 °C.)
- ☁ **Quieter cloud backup**: no round on every tab switch any more (only when the page really closes, at most every
  10 minutes), and the repo is checked once an hour instead of every round.

## New in V0.9.67

A run-through of the whole Director, plus:
- ☁ **A fresh instance gets your projects back by itself.** When an instance has no projects yet, about 1.5 minutes
  after ComfyUI starts, the newest cloud backup of each of your projects is restored (same checks as any restore; your
  imported decisions are applied). It never brings back a project you removed on a running instance (it only runs while
  there are no projects at all). Tick or untick it in 💾 Project > ☁ Cloud backup. With `DIRECTOR_BACKUP_REPO` in your
  Vast template, cloud backup is switched on to that repo without opening the settings (see VAST_TEMPLATE_SETUP.md).
- ☁ **One Hugging Face commit per project per round** (the backup, latest.json, the decisions file and removing the
  oldest backup together). Hugging Face limits commits per hour; before, one round made three or four per project.
- ☁ **Disk check before a cloud backup**: with too little room on /workspace, the round says so and skips that project
  instead of filling the disk mid-generation.
- ⭐ **The main look everywhere a person's photos are used**, not only in the references: a 🔒 built start frame now
  cuts the person from a photo in their main look, a background taken from "the people's own photos" is a photo in
  their main look, and 📝 Describe from photos reads the same main look. Before, those could still pick a photo from
  another day, in other clothes.
- Fixed: the cloud settings page could fail on an instance where the projects folder didn't exist yet.

## New in V0.9.66

- ☁ **Cloud backup to your private Hugging Face repo** (💾 Project > ☁ Cloud backup). Tick "Back up my projects and
  decisions automatically" and type a repo name (it suggests `yourname/director-backups`):
  - every 10 / 15 / 30 / 60 minutes, each project that CHANGED since its last cloud copy is backed up (the same zip as
    ⬇ Backup: the history, plans, measurements, your original uploads and every reference file; takes only if you tick
    "with takes") with its decisions file. Unchanged projects aren't uploaded again;
  - when you close or leave the /director tab, one more round goes at once (only what changed);
  - **☁ Back up now** backs up every project straight away;
  - **☁ Restore from cloud…** lists your backed-up projects and restores the newest one onto this instance, a fresh one
    too, with the same checks as a restore from a file (every file's hash, the history chain, never overwriting a
    different file in ComfyUI);
  - the newest 5 backups of each project are kept in the repo; older ones are removed.
  - The repo is a PRIVATE dataset repo (it holds your photos and videos): it's made private if it doesn't exist, and the
    Director refuses to upload to a public one. It uses the Hugging Face token the installer saved (it needs WRITE
    access; a read-only token gets a clear error). The token is never written into the Director's settings.

## New in V0.9.65

- ⭐ **One main reference per person, supported by all their other photos** (Settings > Person references, on by default).
  With several photos or videos of one person, the Director used to take the stills of whichever upload came first.
  - **The main reference sets the look**: clothes, hair, makeup, accessories. It's the upload you ★ (a new ☆ on every
    photo thumbnail; videos keep their ★ Main reference button), else the upload that shows the person best (a full body
    first). Uploads that are clearly the same look count with it: the same clothes on the torso (measured with the colour
    white-balanced against their own skin, so a darker or warmer room is still the same clothes), or the same photo
    session (practically the same face, e.g. a face crop of the same portrait). **Every body photo, the reference clip
    and the first face come from the main look**, so clothes and makeup from different days never mix.
  - **The other uploads support the face only**: their best faces go to H3 as **close crops of the face** (forehead to
    chin; a crop of your original, nothing drawn), and the prompt says so: "Picture 2 is Maya's main reference: her
    clothes, hair, makeup and accessories are exactly as in it; Pictures 5 and 6 are close-ups of Maya's face from other
    photos, only for the shape and features of the face".
  - **Which supporting faces**: identity agreement with the person's identity across ALL their stills (worked out
    robustly: odd ones out are left out first; a face that reads unlike the rest, like the wrong person picked from a
    group photo, is never used), sharpness, size and exposure, then other uploads and other angles (left / front / right)
    first; near-copies are skipped.
  - **Spare slots** (one-person shots): more supporting faces (Faces per person, at most: 2, 3 or 4; default 3). Two-person
    shots keep four images each, as before.
  - The person identity used by every face check (who is who in a take, sides, starting positions) is now that robust one.
  - Your existing projects choose the same images as before when all of a person's photos are one look (tested on the
    hallway project). A/B test: "⭐ best faces/bodies from all uploads vs first upload".

## New in V0.9.38

- **🎞 A film opens on your picture.** On the 🎬 film card, "Opens on" picks one of your ORIGINAL stills (or a frame of your
  own video) for the film's first shot. Qwen sees it while it plans the film and writes shot 1, and shot 1 is planned to
  open exactly on it (pinned as frame 0, like the Start picker). Any later shot can open on a picture too (a new scene):
  its starting positions then come from that picture, so it gets no 🔗 continuity pass and no side check against the shot
  before, and 📍 Check the joins shows it as "a new start on your picture". Never a generated frame (Clean-Chain).
- **📍 Pick each shot's place.** Every film shot has a place picker: its background is your photo of that place.
- **Qwen no longer crashes when ComfyUI starts it.** Vast's ComfyUI preloads tcmalloc (LD_PRELOAD); passed on to Qwen it
  made PyTorch's generate segfault at random (exit -11), most often while writing a film. Every Qwen launch (film,
  rewrites, 🔍 checks) now goes without it; ComfyUI and H3 keep it.
- **When Qwen dies, it says how:** "crashed inside PyTorch/CUDA", "killed by the system (often: out of memory)", or the
  Python error, instead of its last two lines. Its whole output is kept in `qwen_logs/<film|rewrite|check>_last.txt`.
- **Real error messages.** A refusal from the Director (for example `QWEN_MODEL_8B_OR_32B`) used to show as "the Director
  server is not answering". It now shows the refusal; an internal error says to look in the ComfyUI log.
- **"Wait for my ✓ Keep after each shot" changes the run going now**: no need to stop and restart it.

## New in V0.9.37

- 📍 **Who is on the left and right at the end of a shot is now really measured at the end.** It was each person's AVERAGE position
  over the whole take: when people crossed or moved in a lively take, "the previous shot cuts away with Maya on the left"
  described the middle of the take, and the next shot started from there (often looking like it came from an older take).
  Now the faces are recognised in the last ~0.7 s before the movie cuts; if those frames don't clearly agree, no sides
  are claimed at all. The "swapped sides" warning, which also lowered a take's rank, now compares where the previous shot
  ENDS with where the new take STARTS. It used to compare the two averages, so a good take could be flagged and an older one
  would stand in.
- 🎬 **A film take that starts on swapped sides gets another take.** In ▶ Generate the film in order, a take whose people start
  on the opposite sides from where the shot before ends (measured by recognising their faces; no Qwen needed) counts as not
  making sense: another take with a new seed, within the same limit of 2 more. A swapped take no longer stands in silently.
- 🎯 **Retries aim at what went wrong.** A 🎬 retry used to be the same prompt with a new seed. Now it is planned again with
  one line, "Most important in this shot: …", naming what 🔍 didn't see (your direction's own points, never Qwen's words),
  that only the shot's people are in it, where it happens, and that everyone starts where the shot before ends. All of it
  is positive wording, because H3 runs with no negative prompt. The new seed comes too.
- 📍 **Check the joins** (a button on the film card): for every cut between two of the film's shots in the movie, who is left
  and right where the first one ends and where the next one starts (faces recognised in those frames), ✓ or ✗ swapped,
  and what 🔍 said about the next shot. Measurement only; it tells you which shot to make a New take of.
- 📍 **Starting positions never come from a shot two back.** When you planned or ✨ rewrote a shot while the shot before it was
  still generating (or not sent yet), Qwen was shown the end of the shot BEFORE that one and wrote its positions into the
  direction, and those stuck. Now, while the shot right before isn't back, Qwen is told the starting positions aren't known
  yet (it describes what happens and where people end up), it sees no older ending, and the Director fills the start in
  from how the shot before really ends, just before the shot is sent. Every plan card says where its starting positions
  come from ("📍 from shot 4 take 2, measured where the movie cuts it", or "not known yet: shot 4 isn't back").
  A film shot's context is now the shot before's story beat, not its written-out ending (which was only what was meant).
- 🔗 **Qwen continuity for films** (on by default, a checkbox in 🎬 Make a film). When you ▶ Generate the film in order, after
  each shot comes back and 🔍 has checked it, Qwen rewrites the NEXT shot with the ✨ rewrite's full steps: the real
  last frame of the shot before (for who is where only, never looks), what 🔍 saw done or missed in it, and that shot's 📷
  action photo if you picked one (a new picker on each film shot). The run then sends that version as a new take, with the same
  people, place, length and seed; Qwen's first version stays on the card. If Qwen can't (not installed, unusable
  reply, 25 minutes), the shot goes as planned with the measured positions, and the run says so.
- 🎬 **Films hold up when Qwen's reply is messy.** If Qwen runs out of room part-way through the shot plan, the complete
  shots before the cut are kept and the card says the ending may be missing (the plan's room was also doubled, enough for
  48 shots). Stray text, code fences, trailing commas, lengths written as "6s", "the hallway" for Hallway and a full name
  for a first name are all read correctly. Anyone Qwen invents is still left out, with a note.
- 🎬 **A stop part-way keeps what was written.** If you press Generate while Qwen is writing a film, Qwen now steps aside at
  once so H3 gets the GPU. A time limit or a crash is handled the same way. The shots already written are kept; the rest show the
  planned beat, marked "Qwen didn't get to write this shot out".
- ✍ **Qwen rewrites** on any shot not planned yet: Qwen writes that one shot again, knowing the shots around it as they are
  now (your edits included). **✍ Qwen writes the N unwritten shots** does all the missing ones at once. Same checks as
  the rest of the film; nothing is planned until you press Plan.
- ⏱ **How long the film takes to generate**, under the shot list: worked out from how long YOUR takes really took on this
  ComfyUI (read from ComfyUI's own job history), for each shot's length and your step count. With "takes per shot" above
  it, and the most it could be if the 🔍 check retries shots. Until a take has come back there's no number, and it says so.

## New in V0.9.36
- 🎬 **Make a film** (in "Direct the next shot"): say what the film is about and how long it runs (30 s to 5 min), and
  Qwen does the planning:
  1. It looks at your ORIGINAL photos of the people and places and plans the film as a shot list that tells a whole
     story (opening, build-up, turn, ending): each shot's framing (wide, medium, close-up, over the shoulder, two-shot,
     reactions), who is in it, where, any spoken line and about how long it runs. A 2-minute film is about 15-30 shots.
  2. It writes every shot's full H3 prompt with the same rules as the ✨ rewrite (timed beats, visible acting, listeners
     reacting, no looks), each knowing the shot before and the shot after. One Qwen load for the whole film, with
     progress shown ("writing shot 7 of 22").
  3. **Each shot's length comes from the prompt Qwen really wrote**: spoken words at about 2.5 a second, about a second
     per action beat, 3 to 10 s; the take is generated long enough for it. You see the film's running time against what
     you asked for. Change a shot's seconds to set your own; edit its wording and it is re-measured when you plan.
  4. You read and edit every shot, then **Plan all shots** (in order, each with its own people, place and length; it
     stops at a shot that needs you and carries on from there) and **▶ Generate the film in order**. Nothing is planned
     or generated until you press those buttons. The draft is saved, so a reload brings it back.
- 🔍 **Every take of a film shot is checked for sense** before the next shot goes (on by default; Make a film > 🔍):
  Qwen checks that the take does what the shot says, that only the right people are in it, that it is in the right
  place, and that it carries straight on from how the previous shot ends in your movie (it sees that last moment for
  comparison only; nothing generated ever goes to H3). If no take holds up, another take with a new seed (up to 2
  more), then the run goes on with the best one and tells you. A take that doesn't make sense is ranked down, so the
  next shot builds on one that does. Nothing is kept or rejected for you.
- **Qwen 32B is now the abliterated Qwen3-VL-32B-Instruct** (huihui-ai/Huihui-Qwen3-VL-32B-Instruct-abliterated; BF16,
  ~66 GB, same loader). Settings > Qwen model > 32B Abliterated, then Download (or `bash install.sh --qwen32b`).
  The official 32B is no longer used.

## New in V0.9.35
- 😮 More emotion, real reactions and faster, livelier motion, based on research into how H3 reads prompts:
  - **Acting** (Settings, new, default Expressive): every feeling is written as visible acting H3 can play ("laughs out
    loud, head tipping back, shoulders shaking"), in three real steps (a little / normal / very). Before, even "strong"
    was hedged ("a small genuine smile", "still grounded"). Whoever is silent while someone talks now gets an explicit
    reaction to the speaker. Understated keeps the old wording.
  - **Positive wording only**: H3 runs at CFG 1 with no negative prompt, so lines like "never in slow motion", "nobody
    stands frozen", "no waxy skin" or "no CGI" put those very ideas into the prompt. They now say what IS on screen
    ("at brisk real-time speed", "everyone keeps moving", "true-to-life skin").
  - **Identity in the right place**: the 🪞 no-beautification text moved to subject_definitions (said once, positively),
    so the detailed description holds only the action, in playback order.
  - **A person's reference clip is a weak reference** (with 🏃 Lively): it gives looks and voice, while their movement
    in the new shot is new; a clip could lock the pose and pace. Extension source clips are unchanged.
  - **✨ Rewrite asks for 4-6 timed beats** filling the whole shot ("at once", "halfway through", "by the end"), feelings
    played big, listeners reacting, lines at about 2.5 words a second; the worked examples were redone the same way.
  - **🎛 Sampling** (Settings): 25 steps by default (was 20; about a quarter slower, more room for motion), optionally
    30; the sampler can be res_multistep (MiniMax's official template) instead of euler. Plans made before keep 20/euler.
  - **Intense emotion**: words that are intense in themselves (furious, sobbing, screaming, terrified, devastated,
    hysterical, breaks down) now mean the strong step on their own, and "extremely", "uncontrollably" and the like count
    as "very". At the strong step the feeling is played at full intensity and reaches the voice ("shouting, the voice raw
    with anger", "through sobs", unless you wrote how the line is said) and the soundtrack (sobbing, raised voices, a
    scream); a silent shot stays wordless but keeps its sobs and gasps.
  - A/B tests for acting, steps and sampler.

## New in V0.9.34
- Longer directions: up to 3500 characters typed for one shot (was 2000), and up to 2800 of them kept in the prompt
  (was 1600, cut off at a sentence). MiniMax's guide asks for 350-500 words (~2500 characters) for the shot
  description; the ✨ rewrite's limit is raised to match. If the whole prompt with your references would pass 6800, the
  Director says so with the exact count instead of cutting anything.

## New in V0.9.33
- 🎨 Colour in the movie render, done properly (Settings, both on):
  - **Steady colour within a shot**: each generated shot is held to the look of its own first second, frame by frame,
    so a take whose colour or brightness drifts while it plays is pulled back (a smooth curve over half a second, no
    flicker). Before, every clip got ONE fixed correction, which could not follow a drift.
  - **Match the colour of my originals**: shots are matched to YOUR footage instead of to shot 1 (which copied shot 1's
    look): the end of your original video when the movie cuts from it, else your photo/video of the place, else the
    people's own photos. Saturation, contrast and warm/cool are matched; brightness only halfway (a photo and a take
    can be framed differently).
  It works in Lab (brightness, green-magenta, blue-yellow and saturation kept apart, so making a shot less warm does not
  shift its greens), every change is bounded, your own videos are never touched, and the take itself never changes:
  the render uses a graded copy (renders/graded, cached). With both off, the older one-step match to shot 1 is used.

## New in V0.9.32
- Fixed: with a start picture (or end picture, or the end of the previous shot) showing two people, the rewrite often
  swapped them. The step that reads the picture told Qwen "Maya is the one on the left, Grandpa on the right", but that
  was only the ORDER you picked them in, not the picture. Now the sides come from recognising the faces against
  everyone's ORIGINAL photos ("Grandpa is the one on the left, Maya on the right: recognised from their photos"); when
  that can't be told for sure, Qwen says "the person on the left / right" instead of names, and the rewrite (which sees
  the photos) matches them. The rewrite card shows it: "👥 In the start picture: Grandpa (left) · Maya (right)".

## New in V0.9.31
- Longer prompts: a shot's prompt (and your own edited wording) may now be up to 6800 characters (was 6000); H3 takes
  7000, and the last 200 stay free for the continuity the Director may refresh just before sending. The final graph
  check now uses H3's own 7000 (it refused refreshed prompts above 6000).
- A wrong H3 model file (Turbo, FP8, ...) is refused as NOT_THE_FULL_BF16_H3_MODEL_SET again, not as a vague
  MODEL_CHAIN_INVALID.

## New in V0.9.30
- 🎭 **Who moves like this**: pick the person who follows the movement video (default: the shot's main person). When
  your movement video shows fewer people than the shot (the Director counts them), H3's prompt says "Maya follows the
  guiding movement. Grandpa is not part of it and moves on his own, as described", the rewrite is told to write
  Grandpa's own actions in full, and the shot card says who follows it. A new take keeps the choice. Tips: strength
  0.4-0.5 leaves more room for the free person; put them on the other side of the frame and say how they come in.
- Fixed: when a 🎭 shot's prompt was rebuilt (continuity refreshed before sending, or the stills-only fallback), the
  "no stick figures, skeleton lines or dots in the picture" sentence was left out. It is always there now.

## New in V0.9.29
- Fixed: "build_request() got an unexpected keyword argument 'motion'" when rewriting after an update. An old copy of the
  rewrite module was still in ComfyUI's memory next to the new Studio. The rewrite now checks it has the matching one
  and loads it again from disk if not; if the file on disk is old too it says "UPDATE_INCOMPLETE ... install the update
  again, then restart ComfyUI" instead of crashing. (0.9.28 also reloaded every helper module whenever the Studio
  loaded; that is taken out again.)

## New in V0.9.27
- 🏃 Movement (Settings): **Lively** (new default) or **Calm** (the older wording). Takes were stiff partly because of our
  own words: the prompt said "understated", "never theatrical", "unhurried", "pauses are welcome", and the enhancer put
  "a brief, natural pause" between actions. Lively follows MiniMax's own H3 prompt guides
  (VIDEO_PROMPT_WRITING_GUIDE_ref_en / _base_en): motion is a continuous path (how it starts, develops and ends),
  actions flow into each other, everyone stays alive (weight shifts, gestures, glances, breathing), feelings show in the
  body and face, one gentle camera move in H3's own wording ("pushing in with small amplitude at slow speed"; none
  added when you chose a camera move or wrote one), and the summary section stays one short paragraph.
- ✨ The rewrite is briefed for H3 the same way: 80-160 words of concrete, physical motion that fills the shot's
  length, each action as a path, the listener reacting, sounds next to the actions that make them, at most one camera
  move written as type + amplitude + speed (none if you picked a camera chip). Your lines and how they are said stay
  exactly as written, and looks still stay out of it (they come from the person's card and photos).
- The rewrite also gets two worked examples (made-up people "Ximena" and "Orrin", no looks); a rewrite that copies
  them is refused. It is told the framing chip you picked (a close-up puts the movement in the face, head, shoulders
  and hands; a wide shot uses the space), and both the rewrite and the prompt say "real-time speed, never slow motion".
- 🎥 Look (Settings): **Real life** (new default) or **Cinematic** (the older look). The default tone said "cinematic",
  which the enhancer turned into "shallow depth of field, a gentle film-like contrast": a polished, generated look.
  Real life asks for footage that looks filmed for real: true-to-life skin texture, no beauty filter or waxy skin, the
  place's own light with natural, slightly uneven exposure, no grading look, no CGI; and the gentle camera move is
  handheld with a slight shake. Your own tone is kept as you wrote it. A/B: 🎥 real-life vs cinematic look.
- 🪞 Faithful to the references (Settings, on): every prompt says to keep each person exactly as in their photos: face
  shape and features, skin tone and real skin texture (pores, freckles, moles, wrinkles, blemishes, facial hair), teeth,
  hairline, body shape, weight, height and age; nothing smoothed, slimmed, de-aged, given extra makeup or made more
  attractive. The enhancer no longer adds "soft, flattering light" for "beautiful". A/B: faithful on vs off.
- Speech: **Only the lines I write** is now the default. With no line written, the prompt said "any talk is brief, in
  their own words", which invites talking WITHOUT words: H3 fills it with gibberish or talks about the scene. Now
  nobody talks then (no murmuring, no mouthing words; the sound says no voices), and the rewrite is told never to
  write "they chat" / "she tells him" when your notes have no line. "Ad-lib" is still there if you want it.
- 🎞 The 3 s reference clip H3 gets from a person's video is now the BEST 3 s, not the middle: every 3 s stretch is scored
  by how much it shows the person's face clearly and how much they move (the stills still come from the whole video).
  The person's card says which seconds it is ("3 s clip: 6.2-9.2 s of your 13.0 s video (picked: face visible and
  moving)"); open it to scrub your original and press "Use these 3 seconds" to choose others yourself. Cut again from
  your ORIGINAL upload, captions filled in as before; shots already planned keep their clip. (A place's clip stays the
  middle.) Also: "It's just one person" now keeps the clip without the "several people" note.
- Fixed: a start picture (or end picture, or the end of the previous shot) that doesn't show everyone in the shot
  confused the rewrite. Qwen was asked "where are Maya and Grandpa" about a picture with only Grandpa in it, and invented
  Maya. Now the Director first counts and recognises who is really in the picture (against everyone's ORIGINAL photos):
  Qwen is asked only about them ("Only Grandpa is in this picture. Maya is NOT in it"), the rewrite is told "NOT IN THE
  START PICTURE: Maya ... comes into frame the way your notes say", and H3's prompt says "Maya is not in the first
  frame: she comes into the frame during the shot". The rewrite card shows who was found ("👥 In the start picture").
- Fixed (identity check): with fewer faces than people in a frame (someone off screen or turned away), the visible
  face was always scored against the FIRST person in the shot. It now goes to the person it really matches, so take
  cards no longer say "Maya looks less like her reference" / "Grandpa was not found" for that reason.
- 🏃 Movement score on every take card ("movement: low (0.08) · nobody moves for 3.5 s"), measured from the picture with
  the camera's own movement taken out; also shown next to A and B in an A/B. Rough bands set from the bundled takes:
  it compares your takes with each other, it doesn't grade acting. Older takes get it from their analysis file.
- A/B tests: **🏃 lively vs calm movement wording** and **🧩 your LoRAs on vs off** (same seed; only that differs),
  so your own takes decide what moves better.

## New in V0.9.26
- Fixed: a photo or video of ONE person could "show 2 people". The fast face finder sometimes takes a hand, a pattern,
  a face printed on a T-shirt, a phone screen or a poster for a second face. Now, with 2+ faces found:
  - an extra face counts only if the more careful face finder (YuNet, used for the identity check) sees it too, or the
    fast finder is very sure;
  - a face under half the size on the person's own body (a print, a screen, a photo they hold) is ignored; a bigger
    one there may be a child in front of them, so that is still asked about;
  - a tiny face far smaller than the main one (a poster, someone far in the background) is ignored.
  The same check is used when the Director recognises which side someone you already have is on.
- Fixed: 🎭 Movement takes failed in ComfyUI with "controlnet adaln width 8 does not match the base model's timestep
  embedding width 2688". Comfy-Org's pose ControlNet files are all "pruned": their AdaLN layers use the compressed
  "curve basis" of the PRUNED H3 base models, and the Director uses the full BF16 H3. The Director now BUILDS a
  ControlNet that fits it (`minimax_h3_fun_controlnet_union_2.0_full_adaln_bf16.safetensors`): Comfy-Org's 2.0 file
  with each control block's AdaLN layer taken in full form from Alibaba's original ControlNet-Union-2.0 (only those
  ~5 GB are fetched). Before writing anything it checks the block count, every shape, and that a layer both files share
  is the same (same version). 🎭 Movement shows "Download the pose models" again: press it, then restart ComfyUI.
  Check first without downloading: `/venv/main/bin/python ComfyUI/custom_nodes/ComfyUI-DirectorStudio/studio_posebuild_v0926.py
  ComfyUI/models/model_patches/minimax_h3_fun_controlnet_union_2.0_pruned_bf16.safetensors x --check-only`
- Fixed: 🎭 Movement takes failed in ComfyUI with "controlnet adaln width 8 does not match the base model's timestep
  embedding width 2688". Comfy-Org's pose ControlNet files are all "pruned": their AdaLN layers use the compressed
  "curve basis" of the PRUNED H3 base models, and the Director uses the full BF16 H3. The Director now BUILDS a
  ControlNet that fits it (minimax_h3_fun_controlnet_union_2.0_full_adaln_bf16.safetensors): Comfy-Org's 2.0 file
  with each control block's AdaLN layer taken in full form from Alibaba's original ControlNet-Union-2.0 (only those
  ~5 GB are fetched). Before writing anything it checks the block count, every shape, and that a layer both files share
  is the same (same version). 🎭 Movement shows "Download the pose models" again: press it, then restart ComfyUI.
- Fixed: ✨ Rewrite kept showing an EARLIER rewrite's text. When you edited a rewrite's text, the page remembered it (so a
  redraw wouldn't wipe your typing), but every rewrite box had the same name, so a few seconds after a NEW rewrite came
  in, the page put your earlier edited text back into it (and "Use this wording" took that). Each rewrite now has its
  own boxes and the remembered text is cleared on Use / Discard / Plan.
- The rewrite card says which take it continues from ("🎞 Continues from shot 4, take 2 (the take you kept)"). It is the
  last shot in the movie; until you keep a take that is the best-measured one, so keep the take you want, then rewrite.
- If it still asks "which one?", there's a new answer: **It's just one person** (the clearest face, the rest ignored;
  a video then also gives a reference clip).

## New in V0.9.25
- Fixed: a retake could come back as the same video. A seed was worked out from the shot, take number and prompt, so
  after a discard (or planning a scene again) the same numbers and wording gave the same seed, and the same seed + the
  same prompt is the same video (ComfyUI may even hand back its saved copy). Now no take ever gets a seed any earlier
  take of the project had, discarded ones included.
- "Generate again" / "Retry" on a take that already went to ComfyUI makes a NEW take with a new seed (same plan).
- 🎲 Another take on every shot card: one more take of THAT shot (also a shot in the middle of a scene), same plan,
  new seed. No need to plan the whole scene again when one shot is bad.

## New in V0.9.24
- Fixed: 📝 Describe from photos seemed to do nothing. Qwen wrote the look, but the page redraws itself every few
  seconds and the result went into the replaced box. Now the button stays on "Qwen is looking at the photos… (a few
  minutes)" until it's done (also after a page reload), and the look lands in the Look box with the Edit box held open.
- A look line never says what is NOT there ("no visible jewelry"): a negative in a prompt invites the thing.

## New in V0.9.23
- 📝 Describe from photos (✎ Edit on a person's card): Qwen looks at their original photos once and writes their look
  (each piece of clothing with colour and any logo or text, shoes, accessories, hair). You check and edit it, then Save.
  It uses up to 4 stills of ONE upload (their ★ main video, else the upload with the most stills), so two outfits never mix. Every shot's prompt then uses exactly those words, so clothes and accessories stay right and the same from shot to
  shot (H3 uses words about looks too: with an empty card it got clothes and accessories wrong). A shot with someone whose
  card has no look gets a tip to fill it in.
- The rewriter itself never describes how people look (the card does, the same every time). The 32B kept writing looks into its rewrites ("in her black jersey", "the
  white-haired Grandpa"): they were sent to it from the people's cards, the photos were labelled "how she really looks", and
  nothing forbade it. Words about looks fight the reference photos (H3 already gets every look from the photos and the
  card) and cause drift between shots. Now Qwen gets names and he/she only, the photos are labelled "for telling who is
  who", a rule forbids faces, hair, skin, age, body type, clothes, accessories and their colours, and whatever Qwen still
  writes about looks is taken out (the suggestion lists it). A change your own notes ask for ("she puts on the red hat")
  stays.
- The rewriter keeps the details of poses and positions. Qwen described a single picture very well but lost details when
  it had to describe and rewrite at once, so every picture that pins a moment is now described ALONE first (one Qwen run):
  - your 📷 action photo: the pose goes where your notes say ("ends up in this pose", "starts like this", "holds this pose";
    the end if they don't say);
  - your START picture (this was why a start frame made rewrites worse): the shot begins exactly from where everyone is in it;
  - your END picture: the shot must lead into exactly those positions;
  - otherwise the end of the previous shot: the shot starts from where everyone stood.
  If the rewrite still leaves out a pose or the end positions, the Director puts Qwen's own description in at that spot and
  says so. The suggestion shows each description (📷 pose, 🎞 start, 🏁 end) so you can check them.
- The rewrite uses 🔍 "Did it do what I asked?": what the check SAW happen in the previous shot's take (the one in the movie)
  is passed on as done (not repeated), what it did NOT see as "didn't happen yet: only if this shot's notes want it". The
  suggestion shows what was passed on. Untick "🔍 use the check of the previous shot" next to the Rewrite buttons to
  bypass it (the choice is remembered).
- Fixed (found by the stress test): a trim shorter than 1 second was accepted, and making the movie then failed with an
  internal error. Such a trim is now refused in plain words; one saved earlier (or that becomes too short once the take's
  unusable start is cut off) is skipped for the Director's own cut, with a note.
- The 📷 action photo choice clears after you plan the shot (like 🎭 movement), so the next shot doesn't reuse it.

## New in V0.9.22
- 📷 Action photo for the rewrite: show Qwen the action you want, and the rewrite describes it precisely for your people
  (posture, arms, hands, legs, head, which way they lean or move). Two kinds:
  - one of YOUR original stills (e.g. a photo of you in that pose), added with "my own original" (same checks as every
    original: never a generated take or a frame of one, never a file from ComfyUI's output);
  - a moment of one of your takes (📷 Use this pose on the take card; empty = its last frame): generated, so Qwen may use it
    ONLY for positions and body action, never for how anyone looks.
  The picture never reaches H3: only Qwen's words, which you see first. Appearance still comes only from your originals.

## New in V0.9.21
- 🎭 **Movement from your video** (pose control, built in). Under the start/end pictures, pick one of your original videos
  (or add a "movement video", e.g. you acting out how someone should move) and people in the shot move like the person
  in it: SDPose (ComfyUI core) reads it into stick figures, and the MiniMax H3 Fun ControlNet Union (bf16) steers the take.
  - **👁 Preview** shows the stick figures first (a short ComfyUI run, no H3), so you don't spend a 15-minute take to find
    out the movement wasn't read well.
  - **from … s** picks where in your video the movement starts; **how closely** (0.2-1.0, default 0.6) is how strictly
    H3 follows it.
  - Works with start / end pictures, retakes (a new take keeps its movement; "Remove the movement" takes it off), and
    scenes: one movement video runs through the scene, each shot picking it up where the one before left off.
  - Clean-Chain: only your attested originals and movement videos (checked like every original: never a generated take,
    never a file from ComfyUI/output); only the stick figures reach H3, never your video's pixels; how people look still
    comes only from your photos. The graph check refuses any other video, model or wiring.
  - No stick figures in the picture: the movement steers only the first 60% of sampling (where poses are laid out) and
    lets go for the last steps, strength defaults to a gentle 0.6 (at most 1.0), and the prompt says no stick figures or
    drawn lines. Every take made with movement is also measured for thin bright lines like the stick figures; one that
    has them gets a SKELETON_LINES warning, never stands in for the shot in the movie unless you ✓ Keep it, and counts
    as failed in an overnight run (one more take).
  - Needs three model files (about 9 GB): the panel says what's missing and has a ⏬ Download button; restart ComfyUI
    afterwards. Takes with movement use more GPU memory and time.
  - Not run on real H3 yet: the node and input names follow ComfyUI's SDPose and MiniMax H3 Fun ControlNet nodes, and
    are checked against YOUR ComfyUI before anything is sent (a mismatch is shown in plain words). Try 👁 Preview first.
- The rewriter fills the shot's real length: the ~6 / ~8 / ~10 s chips (or "about 8 seconds" in your notes) reach Qwen, so
  a 10-second shot gets 10 seconds of action, not 5. It's told to keep how each line is said ("very angrily says",
  "whispers") right with its line, and if it changes or drops one anyway, the suggestion says so.
- Overnight: "🔍 one more take if it wasn't done" (off unless you tick it, next to 🌙 Overnight). Before the next shot goes,
  Qwen checks the takes of the shot just made against your direction; if none shows what you asked, ONE more take with
  a new seed, then the night goes on. Costs time (Qwen loads between shots); it never keeps or rejects anything.
- 🗣 Who's talking: on a shot with lines and two people, the Director measures whose mouth moves while there is speech in
  the take's sound (the face models it already has) and shows it on the take card ("🗣 talking: Maya 62% · Grandpa 38%",
  or "can't tell" when it's close). If someone without the line clearly leads, the take gets a TALKING warning. A hint
  only, not calibrated on many takes yet: watch the take to be sure.

## New in V0.9.20
- A line-of-dialogue box per person: pick two or more people under Who and each gets their own box, numbered in speaking
  order; ↑ moves someone earlier, an empty box means they don't speak. The lines go to H3 in that order, each said by its
  own person (the rewriter gets them too). With one person picked, the single box is theirs.
- How each line is said (your idea): next to every dialogue box, pick how it's said (angry, sad, soft, warm, cheerful,
  excited, playful, sarcastic, nervous, hesitant, shy, scared, desperate, firm, cold, proud, tired, whispering, shouting,
  laughing, crying) and how strongly (a little / normal / very). H3 gets it in that person's voice line ("says very
  angrily:", "says in a hushed whisper:"). Typing it works too: 'Grandpa very angrily says "..."', 'Maya whispers "..."',
  'she says, laughing, "..."'. Only words right at the speech verb count, so "holding a warm coffee" stays an action.
- Fixed: when two people spoke, a speaker's name could be left dangling in the action text ("..., then Grandpa").
- At most 3 lines in one shot (what the prompt carries); more is refused in plain words instead of silently dropped.
- Discard a planned shot that was never generated (🗑 Discard shot on its card): it also comes out of ComfyUI's queue, and
  discarding the newest shot frees its number. A planned or queued take can be discarded on its own (✕ Discard take /
  ✕ Cancel in the Generation panel). A take that already arrived is never discarded (reject it instead), and nothing
  is discarded from a storyboard run that is going (Stop it first). Two clicks, no pop-up.

## New in V0.9.19
- Qwen 32B (or 8B) "not downloaded" although it was: Vast's ComfyUI sets its own HF_HOME, so the Director only looked in
  that cache, not in /workspace/.cache/huggingface where the installer puts Qwen. It now looks in every likely cache,
  uses the one that has a complete copy (rewriter, 🔍 check and Download), and a half-finished download no longer counts
  as downloaded. Hover the "✓ downloaded" note in Settings to see where it was found.

## New in V0.9.18
- CPU threads follow the container: Vast rentals see every core of the host (e.g. 384) but get a share (e.g. 46). PyTorch,
  OpenCV and numpy started a thread per visible core, the kernel kept pausing ComfyUI, and every CPU step crawled: making
  references took minutes, the finish after sampling (copying and saving the frames) took 15-30 minutes with the GPU at 0 %,
  and pages ended in HTTP 524. The Director now caps those threads at the real share when ComfyUI starts (and for its
  helpers); the installer adds the same cap to ComfyUI's launch script. Your own OMP_NUM_THREADS wins;
  DIRECTOR_CPU_THREADS=0 turns it off. "Report a problem" shows the share and how often the CPU was throttled.
- No more HTTP 524 while a long job runs: requests wait at most 45 s for the project, then answer "still working on ...";
  the page tries again by itself.
- Stage recorder: while one of your takes runs, every 15 s the Director notes which ComfyUI node is working, what each
  thread is doing (code location only) and its CPU use, GPU use and RAM. "Report a problem" includes it
  (stage_recorder.json, threads_now.json), so a slow stage shows exactly where the time goes. Live view:
  /director/api/threads. Nothing is changed by it.
- The progress line names the real stage (H3 sampling step, decoding the video, saving the video...) instead of calling
  every progress bar "sampling step".

## New in V0.9.17
- Render fix: joining clips no longer fails with "FFMPEG_FAILED ... SAR ... do not match". Every clip is brought to the first
  clip's size, square pixels and one pixel format before the join (takes from different encoders carry different pixel-shape
  tags even at the same size). The same fix is in the two-take auto-edit render.

## New in V0.9.16
- **The rewriter looks.** Qwen now sees, labelled: your ORIGINAL photos of the people and the place (for how they really
  look), your start / end pictures, and the moment the new shot starts from: for an extension the last frame of YOUR
  original video ("✨ Rewrite next (Qwen)" right next to "▶ Extend this video"), otherwise the end of the previous shot in
  your movie. That one is a generated take, so it is used ONLY for who is where, which way they face and what they hold or
  do, never for how anyone looks (a take's drift can't creep into the next prompt). No picture ever goes to H3, only Qwen's
  words, which you review; the suggestion lists what Qwen looked at.
- **Qwen model: 8B or 32B** (Settings). The 32B is sharper at positions, hands and following the rules, and is bigger
  (~66 GB download, ~74 GB of free GPU memory while it runs, so ComfyUI frees H3 first, as always). "Download" in that
  setting fetches it in the background, or it downloads on first use; the fresh-Vast installer can get it too
  (`bash install.sh --qwen32b`; use a ~300 GB disk). The rewriter and the 🔍 check both use the model you pick.

## New in V0.9.15
- **🔍 Did it do what I asked?** When ComfyUI has been idle for a while (after a take, or in the morning after an overnight
  run), Qwen watches each new take and answers your direction point by point on the take card: "✓ Grandpa walks in behind
  Maya (at 1.5 s)", "✗ Maya fixes her hair". It is a hint (Qwen can be wrong), never an approval: it only nudges the ranking
  a little. "🔍 Check takes against what I asked" in Shots & takes runs it now; Settings can turn it off. Qwen only looks:
  frames of a take are never used as references.
- **Pose control test (tools/pose_test_v0915.py).** One generation to find out whether H3's Fun ControlNet Union (pose)
  works with your references: a shot you planned, plus a stick-figure pose video made (with ComfyUI's own SDPose) from one
  of YOUR original videos, so the take moves like the person in it. `--check` says what's missing, `--download` gets the
  ControlNet (bf16) and pose models (about 9 GB). The test take is saved as PoseTest_... and is not added to your movie.

## New in V0.9.14
- **🌙 Overnight.** Next to "Generate in order": "Takes per shot" (1-4) and "🌙 Overnight". The planned shots go one after
  another, each with that many takes (different seeds); every shot continues (text only, Clean-Chain) from how the
  best-ranked take of the shot before ends, with your Remember notes and lasting state as usual. Nobody needs to ✓ Keep
  anything: nothing is approved, the best-ranked take stands in, and in the morning you keep the take you want on each shot.
  If every take of a shot fails (a black/NaN take, a ComfyUI error, or one that never arrives within 45 minutes), it gets
  one more take with a new seed; if that fails too the shot is marked for you and the night moves on. It carries on after a
  ComfyUI restart. Fresh models for every take stays on.
- **Learned take ranking.** Each time you ✓ Keep one take of a shot over another, the Director learns which of its
  measurements predict your choice (clean, faces, problems, movement, quick start, framing, talk that fits, look match),
  starting from its built-in scoring and pulled back toward it, so a few choices can't swing it. It only ranks and explains
  ("ranked first: tighter framing, faces match the cast closely"): it never keeps, rejects or approves. The "Which takes you
  keep" panel shows how many choices it has learned from, your habits in words, and an honesty check (how often it would
  have picked the take you kept). Below 5 choices it is the built-in ranking only, and says so.

## New in V0.9.13
- **✨ Prompt rewriter (Qwen).** Under the direction box: "Rewrite for one shot" and "Rewrite as a scene". The Qwen3-VL you
  already run for checking takes rewrites your notes for H3 and for the way the Director plans: people named (no bare
  she/he), where everyone is, what each person does in order, about the right amount for one take, your spoken lines kept
  word for word. As a scene, it splits the moment into 2-4 shots that each make sense on their own. It is a SUGGESTION: you
  see it first and can edit every shot, then use it, plan it, or discard it; nothing is planned or sent until you choose.
  It is checked like your own words: unknown people refused, Clean-Chain wording ("last frame", "previous video") refused,
  and a warning when it changed, dropped or added a spoken line. Text only (it never sees generated frames). It runs when
  ComfyUI is idle: the Director asks ComfyUI to free the GPU first, like the Qwen observer, so H3 loads again on the next take.
- **End on a position (position reference).** "End on this position" when you plan a shot or a scene: pick one of YOUR
  original stills and the shot ENDS exactly on it (pinned as the last frame with H3's Add Guide), so everyone ends up where
  that picture shows them instead of leaving it to chance. Works with a start frame too (open on one picture, end on
  another), and the last shot of a scene can end on it. Originals only, like the start frame; retakes keep it.
- Start frame, longer shots and whole long directions (below) are part of this version too.

## New in V0.9.12
- **Start frame (optional).** Pick one of YOUR original stills (a photo, or a frame the Director took from your original
  video) under "Start frame" when you plan a shot or a scene: the shot opens exactly on that picture (pinned as frame 0 with
  H3's Add Guide, 9:16 centre-cropped) and moves on from it. Cut-outs on grey, colour-matched copies and anything generated
  are never offered. Retakes open on the same picture.
- **Longer shots really are longer.** Asking for a longer shot (the new ~6 / ~8 / ~10 s chips, or "about 8 seconds" in your
  direction) now generates a longer take for that shot instead of always ~5 s (H3 lengths only; over 7.3 s is experimental:
  more GPU memory and time).
- **Long directions go in whole.** A direction used to be cut at 400 characters, mid-word; now up to 1600 characters, and
  never mid-word. If a prompt would pass H3's 6000-character limit you're told plainly, with the fix.
- **Extend starts on your original's last second.** H3 treats a reference clip as material to copy, so an extension often
  started the new action and then slid back into the original's ending. Now the take starts on the last 22 frames (about
  0.9 s, picture and sound) of YOUR original, pinned with H3's own Add Guide node at frame 0 (never denoised), and no clip is
  sent. The movie cuts that pinned second off, so your original flows straight into the new action. Settings > "Extend your
  video" also offers pinned start + the 3 s clip, or the older clip only. Generated takes are never pinned or extended.
- **Scene planner shots make sense on their own.** Each shot names who does what (no bare "she"/"he"), knows what happened
  in the beat just before it (as context, never re-acted, never with an earlier line's words), keeps both people in frame for
  action beats, and leaves out someone who only walks in later. Each scene shot has its own ▶ Generate and a prompt box to
  read or change before it goes; "Generate in order" sends your newest wording of each shot.
- **he / she from the name.** A new person's pronoun now comes from their name and description ("Old man" -> he) instead of
  defaulting to "she"; a plan warns when a saved pronoun clashes with the name.
- **Putting things down is understood.** "puts her microphone down", "puts it away", "hands the cup to Leo", "lets go of
  the rope", "her hands are empty" clear what someone is holding, so later shots stop being told she still holds it.
- **A broken take from H3 is named plainly.** When H3's output comes back invalid (NaN: a black picture, and ComfyUI's
  Video Combine fails on the sound), the take card says "H3 sent back a broken video", it stays out of the movie and is
  not re-measured over and over. Generate in order pauses and asks you for a new take of that shot instead of waiting.
- **Fresh models for every take** (Settings, on by default). On ComfyUI 0.37 with dynamic VRAM off, only the first take
  after a restart came back right; reusing the loaded H3 model gave invalid (black) takes. While one of your takes runs,
  the Director now asks ComfyUI (its own "free memory" request) to drop its models once that take finishes, so every take
  loads H3 fresh. ComfyUI does this between prompts, never during one; each take starts a little slower.
- **ComfyUI errors stay on screen.** The Generation panel keeps the last ComfyUI error (with Copy and Dismiss) after the
  broken file arrives and after a reload; it also goes into "Report a problem".

## New in V0.9.11
- **Extending a video no longer asks H3 to replay it.** The prompt only mentioned your video as "the main reference for
  how she looks, moves and sounds", marked "fully preserved", which reads as "reproduce this video". Following the
  MiniMax guide (<Video N> is for whole-video roles), <Video 1> now has its own role: the source video, continued from
  right after its final frame, never replayed or repeated. A stray "the filmmaker noted: your original video" line is
  gone too. This is prompt wording only: whether H3 always follows it can only be judged on real takes.

## New in V0.9.10
- **Wait for my ✓ Keep after each shot** (a tick box next to ▶ Generate in order; also used by the scene card's
  button, ticked by default). The next shot is only sent once you've kept a take of the one before, and it continues
  from that exact take. A rejected or undecided take keeps it waiting; make a New take for another try. The status line
  says what it's waiting for. Untick it to run straight through.
- **Abandoned plans aren't remembered.** A planned shot that was never generated only counts in the Director's memory
  (and its lasting changes, like "puts on the red hat") when it comes after the last shot you actually generated, i.e.
  it's part of what you're planning now. Shots where every take was rejected were already left out.

## New in V0.9.9
- **Start points for every take length.** The Director picks where each clip starts in the movie and learns from your
  trims. For longer takes (the 6.6 s and 7.3 s options) that didn't work well: it only looked in the first 2.5 s, it
  averaged what it learned across all lengths (capped at 2 s), and the movie's first shot never learned at all. Now:
  - it searches further into longer takes (up to about 40% of the take);
  - it learns where you start clips separately for each take length (within about ±1 s of length) once you've trimmed
    2 or more of them, and uses the overall habit until then;
  - the first shot of the movie learns where you start it too.
  Checked for every length H3 makes (73, 107, 124, 158 and 175 frames): render, end points, J/L cuts, shot-length trims
  and dialogue timing already follow each take's real length.

- **Generating only what you mean.** ▶ Generate in order and ▶ All at once used to send *every* planned shot that was
  never generated, including old leftovers. Now they first list exactly which shots will go and ask. The scene card
  has its own **▶ Generate this scene in order** (only that scene's shots) and **Stop**, and every planned shot in the
  list has its own **▶ Generate** button.

- **Fixed: a voice recording you picked could vanish** if the page refreshed itself before you ticked "My voice, or I
  have permission" (the same problem photos had before V0.9.5). The voice box now keeps the file and the tick until it's
  sent.

## Planned (not built yet)
- Later: learning how you direct; preview a single cut, frame-step keys, render a section.

## New in V0.9.8
- **🎨 Look check across cuts.** Each take card says how the take begins next to how the shot before it ends in the
  movie: brighter or darker (in %), warmer or cooler, greener or more magenta, flatter or more contrasty. It's measured
  after the render's own colour match, so it says whether the render will even it out (a quiet note) or not (a warning
  in the movie list). It's measurement only; nothing is approved for you. Takes measured before this version are read
  once in the background.
- **🎬 Plan as a scene** (next to Plan next shot): one direction becomes 2–4 consecutive shots that cut like a filmed
  scene. A wide establishes where everyone is; each line gets the speaker's shot (over the shoulder, then a close-up if
  they keep talking); the listener gets a reaction close-up; a hug or handshake is a waist-up two-shot. Write it as
  beats: `Maya walks in, then tells Grandpa "I got the job!", then they hug`. Each shot is planned normally (same
  references, continuity, Clean-Chain) and is undoable. H3 takes can't be synchronised, so it never films the same
  action twice from two angles.
- **Fixed: the wrong person said the line.** In "Maya tells Grandpa "…"" (or asks, answers, walks to, smiles at…), the
  line went to Grandpa, the name nearest the quote. The speaker is now the person doing the telling.
- **Fixed: an earlier shot's line could be spoken again.** A later shot's continuity notes repeated an earlier line
  without its quotes ("Maya tells Grandpa I got the job"), and H3 can speak words it reads in a prompt. Earlier shots are
  now summarised without their lines.

- **Earlier shots' actions are no longer repeated.** H3 can't tell "this already happened" from "do this now", so a
  later prompt that recapped shot 2 ("she fixes her hair") made her do it again in shot 5. The Director still remembers
  the whole story for itself, but H3 now only gets state: what was measured where the movie cuts (who's there, where,
  which way they move), your note on the previous take, your 📌 Remember pins, and **lasting changes** written as state.
  If shot 2 says "puts on the red hat", later shots say "Maya is wearing the red hat (already true at the start of this
  shot; not an action to perform)" until a shot says she takes it off. The same goes for holding something, sitting and
  lying down.
- **Fixed: typing in a box got wiped.** The page redraws itself every few seconds to show new takes; that rebuilt the
  note, Remember and Edit boxes, kicking you out and deleting what you'd typed. Now nothing redraws while your cursor is
  in one of those boxes or in Settings (it catches up a moment after you leave), and typed-but-unsent text, open Edit
  boxes and open panels survive redraws.

## New in V0.9.7
- **ComfyUI no longer dies mid-take on a 123 GB instance.** Cause, measured on Vast: ComfyUI 0.37 turns on "dynamic
  VRAM" by default, which stages the 51 GB text encoder *and* the 66 GB video model in system RAM at the same time
  (RAM reached 114 of 123 GB, then ComfyUI was killed). The fresh-Vast installer now adds `--disable-dynamic-vram
  --disable-pinned-memory` to Vast's ComfyUI launch line (keeping the original as `comfyui.sh.before_director`). The
  health banner warns if ComfyUI runs without them on a machine with less than 192 GB of RAM.
- **Reference clips are 73 frames (about 3 s)**, on H3's own 17k+5 frame grid, instead of 5 s: about half the memory.
  Clips you added before get a 73-frame copy the first time they're used (cut from the Director's already
  caption-cleaned clip; your originals are untouched).
- **Extending a video uses one video only:** the last 73 frames of your original, ending on its last frame. It no longer
  adds a second clip of the same footage. The prompt tells H3 the new shot starts exactly on the final frame of
  <Video 1>; that's text only, no frame is sent as a picture.
- **Full view is the default again**, and "Prompt sent to H3" shows in both views.
- **Report a problem** also includes ComfyUI's launch options, the container's memory limit and out-of-memory kill
  count, and ComfyUI's recent restarts.

- **Fixed a false warning:** the workflow check flagged VideoHelperSuite's `pix_fmt`, `crf`, `save_metadata` and
  `trim_to_audio` as "ignored". They are real settings that VHS lists under each video format, and it uses them. The
  check now reads them there, so the yellow notes are gone. Nothing about generation changed.

## New in V0.9.5
- **⬆ Update** (top bar): install a new Director version without reinstalling and without a terminal. Choose the
  `ComfyUI-DirectorStudio_v0xx.zip` Claude sent you, press **Check** (every file is checked, nothing changes yet), then
  **Install update**. Before anything changes, every project is backed up (story, settings and references, without
  takes) to `/workspace/DirectorBackups/projects_before_update_<time>/`, and the running version is *moved* to
  `/workspace/DirectorBackups/ComfyUI-DirectorStudio_<version>_<time>` (never deleted). Then press **Restart ComfyUI**;
  the page reloads by itself when ComfyUI is back (about a minute). It won't restart while a generation is running
  unless you say so.
  - **Going back:** the same box lists the last 5 kept versions. **↩ Use this** swaps back, then restart.
  - **Space:** the last 5 versions and the last 3 sets of project backups stay; older ones are removed.
  - **Your projects** (`/workspace/DirectorStudio`) are a different folder and are never changed by an update.
  - **Small update zips:** an update may leave out files you already have unchanged (the 46 MB of models), so later
    updates download faster.
  - **What gets refused:** a damaged zip, a zip that isn't a Director package (for example a project backup), and the
    version you already have. In each case nothing changes.
- **Fixed: a picked photo/video could vanish before you pressed Add.** The page refreshes itself every few seconds;
  if that happened between choosing a file and pressing **Add** (or ticking "My own original"), the file and the tick
  were dropped and you got "Choose a photo or video first". The card now keeps both until the file is sent.
- **Clearer messages:** an edited prompt that's too short or too long, and an empty direction, now say what to do.
- **Workflow check:** an input your ComfyUI's node doesn't list is now a note (ComfyUI just ignores that one setting),
  not a "Setup needed" error. A missing node, or a required input the Director doesn't set, is still an error.
- **Stress test** (`tests_v080/fuzz_studio_v094.py`): hundreds of random actions in random order on one project. After
  every step the page data has to load and the saved history has to replay to the same state.

## New in V0.9.4

- **🩺 Report a problem** (top bar). One click saves a small zip that has what's needed to find a bug:
  - versions (package, Python, OpenCV, ComfyUI, GPU/RAM) and the setup check;
  - this server's recent errors and crash reports, with file:line;
  - the project's settings, counts and last events (text only);
  - whether the page summary, story and timeline load, or the traceback if not;
  - the browser's own errors and what the page was showing;
  - background jobs, and the Director's lines from the ComfyUI log.
  It **never** includes photos, videos, voices, references, takes, renders or backups.
- **◐ Simple view** (the default; switch in the top bar, remembered per browser). It shows who's in the shot, what happens, the emotion, the line, the background, Generate / 3 takes / 3 angles, your takes (Keep / Reject / Watch, find a missing take), the movie, Storyboard, voices and Extend. *Full view* adds framing, camera, pacing, shot length, A/B tests, the prompt editor, workflow download, notes and 📌, Qwen hints, identity scores, cut reasons and trim buttons. Nothing is removed.
- **Startup self-check.** About 20 s after ComfyUI starts, every project is opened once in the background (ledger, page summary, story). A project that would fail shows in the top bar with the error and file:line, plus a Report button.
- **Workflow preflight (in the setup check at the top).** The Director builds a sample of its own workflow (two stills, a reference clip, a voice and a LoRA) and checks every node and input against what your running ComfyUI offers. Nothing is queued. If your H3 node or ComfyUI is missing something (for example voice inputs), the top bar says so and names the feature it affects, before you press Generate.
- **Disk-space guard.** The setup check shows free space where takes and projects are saved, and warns under 15 GB. Generate and Render stop with a clear message under 2 GB instead of failing halfway.
- **Take measurements are kept in memory once read.** On a 30-shot test project a page refresh now takes about 0.06 s instead of 0.25 s.
- **Code split into clear parts.** Shared basics, editing (cuts, scores, grades), prompts and the H3 graph each have their own file. Nothing changed for you. Splitting it uncovered and fixed a missing frame-colour cache that older analyses could have hit.

## New in V0.9.3

- **🎙 Voices from your own recordings.** On a person's card, open *Add voice*, choose any audio or video file, and tick that it's your voice or that you have permission.
  - **Formats:** wav, mp3, m4a/aac, ogg/opus, flac, aiff, wma, amr, or the soundtrack of an mp4/mov/webm/mkv.
  - **What the Director keeps:** the clearest 5–15 s of speech. It also removes rumble, levels the volume and saves 48 kHz mono WAV. It tells you how much speech it found and how far it is above the background noise.
  - **How H3 gets it:** the official MiniMax-H3 way. `<Audio N>` is declared as "the voice-timbre reference for `<Subject n>` (S1)", the summary says `+ audio reference`, retention says `reference`, and the line is spoken "in the same voice referenced from `<Audio N>`". The words in your clip are never carried over.
  - **When it's used:** for the people who **speak** in a shot, in every shot of the movie (Storyboard included), until you choose another or press *Stop using it*. Settings has an on/off switch.
  - **Clean-Chain covers sound too:**
    - files from ComfyUI/output, or with generation metadata, are refused;
    - a video's pictures are checked against your takes;
    - the **sound** is compared with every generated take's soundtrack, which catches a take re-encoded or turned down;
    - voices only come from your own recordings.
  - **What it can't check:** whether two people talk in the clip. Use a clip where only that person speaks. H3 makes the final voice; how closely it follows the reference is up to H3.
- **✨ Prompt enhancer** (official format, on by default, switch in Settings, also an A/B option).
  - **What it adds** after your words, which stay exactly as written: plain physical detail for the actions it recognises. That covers how a walk, a hug, sitting down or a sip looks and sounds, about 30 actions in all.
  - **More it adds:** an explicit order for several actions, the camera's amplitude and speed, light continuity, concrete looks for vague words like "cinematic", and matching sound cues.
  - **What it never adds:** words to say, people, props or places, or anything about earlier takes.
  - **How it works:** it's a phrasebook, not a language model, so any sentence works and nothing can go strange. The plan card lists what it added.
- **🎨 Colour matching.**
  - **Extensions match your original video.** When the movie is rendered, the clips that follow one of your original videos are graded toward the colour of its **ending**, where the extension joins (bounded mean/contrast match per channel). Your original is never graded.
  - **Reference photos can be matched to each other** (Settings, off by default, also an A/B option). Each chosen still is moved gently toward one anchor: the place plate, else the background person's own photo. The move covers white balance, tint (hue/chroma) and exposure in LAB, at half strength, capped so skin tones can't swing. A cut-out's grey backdrop stays exactly grey. Matched copies are new references made from your originals, and the originals are untouched. Try it with an A/B test before leaving it on.
- **Takes are measured in the crash-safe helper too** (like uploads), so an OpenCV crash on your machine can't restart ComfyUI. The take is measured normally first. If that crashes, it's measured again with OpenCV's fast CPU paths off. If that also crashes, it gets a reduced measurement without the face finder and motion analysis. Even then the take still goes into the movie, and the clip says so.
- **More A/B options:** prompt enhancer on/off, reference colour-match on/off, official vs classic prompt format.
- **Settings panel scrolls** when it's taller than the window.

## New in V0.9.2.5 (hotfix)

- **Fixed: after ▶ Extend this video, the whole page was empty (HTTP 500).** The shot made from your original video lacked two fields the page summary expected. It now has them, and the summary no longer fails on a missing field. Your original video's thumbnail and "Watch" link also work.
- **A bug now says where it is.** Instead of "server not answering" you get `INTERNAL_ERROR: … at file:line`, and the full traceback goes into the ComfyUI log. Your project is never changed by such an error.

## New in V0.9.2.4 (hotfix)

- **Fixed: your own reference refused as "a frame of a generated take".** H3 copies its reference image into the first frames of a take, so a reference and frame 1 of a take made from it look the same. Two exact checks now come first:
  1. The file is byte-for-byte one the Director already knows as yours, in **any** project on this machine (an original you uploaded, or a reference still/clip it made from one).
  2. The file is byte-for-byte one of the references that matching take was generated **from** (read from the take's embedded workflow).
  A frame saved from a take, or a re-encoded take, is still refused. Only exact files pass, so nothing generated can get in this way.

## New in V0.9.2.3 (hotfix)

- **Found the crash with the report from your instance:** OpenCV's ORB feature finder crashed (segmentation fault) inside the Clean-Chain check (`studio_cleanchain_v081.py:68 fingerprint`). That's the check that makes sure an upload isn't a frame of a generated take. The check now runs in its own child process:
  1. It runs normally.
  2. If OpenCV crashes, it runs again with OpenCV's fast CPU paths (AVX-512/AVX2) and worker threads turned off.
  3. If ORB still crashes, it runs without the detailed crop check. The whole-frame check (which catches a saved last frame, resized or mirrored) and the ending check still run.
  If even that can't run, the file is **refused**, because an unchecked file is never let in. The note on the upload says which of these happened.

## New in V0.9.2.2 (hotfix)

- **The caption/text finder no longer uses OpenCV's `TextDetectionModel_DB` wrapper**, which is the likely cause of the segmentation fault seen on Vast. The same PP-OCRv3 model now runs through the plain loading path the face and cut-out models already use, and its boxes come out nearly the same.
- **If a library still crashes on an optional step** (caption removal, the cut-out, face matching), the upload is processed again without that step. You get your references plus a note naming the step and the exact place it crashed (`file:line`, OpenCV version), to send to Claude.

## New in V0.9.2.1 (hotfix)

- **Uploads no longer vanish.** Your photos and videos are now processed in a separate helper process, so if it crashes or runs out of memory, ComfyUI keeps running. If memory runs out, the helper is the process that gets stopped, not ComfyUI. You then see the real reason ("ran out of memory", "a video library crashed on this file") instead of the file disappearing.
- **The page keeps track of an upload while it redraws itself.** It shows "⏳ Analysing …" until the upload finishes, and a "which person is it?" question no longer gets lost.
- **Jobs survive a restart.** After a restart you get "ComfyUI restarted while this was being processed, nothing was added" instead of "no such job".
- **A reply that isn't from the Director** (for example the Vast proxy page while ComfyUI restarts) is reported as "server not answering". It no longer causes a display error.

## New in V0.9.2

- **Dialogue scripts in Storyboard.** Paste a scene and *Plan all shots*:

      Maya: How was the war, Grandpa?
      (Grandpa looks away.)
      Grandpa: Long. Longer than anyone tells you. We were young, we thought it would all be over by Christmas.
      Maya (softly): I didn't know that.

  - **Every line gets its own shot,** with at most the number of words that can be said naturally in the take (10 for 5.2 s).
  - **Long lines are split** at sentences, then commas.
  - **Stage directions** in parentheses become the action.
  - **The angle alternates** (over the shoulder / close-up) while one person keeps talking.
  - **An unknown name** is reported instead of guessed.
- **Continuity that follows through (text only, Clean-Chain intact).**
  - Each new shot is told how the previous shot *actually ends where the movie cuts away from it*, as measured: who is on which side of the screen (face identity), framing (close-up / medium / wide), and whether people are still or moving and in which direction. It then says "this shot picks up a moment later in the same scene: same sides of the screen, same light, wardrobe and props; movement carries on in the same direction". When the place changes, positions aren't carried over.
  - It uses the take that is **in the movie** (the one you kept, or the best measured), not just the newest.
  - **📌 Remember**: pin a note on a take ("she is holding the red cup"), or pin a Qwen observation you checked. Pinned notes are carried into the next shots as *your* direction. Qwen's own text is never used unless you pin it.
  - A plan made before the previous shot came back is brought up to date when you press Generate (same direction, references and seed; only the continuity text changes). The plan card shows the continuity it carries.
  - **Storyboard → ▶ Generate in order** (recommended): shot 2 is sent when shot 1 is back and measured, rewritten from how shot 1 really ends, and so on. *All at once* is still there when speed matters more. Your click authorises the sequence; *Stop* ends it.
  - The movie warns when two people **swap sides across a cut** (180-degree rule), measured from face identity.
  - What it can't do: H3 only gets words for this, so it follows them most of the time, not always. That's why the swap warning and the ranked takes are there.
- **Several people in one shot.** In *Who*, click more than one person (or *Everyone*). The first one you pick is the focus: their emotion, close-up and line. Exactly the people you picked are in the shot (H3's limit is two). Each extra person gets their own *Emotion* picker (for example Maya smiles while Grandpa is moved); the intensity slider applies to both.
- **Adding people is sturdier.**
  - Using the same name twice now says so plainly.
  - A double click no longer tries to add someone twice.
  - Adding back someone you removed brings them back.
  - Names with accents or non-Latin letters (Zoë, 李娜, Анна) no longer clash with each other.
- **Fixed: the occasional error when adding another person** (`OpenCV … Assertion failed … forwardGraph`). It happened when a photo was being processed while a take was being measured in the background: both used the same face model at once. Every model now takes turns; a stress test with three threads went from 103 errors to 0.
- **Fixed: a kept take not in the movie.** The take you kept *most recently* is the one used (before, an earlier keep of a later take number could win). Keeping a take also puts a hidden shot back in the movie, and a kept take that is still being measured is measured first and says so.
- **Fixed: a finished take that never showed up.** The page kept watching only while it thought something was pending, and one failed refresh (a proxy timeout on Vast) or a display error stopped it for good. It now always keeps watching (every 15 s when idle, again when you come back to the tab), and a take ComfyUI made that didn't match its plan now stays visible with the reason.
- **Take finished in ComfyUI but isn't here?** Under any waiting take: *Find it in ComfyUI output* (lists unclaimed videos and whether each matches the plan) or *upload it*. A video whose embedded workflow matches is taken in as verified; one without it (e.g. re-saved or downloaded) only after you confirm, and it is marked as confirmed by you. Takes are only ever clips in the movie, never references.
- **One broken take never blanks the movie**: it is left out with a note instead.
- **Small things:** Ctrl/⌘+Enter plans the next shot; Plan / Keep / Reject can't be sent twice by a double click; errors read in plain words; each shot card says which take is in the movie.
- **Photos.**
  - Sideways phone photos without rotation info are turned upright before use.
  - "Face cut off at the edge" now reads as plain words.
  - Faces near the edge of wide photos are boxed correctly.
- **View / edit the prompt** on the plan card. Saving makes a new take with your wording: same references, seed and length. Clean-Chain still applies: last-frame requests are refused, and H3's 7,000-character limit is enforced.
- **Retry after a failed generation.** The Generation panel offers *Retry*, *Retry with 6 references* and *Retry without reference clips*, and says which helps with out-of-memory.
- **▶ 3 angles.** The same shot and lines as a wide two-shot, over the shoulder and a close-up on the speaker. They're ranked like any takes, and you keep one. These are separate performances, not synchronised coverage.

## New in V0.9.1 (from your first real session)

- **Extend your own video.** Every original video you upload shows **▶ Extend this video** on its card. Describe what happens next, click it, then click **▶ Generate**:
  - **Its ending goes into the movie.** The last ≤16 s, conformed to 9:16 at 24 fps, become the opening clip.
  - **The new shot continues it.** It uses MiniMax's own **[video continuation]** mode, with the last 5 s of your video as `<Video 1>`.
  - **After that, the Director carries on with pristine references only:** your original stills and clips, never anything generated. A generated take can never be extended.
- **★ Main reference.** Make one of a person's videos their main reference: its stills and clip come first in every shot, and it's also the main look and voice reference when you extend it.
- **The prompt follows MiniMax's official reference format** (MiniMax-H3 docs on Hugging Face):
  - **Six sections:** `subject_definitions`, `summary`, `retention_analysis`, `detailed_description`, `overall_soundscape`, `non_diegetic_music`.
  - **Labels:** `<Subject N>`, `<Picture N>` and `<Video N>` match the order H3 receives the references.
  - **Dialogue** is written as `<Subject 2> (S1), a woman with a warm voice, speaking at a natural, unhurried pace, says: <d>[English] …</d>`.
  - **Quotation marks are never used,** because H3 reads them as on-screen text. Spoken lines are also removed from the action text, so they appear only once.
  - **Silent people** are stated as not speaking, and the soundscape is always filled in, so no talk gets invented.
  - **Line length is checked:** a line longer than fits naturally (about 2.5 words per second) gets a "may sound rushed" warning.
  - **Voice** is a new field on each person (✎ Edit). The old wording stays available in Settings for A/B tests.
- **Speech setting:** *Natural talk* (never the directions), *Only my lines*, or *No words*.
- **Captions, subtitles and watermarks** in uploaded videos and photos are removed before the references are made. Text on the person (a jersey number) is kept, and frames with captions across the person are avoided. Reference clips get burned-in captions filled in, or are skipped when a caption covers the face.
- **Generation progress** appears in the sidebar: shot and take, H3 step x/y with a bar and time estimate, the queue, and the real error if ComfyUI fails.
- **Shot length:** ~2 s / ~3 s / ~4 s / Full take for the edit (also understood in words), plus an *experimental* generated take length in Settings. 5.2 s is the only proven length.
- **Nothing is lost when you close the page:**
  - the open plan card and the last movie come back;
  - a running render is picked up again;
  - your typed draft, chips and storyboard are kept in the browser.
- **Fixes from your session:**
  - Undo could break a project (500 everywhere). It now refuses to undo a shot that was already generated, cancels the job in ComfyUI when you undo one that's still queued, and old projects with that problem open again.
  - Re-adding or replacing a photo could silently add nothing.
  - Uploads now run in the background.
  - A photo with no detectable face offers "use as full body / upper body / face".
  - Choosing a file adds it straight away.

## New in V0.9

- **💾 Project → Backup.** Download one .zip holding the project history, your original references, measurements and what the editor learned. *Backup with takes* adds the videos.
  - **Restore** on a fresh Vast instance: every file is hash-checked before anything is written, nothing different is overwritten, and the project appears when complete.
- **Who is who.** Every take is face-matched against each person's references, using SFace and YuNet from the OpenCV model zoo (Apache-2.0 / MIT, shipped in `models/`). The take card shows "🧑 Maya 0.71 · Grandpa 0.38".
  - **Warnings:** a person not found, a person looking less like their reference, a face that matches nobody, or the two people swapping sides between shots.
  - **Group uploads:** a person you already know is found automatically in a group photo or video.
  - **Measured on your real takes:** the same person scores 0.34–0.82, the other person 0.02–0.34. The TEST take's hidden cut is caught (nobody matches after F101).
- **▶ 3 takes.** Three seeds of the same plan. They're ranked on the measurements (hidden cuts, people count, background, identity), and the best-measured one is cut in until you keep one. Your choice always wins.
- **A/B test.** One shot, same seed, two settings: numbered mapping on/off, 9 vs 6 images, reference clips on/off, or raw vs clean cut-outs. Pick A, B or *no difference*; the Director keeps the tally. A setting that changes nothing for that shot is refused.
- **📋 Storyboard.** Write a scene as one shot per line and click *Plan all shots*. Each shot knows the ones planned before it. *Generate all planned shots* queues them.
- **Finishing.**
  - **Audio fades:** 12 ms at every cut, so no clicks.
  - **Automatic J/L cuts:** where speech runs across a cut, the line finishes over the next picture (L) or the next clip is heard a moment early (J). It's 6 frames, the picture cut never moves, and it's shown as 🔊 on the clip.
- **Nothing waits.** Measuring new takes, rendering, backups and exports run in the background; the page polls and updates itself. Before this, a busy moment could hold a click long enough for the Vast/HTTP proxy to time out (504); this was found and fixed in the browser test.
- **Export edit.** A zip with the takes plus an EDL, Premiere/Resolve XML and Final Cut XML. Cuts are frame-exact, J/L cuts become separate audio edits, and it relinks from the `media/` folder.

## It remembers the film, and it never loses a take

- **Story so far.** Every new shot is written with the whole film so far in mind: your direction for every earlier shot you kept, your notes, and what was measured at the end of the previous shot (who was in frame, left/right positions). It's passed to H3 **as words only**, never as a frame, and shown under **📖 Story so far**. It lives in the project ledger, so it survives restarts and new sessions.
- **Recovery.** A take is only read once ComfyUI has finished writing it. When something goes wrong it is retried on every refresh and announced once:
  - a failed measurement;
  - a lost measurement file;
  - a Qwen job that couldn't be queued;
  - a missing workflow file;
  - a take file that went missing and came back.

  Generations you sent are listed until they arrive, including after a restart.
- **Import hallway project.** Use the button in the header, which shows when `/workspace/DirectorProjects/hallway_interview_v1` exists. It brings in Presenter, Grandpa and the Hallway from the hash-checked originals, Shots 1–2 and any later takes, and the active F36 cut. Anything whose video is missing is remembered as story only. Your old project isn't changed.

## The editor (every clip says why)

**Seven measured factors per candidate cut:**
- Cut at pauses, not mid-sound.
- Skip H3's still "settling" start.
- Smooth visual match.
- How much of the start to skip.
- **Avoid cutting during camera moves** (optical flow).
- **Cut on action**: motion direction continues across the cut (optical flow).
- **No jarring jump in motion energy** (optical flow).

**Rules it always follows:**
- Takes with hidden cuts contribute only their clean part.
- The outgoing shot's tail is never trimmed just for being quiet.
- Same-framing cuts get a jump-cut warning.

**It learns from every shot:**
- **Cut timing:** each time you nudge one of its cuts, it adjusts how much each factor counts (bounded) and how much lead-in you like skipped. Undo un-learns it, and what it learned carries over as a starting point for your other projects.
- **Lessons:** when you reject a take with a note ("too stiff"), that note goes into the direction of later shots with the same people. Notes on kept takes go in as "keep this".
- Everything learned is shown under **🧠 What the Director has learned**. *Forget lessons* and turning learning off are in Settings.

## Qwen observer

Every take is also queued for the existing Qwen3-VL observer (V0.7.4) in the original V0.6.6 job format, with a cast profile made from your people.
- **Running it:** click **Observe now** on a take, or set *Run Qwen automatically* in Settings.
- **V0.7.5.1 dispatcher:** it now waits for ComfyUI to be idle, **asks ComfyUI to unload H3**, waits until `nvidia-smi` shows enough free GPU memory, and only then starts Qwen. V0.7.5 skipped the unload, so Qwen ran out of memory right after a generation. If memory doesn't free up, it waits and tries again later instead of marking a failure.
- **Results** appear on each take as 🔎 hints in your own character names, always marked **unverified**. They never edit, approve or condition H3.

## Clean-Chain: no last-frame references, ever

Generated frames and clips are never H3 visual input. Every reference image or clip is your attested original or derived from it, and nothing is final-approved automatically.

**Pixel check on every upload.** Each upload is compared against every generated take in ComfyUI/output and this project's renders:
- **Refused:** a saved last frame (or any frame) of a take, whether resized, recompressed, brightened, mirrored or cropped to a face or upper body, and a take re-encoded to look like a phone video.
- **Accepted:** your real originals still pass.

**Requests in words.** "Use the last frame…", "start from the final frame" and "a screenshot of the previous clip" are refused and shown as *Not done*. Editing phrases like "cut at the last frame" are not refused.

## Tested vs not tested

**Tested offline:**
- **Browser end-to-end:** the real `/director` page against ComfyUI's aiohttp 3.11 server, with the custom node loaded the way ComfyUI loads it, and a fake ComfyUI that writes real clips with embedded workflows and rejects missing input files.
- **Robustness:** synthesized phone media (rotations, HEVC 4K60 .mov, VP9, alpha, greyscale, tiny, two-person) and a second location for the clean/raw choice.
- **Real footage:** media detection on your takes (finds the F101 hidden cut, no false cuts; the optical-flow spike sits exactly at F101).
- **Qwen compatibility:** Studio jobs run through the real V0.7.5.1 dispatcher and the real V0.7.2.2 validators.
- **Inherited:** the original Qwen-chain suites (252 tests) and the V0.7.39 suites.

**Not verified until you run it on Vast:**
- The page opening through your Vast link.
- A real H3 job.
- **7–9 image refs and video refs**: your instance has only ever run 6 image refs and no video refs. The fallback covers missing video nodes, but how well H3 uses video refs is untested.
- LoRAs with H3.
- **H3's continuation mode.** Whether the new shot really starts at your video's last frame is untested: the prompt follows the official guide, but nobody has run it on your instance yet.
- **The official format vs the classic wording on real H3.** Try an A/B test.
- **Caption removal on real phone footage.** It was tested on your footage with synthetic captions.
- **Importing the export files into Resolve, Premiere or Final Cut.** The files are generated to the published formats and parsed here, but no editor was available to open them.
- **J/L cuts by ear.** The renders are verified frame- and sample-exact, but nobody has listened to them yet.
- **Identity scores on new footage.** The thresholds come from your two real people only.
- **Whether H3 uses the numbered mapping.** It's plain prompt text; compare a two-person shot with it on and off.
- **The background from the people's own photos** with real H3, and multi-person separation on your own footage. It was tested here only on your two real two-person takes and synthesized media. No new faces could be downloaded in this sandbox, so the "from scratch" project test reuses your real people under new names and places.
- **Real Qwen inference** after the new memory hand-over.
- ComfyUI's own upload size limit (default 100 MB) may block long phone videos. Trim them, or start ComfyUI with `--max-upload-size`.

## Still here
`run_director.sh "make her react"` (V0.7.39 CLI for the hallway project), the V0.7.35/36/39 ledgers, `save_memory_backup.sh`, and `README_V0739.md`.
