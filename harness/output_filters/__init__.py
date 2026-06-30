"""Done-owned output filters: pure (command, output, returncode) -> str|None
functions that compact verbose command output before it reaches the model.
Fail-open by contract — see dispatch.filter_output."""
