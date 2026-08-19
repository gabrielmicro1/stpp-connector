"""Make scripts/ importable so tests can import seed_rfff directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
