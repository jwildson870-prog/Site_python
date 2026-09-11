(function () {
  'use strict';

  let deferredPrompt = null;
  const installButton = document.getElementById('pwaInstallButton');

  function isStandalone() {
    return window.matchMedia('(display-mode: standalone)').matches ||
      window.navigator.standalone === true;
  }

  function isIOS() {
    return /iphone|ipad|ipod/i.test(window.navigator.userAgent) && !window.MSStream;
  }

  function showInstallButton() {
    if (installButton && !isStandalone()) {
      installButton.hidden = false;
    }
  }

  function hideInstallButton() {
    if (installButton) installButton.hidden = true;
  }

  function showIOSInstructions() {
    window.alert('Para instalar no iPhone/iPad: toque em Compartilhar no Safari e escolha “Adicionar à Tela de Início”.');
  }

  window.addEventListener('beforeinstallprompt', function (event) {
    event.preventDefault();
    deferredPrompt = event;
    showInstallButton();
  });

  window.addEventListener('appinstalled', function () {
    deferredPrompt = null;
    hideInstallButton();
  });

  installButton?.addEventListener('click', async function () {
    if (isStandalone()) return;

    if (deferredPrompt) {
      deferredPrompt.prompt();
      const result = await deferredPrompt.userChoice;
      if (result.outcome === 'accepted') hideInstallButton();
      deferredPrompt = null;
      return;
    }

    if (isIOS()) {
      showIOSInstructions();
      return;
    }

    window.alert('Se a opção de instalação não aparecer, abra o menu do navegador e escolha “Instalar aplicativo” ou “Adicionar à tela inicial”.');
  });

  // Alguns navegadores não expõem beforeinstallprompt, mas continuam permitindo
  // a instalação pelo menu. O botão fica disponível nesses casos quando faz sentido.
  window.addEventListener('load', function () {
    if (!isStandalone() && (isIOS() || /android/i.test(navigator.userAgent))) {
      showInstallButton();
    }
  });
})();


// Registra o service worker para habilitar o funcionamento como PWA.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('/static/service-worker.js').catch(function (error) {
      console.warn('Não foi possível registrar o Service Worker:', error);
    });
  });
}
