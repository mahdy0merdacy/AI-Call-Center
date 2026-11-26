"""history.py and its scenarios import from data/simulator; history.py itself puts
the orchestrator (services/orchestrator) and libs/ on the path.

    .venv/bin/python -m pytest data/simulator/tests
"""

import sys
from pathlib import Path

SIM = Path(__file__).resolve().parents[1]
if str(SIM) not in sys.path:
    sys.path.insert(0, str(SIM))
