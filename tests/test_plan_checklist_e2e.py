"""End-to-end regression guard for the plan checklist: the full chain from the
agent's intercepted `plan ...` sentinel command through render → reduce → the
widget's show decision. Spans modules, so it lives on its own."""

import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

from harness.acp_emit import parse_plan_command, plan_update
from harness.tui.render import render_update
from harness.tui.state import reduce, initial_snapshot, TurnStarted, ItemReceived
from harness.tui.widgets.activity_region import ActivityRegion


def _emit_plan(fs, cmd):
    """Mimic the live path: parse the sentinel command, build the ACP update,
    render it, and fold it into the snapshot."""
    entries = parse_plan_command(cmd)
    assert entries is not None
    item = render_update(plan_update(entries))
    return reduce(fs, ItemReceived(item))


def test_plan_command_drives_checklist_end_to_end():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = _emit_plan(fs, 'plan "Push + PR:in_progress" "CI + merge:pending" "Sync + prune:pending"')

    assert [(t.label, t.status) for t in fs.active.plan] == [
        ("Push + PR", "in_progress"),
        ("CI + merge", "pending"),
        ("Sync + prune", "pending"),
    ]
    assert ActivityRegion.show_plan(fs.active) is True


def test_replan_replaces_and_ticks_off():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = _emit_plan(fs, 'plan "A:in_progress" "B:pending"')
    fs = _emit_plan(fs, 'plan "A:completed" "B:in_progress"')
    assert [(t.label, t.status) for t in fs.active.plan] == [("A", "done"), ("B", "in_progress")]


def test_next_turn_clears_the_checklist():
    fs = reduce(initial_snapshot(), TurnStarted())
    fs = _emit_plan(fs, 'plan "A:completed"')
    assert fs.active.plan != ()
    fs = reduce(fs, TurnStarted())
    assert fs.active.plan == ()
    assert ActivityRegion.show_plan(fs.active) is False
