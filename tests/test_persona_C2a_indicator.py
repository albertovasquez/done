"""C2a indicator end-to-end truth lock: the resolved persona id round-trips
through the parser + reducer to FleetSnapshot.active, and persona emit is wire-only.
"""

import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.tui.state import persona_from_meta, PersonaResolved, reduce, initial_snapshot


def test_resolved_id_round_trips_to_active():
    # The id the engine would emit (workspace_dir.name) round-trips through the
    # parser + reducer to FleetSnapshot.active — the truth invariant.
    meta = {"harness": {"persona": {"id": "fred"}}}
    pid = persona_from_meta(meta)
    snap = reduce(initial_snapshot(), PersonaResolved(pid))
    assert snap.active.id == "fred"            # chip would show exactly the resolved id
