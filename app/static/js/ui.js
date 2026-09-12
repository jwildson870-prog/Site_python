// Melhorias de interface do Portal Python.
// A autenticação, sessão e regras de negócio continuam no servidor.
(function () {
  const menuCheck = document.getElementById('menuCheck');
  const menuToggle = document.querySelector('.menu-toggle');
  const menu = document.querySelector('.side-menu');
  const overlay = document.querySelector('.menu-overlay');

  if (!menuCheck || !menu || !menuToggle) return;

  const mobileQuery = window.matchMedia('(max-width: 760px)');

  function syncMenu() {
    const isOpen = menuCheck.checked;
    const isMobile = mobileQuery.matches || document.documentElement.classList.contains('mobile-device');

    menuToggle.setAttribute('aria-expanded', String(isOpen));
    menuToggle.setAttribute('aria-label', isOpen ? 'Fechar menu' : 'Abrir menu');
    menuToggle.setAttribute('title', isOpen ? 'Fechar menu' : 'Abrir menu');
    menu.setAttribute('aria-hidden', String(!isOpen && isMobile));

    if (isMobile) {
      document.body.classList.toggle('menu-open', isOpen);
    } else {
      document.body.classList.remove('menu-open');
    }
  }

  menuCheck.addEventListener('change', syncMenu);

  // Ao escolher uma página no celular, fecha a gaveta antes da navegação.
  menu.querySelectorAll('a').forEach(function (link) {
    link.addEventListener('click', function () {
      if (mobileQuery.matches || document.documentElement.classList.contains('mobile-device')) {
        menuCheck.checked = false;
        syncMenu();
      }
    });
  });

  // Esc fecha o menu.
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && menuCheck.checked) {
      menuCheck.checked = false;
      syncMenu();
      menuToggle.focus();
    }
  });

  // Se o usuário passar para desktop com o menu aberto, limpa o estado mobile.
  function handleViewportChange() {
    syncMenu();
  }

  if (mobileQuery.addEventListener) {
    mobileQuery.addEventListener('change', handleViewportChange);
  } else if (mobileQuery.addListener) {
    mobileQuery.addListener(handleViewportChange);
  }

  if (overlay) {
    overlay.addEventListener('click', function () {
      menuCheck.checked = false;
      syncMenu();
    });
  }

  syncMenu();
})();
