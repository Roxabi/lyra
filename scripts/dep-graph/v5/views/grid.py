"""Grid view — lane-swim matrix (milestones × column groups).

Reproduces v3.1 exactly. One <section class="view view-grid"> root.
"""
from __future__ import annotations

import html
from collections import defaultdict
from typing import Any

from ..components.card import render_card
from ..data.derive import sort_cards_in_cell, status_of
from ..data.model import COLUMN_GROUPS, MILESTONES, GraphData, Lane


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
            if epic_num else "#"
        )
        header = (
            f'<a class="epic-header" data-tone="{meta.color}" '
            f'data-epic="{code}" href="{epic_url}" '
            f'target="_blank" rel="noopener" '
            f'title="Open epic #{epic_num} on GitHub">'
            f'<span class="epic-code">{code}</span>'
            f'<span class="epic-name">{name}</span>'
            f'{f"<span class=epic-tag>{tag}</span>" if tag else ""}'
            f'{f"<span class=epic-ref>#{epic_num}</span>" if epic_num else ""}'
            f'</a>'
        )
        sorted_cards = sort_cards_in_cell(cards, data.depth_by_key)
        rendered: list[str] = []
        for iss in sorted_cards:
            st = status_of(iss, data.issues)
            d = data.depth_by_key.get(f"{iss['repo']}#{iss['number']}", 0)
            rendered.append(render_card(
                iss,
                epic_tone=code,
                issues=data.issues,
                status=st,
                depth=d,
            ))
        groups.append(
            f'<div class="epic-group" data-epic="{code}">'
            f'{header}<div class="epic-cards">{"".join(rendered)}</div></div>'
        )
    return "".join(groups) if groups else '<div class="cell-empty">·</div>'


def _render_col_headers(data: GraphData) -> list[str]:
    headers: list[str] = []
    for col_label, col_tone, codes in COLUMN_GROUPS:
        epics: list[str] = []
        for c in codes:
            m = data.lane_by_code[c]
            epics.append(
                f'<span class="col-epic" data-tone="{c}">'
                f'{c} · {html.escape(m.name)}</span>'
            )
        headers.append(
            f'<div class="col-header">'
            f'<div class="col-label" data-tone="{col_tone}">{col_label}</div>'
            f'<div class="col-epics">{" ".join(epics)}</div>'
            f'</div>'
        )
    return headers


def _render_rows(data: GraphData) -> list[str]:
    rows: list[str] = []
    for ms_key, ms_code, ms_name in MILESTONES:
        cells = [
            f'<div class="row-header">'
            f'<div class="ms-code">{ms_code}</div>'
            f'<div class="ms-name">{html.escape(ms_name)}</div>'
            f'</div>'
        ]
        for col_label, _, codes in COLUMN_GROUPS:
            by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for code in codes:
                for iss in data.matrix.get((ms_key, code), []):
                    by_lane[code].append(iss)
            cells.append(
                f'<div class="grid-cell" data-col="{col_label}" data-ms="{ms_code}">'
                f'{_render_cell(by_lane, codes, data)}'
                f'</div>'
            )
        rows.append(
            f'<div class="grid-row" data-ms="{ms_code}">{"".join(cells)}</div>'
        )
    return rows


def render(data: GraphData, *, active: bool = False) -> str:
    active_cls = " view-active" if active else ""
    col_headers = _render_col_headers(data)
    rows = _render_rows(data)
    n_cols = len(COLUMN_GROUPS)
    return (
        f'<section class="view view-grid{active_cls}" data-view="grid">\n'
        f'<div class="lane-swim-grid" style="--cols: {n_cols};">\n'
        '  <div class="grid-head">\n'
        '    <div class="spacer"></div>\n'
        f'    {"".join(col_headers)}\n'
        '  </div>\n'
        f'  {"".join(rows)}\n'
        '</div>\n'
        '</section>\n'
    )
