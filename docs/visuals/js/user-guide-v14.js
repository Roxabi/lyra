/* ══════════════════════════════════════════════════
   Lyra User Guide v14 — Theme, Tabs, ARIA, Hash
   ══════════════════════════════════════════════════ */

// ── Theme toggle ──────────────────────────────────
const root  = document.documentElement;
const btn   = document.getElementById('themeBtn');
const saved = localStorage.getItem('lyra-v14-theme') || 'dark';
root.setAttribute('data-theme', saved);
btn.textContent = saved === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';

btn.addEventListener('click', () => {
  const current = root.getAttribute('data-theme');
  const next    = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  btn.textContent = next === 'dark' ? '\u{1F319}' : '\u{2600}\u{FE0F}';
  localStorage.setItem('lyra-v14-theme', next);
});

// ── Elements ──────────────────────────────────────
const tabBtns      = document.querySelectorAll('.tab-btn');
const tabPanels    = document.querySelectorAll('.tab-panel');
const mainEl       = document.querySelector('.main');
const hero         = document.querySelector('.hero');
const readingGuide = document.querySelector('.reading-guide');
const tabsBar      = document.getElementById('tabsBar');
const tabsWrap     = document.getElementById('tabsWrap');

// ── Lazy content loading ──────────────────────────
async function loadPanel(panel) {
  const src = panel.dataset.src;
  if (!src || panel.dataset.loaded === 'ok') return;
  try {
    const r = await fetch(src);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    panel.innerHTML = await r.text();
    panel.dataset.loaded = 'ok';
  } catch (e) {
    panel.dataset.loaded = 'error';
    panel.innerHTML = `<div style="padding:2rem;color:var(--textdim)">Failed to load ${src}: ${e.message}</div>`;
  }
}

// ── Tab switching with ARIA ───────────────────────
function switchTab(target, updateHash) {
  if (updateHash === undefined) updateHash = true;

  // Update button ARIA + active state
  tabBtns.forEach(b => {
    const isActive = b.dataset.tab === target;
    b.classList.toggle('active', isActive);
    b.setAttribute('aria-selected', isActive ? 'true' : 'false');
    b.setAttribute('tabindex', isActive ? '0' : '-1');
  });

  // Update panel visibility
  tabPanels.forEach(p => {
    const isTarget = p.id === 'tab-' + target;
    p.classList.toggle('active', isTarget);
    // Reset animation by re-triggering reflow
    if (isTarget) {
      p.style.animation = 'none';
      p.offsetHeight; // force reflow
      p.style.animation = '';
    }
  });

  loadPanel(document.getElementById('tab-' + target));

  // Show hero + reading guide only on Quick Start tab
  var isHome = target === 'qs';
  if (hero) hero.style.display = isHome ? '' : 'none';
  if (readingGuide) readingGuide.style.display = isHome ? '' : 'none';

  // Adjust main padding: hero pushes main down naturally on QS,
  // other tabs need full padding to clear fixed nav + tabs bar.
  if (mainEl) mainEl.style.paddingTop = isHome ? '16px' : '';

  // URL hash
  if (updateHash) {
    history.replaceState(null, '', '#' + target);
  }

  // Scroll active tab into view
  var activeBtn = document.getElementById('btn-' + target);
  if (activeBtn && tabsBar) {
    activeBtn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }

  window.scrollTo({ top: 0, behavior: 'instant' });
}

// Tab click handlers
tabBtns.forEach(function(tb) {
  tb.addEventListener('click', function() { switchTab(tb.dataset.tab); });
});

// ── Keyboard navigation within tablist ────────────
if (tabsBar) {
  tabsBar.addEventListener('keydown', function(e) {
    var tabs = Array.from(tabBtns);
    var idx  = tabs.indexOf(document.activeElement);
    if (idx === -1) return;

    var newIdx;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      newIdx = (idx + 1) % tabs.length;
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      newIdx = (idx - 1 + tabs.length) % tabs.length;
    } else if (e.key === 'Home') {
      e.preventDefault();
      newIdx = 0;
    } else if (e.key === 'End') {
      e.preventDefault();
      newIdx = tabs.length - 1;
    }

    if (newIdx !== undefined) {
      tabs[newIdx].focus();
      switchTab(tabs[newIdx].dataset.tab);
    }
  });
}

// ── Hash-based deep linking ───────────────────────
function initFromHash() {
  var hash      = location.hash.slice(1);
  var validTabs = Array.from(tabBtns).map(function(b) { return b.dataset.tab; });

  if (hash && validTabs.indexOf(hash) !== -1) {
    switchTab(hash, false);
  } else {
    // Default: QS tab active, hero visible
    if (mainEl) mainEl.style.paddingTop = '16px';
    loadPanel(document.querySelector('.tab-panel.active'));
  }
}

initFromHash();
window.addEventListener('hashchange', initFromHash);

// ── Reading guide card click handlers ─────────────
document.querySelectorAll('.rg-card[data-guide]').forEach(function(card) {
  function handler() { switchTab(card.dataset.guide); }
  card.addEventListener('click', handler);
  card.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handler();
    }
  });
});

// ── Hero CTA ──────────────────────────────────────
var heroCta = document.getElementById('heroGetStarted');
if (heroCta) {
  heroCta.addEventListener('click', function(e) {
    e.preventDefault();
    switchTab('qs');
  });
}

// ── Nav logo → home ──────────────────────────────
var navLogo = document.getElementById('navLogoLink');
if (navLogo) {
  navLogo.addEventListener('click', function(e) {
    e.preventDefault();
    switchTab('qs');
  });
}

// ── Tab scroll affordance ─────────────────────────
function updateScrollHints() {
  if (!tabsWrap || !tabsBar) return;
  var sl = tabsBar.scrollLeft;
  var sw = tabsBar.scrollWidth;
  var cw = tabsBar.clientWidth;
  tabsWrap.classList.toggle('scroll-left', sl > 8);
  tabsWrap.classList.toggle('scroll-right', sl < sw - cw - 8);
}

if (tabsBar && tabsWrap) {
  tabsBar.addEventListener('scroll', updateScrollHints, { passive: true });
  window.addEventListener('resize', updateScrollHints);
  // Initial check after layout + fonts
  requestAnimationFrame(updateScrollHints);
  if (document.fonts) document.fonts.ready.then(updateScrollHints);
}
