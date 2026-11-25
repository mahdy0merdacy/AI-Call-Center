from app.summary import summarize


def test_empty_call():
    s = summarize([])
    assert s["turns"] == 0 and s["dominant"] is None and not s["escalation_risk"]


def test_happy_call_improves():
    s = summarize(["neutral", "confused", "neutral", "satisfied"])
    assert s["trend"] == "improving"
    assert s["last"] == "satisfied"
    assert s["dominant"] == "neutral"
    assert not s["escalation_risk"]


def test_angry_end_is_escalation_risk():
    s = summarize(["neutral", "frustrated", "angry"])
    assert s["trend"] == "worsening"
    assert s["escalation_risk"]


def test_two_upset_turns_is_risk_even_if_calm_at_end():
    assert summarize(["angry", "frustrated", "neutral"])["escalation_risk"]


def test_one_upset_turn_resolved_is_not_risk():
    assert not summarize(["neutral", "frustrated", "neutral", "satisfied"])["escalation_risk"]


def test_counts():
    assert summarize(["sad", "sad", "neutral"])["counts"] == {"sad": 2, "neutral": 1}
