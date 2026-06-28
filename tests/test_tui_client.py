import asyncio
from types import SimpleNamespace as NS

from acp.schema import AllowedOutcome, DeniedOutcome, PermissionOption

from harness.tui.client import TuiClient
from harness.tui.messages import SessionUpdate, PermissionRequest


class _FakeApp:
    """Records posted Textual messages without a running app."""
    def __init__(self, gen=7):
        self.posted = []
        self._gen = gen
    def post_message(self, msg):
        self.posted.append(msg)


def test_session_update_posts_message():
    app = _FakeApp(gen=7)
    client = TuiClient(app)
    update = NS(field_meta=None)
    asyncio.run(client.session_update("sid", update))
    assert len(app.posted) == 1
    assert isinstance(app.posted[0], SessionUpdate)
    assert app.posted[0].update is update
    # the client stamps the app's CURRENT generation so the freshness filter is live
    assert app.posted[0].gen == 7
    assert app.posted[0].session_id == "sid"


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


def test_request_permission_reject_option_denies():
    """Regression test: resolving future with reject_once option_id must deny.

    Before the fix, if option_id was truthy (e.g., "reject_once"), the code would
    wrap it in AllowedOutcome and the agent would allow the command. This test
    ensures that reject-kind options result in DeniedOutcome.
    """
    app = _FakeApp()
    client = TuiClient(app)
    opts = [
        PermissionOption(kind="allow_once", name="Allow once", option_id="allow_once"),
        PermissionOption(kind="reject_once", name="Reject", option_id="reject_once"),
    ]

    async def go():
        task = asyncio.ensure_future(
            client.request_permission(options=opts, session_id="sid", tool_call=NS())
        )
        await asyncio.sleep(0)
        req = app.posted[-1]
        assert isinstance(req, PermissionRequest)
        req.future.set_result("reject_once")        # user chose reject
        return await task

    resp = asyncio.run(go())
    assert isinstance(resp.outcome, DeniedOutcome)
    assert resp.outcome.outcome == "cancelled"
