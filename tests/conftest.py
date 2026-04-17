import json
from pathlib import Path
import pytest
from src.types import OHLC

FIXTURES = Path(__file__).parent / "fixtures"

@pytest.fixture
def load_ohlc():
    def _load(name: str) -> list[OHLC]:
        data = json.loads((FIXTURES / f"{name}.json").read_text())
        return [OHLC(**row) for row in data]
    return _load
