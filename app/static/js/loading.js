(function () {
  'use strict';

  const loader = document.getElementById('pageLoader');
  if (!loader) return;

  let hiding = false;

  function showLoader() {
    hiding = false;
    loader.classList.remove('is-hidden');
    loader.setAttribute('aria-hidden', 'false');
    document.documentElement.classList.add('is-loading');
  }

  function hideLoader() {
    if (hiding) return;
    hiding = true;
    loader.classList.add('is-hidden');
    loader.setAttribute('aria-hidden', 'true');
    document.documentElement.classList.remove('is-loading');
  }

  // Sempre exibe a tela ao abrir/recarregar o portal e a retira depois que a página
  // estiver pronta, evitando que ela fique presa sobre o conteúdo.
  showLoader();

  window.addEventListener('load', function () {
    window.setTimeout(hideLoader, 420);
  }, { once: true });

  // Voltar/avançar pelo histórico também recebe a transição, mas o bfcache
  // não deve deixar o loader preso na tela.
  window.addEventListener('pageshow', function () {
    window.setTimeout(hideLoader, 120);
  });

  function isInternalNavigation(link) {
    if (!link || !link.href) return false;
    if (link.target && link.target !== '_self') return false;
    if (link.hasAttribute('download')) return false;
    if (link.dataset.noLoader !== undefined) return false;
    if (link.getAttribute('href')?.startsWith('#')) return false;
    try {
      const url = new URL(link.href, window.location.href);
      return url.origin === window.location.origin && url.pathname !== window.location.pathname;
    } catch (_) {
      return false;
    }
  }

  document.addEventListener('click', function (event) {
    const link = event.target.closest('a');
    if (isInternalNavigation(link) && !event.defaultPrevented) {
      showLoader();
    }
  }, true);

  // Formulários internos também mostram o carregamento antes da navegação.
  document.addEventListener('submit', function (event) {
    if (event.defaultPrevented) return;
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.noLoader !== undefined) return;
    if ((form.getAttribute('target') || '_self') !== '_self') return;
    showLoader();
  }, true);
})();
