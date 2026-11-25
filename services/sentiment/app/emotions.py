"""The emotion classes the model predicts. Order matters: index = class id."""

EMOTIONS = [
    "neutral",
    "satisfied",
    "confused",
    "frustrated",
    "angry",
    "anxious",
    "sad",
]
EMOTION_TO_ID = {e: i for i, e in enumerate(EMOTIONS)}
ID_TO_EMOTION = {i: e for e, i in EMOTION_TO_ID.items()}

# Emotions that should raise an escalation-risk flag at call level.
NEGATIVE_HIGH = {"frustrated", "angry"}
