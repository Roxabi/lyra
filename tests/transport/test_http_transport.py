import pytest

from lyra.transport._result import Err
from lyra.transport.http_transport import HttpTransport


@pytest.mark.asyncio
async def test_call_returns_err_placeholder():
    t = HttpTransport()
    r = await t.call("any.subject", b"")
    assert isinstance(r, Err)
    assert r.error.code == "NOT_WIRED"


@pytest.mark.asyncio
async def test_publish_raises():
    with pytest.raises(NotImplementedError, match="codec-level request/response"):
        await HttpTransport().publish("s", b"", reply_subject="r")


def test_open_inbox_raises_with_consensus_message():
    with pytest.raises(NotImplementedError, match=r"use codec-level SSE\."):
        HttpTransport().open_inbox()
