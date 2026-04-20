"""Tests for v5 component renderers — pure HTML fragment functions."""
from __future__ import annotations

import pytest

from v5.components.card import render_card
from v5.components.header import render_footer, render_header
from v5.components.toggle import render_toggle
from v5.components.toolbar import render_toolbar

# ─── render_card ─────────────────────────────────────────────────────────────

def _issue(  # noqa: PLR0913
    num: int = 1,
    title: str = "Test issue",
    state: str = "open",
    blocked_by: list | None = None,
    blocking: list | None = None,
    repo: str = "Roxabi/lyra",
    size: str | None = None,
) -> dict:
    return {
        "repo": repo,
        "number": num,
        "title": title,
        "state": state,
        "blocked_by": blocked_by or [],
        "blocking": blocking or [],
        "size": size,
    }


class TestRenderCard:
    def _render(self, iss: dict, **kwargs) -> str:
        defaults = {
            "epic_tone": "a1",
            "issues": {},
            "status": "ready",
            "depth": 0,
        }
        defaults.update(kwargs)
        return render_card(iss, **defaults)

    def test_contains_data_iss(self):
        html = self._render(_issue(num=5))
        assert 'data-iss="Roxabi/lyra#5"' in html

    def test_contains_data_tone(self):
        html = self._render(_issue(), epic_tone="c1")
        assert 'data-tone="c1"' in html

    def test_contains_status_class(self):
        html = self._render(_issue(), status="blocked")
        assert "blocked" in html

    def test_contains_href(self):
        html = self._render(_issue(num=42))
        assert 'href="https://github.com/Roxabi/lyra/issues/42"' in html

    def test_contains_title_attribute(self):
        html = self._render(_issue(title="My title"))
        assert 'title="My title"' in html

    def test_escapes_html_in_title(self):
        html = self._render(_issue(title="<script>alert(1)</script>"))
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_size_pill_present_when_size_set(self):
        html = self._render(_issue(size="L"))
        assert 'class="card-size"' in html
        assert ">L<" in html

    def test_size_pill_absent_when_no_size(self):
        html = self._render(_issue(size=None))
        assert 'class="card-size"' not in html

    def test_data_blockedby_empty_string_when_no_blockers(self):
        html = self._render(_issue(blocked_by=[]))
        assert 'data-blockedby=""' in html

    def test_data_blockedby_populated(self):
        iss = _issue(blocked_by=[{"repo": "Roxabi/lyra", "issue": 2}])
        html = self._render(iss)
        assert "Roxabi/lyra#2" in html

    def test_data_blocking_populated(self):
        iss = _issue(blocking=[{"repo": "Roxabi/lyra", "issue": 3}])
        html = self._render(iss)
        assert "Roxabi/lyra#3" in html

    def test_style_attribute_injected(self):
        html = self._render(_issue(), style="left:50%; top:25%;")
        assert 'style="left:50%; top:25%;"' in html

    def test_no_style_attribute_when_empty(self):
        html = self._render(_issue(), style="")
        assert " style=" not in html

    def test_cross_repo_prefix_letter(self):
        """Blocked-by from Roxabi/voiceCLI → prefix 'V:'."""
        iss = _issue(blocked_by=[{"repo": "Roxabi/voiceCLI", "issue": 10}])
        issues = {
            "Roxabi/voiceCLI#10": {
                "repo": "Roxabi/voiceCLI", "number": 10, "title": "TTS",
                "state": "open",
            }
        }
        html = render_card(
            iss, epic_tone="a1", issues=issues, status="blocked", depth=0
        )
        assert "V:#10" in html

    def test_closed_blocker_adds_closed_class(self):
        """Blocker that is closed → dep-ref span gets .closed."""
        iss = _issue(blocked_by=[{"repo": "Roxabi/lyra", "issue": 4}])
        issues = {
            "Roxabi/lyra#4": {
                "repo": "Roxabi/lyra", "number": 4, "title": "done",
                "state": "closed",
            }
        }
        html = render_card(
            iss, epic_tone="a1", issues=issues, status="ready", depth=0
        )
        assert "closed" in html

    def test_long_title_truncated_with_ellipsis(self):
        long_title = "A" * 60
        html = self._render(_issue(title=long_title))
        assert "…" in html

    def test_short_title_not_truncated(self):
        short_title = "Short"
        html = self._render(_issue(title=short_title))
        assert "…" not in html

    def test_internal_dep_no_prefix(self):
        """Same-repo blocker → no prefix letter."""
        iss = _issue(blocked_by=[{"repo": "Roxabi/lyra", "issue": 2}])
        issues = {
            "Roxabi/lyra#2": {
                "repo": "Roxabi/lyra", "number": 2, "title": "other",
                "state": "open",
            }
        }
        html = render_card(
            iss, epic_tone="a1", issues=issues, status="blocked", depth=0
        )
        # Internal refs show just "#2" with no "R:" prefix
        assert "R:#2" not in html
        assert "#2" in html


# ─── render_toggle ───────────────────────────────────────────────────────────

class TestRenderToggle:
    def test_graph_active_graph_button_has_active_class(self):
        html = render_toggle("graph")
        # Graph button should have "active" in its class attribute
        assert 'class="graph active"' in html

    def test_graph_active_grid_button_has_no_active_class(self):
        html = render_toggle("graph")
        assert 'class="grid"' in html
        assert 'class="grid active"' not in html

    def test_graph_active_aria_pressed_graph_true(self):
        html = render_toggle("graph")
        assert 'data-view="graph"' in html
        # Graph button aria-pressed=true
        assert 'aria-pressed="true"' in html

    def test_graph_active_aria_pressed_grid_false(self):
        html = render_toggle("graph")
        assert 'aria-pressed="false"' in html

    def test_grid_active_grid_button_has_active_class(self):
        html = render_toggle("grid")
        assert 'class="grid active"' in html

    def test_grid_active_graph_button_has_no_active_class(self):
        html = render_toggle("grid")
        assert 'class="graph"' in html
        assert 'class="graph active"' not in html

    def test_grid_active_aria_pressed_grid_true(self):
        html = render_toggle("grid")
        # Grid button is second → aria-pressed="true" should appear somewhere
        lines = html.split("\n")
        grid_line = next(ln for ln in lines if 'data-view="grid"' in ln)
        assert 'aria-pressed="true"' in grid_line

    def test_invalid_value_raises_assertion(self):
        with pytest.raises(AssertionError):
            render_toggle("table")

    def test_invalid_empty_raises_assertion(self):
        with pytest.raises(AssertionError):
            render_toggle("")

    def test_has_mode_toggle_class(self):
        html = render_toggle()
        assert 'class="mode-toggle"' in html

    def test_has_two_buttons(self):
        html = render_toggle()
        assert html.count("<button") == 2


# ─── render_toolbar ──────────────────────────────────────────────────────────

class TestRenderToolbar:
    def test_has_toggle_epic_checkbox(self):
        html = render_toolbar()
        assert 'id="toggle-epic"' in html

    def test_has_toggle_closed_checkbox(self):
        html = render_toolbar()
        assert 'id="toggle-closed"' in html

    def test_has_toggle_ready_checkbox(self):
        html = render_toolbar()
        assert 'id="toggle-ready"' in html

    def test_has_three_checkboxes(self):
        html = render_toolbar()
        assert html.count('type="checkbox"') == 3

    def test_has_legend_pills(self):
        html = render_toolbar()
        assert 'class="legend-pills"' in html

    def test_has_ready_pill(self):
        html = render_toolbar()
        assert ">ready<" in html

    def test_has_blocked_pill(self):
        html = render_toolbar()
        assert ">blocked<" in html

    def test_has_done_pill(self):
        html = render_toolbar()
        assert ">done<" in html


# ─── render_header ───────────────────────────────────────────────────────────

class TestRenderHeader:
    def test_escapes_title(self):
        html = render_header("<b>Title</b>", "sub")
        assert "&lt;b&gt;" in html
        assert "<b>" not in html

    def test_escapes_subtitle(self):
        html = render_header("Title", "<em>sub</em>")
        assert "&lt;em&gt;" in html

    def test_contains_h1(self):
        html = render_header("Title", "sub")
        assert "<h1>" in html

    def test_contains_subtitle_div(self):
        html = render_header("Title", "sub")
        assert 'class="subtitle"' in html

    def test_has_header_tag(self):
        html = render_header("Title", "sub")
        assert "<header" in html


# ─── render_footer ───────────────────────────────────────────────────────────

class TestRenderFooter:
    def test_has_footer_tag(self):
        html = render_footer("Roxabi/lyra", "2026-04-20")
        assert "<footer" in html

    def test_date_in_output(self):
        html = render_footer("Roxabi/lyra", "2026-04-20")
        assert "2026-04-20" in html

    def test_repo_url_in_output(self):
        html = render_footer("Roxabi/lyra", "2026-04-20")
        assert "https://github.com/Roxabi/lyra/issues" in html
