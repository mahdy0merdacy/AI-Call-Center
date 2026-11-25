"""Load a fine-tuned emotion model and predict. Reused by the CLI and the service."""
from pathlib import Path

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def latest_model_dir(models_root: str = "models") -> Path:
    """Newest models/<version>/model folder."""
    versions = sorted(p for p in Path(models_root).glob("*/model") if p.is_dir())
    if not versions:
        raise FileNotFoundError(
            f"No trained model under {models_root}/ - run scripts/train.py first")
    return versions[-1]


def to_input(text: str, slot_failures: int = 0, barge_in: bool = False) -> str:
    """Must match scripts/train.py exactly (signals as a prefix)."""
    sig = f"[fails={min(slot_failures, 3)}]" + ("[barge]" if barge_in else "")
    return f"{sig} {text}"


class EmotionPredictor:
    def __init__(self, model_dir: str | Path | None = None, max_len: int = 64):
        model_dir = Path(model_dir) if model_dir else latest_model_dir()
        self.model_dir = model_dir
        self.max_len = max_len
        self.tok = AutoTokenizer.from_pretrained(model_dir)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_dir).eval()
        self.id2label = self.model.config.id2label

    @torch.inference_mode()
    def predict(self, text: str, slot_failures: int = 0, barge_in: bool = False, top_k: int = 3):
        enc = self.tok(to_input(text, slot_failures, barge_in), truncation=True,
                       max_length=self.max_len, return_tensors="pt")
        probs = self.model(**enc).logits.softmax(-1)[0]
        top = probs.topk(min(top_k, len(probs)))
        return [{"emotion": self.id2label[int(i)], "score": round(float(s), 4)}
                for s, i in zip(top.values, top.indices, strict=True)]
