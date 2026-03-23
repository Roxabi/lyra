/* 414-personality-voice.js — tab loader · theme toggle · hash routing */

const loaded = new Set();

async function loadPanel(id) {
  if (loaded.has(id)) return;
  const res = await fetch(`tabs/414/tab-${id}.html`);
  const html = await res.text();
  document.getElementById(id).innerHTML = html;
  loaded.add(id);
}

// Tab switching
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tc').forEach(c => c.classList.remove('active'));
    btn.classList.add('active');
    await loadPanel(id);
    document.getElementById(id).classList.add('active');
    history.replaceState(null, '', '#' + id);
    window.scrollTo({ top: 0, behavior: 'instant' });
  });
});

// Theme toggle
const root = document.documentElement;
const themeBtn = document.querySelector('.theme-btn');
const saved = localStorage.getItem('lyra-414-theme');
if (saved) root.dataset.theme = saved;

function updateBtn() {
  if (themeBtn) themeBtn.textContent = root.dataset.theme === 'light' ? '◐ dark' : '◑ light';
}
themeBtn?.addEventListener('click', () => {
  root.dataset.theme = root.dataset.theme === 'light' ? 'dark' : 'light';
  localStorage.setItem('lyra-414-theme', root.dataset.theme);
  updateBtn();
});
updateBtn();

// Initial tab from hash
const validTabs = ['lyra', 'industry', 'constraints'];
const hash = location.hash.slice(1);
const initial = validTabs.includes(hash) ? hash : 'lyra';

document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
const initBtn = document.querySelector(`[data-tab="${initial}"]`);
if (initBtn) initBtn.classList.add('active');

document.querySelectorAll('.tc').forEach(c => c.classList.remove('active'));
document.getElementById(initial).classList.add('active');
loadPanel(initial);
