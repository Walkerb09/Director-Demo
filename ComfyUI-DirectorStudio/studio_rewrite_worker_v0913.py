"""Runs in the Qwen environment (h3-qwen-venv): one rewrite with Qwen3-VL (your notes, plus labelled pictures when
given), then exits (frees the GPU).
usage: python -I studio_rewrite_worker_v0913.py request.json out.json"""
import json, sys

def main():
    req = json.load(open(sys.argv[1]))
    import torch
    import transformers
    _tk = {}
    from transformers import AutoProcessor
    _new = any(k in str(req['model_id']).lower() for k in ('qwen3.5', 'qwen3.6'))     # V0.9.55 Qwen3.6: vision built in, transformers 5
    Qwen3VLForConditionalGeneration = ((getattr(transformers, 'AutoModelForMultimodalLM', None) or getattr(transformers, 'AutoModelForImageTextToText'))
                                       if _new else transformers.Qwen3VLForConditionalGeneration)
    if not torch.cuda.is_available(): raise SystemExit('QWEN_CUDA_UNAVAILABLE')
    if _new: _tk = {'enable_thinking': False}   # a quick, direct answer
    processor = AutoProcessor.from_pretrained(req['model_id'])
    model = Qwen3VLForConditionalGeneration.from_pretrained(req['model_id'], dtype=torch.bfloat16, device_map={'': 0}, attn_implementation='sdpa')
    model.eval()
    steps = {}
    for ps in req.get('pre_steps') or []:     # step 1 (V0.9.23): each pinned picture ALONE in front of Qwen -> its description
        from PIL import Image
        msgs1 = [{'role': 'user', 'content': [{'type': 'image', 'image': Image.open(ps['image']).convert('RGB')}, {'type': 'text', 'text': ps['prompt']}]}]
        inp1 = processor.apply_chat_template(msgs1, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt', **_tk)
        inp1.pop('token_type_ids', None); n1 = inp1['input_ids'].shape[-1]
        with torch.inference_mode():
            o1 = model.generate(**inp1.to(model.device), do_sample=False, max_new_tokens=300, repetition_penalty=1.05)
        text = ' '.join(processor.decode(o1[0][n1:], skip_special_tokens=True, clean_up_tokenization_spaces=False).split())[:900]
        steps[ps['key']] = text
        req['user'] = req['user'].replace('{' + ps['key'] + '}', text)
    content = []
    if req.get('images'):
        from PIL import Image
        for label, path in req['images']:
            content.append({'type': 'text', 'text': f'PICTURE: {label}'})
            content.append({'type': 'image', 'image': Image.open(path).convert('RGB')})
    content.append({'type': 'text', 'text': req['user']})
    msgs = [{'role': 'system', 'content': [{'type': 'text', 'text': req['system']}]}, {'role': 'user', 'content': content}]
    inp = processor.apply_chat_template(msgs, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors='pt', **_tk)
    inp.pop('token_type_ids', None)
    n = inp['input_ids'].shape[-1]
    with torch.inference_mode():
        out = model.generate(**inp.to(model.device), do_sample=False, max_new_tokens=1200, repetition_penalty=1.05)
    reply = processor.decode(out[0][n:], skip_special_tokens=True, clean_up_tokenization_spaces=False).strip()
    with open(sys.argv[2], 'x') as f: json.dump({'reply': reply, 'model_id': req['model_id'], 'steps': steps}, f)
    print('REWRITE_DONE', len(reply), flush=True)

if __name__ == '__main__':
    main()
