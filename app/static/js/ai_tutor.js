(function () {
  'use strict';

  function init() {
    var root = document.getElementById('aiTutor');
    if (!root) return;
    var contentId = root.dataset.contentId;
    var form = document.getElementById('aiTutorForm');
    var question = document.getElementById('aiTutorQuestion');
    var submit = document.getElementById('aiTutorSubmit');
    var messages = document.getElementById('aiTutorMessages');
    var status = document.getElementById('aiTutorStatus');
    var clear = document.getElementById('aiTutorClear');
    var csrf = document.getElementById('aiTutorCsrf');

    function showStatus(text, isError) {
      status.hidden = !text;
      status.textContent = text || '';
      status.setAttribute('aria-live', 'polite');
      if (isError) status.classList.add('error'); else status.classList.remove('error');
    }

    function addMessage(label, text, ai) {
      var item = document.createElement('div');
      item.style.cssText = 'padding:.85rem 1rem;border-radius:12px;background:rgba(255,255,255,.045);border:1px solid rgba(255,255,255,.09);';
      var heading = document.createElement('strong');
      heading.textContent = ai ? '🤖 Tutor' : label;
      var body = document.createElement('div');
      body.style.marginTop = '.35rem';
      body.style.whiteSpace = 'pre-wrap';
      body.textContent = text;
      item.appendChild(heading);
      item.appendChild(body);
      if (ai) {
        var badge = document.createElement('small');
        badge.textContent = '🤖 Gerado por IA — pode conter erros';
        badge.style.display = 'block';
        badge.style.marginTop = '.55rem';
        item.appendChild(badge);
      }
      messages.appendChild(item);
      item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    fetch('/ai/tutor/status/' + encodeURIComponent(contentId), { credentials: 'same-origin' })
      .then(function (response) {
        if (!response.ok) throw new Error('disabled');
        return response.json();
      })
      .then(function (data) {
        if (data.enabled) root.hidden = false;
      })
      .catch(function () {
        root.remove();
      });

    form.addEventListener('submit', function (event) {
      event.preventDefault();
      var text = question.value.trim();
      if (!text || submit.disabled) return;
      submit.disabled = true;
      question.disabled = true;
      showStatus('Consultando o material...', false);
      addMessage('Você', text, false);

      fetch('/ai/tutor/' + encodeURIComponent(contentId), {
        method: 'POST',
        credentials: 'same-origin',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrf.value
        },
        body: JSON.stringify({ question: text })
      })
        .then(function (response) {
          return response.json().catch(function () { return {}; }).then(function (data) {
            if (!response.ok) {
              var err = data.error || 'Tutor indisponível no momento, tente novamente em instantes.';
              throw new Error(err);
            }
            return data;
          });
        })
        .then(function (data) {
          addMessage('Tutor', data.text, true);
          question.value = '';
          showStatus('', false);
        })
        .catch(function (error) {
          showStatus(error.message || 'Tutor indisponível no momento, tente novamente em instantes.', true);
        })
        .finally(function () {
          submit.disabled = false;
          question.disabled = false;
          question.focus();
        });
    });

    clear.addEventListener('click', function () {
      fetch('/ai/tutor/' + encodeURIComponent(contentId) + '/clear', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRFToken': csrf.value }
      }).then(function () {
        messages.replaceChildren();
        showStatus('Conversa limpa.', false);
      }).catch(function () {
        showStatus('Não foi possível limpar a conversa.', true);
      });
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
