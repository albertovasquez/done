import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'upstream' / 'src'))
sys.path.insert(0, str(REPO))

from harness.tui.app import HarnessTui

app = HarnessTui(agent_cmd=['x'], cwd='.', model='mock', version='0.5.0')
markup = app._header_markup()
print(markup)
assert 'DON≡' in markup, f"Expected branded title 'DON≡' in header markup, got: {markup!r}"
assert 'DONE' not in markup, f"Unexpected unbranded title in header markup: {markup!r}"
print('header branding check passed')
