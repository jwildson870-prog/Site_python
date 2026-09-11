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

  document.addEventListener('DOMContentLoaded', initSidebar);
})();
