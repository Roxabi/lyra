"""Unit tests for StateMachine[K, V] — Phase 5 streaming primitives (#1282).

Slice 2 tests. Covers open/close/is_open, mark_seen dedup semantics,
drain snapshot-and-clear, type-variance smoke, and instance independence.
"""

from __future__ import annotations

from factory.streaming.state_machine import StateMachine


class TestOpen:
    """open(k, v) registers the block so is_open returns True and value is stored."""

    def test_open_makes_key_open(self) -> None:
        # Arrange
        sm: StateMachine[str, int] = StateMachine()

        # Act
        sm.open("a", 42)

        # Assert
        assert sm.is_open("a") is True

    def test_open_stores_value_in_open_blocks(self) -> None:
        # Arrange
        sm: StateMachine[str, int] = StateMachine()

        # Act
        sm.open("a", 42)

        # Assert
        assert sm.open_blocks["a"] == 42

    def test_open_overwrites_existing_value(self) -> None:
        # Arrange
        sm: StateMachine[str, int] = StateMachine()
        sm.open("a", 1)

        # Act
        sm.open("a", 99)

        # Assert
        assert sm.open_blocks["a"] == 99

    def test_key_not_open_before_open_called(self) -> None:
        # Arrange
        sm: StateMachine[str, int] = StateMachine()

        # Act / Assert — no open() call made
        assert sm.is_open("a") is False


class TestClose:
    """close(k) returns stored value once, then None on subsequent calls."""

    def test_close_returns_stored_value(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.open("block-1", "text")

        # Act
        result = sm.close("block-1")

        # Assert
        assert result == "text"

    def test_close_removes_key_from_open_blocks(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.open("block-1", "text")

        # Act
        sm.close("block-1")

        # Assert
        assert sm.is_open("block-1") is False

    def test_second_close_returns_none(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.open("block-1", "text")
        sm.close("block-1")

        # Act
        result = sm.close("block-1")

        # Assert
        assert result is None

    def test_close_unknown_key_returns_none(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()

        # Act — key never opened; must NOT raise KeyError
        result = sm.close("never-opened")

        # Assert
        assert result is None


class TestMarkSeen:
    """mark_seen(k) returns True only on the first call for a given key."""

    def test_first_mark_seen_returns_true(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()

        # Act
        result = sm.mark_seen("tool-abc")

        # Assert
        assert result is True

    def test_second_mark_seen_returns_false(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.mark_seen("tool-abc")

        # Act
        result = sm.mark_seen("tool-abc")

        # Assert
        assert result is False

    def test_mark_seen_third_call_still_false(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.mark_seen("tool-abc")
        sm.mark_seen("tool-abc")

        # Act
        result = sm.mark_seen("tool-abc")

        # Assert
        assert result is False

    def test_different_keys_are_independent(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.mark_seen("key-1")

        # Act
        result = sm.mark_seen("key-2")

        # Assert — key-2 has never been seen
        assert result is True


class TestDrain:
    """drain() yields pending items in insertion order then clears the queue."""

    def test_drain_returns_items_in_insertion_order(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.pending.append("first")
        sm.pending.append("second")
        sm.pending.append("third")

        # Act
        result = list(sm.drain())

        # Assert
        assert result == ["first", "second", "third"]

    def test_drain_clears_pending_after_iteration(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.pending.append("item")

        # Act
        list(sm.drain())  # consume fully

        # Assert
        assert len(sm.pending) == 0

    def test_drain_empty_pending_returns_empty(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()

        # Act
        result = list(sm.drain())

        # Assert
        assert result == []

    def test_drain_twice_second_call_is_empty(self) -> None:
        # Arrange
        sm: StateMachine[str, str] = StateMachine()
        sm.pending.append("only-once")

        # Act
        list(sm.drain())
        result_second = list(sm.drain())

        # Assert
        assert result_second == []


class TestTypeVarianceSmoke:
    """StateMachine is usable with distinct concrete K and V types."""

    def test_int_key_str_value(self) -> None:
        # Arrange
        sm: StateMachine[int, str] = StateMachine()

        # Act
        sm.open(1, "a")
        sm.open(2, "b")

        # Assert
        assert sm.is_open(1) is True
        assert sm.open_blocks[1] == "a"
        assert sm.close(2) == "b"

    def test_str_key_dict_value(self) -> None:
        # Arrange
        sm: StateMachine[str, dict] = StateMachine()  # type: ignore[type-arg]
        payload: dict = {"tool": "bash", "input": "ls"}

        # Act
        sm.open("call-99", payload)

        # Assert
        assert sm.open_blocks["call-99"] == {"tool": "bash", "input": "ls"}
        assert sm.close("call-99") == payload


class TestInstanceIndependence:
    """Two StateMachine instances must not share any state."""

    def test_open_on_one_does_not_affect_other(self) -> None:
        # Arrange
        sm_a: StateMachine[str, int] = StateMachine()
        sm_b: StateMachine[str, int] = StateMachine()

        # Act
        sm_a.open("shared-key", 1)

        # Assert — sm_b is unaffected
        assert sm_b.is_open("shared-key") is False
        assert "shared-key" not in sm_b.open_blocks

    def test_mark_seen_on_one_does_not_affect_other(self) -> None:
        # Arrange
        sm_a: StateMachine[str, str] = StateMachine()
        sm_b: StateMachine[str, str] = StateMachine()
        sm_a.mark_seen("tool-id")

        # Act
        result = sm_b.mark_seen("tool-id")

        # Assert — sm_b sees it for the first time
        assert result is True

    def test_pending_on_one_does_not_affect_other(self) -> None:
        # Arrange
        sm_a: StateMachine[str, str] = StateMachine()
        sm_b: StateMachine[str, str] = StateMachine()
        sm_a.pending.append("item")

        # Act
        result = list(sm_b.drain())

        # Assert — sm_b.pending was empty
        assert result == []
