"""Fine-tune a multilingual transformer to predict the caller's emotion per turn.

Usage:
  python scripts/train.py --data data/synthetic --model xlm-roberta-base --epochs 3
Data = any folder with train/val/test .jsonl in the schema of app/schema.py
(synthetic now, real Kafka-exported data later - same command).
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import torch
from datasets import load_dataset
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.emotions import EMOTION_TO_ID, EMOTIONS, ID_TO_EMOTION  # noqa: E402


def to_input(row):
    """Text plus the orchestrator signals, as a short prefix the model can read."""
    sig = f"[fails={min(row['slot_failures'], 3)}]" + ("[barge]" if row["barge_in"] else "")
    return f"{sig} {row['text']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/synthetic")
    ap.add_argument("--model", default="xlm-roberta-base")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--max-len", type=int, default=64)
    ap.add_argument("--out", default="models")
    a = ap.parse_args()

    ds = load_dataset("json", data_files={s: f"{a.data}/{s}.jsonl" for s in ("train", "val", "test")})
    tok = AutoTokenizer.from_pretrained(a.model)

    def prep(row):
        enc = tok(to_input(row), truncation=True, max_length=a.max_len)
        enc["labels"] = EMOTION_TO_ID[row["emotion"]]
        return enc

    ds = ds.map(prep, remove_columns=ds["train"].column_names)

    model = AutoModelForSequenceClassification.from_pretrained(
        a.model, num_labels=len(EMOTIONS), id2label=ID_TO_EMOTION, label2id=EMOTION_TO_ID)

    def metrics(p):
        return {"macro_f1": f1_score(p.label_ids, p.predictions.argmax(-1), average="macro")}

    version = datetime.now().strftime("%Y%m%d-%H%M")
    out = Path(a.out) / version
    args = TrainingArguments(
        output_dir=str(out / "ckpt"), num_train_epochs=a.epochs,
        per_device_train_batch_size=a.batch, per_device_eval_batch_size=32,
        learning_rate=a.lr, weight_decay=0.01, warmup_steps=0.1,  # <1 = fraction of total steps (transformers v5)
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="macro_f1", save_total_limit=1,
        fp16=torch.cuda.is_available(), report_to="none", logging_steps=50)
    trainer = Trainer(model=model, args=args, train_dataset=ds["train"], eval_dataset=ds["val"],
                      processing_class=tok, compute_metrics=metrics)
    trainer.train()

    pred = trainer.predict(ds["test"])
    y_pred, y_true = pred.predictions.argmax(-1), pred.label_ids
    report = classification_report(y_true, y_pred, target_names=EMOTIONS, output_dict=True, zero_division=0)
    result = {"version": version, "base_model": a.model, "data": a.data,
              "macro_f1": report["macro avg"]["f1-score"], "report": report,
              "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(), "labels": EMOTIONS}
    final = out / "model"
    trainer.save_model(str(final))
    tok.save_pretrained(str(final))
    (out / "metrics.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nTest macro-F1: {result['macro_f1']:.3f}  ->  saved to {final}")


if __name__ == "__main__":
    main()
