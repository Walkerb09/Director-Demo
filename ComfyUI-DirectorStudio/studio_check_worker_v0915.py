"""Runs in the Qwen environment (h3-qwen-venv): Qwen3-VL looks at frames of each take and answers its checklist.
One model load for the whole batch, then exits (frees the GPU).
usage: python -I studio_check_worker_v0915.py request.json out.json"""
import json, sys

def main():
    req = json.load(open(sys.argv[1]))
    import torch
    from PIL import Image
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
    replies = {}
    for job in req['jobs']:
        r = job['request']; content = []
        for t, path in r['frames']:
            content.append({'type': 'text', 'text': t if isinstance(t, str) else f'Frame at {t:.1f} s'})   # V0.9.36: a labelled picture
            content.append({'type': 'image', 'image': Image.open(path).convert('RGB')})
        content.append({'type': 'text', 'text': r['prompt']})
        inp = processor.apply_chat_template([{'role': 'user', 'content': content}], tokenize=True, add_generation_prompt=True, **_tk,
                                            return_dict=True, return_tensors='pt')
        inp.pop('token_type_ids', None)
        n = inp['input_ids'].shape[-1]
        with torch.inference_mode():
            out = model.generate(**inp.to(model.device), do_sample=False, max_new_tokens=int(req.get('max_new_tokens') or 900))   # V0.9.55 room for the frame notes
        replies[job['key']] = processor.decode(out[0][n:], skip_special_tokens=True, clean_up_tokenization_spaces=False).strip()
        print('CHECK_DONE', job['key'], flush=True)
    with open(sys.argv[2], 'x') as f: json.dump({'replies': replies, 'model_id': req['model_id']}, f)

if __name__ == '__main__':
    main()
