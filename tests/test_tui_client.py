import sys
sys.path.insert(0, "upstream/src")
sys.path.insert(0, ".")

import asyncio
from types import SimpleNamespace as NS

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption

from trace.tui.client import TuiClient
from trace.tui.messages import SessionUpdate, PermissionRequest


class _FakeApp:
    """Records posted Textual messages without a running app."""
    def __init__(self):
        self.posted = []
    def post_message(self, msg):
        self.posted.append(msg)


def test_session_update_posts_message():
    app = _FakeApp()
    client = TuiClient(app)
    update = NS(field_meta=None)
    asyncio.run(client.session_update("sid", update))
    assert len(app.posted) == 1
    assert isinstance(app.posted[0], SessionUpdate)
    assert app.posted[0].update is update


def test_request_permission_allow():
    app = _FakeApp()
    client = TuiClient(app)
    opts = [PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once")]

    async def go():
        # run request_permission; resolve the future it posts with an option_id
        task = asyncio.ensure_future(
            client.request_permission(options=opts, session_id="sid", tool_call=NS())
        )
        await asyncio.sleep(0)                      # let it post + await
        req = app.posted[-1]
        assert isinstance(req, PermissionRequest)
        req.future.set_result("allow_once")         # simulate the modal button
        return await task

    resp = asyncio.run(go())
    assert isinstance(resp.outcome, AllowedOutcome)
    assert resp.outcome.outcome == "selected"
    assert resp.outcome.option_id == "allow_once"


def test_request_permission_reject():
    app = _FakeApp()
    client = TuiClient(app)

    async def go():
        task = asyncio.ensure_future(
            client.request_permission(options=[], session_id="sid", tool_call=NS())
        )
        await asyncio.sleep(0)
        app.posted[-1].future.set_result(None)      # None => reject
        return await task

    resp = asyncio.run(go())
    assert isinstance(resp.outcome, DeniedOutcome)
    assert resp.outcome.outcome == "cancelled"
