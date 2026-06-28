from harness.tui.messages import FleetUpdated
from harness.tui.state import initial_snapshot


def test_fleet_updated_carries_snapshot():
    fs = initial_snapshot()
    msg = FleetUpdated(fs)
    assert msg.snapshot is fs
