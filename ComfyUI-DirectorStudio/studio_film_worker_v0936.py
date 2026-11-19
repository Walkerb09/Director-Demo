"""Runs in the Qwen environment (h3-qwen-venv): 🎬 one film = the outline, then every shot written out, with ONE model load,
then exits (frees the GPU). Progress goes to req['progress'] after every step.
V0.9.39 🧠: with req['think'] (a Qwen3 Thinking model) each step thinks first, within a budget (studio_thinking_v0939);
V0.9.40 🎯: a step that thinks gets the H3 checklist to think through (th.check_for).
V0.9.37: what is written so far is saved to req['partial'] after every step, so a stop part-way (ComfyUI got work, a time
limit, a crash) keeps the shots already written. With req['shots_only'] (a list of shot requests) it writes just those
shots again, without a new outline.
usage: python -I studio_film_worker_v0936.py request.json out.json"""
import json, os, sys, time

def _atomic(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f: json.dump(obj, f)
    os.replace(tmp, path)

def main():
    req = json.load(open(sys.argv[1]))
    sys.path.insert(0, req['package_dir'])          # -I leaves the script's folder out; the film module is pure Python
    import studio_film_v0936 as film, studio_thinking_v0939 as th
    prog = req.get('progress'); partial = req.get('partial')
    think = req.get('think') or None                 # V0.9.39 🧠 {'outline': tokens, 'shots': tokens} for a Thinking model
    phase = {'now': None, 'last': {}, 'live': None}
    control = req.get('control')                     # V0.9.43 🧠✋ the page writes your notes here; the thinking is shown live
    def report(**kw):
        phase['last'] = kw
        if prog: _atomic(prog, {**kw, **({'thinking': phase['now'] == 'thinking', 'think': True} if think else {}),
                                **({'live': phase['live'], 'can_interject': True} if control else {}), 'at': time.time()})
    seen = set(); peek = {'t': 0.0, 'any': False}; poll_s = float(req.get('_poll_s', 0.5))
    def _items():
        try: items = json.load(open(control))
        except (OSError, ValueError): return []
        return [c for c in items if isinstance(c, dict) and c.get('id') not in seen] if isinstance(items, list) else []
    def pending():                                   # is a note / ⏩ waiting? (checked while Qwen generates, at most every poll_s)
        if not control: return False
        now = time.time()
        if now - peek['t'] >= poll_s: peek['t'] = now; peek['any'] = bool(_items())
        return peek['any']
    def poll():                                      # the next waiting item, taken (each one is used once)
        if not control: return None
        items = _items(); peek['any'] = len(items) > 1; peek['t'] = time.time()
        if not items: return None
        c = items[0]; seen.add(c.get('id'))
        return {'kind': c.get('kind'), 'text': str(c.get('text') or '')[:300]} if c.get('kind') in ('note', 'finish') else None
    notes_out = []
    def keep(result):
        if partial: _atomic(partial, result)
    report(stage='loading', done=0, total=0)
    import torch
    import transformers
    _tk = lambda on: ({'enable_thinking': bool(on)} if _new else {})   # 🧠 on only for a step that thinks (V0.9.55 Qwen3.6)
    from transformers import AutoProcessor
    _new = any(k in str(req['model_id']).lower() for k in ('qwen3.5', 'qwen3.6'))     # V0.9.55 Qwen3.6: vision built in, transformers 5
    Qwen3VLForConditionalGeneration = ((getattr(transformers, 'AutoModelForMultimodalLM', None) or getattr(transformers, 'AutoModelForImageTextToText'))
                                       if _new else transformers.Qwen3VLForConditionalGeneration)
    if not torch.cuda.is_available(): raise SystemExit('QWEN_CUDA_UNAVAILABLE')
    processor = AutoProcessor.from_pretrained(req['model_id'])
    model = Qwen3VLForConditionalGeneration.from_pretrained(req['model_id'], dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa')
    model.eval()
    live = {'step': None, 'parts': [], 't': 0.0}
    def show(force=False):
        if not control or not live['parts']: return
        now = time.time()
        if not force and now - live['t'] < 1.5: return
        live['t'] = now
        text = ''.join(processor.decode(x, skip_special_tokens=True, clean_up_tokenization_spaces=False) if k == 'ids' else x for k, x in live['parts'])
        phase['live'] = {'step': live['step'], 'phase': phase['now'], 'text': text[-6000:]}
        if phase['last']: report(**phase['last'])
    def say(text):                                   # your note, shown in the live text where it went in
        live['parts'].append(('text', f'\n\n✋ YOUR NOTE: {text}\n\n')); show(force=True)
    class Streamer:                                  # transformers' streamer interface: put(token ids), end()
        def __init__(self): self.first = True
        def put(self, value):
            if self.first: self.first = False; return                # generate hands over the prompt first
            v = value.tolist() if hasattr(value, 'tolist') else value
            while isinstance(v, list) and v and isinstance(v[0], list): v = [y for x in v for y in x]
            v = v if isinstance(v, list) else [v]
            if not live['parts'] or live['parts'][-1][0] != 'ids': live['parts'].append(('ids', []))
            live['parts'][-1][1].extend(int(x) for x in v); show()
        def end(self): show(force=True)
    def watch():                                     # extra generate() arguments: stream the text, stop when you step in
        if not control: return {}
        import transformers as _tf
        class Stop(getattr(_tf, 'StoppingCriteria', object)):
            def __call__(self, input_ids, scores=None, **kw):
                flag = pending()
                try: return torch.full((input_ids.shape[0],), flag, dtype=torch.bool, device=input_ids.device)
                except Exception: return flag
        lst = getattr(_tf, 'StoppingCriteriaList', list)
        return {'streamer': Streamer(), 'stopping_criteria': lst([Stop()])}
    def ask(r):
        content = []
        if r.get('images'):
            from PIL import Image
            for label, path in r['images']:
                content.append({'type': 'text', 'text': f'PICTURE: {label}'})
                content.append({'type': 'image', 'image': Image.open(path).convert('RGB')})
        content.append({'type': 'text', 'text': r['user']})
        system = r['system']
        if think and int(r.get('think_budget') or 0) > 0: system += th.check_for(r.get('_step'), req.get('angle'))   # V0.9.40 🎯 think against H3
        step = r.get('_step', '?'); live['step'] = step; live['parts'] = []; phase['live'] = None
        step_thinks = bool(think and int(r.get('think_budget') or 0) > 0)
        def template(extra=''):
            cont = content if not extra else content[:-1] + [{'type': 'text', 'text': content[-1]['text'] + extra}]
            msgs = [{'role': 'system', 'content': [{'type': 'text', 'text': system}]}, {'role': 'user', 'content': cont}]
            x = processor.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt', **_tk(step_thinks))
            x.pop('token_type_ids', None); return x
        inp = template(); n = inp['input_ids'].shape[-1]
        if not think or (_new and not step_thinks):          # Qwen3.6 with thinking off answers straight away
            mine = []
            while True:                              # ✋ a note while Qwen writes (no thinking): it starts this step again with it
                with torch.inference_mode():
                    out = model.generate(**inp.to(model.device), do_sample=False, max_new_tokens=int(r.get('max_new_tokens') or 1200), repetition_penalty=1.05, **watch())
                c = poll()
                while c and c['kind'] != 'note':        # ⏩ means nothing without thinking: it just goes on writing
                    have = out[0][n:].tolist() if hasattr(out[0][n:], 'tolist') else list(out[0][n:])
                    left_ = int(r.get('max_new_tokens') or 1200) - len(have)
                    if left_ <= 0: c = None; break
                    x = dict(inp); x['input_ids'] = torch.tensor([out[0].tolist() if hasattr(out[0], 'tolist') else list(out[0])], device=model.device)
                    x['attention_mask'] = torch.ones_like(x['input_ids'])
                    for k, v in inp.items():
                        if k not in ('input_ids', 'attention_mask') and hasattr(v, 'dim') and v.dim() == 2 and v.shape[-1] == n:
                            x[k] = torch.cat([v, torch.zeros((1, x['input_ids'].shape[-1] - n), dtype=v.dtype, device=v.device)], dim=-1)
                    with torch.inference_mode():
                        out = model.generate(**x, do_sample=False, max_new_tokens=left_, repetition_penalty=1.05, **watch())
                    c = poll()
                if c and c['kind'] == 'note' and c['text'].strip():
                    mine.append(' '.join(c['text'].split())); notes_out.append({'step': step, 'text': mine[-1]}); say(mine[-1])
                    inp = template('\n\n' + film.NOTES_HEAD + ' '.join(f'({k + 1}) {x}' for k, x in enumerate(mine))); n = inp['input_ids'].shape[-1]
                    continue
                return processor.decode(out[0][n:], skip_special_tokens=True, clean_up_tokenization_spaces=False).strip()
        # V0.9.39 🧠: think (up to the budget), then answer with the usual limit; only the answer is returned
        inp = inp.to(model.device); tok = processor.tokenizer
        end_id = tok.convert_tokens_to_ids('</think>')
        eos = model.generation_config.eos_token_id; eos = list(eos) if isinstance(eos, (list, tuple)) else ([eos] if eos is not None else [])
        def gen(ids, max_new, stop_ids, sample, seed):
            x = dict(inp); x['input_ids'] = torch.tensor([ids], device=model.device); x['attention_mask'] = torch.ones_like(x['input_ids'])
            for k, v in inp.items():                  # any other per-token input (e.g. which tokens are picture tokens): extend with 0s
                if k not in ('input_ids', 'attention_mask') and hasattr(v, 'dim') and v.dim() == 2 and v.shape[-1] == n and len(ids) > n:
                    x[k] = torch.cat([v, torch.zeros((1, len(ids) - n), dtype=v.dtype, device=v.device)], dim=-1)
            kw = dict(th.SAMPLE) if sample else {'do_sample': False}
            if sample: torch.manual_seed(seed)
            with torch.inference_mode():
                o = model.generate(**x, max_new_tokens=max_new, eos_token_id=eos + list(stop_ids), repetition_penalty=1.05, **kw, **watch())
            return o[0][len(ids):].tolist()
        def on_phase(p):
            phase['now'] = p
            if phase['last']: report(**phase['last'])
        common = dict(think_end_id=end_id, encode=lambda t: tok.encode(t, add_special_tokens=False),
                      budget=int(r.get('think_budget') or 0), answer_tokens=int(r.get('max_new_tokens') or 1200),
                      seed=th.SEED + (step if isinstance(step, int) else 0), on_phase=on_phase, end_of_turn=tuple(eos))
        if control:                                  # V0.9.43 🧠✋ you can step in
            got = th.think_live(gen, inp['input_ids'][0].tolist(), poll=poll, on_note=say, **common)
            notes_out.extend({'step': step, 'text': x} for x in got['notes'])
        else: got = th.think_then_answer(gen, inp['input_ids'][0].tolist(), **common)
        cut_at = th.after_think(got['ids'], end_id)          # split on the '</think>' TOKEN (decoding may drop it as a special token)
        dec = lambda ids: processor.decode(ids, skip_special_tokens=True, clean_up_tokenization_spaces=False)
        thoughts, answer = dec(got['ids'][:max(0, cut_at - 1)]).strip(), dec(got['ids'][cut_at:]).strip()
        stats.append({'step': step, 'thought_tokens': got['thought_tokens'], 'cut': got['cut'], 'skipped': got['skipped']})
        if req.get('think_log') and thoughts:
            try:
                with open(req['think_log'], 'a') as f: f.write(f'===== {step} ({got["thought_tokens"]} tokens{", cut short" if got["cut"] else ""})\n{thoughts}\n\n')
            except OSError: pass
        return answer
    stats = []
    if think and req.get('think_log'):
        try: open(req['think_log'], 'w').close()
        except OSError: pass

    if req.get('shots_only'):                        # ✍ write these shots again (the outline is already in the film)
        shots = req['shots_only']; result = {'model_id': req['model_id'], 'shot_replies': [], **({'thinking': stats} if think else {})}
        nums = req.get('numbers') or list(range(1, len(shots) + 1))
        for i, r in enumerate(shots):
            report(stage='shots', done=i, total=len(shots)); keep(result)
            r = {**r, '_step': int(nums[i]) if i < len(nums) else i + 1}           # V0.9.43: every step knows which it is (for your notes)
            if think: r = {**r, 'think_budget': think.get('shots', 0)}
            result['shot_replies'].append(ask(r))
            if notes_out: result['notes'] = list(notes_out)
        keep(result); report(stage='done', done=len(shots), total=len(shots))
        with open(sys.argv[2], 'x') as f: json.dump(result, f)
        print('FILM_SHOTS_DONE', len(shots), flush=True); return

    report(stage='outline', done=0, total=0)
    outline_reply = ask({**req['outline'], 'think_budget': think.get('outline', 0), '_step': 'outline'} if think else {**req['outline'], '_step': 'outline'})
    result = {'model_id': req['model_id'], 'outline_reply': outline_reply, 'shot_replies': [], **({'thinking': stats} if think else {})}
    try:
        outline = film.check_outline(outline_reply, names=req['names'], places=req['place_names'], total=req['total'], angle=req.get('angle'))
    except ValueError as e:
        result['outline_error'] = str(e)
        with open(sys.argv[2], 'x') as f: json.dump(result, f)
        print('FILM_OUTLINE_UNUSABLE', e, flush=True); return
    if notes_out: result['notes'] = list(notes_out)
    keep(result)
    shots = film.shot_requests(outline, cast=req['cast'], motion=req.get('motion', 'lively'), model=req['model_id'], angle=req.get('angle'),
                               opens_on=({i: req['opening_picture'] for i in range(len(outline['shots']))} if req.get('pin_all') else {0: req['opening_picture']})
                               if req.get('opening_picture') else None,                                        # V0.9.38 🎞 / V0.9.48 every shot
                               lead_in=req.get('lead_in'))                                                     # ⏱ V0.9.48
    plan_notes = [x['text'] for x in notes_out if x['step'] == 'outline']
    if plan_notes: shots = [film.with_notes(r, plan_notes) for r in shots]          # V0.9.43 ✋ your notes on the plan reach every shot
    for i, r in enumerate(shots):
        report(stage='shots', done=i, total=len(shots), title=outline['title'])
        r = {**r, '_step': i + 1}
        if think: r = {**r, 'think_budget': think.get('shots', 0)}
        result['shot_replies'].append(ask(r))
        if notes_out: result['notes'] = list(notes_out)
        keep(result)
    report(stage='done', done=len(shots), total=len(shots), title=outline['title'])
    with open(sys.argv[2], 'x') as f: json.dump(result, f)
    print('FILM_DONE', len(shots), flush=True)

if __name__ == '__main__':
    main()
