from harness.tui.roster import persona_rows, PersonaRow
from harness.tui.state import AgentState


def _names(d):
    return lambda pid: d.get(pid)


def test_active_row_carries_status_others_idle():
    rows = persona_rows(["default", "fred"], "fred", _names({}),
                        active_status=AgentState.RUNNING_TOOL)
    by_id = {r.id: r for r in rows}
    assert by_id["fred"].status == AgentState.RUNNING_TOOL    # active = real state
    assert by_id["default"].status == AgentState.IDLE         # others idle


def test_status_defaults_to_idle_when_not_passed():
    rows = persona_rows(["fred"], "fred", _names({}))
    assert rows[0].status == AgentState.IDLE


def test_personarow_still_constructs_with_three_positional_fields():
    # back-compat: existing call sites + equality assertions must keep working
    assert PersonaRow(id="x", name="X", active=True).status == AgentState.IDLE


def test_rows_compose_with_names_and_active_flag():
    rows = persona_rows(["default", "fred"], "fred", _names({"fred": "Fred R."}))
    assert rows == (
        PersonaRow(id="default", name="default", active=False),
        PersonaRow(id="fred", name="Fred R.", active=True),
    )

def test_name_falls_back_to_id_when_name_of_returns_none():
    rows = persona_rows(["fred"], "fred", _names({}))
    assert rows == (PersonaRow(id="fred", name="fred", active=True),)

def test_active_id_always_appears_even_if_absent_from_personas():
    # invariant: the active persona must always be a row, appended if missing
    rows = persona_rows(["default"], "ghost", _names({}))
    assert PersonaRow(id="ghost", name="ghost", active=True) in rows
    assert rows[-1].id == "ghost"          # appended last
    assert [r.id for r in rows] == ["default", "ghost"]

def test_no_duplicate_when_active_in_personas():
    rows = persona_rows(["default", "fred"], "default", _names({}))
    assert [r.id for r in rows] == ["default", "fred"]   # no dup
    assert sum(r.active for r in rows) == 1              # exactly one active

def test_order_preserved():
    rows = persona_rows(["b", "a", "c"], "a", _names({}))
    assert [r.id for r in rows] == ["b", "a", "c"]
