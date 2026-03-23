/* ══════════════════════════════════════════════════
   Lyra User Guide v6 — Theme, Tabs, SVG recolor
   ══════════════════════════════════════════════════ */

// ── Theme toggle ──────────────────────────────────
const root = document.documentElement;
const btn  = document.getElementById('themeBtn');
const saved = localStorage.getItem('lyra-theme') || 'dark';
root.setAttribute('data-theme', saved);
btn.textContent = saved === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';

// 3-column color map: [htmlOriginal, improvedDark, light]
const SVG_COLOR_MAP = [
  // SVG backgrounds / outer canvas
  ['#07090f',  '#0d1117',  '#f1f5f9'],
  // Default boxes (dark navy)
  ['#111827',  '#1e293b',  '#ffffff'],
  ['#0e1118',  '#161d28',  '#f8fafc'],
  ['#1a2236',  '#1a2236',  '#f1f0ec'],
  // Telegram adapter (blue tints)
  ['#091626',  '#0f2236',  '#dbeafe'],
  ['#0d1827',  '#122036',  '#bfdbfe'],
  // Discord adapter (indigo tints)
  ['#0b0c26',  '#10122e',  '#eef2ff'],
  ['#0d0f27',  '#12142e',  '#e0e7ff'],
  ['#0c0e28',  '#11132e',  '#c7d2fe'],
  // Parser (violet)
  ['#141827',  '#1c1f3a',  '#f5f3ff'],
  // Router (purple)
  ['#1a1030',  '#221438',  '#ede9fe'],
  ['#120d1e',  '#1a1128',  '#f3e8ff'],
  // Inbound bus
  ['#0f1120',  '#161a30',  '#e8eaf6'],
  // Auth / rate limit (red tints)
  ['#1a080d',  '#22100a',  '#fef2f2'],
  ['#1a0808',  '#221010',  '#fee2e2'],
  ['#6b3040',  '#f87171',  '#9f1239'],
  ['#5a2030',  '#fca5a5',  '#b91c1c'],
  // Pool / agent (amber tints)
  ['#14120a',  '#1e180c',  '#fffbeb'],
  ['#1a120a',  '#22180e',  '#fef3c7'],
  ['#1a1208',  '#22180a',  '#fde68a'],
  // Green / LLM boxes
  ['#0f1e13',  '#132516',  '#f0fdf4'],
  ['#0f1e16',  '#132519',  '#dcfce7'],
  ['#0a2010',  '#0e2814',  '#bbf7d0'],
  ['#0a1f10',  '#0e2714',  '#dcfce7'],
  // Teal / cyan boxes
  ['#07191a',  '#0b2223',  '#ecfeff'],
  ['#081a1f',  '#0c2328',  '#cffafe'],
  ['#0a1a1e',  '#0e2328',  '#a5f3fc'],
  // Purple plugin boxes
  ['#130d26',  '#1c1030',  '#faf5ff'],
  ['#0d0e26',  '#13142e',  '#eef2ff'],
  ['#120c26',  '#1a1030',  '#f5f3ff'],
  // Passthrough / voice (amber)
  ['#1a1108',  '#22180a',  '#fffbeb'],
  // Error (red)
  ['#1a0a08',  '#22100a',  '#fff1f2'],
  // Pink / audio
  ['#1a0f14',  '#1a0f14',  '#fdf2f8'],
  ['#2a1020',  '#2a1020',  '#fce7f3'],
  // Happy Paths SVG fills
  ['#2a1530',  '#2a1530',  '#fdf2f8'],
  ['#0d2030',  '#0d2030',  '#ecfeff'],
  ['#1a4a5a',  '#3d6a7a',  '#155e75'],
  ['#3d6a7a',  '#3d6a7a',  '#155e75'],
  // Text colors
  ['#e2e8f0',  '#f1f5f9',  '#1e293b'],
  ['#cbd5e1',  '#e2e8f0',  '#334155'],
  ['#94a3b8',  '#cbd5e1',  '#475569'],
  ['#6b7a99',  '#94a3b8',  '#64748b'],
  ['#4a5568',  '#64748b',  '#94a3b8'],
  ['#3d5a7a',  '#7098b8',  '#64748b'],
  ['#3d4f6b',  '#6b7a99',  '#6b7280'],
  // Subtitle / accent text
  ['#2a5a8a',  '#60a5fa',  '#1d4ed8'],
  ['#3a3a7a',  '#818cf8',  '#3730a3'],
  ['#2a6a5a',  '#34d399',  '#065f46'],
  ['#5a3a7a',  '#a78bfa',  '#6d28d9'],
  ['#5a4a7a',  '#7c6aaa',  '#6d28d9'],
  // Bright accent fills
  ['#22d3ee',  '#22d3ee',  '#0891b2'],
  ['#8b5cf6',  '#a78bfa',  '#7c3aed'],
  ['#34d399',  '#34d399',  '#059669'],
  ['#f59e0b',  '#fbbf24',  '#d97706'],
  ['#f87171',  '#fca5a5',  '#dc2626'],
  ['#ec4899',  '#f472b6',  '#db2777'],
  ['#f472b6',  '#f472b6',  '#db2777'],
  ['#60a5fa',  '#93c5fd',  '#2563eb'],
  ['#26a5e4',  '#38bdf8',  '#0284c7'],
  ['#5865f2',  '#818cf8',  '#4f46e5'],
  ['#7c84f8',  '#818cf8',  '#4f46e5'],
  ['#818cf8',  '#818cf8',  '#4f46e5'],
  ['#fbbf24',  '#fbbf24',  '#d97706'],
  ['#06b6d4',  '#22d3ee',  '#0891b2'],
  ['#fb7185',  '#fca5a5',  '#dc2626'],
];

const TO_DARK  = Object.fromEntries(SVG_COLOR_MAP.map(([o,d])   => [o, d]));
const TO_LIGHT = Object.fromEntries(SVG_COLOR_MAP.map(([o,,l]) => [o, l]));

function applyThemeToSvgs(theme) {
  const map = theme === 'light' ? TO_LIGHT : TO_DARK;
  document.querySelectorAll('.diagram-wrap svg *').forEach(el => {
    const fill = el.getAttribute('fill');
    if (fill && fill !== 'none' && !fill.startsWith('url(')) {
      if (!el.dataset.origFill) el.dataset.origFill = fill;
      const target = map[el.dataset.origFill];
      if (target) el.style.fill = target;
      else el.style.fill = '';
    }
    const stroke = el.getAttribute('stroke');
    if (stroke && stroke !== 'none' && !stroke.startsWith('url(')) {
      if (!el.dataset.origStroke) el.dataset.origStroke = stroke;
      const target = map[el.dataset.origStroke];
      if (target) el.style.stroke = target;
    }
  });
  // Dot patterns
  document.querySelectorAll('.diagram-wrap svg pattern circle').forEach(c => {
    c.style.fill = theme === 'light' ? 'rgba(0,0,0,0.05)' : 'rgba(255,255,255,0.04)';
  });
  // Glow ellipses
  const glowMap = {
    'rgba(34,211,238,0.02)':   theme === 'light' ? 'rgba(14,165,233,0.08)'  : 'rgba(34,211,238,0.03)',
    'rgba(139,92,246,0.015)':  theme === 'light' ? 'rgba(139,92,246,0.08)'  : 'rgba(139,92,246,0.03)',
    'rgba(34,211,238,0.015)':  theme === 'light' ? 'rgba(14,165,233,0.07)'  : 'rgba(34,211,238,0.025)',
    'rgba(52,211,153,0.02)':   theme === 'light' ? 'rgba(16,185,129,0.08)'  : 'rgba(52,211,153,0.03)',
    'rgba(245,158,11,0.02)':   theme === 'light' ? 'rgba(217,119,6,0.08)'   : 'rgba(245,158,11,0.03)',
    'rgba(129,140,248,0.015)': theme === 'light' ? 'rgba(99,102,241,0.08)'  : 'rgba(129,140,248,0.025)',
  };
  document.querySelectorAll('.diagram-wrap svg ellipse').forEach(el => {
    const f = el.getAttribute('fill') || '';
    if (!el.dataset.origFill) el.dataset.origFill = f;
    const target = glowMap[el.dataset.origFill];
    if (target) el.setAttribute('fill', target);
  });
}

applyThemeToSvgs(saved);

btn.addEventListener('click', () => {
  const current = root.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  btn.textContent = next === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';
  localStorage.setItem('lyra-theme', next);
  applyThemeToSvgs(next);
});

// ── Tab switching + lazy content loading ──────────
const tabBtns   = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');

const hero = document.querySelector('.hero');
const readingGuide = document.querySelector('.reading-guide');

async function loadPanel(panel) {
  const src = panel.dataset.src;
  if (!src || panel.dataset.loaded) return;
  panel.dataset.loaded = 'true';
  try {
    const html = await fetch(src).then(r => r.text());
    panel.innerHTML = html;
    applyThemeToSvgs(root.getAttribute('data-theme'));
  } catch (e) {
    panel.innerHTML = `<div style="padding:2rem;color:var(--textdim)">Failed to load ${src}</div>`;
  }
}

tabBtns.forEach(tb => {
  tb.addEventListener('click', () => {
    const target = tb.dataset.tab;
    tabBtns.forEach(b => b.classList.toggle('active', b.dataset.tab === target));
    tabPanels.forEach(p => p.classList.toggle('active', p.id === 'tab-' + target));
    loadPanel(document.getElementById('tab-' + target));
    // Show hero + reading guide only on Quick Start tab
    const isHome = target === 'quickstart';
    if (hero) hero.style.display = isHome ? '' : 'none';
    if (readingGuide) readingGuide.style.display = isHome ? '' : 'none';
    window.scrollTo({ top: 0, behavior: 'instant' });
  });
});

// Load the initially active tab
loadPanel(document.querySelector('.tab-panel.active'));
