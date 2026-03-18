"""Tests for MessageManager — template loading, variable substitution,
no-raise guarantee, and resolution order (SC-11a, SC-11c, SC-11d, SC-11b).

Covers:
  V1 (T1.3) — SC-11a: template loading from TOML
               SC-11c: variable substitution ({command_name}, {retry_secs})
               SC-11d: no-raise guarantee (missing key, bad path, wrong kwargs)
  V4 (T4.5) — SC-11b: resolution order (all 4 fallback steps)
"""

from __future__ import annotations

from lyra.core.messages import MessageManager

from .conftest import MESSAGES_TOML_PATH

# ---------------------------------------------------------------------------
# V1 — SC-11a: Template loading from TOML
# ---------------------------------------------------------------------------


class TestTemplateLoading:
    """SC-11a: MessageManager loads strings from TOML file."""

    def test_loads_generic_error_string(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("generic")

        # Assert
        assert result == "Something went wrong. Please try again."

    def test_loads_help_header_string(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("help_header")

        # Assert
        assert result == "Available commands:"

    def test_loads_platform_specific_string(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("backpressure_ack", platform="telegram")

        # Assert — platform-specific EN string resolves
        assert "Processing" in result
        assert result != ""

    def test_loads_discord_platform_string(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("backpressure_ack", platform="discord")

        # Assert
        assert "Processing" in result


# ---------------------------------------------------------------------------
# V1 — SC-11c: Variable substitution
# ---------------------------------------------------------------------------


class TestVariableSubstitution:
    """SC-11c: {command_name} and {retry_secs} placeholders are rendered."""

    def test_substitutes_command_name(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("unknown_command", command_name="/foo")

        # Assert — the substituted value appears in the output
        assert "/foo" in result

    def test_substitutes_retry_secs(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("unavailable", retry_secs="30")

        # Assert
        assert "30" in result

    def test_substitution_produces_full_sentence(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("unknown_command", command_name="/pizza")

        # Assert — full template rendered (not just the substituted fragment)
        assert "/pizza" in result
        assert "/help" in result


# ---------------------------------------------------------------------------
# V1 — SC-11d: No-raise guarantee
# ---------------------------------------------------------------------------


class TestNoRaiseGuarantee:
    """SC-11d: get() never raises regardless of errors at any level."""

    def test_no_raise_missing_key(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act — completely unknown key
        result = mm.get("totally.nonexistent.key")

        # Assert — returns str, never raises
        assert isinstance(result, str)

    def test_no_raise_bad_toml_path(self) -> None:
        # Arrange — path does not exist; MessageManager should swallow the error
        mm = MessageManager("/nonexistent/path/messages.toml")

        # Act
        result = mm.get("generic")

        # Assert — returns a fallback string, never raises
        assert isinstance(result, str)

    def test_no_raise_wrong_kwargs_for_no_placeholder_key(self) -> None:
        # Arrange — "generic" has no {placeholders}; extra kwargs are harmless
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("generic", wrong_kwarg="x")

        # Assert — still returns the string without raising
        assert isinstance(result, str)
        assert "Something went wrong" in result

    def test_no_raise_missing_kwargs_for_placeholder_key(self) -> None:
        # Arrange — "unknown_command" requires {command_name}; if omitted the
        # format_map call would raise KeyError — MessageManager must handle it
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("unknown_command")  # intentionally omit command_name=

        # Assert — no raise; returns some string (fallback or partially-rendered)
        assert isinstance(result, str)

    def test_no_raise_empty_key(self) -> None:
        # Arrange
        mm = MessageManager(MESSAGES_TOML_PATH)

        # Act
        result = mm.get("")

        # Assert
        assert isinstance(result, str)

    def test_bad_path_still_returns_fallback_for_known_key(self) -> None:
        # Arrange — TOML fails to load, but hardcoded fallbacks exist
        mm = MessageManager("/no/such/file.toml")

        # Act
        result = mm.get("generic")

        # Assert — returns hardcoded fallback
        assert isinstance(result, str)
        # The fallback for "generic" is the same as the TOML value
        assert "Something went wrong" in result


# ---------------------------------------------------------------------------
# V4 — SC-11b: Resolution order (all 4 fallback steps)
# ---------------------------------------------------------------------------


class TestResolutionOrder:
    """SC-11b: All four resolution steps are reachable independently.

    Resolution order (from spec):
      (a) adapters.{platform}.{lang}.{key}  — platform + language match
      (b) adapters.{platform}.en.{key}      — platform match, EN fallback
      (c) errors.{lang}.{key}               — global key, active language
      (d) errors.en.{key}                   — global key, EN fallback
      (e) _FALLBACKS[key]                   — hardcoded safety net (never raises)
    """

    def test_step_a_platform_lang_match(self) -> None:
        # Step (a): adapters.telegram.fr.backpressure_ack wins when lang="fr"
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")
        result = mm.get("backpressure_ack", platform="telegram")
        assert result == "Traitement de ta requ\u00eate\u2026"

    def test_step_b_platform_en_fallback(self) -> None:
        # Step (b): lang="de" has no telegram.de entries → falls to telegram.en
        mm = MessageManager(MESSAGES_TOML_PATH, language="de")
        result = mm.get("backpressure_ack", platform="telegram")
        assert result == "Processing your request\u2026"

    def test_step_c_global_lang_match(self) -> None:
        # Step (c): "generic" has no adapters.*.* entry → goes to errors.fr
        # Even when platform="telegram" is passed, no adapters.telegram.*.generic
        # exists so resolution falls to errors.fr.generic
        mm = MessageManager(MESSAGES_TOML_PATH, language="fr")
        result = mm.get("generic", platform="telegram")
        assert result == "Une erreur s'est produite. R\u00e9essaie."

    def test_step_d_global_en_fallback(self) -> None:
        # Step (d): lang="de" + no platform + key is in errors.en only
        mm = MessageManager(MESSAGES_TOML_PATH, language="de")
        result = mm.get("generic")
        assert result == "Something went wrong. Please try again."

    def test_step_e_hardcoded_fallback(self) -> None:
        # Step (e): key not present in TOML at all — uses _FALLBACKS
        mm = MessageManager("/nonexistent/path.toml")
        result = mm.get("generic")
        # Hardcoded fallback should match the known value
        assert "Something went wrong" in result

    def test_platform_lang_beats_platform_en(self) -> None:
        # FR string should win over EN for same platform when lang="fr"
        mm_en = MessageManager(MESSAGES_TOML_PATH, language="en")
        mm_fr = MessageManager(MESSAGES_TOML_PATH, language="fr")
        result_en = mm_en.get("backpressure_ack", platform="telegram")
        result_fr = mm_fr.get("backpressure_ack", platform="telegram")
        assert result_en != result_fr
        assert "Processing" in result_en
        assert "Traitement" in result_fr

    def test_platform_en_beats_global_en(self) -> None:
        # adapters.telegram.en.backpressure_ack exists; errors.en has no
        # backpressure_ack — so platform.en is the only match
        mm = MessageManager(MESSAGES_TOML_PATH, language="en")
        result_with_platform = mm.get("backpressure_ack", platform="telegram")
        result_without_platform = mm.get("backpressure_ack")
        # Without platform, there's no errors.en.backpressure_ack entry —
        # it falls through to _FALLBACKS which still contains the same string
        assert "Processing" in result_with_platform
        assert isinstance(result_without_platform, str)
