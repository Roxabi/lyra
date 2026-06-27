"""Tests for NatsSocialMediaClient (thin 3-layer composition)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.nats.socialmedia.nats_socialmedia_client import (
    NatsSocialMediaClient,
    SocialMediaUnavailableError,
)
from factory.nats.socialmedia.nats_socialmedia_codec import SocialMediaResult
from factory.transport._result import Err, Ok, SanitizedError
from roxabi_contracts.socialmedia.models import SocialMediaListGroupsResponse


def _make_pool(*, alive: bool = True) -> MagicMock:
    pool = MagicMock()
    pool.is_pool_alive.return_value = alive
    pool.start = AsyncMock()
    pool.stop = AsyncMock()
    return pool


def _ok_list_groups_response() -> SocialMediaListGroupsResponse:
    import json

    raw = json.dumps(
        {
            "contract_version": "1",
            "trace_id": "t",
            "issued_at": "2026-04-19T00:00:00+00:00",
            "ok": True,
            "request_id": "r1",
            "groups": [
                {
                    "contract_version": "1",
                    "trace_id": "t",
                    "issued_at": "2026-04-19T00:00:00+00:00",
                    "id": "g1",
                    "slug": "enichu",
                    "name": "Enichu",
                }
            ],
        }
    ).encode()
    return SocialMediaListGroupsResponse.model_validate_json(raw)


class TestNatsSocialMediaClientAvailability:
    def test_is_available_delegates_to_pool(self) -> None:
        pool = _make_pool(alive=True)
        client = NatsSocialMediaClient(pool, MagicMock())
        assert client.is_available() is True

    def test_is_available_false_when_pool_dead(self) -> None:
        pool = _make_pool(alive=False)
        client = NatsSocialMediaClient(pool, MagicMock())
        assert client.is_available() is False


class TestNatsSocialMediaClientListGroups:
    @pytest.mark.asyncio
    async def test_success_returns_response(self) -> None:
        resp = _ok_list_groups_response()
        pool = _make_pool()
        pool.request = AsyncMock(return_value=Ok(b"raw"))
        codec = MagicMock()
        codec.envelope_fields.return_value = {
            "contract_version": "1",
            "trace_id": "t",
            "issued_at": "2026-04-19T00:00:00+00:00",
            "job_id": "j1",
        }
        codec.encode.return_value = b"payload"
        codec.decode.return_value = SocialMediaResult(response=resp, error="")
        client = NatsSocialMediaClient(pool, codec)
        result = await client.list_groups()
        assert isinstance(result, SocialMediaListGroupsResponse)
        assert result.ok is True
        assert result.groups[0].slug == "enichu"

    @pytest.mark.asyncio
    async def test_codec_error_raises_unavailable(self) -> None:
        pool = _make_pool()
        pool.request = AsyncMock(return_value=Ok(b"raw"))
        codec = MagicMock()
        codec.envelope_fields.return_value = {
            "contract_version": "1",
            "trace_id": "t",
            "issued_at": "2026-04-19T00:00:00+00:00",
            "job_id": "j1",
        }
        codec.encode.return_value = b"payload"
        codec.decode.return_value = SocialMediaResult(
            response=None, error="socialmedia.worker_error"
        )
        client = NatsSocialMediaClient(pool, codec)
        with pytest.raises(SocialMediaUnavailableError, match="socialmedia.worker_error"):  # noqa: E501
            await client.list_groups()

    @pytest.mark.asyncio
    async def test_pool_err_propagates_via_codec_decode(self) -> None:
        err_result = Err(
            SanitizedError(
                code="pool.no_live_workers", message="NoLiveWorkers", retryable=True
            )
        )
        pool = _make_pool()
        pool.request = AsyncMock(return_value=err_result)
        codec = MagicMock()
        codec.envelope_fields.return_value = {
            "contract_version": "1",
            "trace_id": "t",
            "issued_at": "2026-04-19T00:00:00+00:00",
            "job_id": "j1",
        }
        codec.encode.return_value = b"payload"
        codec.decode.return_value = SocialMediaResult(
            response=None, error="pool.no_live_workers"
        )
        client = NatsSocialMediaClient(pool, codec)
        with pytest.raises(SocialMediaUnavailableError, match="pool.no_live_workers"):
            await client.list_groups()

    @pytest.mark.asyncio
    async def test_start_delegates_to_pool(self) -> None:
        pool = _make_pool()
        mock_nc = AsyncMock()
        client = NatsSocialMediaClient(pool, MagicMock(), nc=mock_nc)
        await client.start()
        pool.start.assert_awaited_once_with(mock_nc)

    @pytest.mark.asyncio
    async def test_stop_delegates_to_pool(self) -> None:
        pool = _make_pool()
        client = NatsSocialMediaClient(pool, MagicMock())
        await client.stop()
        pool.stop.assert_awaited_once()