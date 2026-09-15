// Melhorias de interação do Portal Python.
// Não altera rotas, autenticação nem regras de negócio — apenas feedback visual.
(function () {
  'use strict';

  var DELETE_ACTION_PATTERN = /(excluir|delete|remover)/i;

  function isDeleteForm(form) {
    if (!form || form.tagName !== 'FORM') return false;
    if (form.hasAttribute('data-no-confirm')) return false;
    var action = form.getAttribute('action') || form.action || '';
    return DELETE_ACTION_PATTERN.test(action);
  }

  function findSubmitControl(form, submitter) {
    if (submitter && (submitter.tagName === 'BUTTON' || submitter.tagName === 'INPUT')) {
      return submitter;
    }
    return form.querySelector('button[type="submit"], input[type="submit"]') || null;
  }

  function setButtonLoading(btn) {
    if (!btn) return;
    btn.classList.add('is-loading');
    btn.setAttribute('aria-busy', 'true');
    btn.disabled = true;
  }

  // ------------------------------------------------------------------
  // Confirmação antes de excluir (substitui o confirm() nativo do navegador
  // por um cartão consistente com o visual do portal).
  // ------------------------------------------------------------------
  function askConfirmation(message) {
    return new Promise(function (resolve) {
      var backdrop = document.createElement('div');
      backdrop.className = 'pjm-confirm-backdrop';

      var card = document.createElement('div');
      card.className = 'pjm-confirm-card';
      card.setAttribute('role', 'alertdialog');
      card.setAttribute('aria-modal', 'true');
      card.innerHTML =
        '<h2>Confirmar exclusão</h2>' +
        '<p>' + message + '</p>' +
        '<div class="pjm-confirm-actions">' +
        '<button type="button" class="btn secondary" data-action="cancel">Cancelar</button>' +
        '<button type="button" class="btn" data-action="confirm">Excluir</button>' +
        '</div>';

      backdrop.appendChild(card);
      document.body.appendChild(backdrop);

      var confirmBtn = card.querySelector('[data-action="confirm"]');
      var cancelBtn = card.querySelector('[data-action="cancel"]');
      var previouslyFocused = document.activeElement;

      function cleanup(result) {
        document.removeEventListener('keydown', onKeydown);
        backdrop.remove();
        if (previouslyFocused && previouslyFocused.focus) previouslyFocused.focus();
        resolve(result);
      }

      function onKeydown(evt) {
        if (evt.key === 'Escape') cleanup(false);
      }

      confirmBtn.addEventListener('click', function () { cleanup(true); });
      cancelBtn.addEventListener('click', function () { cleanup(false); });
      backdrop.addEventListener('click', function (evt) {
        if (evt.target === backdrop) cleanup(false);
      });

      document.addEventListener('keydown', onKeydown);
      confirmBtn.focus();
    });
  }

  // ------------------------------------------------------------------
  // Envio de formulários: evita duplo clique e mostra estado de carregamento.
  // Para formulários de exclusão, pede confirmação antes de enviar.
  // ------------------------------------------------------------------
  // Formulários de exclusão pedem confirmação própria: evita que o loader de
  // página inteira (loading.js) apareça e fique preso atrás do diálogo.
  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('form').forEach(function (form) {
      if (isDeleteForm(form)) form.dataset.noLoader = '';
    });
  });

  var lastSubmitter = null;

  document.addEventListener('click', function (evt) {
    var target = evt.target.closest('button, input[type="submit"]');
    if (target) lastSubmitter = target;
  }, true);

  document.addEventListener('submit', function (evt) {
    var form = evt.target;
    if (!form || form.tagName !== 'FORM') return;
    if (form.dataset.pjmConfirmed === 'true') return; // já confirmado, deixa seguir

    var submitBtn = findSubmitControl(form, lastSubmitter);

    if (isDeleteForm(form)) {
      evt.preventDefault();
      askConfirmation('Esta ação não pode ser desfeita. Deseja continuar?').then(function (ok) {
        if (!ok) return;
        form.dataset.pjmConfirmed = 'true';
        setButtonLoading(submitBtn);
        if (typeof form.requestSubmit === 'function') {
          form.requestSubmit(submitBtn && submitBtn.type === 'submit' ? submitBtn : undefined);
        } else {
          form.submit();
        }
      });
      return;
    }

    if (form.dataset.pjmSubmitting === 'true') {
      evt.preventDefault();
      return;
    }
    form.dataset.pjmSubmitting = 'true';
    setButtonLoading(submitBtn);

    // Reset de segurança: se algo impedir a navegação (erro de validação
    // tratado por outro script, por exemplo), o botão não fica travado.
    window.setTimeout(function () {
      form.dataset.pjmSubmitting = 'false';
      if (submitBtn) {
        submitBtn.classList.remove('is-loading');
        submitBtn.removeAttribute('aria-busy');
        submitBtn.disabled = false;
      }
    }, 8000);
  }, true);

  // ------------------------------------------------------------------
  // Mensagens flash: some sozinha depois de um tempo, sem exigir clique.
  // ------------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    var flashes = document.querySelectorAll('.flash');
    flashes.forEach(function (flash, index) {
      window.setTimeout(function () {
        flash.classList.add('is-dismissing');
        window.setTimeout(function () {
          flash.remove();
        }, 400);
      }, 6000 + index * 400);
    });
  });
})();
