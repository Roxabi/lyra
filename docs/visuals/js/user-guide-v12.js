/* ══════════════════════════════════════════════════
   Lyra User Guide v12 — Theme, Tabs, Lazy Load
   ══════════════════════════════════════════════════ */

// ── Theme toggle ──────────────────────────────────
const root = document.documentElement;
const btn  = document.getElementById('themeBtn');
const saved = localStorage.getItem('lyra-v12-theme') || 'dark';
root.setAttribute('data-theme', saved);
btn.textContent = saved === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';

btn.addEventListener('click', () => {
  const current = root.getAttribute('data-theme');
  const next = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  btn.textContent = next === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';
  localStorage.setItem('lyra-v12-theme', next);
});

// ── Tab switching + lazy content loading ──────────
const tabBtns   = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');
const mainEl    = document.querySelector('.main');
const hero      = document.querySelector('.hero');
const readingGuide = document.querySelector('.reading-guide');

async function loadPanel(panel) {
  const src = panel.dataset.src;
  if (!src || panel.dataset.loaded === 'ok') return;
  try {
    const r = await fetch(src, { cache: 'no-cache' });
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    panel.innerHTML = await r.text();
    panel.dataset.loaded = 'ok';
  } catch (e) {
    panel.dataset.loaded = 'error';
    panel.innerHTML = `<div style="padding:2rem;color:var(--textdim)">Failed to load ${src}: ${e.message}</div>`;
  }
}

function switchTab(target) {
  tabBtns.forEach(b => b.classList.toggle('active', b.dataset.tab === target));
  tabPanels.forEach(p => p.classList.toggle('active', p.id === 'tab-' + target));
  loadPanel(document.getElementById('tab-' + target));

  // Show hero + reading guide only on Quick Start tab
  const isHome = target === 'qs';
  if (hero) hero.style.display = isHome ? '' : 'none';
  if (readingGuide) readingGuide.style.display = isHome ? '' : 'none';

  // Adjust main padding: when hero is visible it pushes main down naturally,
  // so we only need a small gap. When hero is hidden, we need full padding
  // to clear the fixed nav + tabs bar.
  if (mainEl) mainEl.style.paddingTop = isHome ? '16px' : '';

  window.scrollTo({ top: 0, behavior: 'instant' });
}

tabBtns.forEach(tb => {
  tb.addEventListener('click', () => switchTab(tb.dataset.tab));
});

// Initial state — QS tab is active, hero is visible
if (mainEl) mainEl.style.paddingTop = '16px';

// Load the initially active tab
loadPanel(document.querySelector('.tab-panel.active'));
