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
      // Mensagem específica do item (ex.: "Excluir esta atividade?"), quando
      // o formulário define data-confirm-message; senão, mensagem genérica.
      var message = form.dataset.confirmMessage ||
        'Esta ação não pode ser desfeita. Deseja continuar?';
      askConfirmation(message).then(function (ok) {
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

  // ------------------------------------------------------------------
  // "Desfazer" ao remover um favorito: é uma ação não destrutiva e
  // reversível — o próprio endpoint já alterna favorito/não-favorito —
  // então desfazer é só reenviar o mesmo formulário de novo. Não cria
  // rota nem lógica nova nenhuma, só reaproveita o toggle existente.
  // Notificações não recebem "desfazer" aqui porque marcar como lida
  // não tem uma ação de "voltar a não lida" no backend.
  // ------------------------------------------------------------------
  document.addEventListener('DOMContentLoaded', function () {
    var UNDO_FAVORITE_TEXT = 'Removido dos favoritos.';
    document.querySelectorAll('.flash.success').forEach(function (flash) {
      if ((flash.textContent || '').trim() !== UNDO_FAVORITE_TEXT) return;
      var toggleForm = document.querySelector('form[action*="/favoritar"]');
      if (!toggleForm) return;

      var undoBtn = document.createElement('button');
      undoBtn.type = 'button';
      undoBtn.className = 'text-button flash-undo';
      undoBtn.textContent = 'Desfazer';
      undoBtn.addEventListener('click', function () {
        flash.remove();
        if (typeof toggleForm.requestSubmit === 'function') {
          toggleForm.requestSubmit();
        } else {
          toggleForm.submit();
        }
      });
      flash.appendChild(undoBtn);
    });
  });

  var prefersReducedMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ------------------------------------------------------------------
  // Efeito cascata na entrada dos cartões: cada item do mesmo grupo
  // (mesmo pai) ganha um pequeno atraso extra, para não entrarem todos
  // de uma vez. A animação em si é definida em CSS (interactions.css);
  // aqui só calculamos o atraso de cada item.
  // ------------------------------------------------------------------
  var CARD_SELECTOR = [
    '.achievement-card', '.activity-admin-card', '.activity-settings-card',
    '.bank-import-card', '.calendar-card', '.continue-study-card', '.manage-card',
    '.notification-card', '.performance-summary-card', '.progress-hero-card',
    '.publisher-card', '.question-student-card', '.ready-activity-card',
    '.search-result-card', '.settings-card', '.smart-alert-card',
    '.student-activity-card', '.student-material-card', '.student-profile-card',
    '.student-summary-card', '.upcoming-card', '.professor-metric', '.student-stat'
  ].join(',');

  function staggerCardEntrance() {
    if (prefersReducedMotion) return;
    var STEP_MS = 45;
    var MAX_DELAY_MS = 320;
    var seenParents = [];

    document.querySelectorAll(CARD_SELECTOR).forEach(function (card) {
      var parent = card.parentElement;
      if (!parent) return;

      var parentIndex = seenParents.indexOf(parent);
      if (parentIndex === -1) {
        parent._pjmChildCount = 0;
        seenParents.push(parent);
        parentIndex = seenParents.length - 1;
      }

      var order = parent._pjmChildCount || 0;
      parent._pjmChildCount = order + 1;

      var delay = Math.min(order * STEP_MS, MAX_DELAY_MS);
      card.style.setProperty('--pjm-in-delay', delay + 'ms');
    });
  }

  document.addEventListener('DOMContentLoaded', staggerCardEntrance);

  // ------------------------------------------------------------------
  // Contagem animada dos números de estatística (materiais, atividades,
  // projetos etc.). Só anima uma vez por elemento, quando ele entra na
  // tela, e preserva o formato original (casas decimais, "%", "—").
  // ------------------------------------------------------------------
  var STAT_SELECTOR = [
    '.professor-metric strong', '.student-stat strong',
    '.student-summary-card strong', '.performance-summary-card strong',
    '.progress-hero-card strong'
  ].join(',');

  function animateCountUp(el) {
    var raw = (el.textContent || '').trim();
    var match = raw.match(/^(\d+(?:[.,]\d+)?)(.*)$/);
    if (!match) return; // ex.: "—" (sem dado ainda) — não anima

    var target = parseFloat(match[1].replace(',', '.'));
    var suffix = match[2] || '';
    var decimals = (match[1].split(/[.,]/)[1] || '').length;

    if (prefersReducedMotion || !isFinite(target)) return;

    var duration = 700;
    var start = null;

    function step(timestamp) {
      if (start === null) start = timestamp;
      var progress = Math.min((timestamp - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      var value = target * eased;
      el.textContent = value.toFixed(decimals) + suffix;
      if (progress < 1) {
        window.requestAnimationFrame(step);
      } else {
        el.textContent = raw; // garante o valor/formatação original no final
      }
    }

    window.requestAnimationFrame(step);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var stats = document.querySelectorAll(STAT_SELECTOR);
    if (!stats.length) return;

    if (prefersReducedMotion || typeof IntersectionObserver === 'undefined') {
      return; // mantém os números como já vêm renderizados pelo servidor
    }

    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        animateCountUp(entry.target);
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.4 });

    stats.forEach(function (stat) { observer.observe(stat); });
  });
})();
