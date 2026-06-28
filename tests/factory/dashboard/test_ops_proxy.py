"""Unit tests for dashboard ops HTTP proxy (#1774)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from factory.dashboard.ops_proxy import (
    _parse_loki_streams,
    fetch_ops_health,
    fetch_ops_logs,
)


class TestParseLokiStreams:
    def test_parses_stream_values(self) -> None:
        payload = {
            "data": {
                "result": [
                    {
                        "stream": {
                            "job": "factory-journal",
                            "systemd_unit": "factory-hub.service",
                        },
                        "values": [
                            ["1719561600000000000", "ERROR hub crash"],
                            ["1719561500000000000", "INFO heartbeat"],
                        ],
                    }
                ]
            }
        }
        entries = _parse_loki_streams(payload, limit=10)
        assert len(entries) == 2
        assert entries[0].line == "ERROR hub crash"
        assert entries[0].labels["systemd_unit"] == "factory-hub.service"

    def test_empty_payload(self) -> None:
        assert _parse_loki_streams({}, limit=10) == []


class TestFetchOpsHealth:
    @pytest.mark.asyncio
    async def test_all_engines_probed(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        ok = MagicMock()
        ok.status_code = 200
        fail = MagicMock()
        fail.status_code = 503
        mock_client.get = AsyncMock(side_effect=[ok, ok, fail])

        with patch("factory.dashboard.ops_proxy.httpx.AsyncClient", return_value=mock_client):
            res = await fetch_ops_health()

        assert len(res.engines) == 3
        assert res.engines[0].engine == "loki"
        assert res.engines[0].reachable is True
        assert res.engines[2].reachable is False


class TestFetchOpsLogs:
    @pytest.mark.asyncio
    async def test_unreachable_on_http_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("factory.dashboard.ops_proxy.httpx.AsyncClient", return_value=mock_client):
            res = await fetch_ops_logs("hub-errors")

        assert res.engine_reachable is False
        assert res.entries == []

    @pytest.mark.asyncio
    async def test_parses_success_response(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        ok = MagicMock()
        ok.status_code = 200
        ok.json.return_value = {
            "data": {
                "result": [
                    {
                        "stream": {"job": "factory-operator"},
                        "values": [["1719561600000000000", "converge_start"]],
                    }
                ]
            }
        }
        mock_client.get = AsyncMock(return_value=ok)

        with patch("factory.dashboard.ops_proxy.httpx.AsyncClient", return_value=mock_client):
            res = await fetch_ops_logs("operator-events", limit=5)

        assert res.engine_reachable is True
        assert len(res.entries) == 1
        assert res.entries[0].line == "converge_start"