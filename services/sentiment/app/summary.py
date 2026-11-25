"""Turn the per-turn emotions of one call into a call-level summary.

Pure Python (no torch), so it is cheap to unit-test.
"""
from collections import Counter

from app.emotions import NEGATIVE_HIGH

# How good/bad each emotion is for the caller, from -1 (worst) to +1 (best).
VALENCE = {
    "satisfied": 1.0,
    "neutral": 0.0,
    "confused": -0.25,
    "anxious": -0.5,
    "sad": -0.5,
    "frustrated": -0.75,
    "angry": -1.0,
}
TREND_THRESHOLD = 0.25


def summarize(emotions: list[str]) -> dict:
    """emotions = one label per caller turn, in order."""
    if not emotions:
        return {"turns": 0, "dominant": None, "first": None, "last": None,
                "counts": {}, "trend": "stable", "escalation_risk": False}
    counts = Counter(emotions)
    delta = VALENCE[emotions[-1]] - VALENCE[emotions[0]]
    if delta > TREND_THRESHOLD:
        trend = "improving"
    elif delta < -TREND_THRESHOLD:
        trend = "worsening"
    else:
        trend = "stable"
    negative_turns = sum(1 for e in emotions if e in NEGATIVE_HIGH)
    return {
        "turns": len(emotions),
        "dominant": counts.most_common(1)[0][0],
        "first": emotions[0],
        "last": emotions[-1],
        "counts": dict(counts),
        "trend": trend,
        # Caller ended upset, or was upset on several turns: a human should look.
        "escalation_risk": emotions[-1] in NEGATIVE_HIGH or negative_turns >= 2,
    }
