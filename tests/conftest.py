import datetime as dt

import pytest

from voice_labs.skills import clock


@pytest.fixture(autouse=True)
def clock_now(monkeypatch):
    """The clock skill's now, the same in every test: 4:20 PM, so 45 minutes
    from now is 5:05 PM and 5 PM is 40 minutes away."""
    monkeypatch.setattr(clock, "now", lambda: dt.datetime(2026, 10, 28, 16, 20))
