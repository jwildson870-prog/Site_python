(function () {
  'use strict';

  function closeSidebar() {
    const menu = document.getElementById('sideMenu');
    const overlay = document.getElementById('menuOverlay');
    const toggle = document.getElementById('menuToggle');
    menu?.classList.remove('open');
    overlay?.classList.remove('open');
    toggle?.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('menu-open');
  }

  function openSidebar() {
    const menu = document.getElementById('sideMenu');
    const overlay = document.getElementById('menuOverlay');
    const toggle = document.getElementById('menuToggle');
    menu?.classList.add('open');
    overlay?.classList.add('open');
    toggle?.setAttribute('aria-expanded', 'true');
    document.body.classList.add('menu-open');
  }

  function initSidebar() {
    const toggle = document.getElementById('menuToggle');
    const close = document.getElementById('menuClose');
    const overlay = document.getElementById('menuOverlay');

    toggle?.addEventListener('click', () => {
      const menu = document.getElementById('sideMenu');
      if (menu?.classList.contains('open')) closeSidebar();
      else openSidebar();
    });
    close?.addEventListener('click', closeSidebar);
    overlay?.addEventListener('click', closeSidebar);

    document.querySelectorAll('.side-link').forEach((link) => {
      link.addEventListener('click', () => {
        if (window.innerWidth <= 900) closeSidebar();
      });
    });

    window.addEventListener('resize', () => {
      if (window.innerWidth > 900) closeSidebar();
    });
  }

  const THEMES = ['dark', 'light', 'pink', 'sea', 'aurora', 'nature'];
  const STORAGE_KEY = 'portal-python-theme';

  function applyTheme(theme) {
    if (!THEMES.includes(theme)) theme = 'dark';
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(STORAGE_KEY, theme); } catch (_) {}
    document.querySelectorAll('[data-theme-choice]').forEach((button) => {
      const active = button.dataset.themeChoice === theme;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      const colors = { dark:'#07100b', light:'#f3f6f4', pink:'#170b16', sea:'#06151b', aurora:'#0b0920', nature:'#07130d' };
      meta.setAttribute('content', colors[theme] || colors.dark);
    }
  }

  function getSavedTheme() {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved && THEMES.includes(saved)) return saved;
    } catch (_) {}
    return 'dark';
  }

  function initThemeSelector() {
    const control = document.getElementById('themeControl');
    const trigger = document.getElementById('themeTrigger');
    const panel = document.getElementById('themePanel');
    if (!control || !trigger || !panel) return;

    applyTheme(getSavedTheme());
    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      const open = panel.classList.toggle('open');
      trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    panel.querySelectorAll('[data-theme-choice]').forEach((button) => {
      button.addEventListener('click', () => {
        applyTheme(button.dataset.themeChoice);
        panel.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
      });
    });
    document.addEventListener('click', (event) => {
      if (!control.contains(event.target)) {
        panel.classList.remove('open');
        trigger.setAttribute('aria-expanded', 'false');
      }
    });
  }

  function initThemes() {
    applyTheme(getSavedTheme());
    initThemeSelector();
  }

  document.addEventListener('DOMContentLoaded', () => {
    initSidebar();
    initThemes();
  });
})();
