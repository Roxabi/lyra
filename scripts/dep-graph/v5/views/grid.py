"""Grid view — lane-swim matrix (milestones × column groups).

Reproduces v3.1 exactly. One <section class="view view-grid"> root.
"""

from __future__ import annotations

import html
from collections import defaultdict
from typing import Any

from ..components.card import render_card
from ..data.derive import sort_cards_in_cell, status_of
from ..data.model import (
    NO_LANE,
    NO_MS,
    GraphData,
    Lane,
)


def _render_cell(
    cards_by_lane: dict[str, list[dict[str, Any]]],
    lane_codes: list[str],
    data: GraphData,
) -> str:
    groups: list[str] = []
    for code in lane_codes:
        cards = cards_by_lane.get(code, [])
        if not cards:
            continue
        meta: Lane = data.lane_by_code[code]
        tag = html.escape(meta.epic.tag if meta.epic else "")
        epic_num = meta.epic.issue if meta.epic else None
        name = html.escape(meta.name)
        epic_url = (
            f"https://github.com/{data.primary_repo}/issues/{epic_num}"
            if epic_num
            else "#"
        )
        header = (
            f'<a class="epic-header" data-tone="{meta.color}" '
            f'data-epic="{code}" href="{epic_url}" '
            f'target="_blank" rel="noopener" '
            f'title="Open epic #{epic_num} on GitHub">'
            f'<span class="epic-code">{code}</span>'
            f'<span class="epic-name">{name}</span>'
            f"{f'<span class=epic-tag>{tag}</span>' if tag else ''}"
            f"{f'<span class=epic-ref>#{epic_num}</span>' if epic_num else ''}"
            f"</a>"
        )
        sorted_cards = sort_cards_in_cell(cards, data.depth_by_key)
        rendered: list[str] = []
        for iss in sorted_cards:
            st = status_of(iss, data.issues)
            d = data.depth_by_key.get(f"{iss['repo']}#{iss['number']}", 0)
            rendered.append(
                render_card(
                    iss,
                    epic_tone=code,
                    issues=data.issues,
                    status=st,
                    depth=d,
                )
            )
        groups.append(
            f'<div class="epic-group" data-epic="{code}">'
            f'{header}<div class="epic-cards">{"".join(rendered)}</div></div>'
        )
    return "".join(groups) if groups else '<div class="cell-empty">·</div>'


def _render_sentinel_cell(cards: list[dict[str, Any]], data: GraphData) -> str:
    """Render a cell without epic grouping (for NO_MS / NO_LANE sentinel cells)."""
    if not cards:
        return '<div class="cell-empty">·</div>'
    sorted_cards = sort_cards_in_cell(cards, data.depth_by_key)
    rendered: list[str] = []
    for iss in sorted_cards:
        st = status_of(iss, data.issues)
        d = data.depth_by_key.get(f"{iss['repo']}#{iss['number']}", 0)
        rendered.append(
            render_card(
                iss,
                epic_tone="",
                issues=data.issues,
                status=st,
                depth=d,
            )
        )
    return (
        f'<div class="epic-group" data-epic="none">'
        f'<div class="epic-cards">{"".join(rendered)}</div></div>'
    )


def _has_no_lane(data: GraphData) -> bool:
    return any(lane == NO_LANE and v for (_, lane), v in data.matrix.items())


def _has_no_ms(data: GraphData) -> bool:
    return any(ms == NO_MS and v for (ms, _), v in data.matrix.items())


def _render_col_headers(data: GraphData, with_no_lane: bool) -> list[str]:
    headers: list[str] = []
    if with_no_lane:
        headers.append(
            '<div class="col-header">'
            '<div class="col-label" data-tone="none">—</div>'
            '<div class="col-epics"><span class="col-epic">No lane</span></div>'
            "</div>"
        )
    for col_label, col_tone, codes in data.column_groups:
        epics: list[str] = []
        for c in codes:
            m = data.lane_by_code[c]
            esc_c = html.escape(c)
            epics.append(
                f'<span class="col-epic" data-tone="{esc_c}">'
                f"{esc_c} · {html.escape(m.name)}</span>"
            )
        headers.append(
            f'<div class="col-header">'
            f'<div class="col-label" data-tone="{html.escape(col_tone)}">'
            f"{html.escape(col_label)}</div>"
            f'<div class="col-epics">{" ".join(epics)}</div>'
            f"</div>"
        )
    return headers


def _render_ms_row(
    ms_key: str,
    ms_code: str,
    ms_name: str,
    data: GraphData,
    with_no_lane: bool,
) -> str:
    esc_ms_code = html.escape(ms_code)
    cells = [
        f'<div class="row-header">'
        f'<div class="ms-code">{esc_ms_code}</div>'
        f'<div class="ms-name">{html.escape(ms_name)}</div>'
        f"</div>"
    ]
    if with_no_lane:
        cards = data.matrix.get((ms_key, NO_LANE), [])
        cells.append(
            f'<div class="grid-cell" data-col="No lane" data-ms="{esc_ms_code}">'
            f"{_render_sentinel_cell(cards, data)}"
            f"</div>"
        )
    for col_label, _, codes in data.column_groups:
        by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for code in codes:
            for iss in data.matrix.get((ms_key, code), []):
                by_lane[code].append(iss)
        cells.append(
            f'<div class="grid-cell" data-col="{html.escape(col_label)}" '
            f'data-ms="{esc_ms_code}">'
            f"{_render_cell(by_lane, codes, data)}"
            f"</div>"
        )
    return f'<div class="grid-row" data-ms="{esc_ms_code}">{"".join(cells)}</div>'


def _render_rows(data: GraphData, with_no_lane: bool) -> list[str]:
    rows: list[str] = []
    if _has_no_ms(data):
        rows.append(_render_ms_row(NO_MS, "—", "No milestone", data, with_no_lane))
    for ms_key, ms_code, ms_name in data.milestones:
        rows.append(_render_ms_row(ms_key, ms_code, ms_name, data, with_no_lane))
    return rows


def render(data: GraphData, *, active: bool = False) -> str:
    active_cls = " view-active" if active else ""
    with_no_lane = _has_no_lane(data)
    col_headers = _render_col_headers(data, with_no_lane)
    rows = _render_rows(data, with_no_lane)
    n_cols = len(data.column_groups) + (1 if with_no_lane else 0)
    return (
        f'<section class="view view-grid{active_cls}" data-view="grid">\n'
        f'<div class="lane-swim-grid" style="--cols: {n_cols};">\n'
        '  <div class="grid-head">\n'
        '    <div class="spacer"></div>\n'
        f"    {''.join(col_headers)}\n"
        "  </div>\n"
        f"  {''.join(rows)}\n"
        "</div>\n"
        "</section>\n"
    )
