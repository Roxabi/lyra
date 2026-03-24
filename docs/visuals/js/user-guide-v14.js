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

// ── Forge loader SVG ──────────────────────────────
var FORGE_LOADER = '<div class="forge-loader">'
  + '<svg viewBox="0 0 80 60" xmlns="http://www.w3.org/2000/svg" class="forge-loader-svg">'
  // anvil body
  + '<rect x="20" y="32" width="40" height="10" rx="2" fill="#2a2a35" stroke="#3a3a45" stroke-width="0.8"/>'
  + '<rect x="28" y="42" width="24" height="6" rx="1" fill="#1f2937" stroke="#2a2a35" stroke-width="0.6"/>'
  // anvil top highlight
  + '<line x1="22" y1="32" x2="58" y2="32" stroke="#e85d04" stroke-width="0.5" opacity="0.4"/>'
  // hammer
  + '<rect x="36" y="16" width="8" height="14" rx="1.5" fill="#2a2a35" stroke="#3a3a45" stroke-width="0.6">'
  +   '<animateTransform attributeName="transform" type="rotate" values="0 40 30;-12 40 30;0 40 30" dur="0.6s" repeatCount="indefinite" calcMode="spline" keySplines="0.4 0 0.2 1;0.4 0 0.2 1"/>'
  + '</rect>'
  // sparks — 5 particles burst on each hammer strike
  + '<circle cx="40" cy="32" r="1.2" fill="#e85d04"><animate attributeName="cy" values="32;12;4" dur="0.6s" repeatCount="indefinite"/><animate attributeName="cx" values="40;34;30" dur="0.6s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;0.9;0" dur="0.6s" repeatCount="indefinite"/></circle>'
  + '<circle cx="40" cy="32" r="1" fill="#f97316"><animate attributeName="cy" values="32;16;8" dur="0.6s" begin="0.05s" repeatCount="indefinite"/><animate attributeName="cx" values="40;46;52" dur="0.6s" begin="0.05s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;0.8;0" dur="0.6s" begin="0.05s" repeatCount="indefinite"/></circle>'
  + '<circle cx="40" cy="32" r="0.8" fill="#fafafa"><animate attributeName="cy" values="32;14;2" dur="0.6s" begin="0.1s" repeatCount="indefinite"/><animate attributeName="cx" values="40;42;44" dur="0.6s" begin="0.1s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;0.7;0" dur="0.6s" begin="0.1s" repeatCount="indefinite"/></circle>'
  + '<circle cx="40" cy="32" r="1" fill="#e85d04"><animate attributeName="cy" values="32;18;10" dur="0.6s" begin="0.08s" repeatCount="indefinite"/><animate attributeName="cx" values="40;36;28" dur="0.6s" begin="0.08s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;0.6;0" dur="0.6s" begin="0.08s" repeatCount="indefinite"/></circle>'
  + '<circle cx="40" cy="32" r="0.7" fill="#fafafa"><animate attributeName="cy" values="32;20;14" dur="0.6s" begin="0.12s" repeatCount="indefinite"/><animate attributeName="cx" values="40;48;56" dur="0.6s" begin="0.12s" repeatCount="indefinite"/><animate attributeName="opacity" values="0;0.5;0" dur="0.6s" begin="0.12s" repeatCount="indefinite"/></circle>'
  + '</svg>'
  + '<span class="forge-loader-text">Forging…</span>'
  + '</div>';

// ── Lazy content loading with decreasing forge loader ──
var tabsOpened = 0;
function getLoaderDelay() {
  // 1.2s → 0.5s over the first ~8 tabs
  var delay = Math.max(500, 1200 - tabsOpened * 100);
  tabsOpened++;
  return delay;
}

async function loadPanel(panel) {
  var src = panel.dataset.src;
  if (!src || panel.dataset.loaded === 'ok') return;
  // Show forge loader with decreasing minimum display time
  panel.innerHTML = FORGE_LOADER;
  var minDelay = getLoaderDelay();
  var loadStart = Date.now();
  try {
    var r = await fetch(src);
    if (!r.ok) throw new Error(r.status + ' ' + r.statusText);
    var content = await r.text();
    // Wait remaining delay so the anvil animation is visible
    var elapsed = Date.now() - loadStart;
    if (elapsed < minDelay) {
      await new Promise(function(resolve) { setTimeout(resolve, minDelay - elapsed); });
    }
    panel.innerHTML = content;
    panel.dataset.loaded = 'ok';
  } catch (e) {
    panel.dataset.loaded = 'error';
    panel.innerHTML = '<div style="padding:2rem;color:var(--textdim)">Failed to load ' + src + ': ' + e.message + '</div>';
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

  // Sync mobile dropdown
  var mobileSelect = document.getElementById('tabsMobile');
  if (mobileSelect) mobileSelect.value = target;

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

// Mobile dropdown handler
var tabsMobile = document.getElementById('tabsMobile');
if (tabsMobile) {
  tabsMobile.addEventListener('change', function() { switchTab(this.value); });
}

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
    // If already on QS tab, scroll to content; otherwise switch to QS
    var activeTab = document.querySelector('.tab-btn.active');
    if (activeTab && activeTab.dataset.tab === 'qs') {
      var rg = document.querySelector('.reading-guide') || document.querySelector('.tabs-wrap');
      if (rg) rg.scrollIntoView({ behavior: 'smooth' });
    } else {
      switchTab('qs');
    }
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

// ── Glitch text decode — Black Mirror style ──────
var GLITCH_CHARS = "!@#$%^&*()_+-=[]{}|;:',.<>?/~`0123456789";
function glitchReveal(el, delayMs) {
  var target = el.getAttribute('data-glitch');
  if (!target) return;
  var len = target.length;
  var duration = 2000; // 2s settle
  var startTime = null;
  var settled = false;

  // Measure each target character's real width in the actual font
  el.textContent = target;
  el.style.opacity = '0';
  el.style.visibility = 'hidden';
  // Force layout so we can measure
  var cs = getComputedStyle(el);
  var measurer = document.createElement('span');
  measurer.style.cssText = 'font:' + cs.font + ';font-weight:' + cs.fontWeight
    + ';letter-spacing:' + cs.letterSpacing + ';text-transform:' + cs.textTransform
    + ';position:absolute;visibility:hidden;white-space:pre;';
  document.body.appendChild(measurer);
  var charWidths = [];
  for (var m = 0; m < len; m++) {
    measurer.textContent = target[m];
    charWidths.push(measurer.getBoundingClientRect().width);
  }
  document.body.removeChild(measurer);

  // Build character slots — group letters into nowrap word spans so words don't break mid-line
  var slots = [];
  el.innerHTML = '';
  el.style.visibility = '';
  var words = target.split(' ');
  var charIdx = 0;
  for (var w = 0; w < words.length; w++) {
    if (w > 0) {
      el.appendChild(document.createTextNode(' '));
      slots.push(null); // slot for the space
      charIdx++; // skip the space in charWidths
    }
    var wordWrap = document.createElement('span');
    wordWrap.style.whiteSpace = 'nowrap';
    wordWrap.style.display = 'inline';
    for (var c = 0; c < words[w].length; c++) {
      var span = document.createElement('span');
      span.style.display = 'inline-block';
      span.style.width = charWidths[charIdx] + 'px';
      span.style.textAlign = 'center';
      span.style.overflow = 'hidden';
      span.textContent = GLITCH_CHARS[Math.floor(Math.random() * GLITCH_CHARS.length)];
      wordWrap.appendChild(span);
      slots.push(span);
      charIdx++;
    }
    el.appendChild(wordWrap);
  }
  el.style.opacity = '1';

  setTimeout(function() {
    function tick(ts) {
      if (!startTime) startTime = ts;
      var elapsed = ts - startTime;
      var progress = Math.min(elapsed / duration, 1);

      for (var i = 0; i < len; i++) {
        var ch = target[i];
        if (ch === ' ') continue;
        var charStart = i / len;
        var charEnd = Math.min((i + 3) / len, 1);
        var charProgress = Math.max(0, Math.min(1, (progress - charStart) / (charEnd - charStart)));
        if (charProgress >= 1) {
          slots[i].textContent = ch;
        } else if (Math.random() < 0.8 * (1 - charProgress)) {
          slots[i].textContent = GLITCH_CHARS[Math.floor(Math.random() * GLITCH_CHARS.length)];
        } else {
          slots[i].textContent = ch;
        }
      }

      if (progress < 1) {
        requestAnimationFrame(tick);
      } else if (!settled) {
        settled = true;
        // Leave spans in place — already showing correct chars
      }
    }
    requestAnimationFrame(tick);
  }, delayMs);
}

// Launch glitch on hero title after logo animation
var glitchEl = document.querySelector('[data-glitch]');
if (glitchEl) {
  glitchEl.style.opacity = '0';
  glitchReveal(glitchEl, 1400); // start at 1.4s (when diamond crystallizes)
}

// ── Reduced motion — skip all animations ─────────
if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  document.querySelectorAll('.hero-mark animate, .hero-mark animateMotion, .hero-mark animateTransform').forEach(function(el) {
    el.setAttribute('dur', '0.001s');
    el.setAttribute('repeatCount', '1');
  });
  // Show text immediately
  if (glitchEl) {
    glitchEl.textContent = glitchEl.getAttribute('data-glitch');
    glitchEl.style.opacity = '1';
  }
  document.querySelectorAll('.hero-sub-reveal, .hero-ctas-reveal').forEach(function(el) {
    el.style.opacity = '1';
    el.style.transform = 'none';
    el.style.animation = 'none';
  });
}

// ── Scroll down button ───────────────────────────
var scrollBtn = document.getElementById('heroScroll');
if (scrollBtn) {
  scrollBtn.addEventListener('click', function() {
    var target = document.querySelector('.reading-guide') || document.querySelector('.tabs-wrap');
    if (target) target.scrollIntoView({ behavior: 'smooth' });
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
