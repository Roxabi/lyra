from lyra.llm.cli_nats_codec import CliNatsCodec
from lyra.llm.codec import LlmCodec


def test_cli_nats_codec_satisfies_protocol() -> None:
    codec: LlmCodec = CliNatsCodec()  # type-check: structural conformance
    assert isinstance(codec, LlmCodec)  # runtime: runtime_checkable Protocol


def test_protocol_surface() -> None:
    members = {"encode", "decode", "decode_chunk", "encode_control", "set_session_store"}  # noqa: E501
    for m in members:
        assert hasattr(LlmCodec, m), f"Protocol missing {m}"
