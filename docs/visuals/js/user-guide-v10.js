const loaded = new Set();

async function loadPanel(id) {
  if (loaded.has(id)) return;
  const res = await fetch(`tabs/v10/tab-${id}.html`);
  const html = await res.text();
  document.getElementById(id).innerHTML = html;
  loaded.add(id);
}

async function switchTab(id) {
  document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${id}"]`);
  if (btn) {
    btn.classList.add('active');
    btn.scrollIntoView({ block: 'nearest', inline: 'center' });
  }
  await loadPanel(id);
  document.getElementById(id).classList.add('active');
}
window.switchTab = switchTab;

document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// Theme toggle
(function () {
  const html = document.documentElement;
  const themeBtn = document.getElementById('themeBtn');

  function applyTheme(theme) {
    html.dataset.theme = theme;
    try { localStorage.setItem('lyra-theme', theme); } catch (e) {}
  }

  let saved;
  try { saved = localStorage.getItem('lyra-theme'); } catch (e) {}
  const sys = window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  applyTheme(saved || sys);

  themeBtn.addEventListener('click', () => {
    applyTheme(html.dataset.theme === 'dark' ? 'light' : 'dark');
  });
})();

// Load initial tab (script is at end of body — DOM is ready)
switchTab('qs');
