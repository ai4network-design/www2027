"""Dependency-free LoRA and soft-prompt adapters for the two-stage compiler (exploration only).

* ``inject_lora(model, r, alpha, targets)`` wraps the named nn.Linear modules with
  trainable low-rank updates; base weights stay frozen.
* ``SoftPrompt`` prepends ``n`` learned embedding vectors to every input (prompt
  tuning); the base model is untouched.
* ``save_adapter`` / ``load_adapter`` store only the trainable tensors.

torch only; no peft. Not used by the frozen protocol runner unless an arm names
an adapter file explicitly.
"""
import json
import math
from pathlib import Path

import torch
import torch.nn as nn


class LoRALinear(nn.Module):
    def __init__(self, base: nn.Linear, r: int, alpha: float, dropout: float = 0.0):
        super().__init__()
        self.base = base
        for p in self.base.parameters():
            p.requires_grad_(False)
        self.r = r; self.scaling = alpha / r
        self.lora_A = nn.Parameter(torch.zeros(r, base.in_features, dtype=base.weight.dtype, device=base.weight.device))
        self.lora_B = nn.Parameter(torch.zeros(base.out_features, r, dtype=base.weight.dtype, device=base.weight.device))
        nn.init.kaiming_uniform_(self.lora_A, a=math.sqrt(5))
        self.drop = nn.Dropout(dropout) if dropout else nn.Identity()

    def forward(self, x):
        return self.base(x) + (self.drop(x) @ self.lora_A.t() @ self.lora_B.t()) * self.scaling


def inject_lora(model, r=8, alpha=16, targets=('q_proj', 'v_proj', 'k_proj', 'o_proj'), dropout=0.0):
    count = 0
    for name, module in list(model.named_modules()):
        for child_name, child in list(module.named_children()):
            if isinstance(child, nn.Linear) and child_name in targets:
                setattr(module, child_name, LoRALinear(child, r, alpha, dropout)); count += 1
    return count


class SoftPrompt(nn.Module):
    """Learned prefix of ``n`` token embeddings, initialised from real token embeddings."""
    def __init__(self, embedding: nn.Embedding, n: int, init_ids=None):
        super().__init__()
        base = embedding.weight
        if init_ids is None:
            init_ids = torch.randint(0, base.shape[0], (n,))
        self.prompt = nn.Parameter(base[init_ids[:n]].detach().clone())

    def forward(self, inputs_embeds):
        b = inputs_embeds.shape[0]
        return torch.cat([self.prompt.unsqueeze(0).expand(b, -1, -1).to(inputs_embeds.dtype), inputs_embeds], dim=1)


def trainable_state(model, soft=None):
    state = {k: v.detach().cpu() for k, v in model.state_dict().items() if 'lora_A' in k or 'lora_B' in k}
    if soft is not None:
        state['soft_prompt.prompt'] = soft.prompt.detach().cpu()
    return state


def save_adapter(path, model, meta, soft=None):
    path = Path(path); path.mkdir(parents=True, exist_ok=True)
    torch.save(trainable_state(model, soft), path / 'adapter.pt')
    (path / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1))


def load_adapter(path, model):
    """Returns (meta, soft_prompt_tensor_or_None). Call inject_lora first with meta['lora'] settings."""
    path = Path(path); meta = json.loads((path / 'meta.json').read_text())
    state = torch.load(path / 'adapter.pt', map_location='cpu')
    soft = state.pop('soft_prompt.prompt', None)
    missing = model.load_state_dict({k: v.to(next(model.parameters()).device) for k, v in state.items()}, strict=False)
    unexpected = [k for k in missing.unexpected_keys]
    if unexpected:
        raise ValueError(f'adapter keys not found in model: {unexpected[:3]}')
    return meta, soft
