"""Try the model by hand.

  python scripts/predict.py                      # interactive: type sentences
  python scripts/predict.py "I am so tired of this"  # one sentence
  python scripts/predict.py --fails 2 --barge "Again?"   # with orchestrator signals
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.predictor import EmotionPredictor  # noqa: E402


def show(p, text, fails, barge):
    res = p.predict(text, fails, barge)
    print("  " + "   ".join(f"{r['emotion']} {r['score']:.2f}" for r in res))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?")
    ap.add_argument("--model", default=None, help="model dir (default: newest in models/)")
    ap.add_argument("--fails", type=int, default=0)
    ap.add_argument("--barge", action="store_true")
    a = ap.parse_args()

    p = EmotionPredictor(a.model)
    print(f"model: {p.model_dir}")
    if a.text:
        show(p, a.text, a.fails, a.barge)
        return
    print("Type a sentence (Ctrl+C or empty line to quit).")
    while True:
        try:
            t = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not t:
            break
        show(p, t, a.fails, a.barge)


if __name__ == "__main__":
    main()
