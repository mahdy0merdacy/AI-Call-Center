"""One caller turn. Same fields the real Kafka events will carry, so real data
can replace the synthetic data without code changes. `emotion` is the label
(synthetic now, human-reviewed later)."""
from dataclasses import asdict, dataclass


@dataclass
class Turn:
    call_id: str
    tenant_slug: str
    locale: str          # en-US / fr-FR / ar
    turn_idx: int
    text: str            # final STT transcript of the caller
    slot_failures: int   # failed slot attempts so far in this call
    barge_in: bool       # caller interrupted the agent on this turn
    emotion: str         # label

    def to_dict(self) -> dict:
        return asdict(self)
