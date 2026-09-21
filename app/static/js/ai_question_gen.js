(() => {
  const content = document.getElementById('aiQuestionContent');
  const count = document.getElementById('aiQuestionCount');
  const generate = document.getElementById('aiQuestionGenerate');
  const status = document.getElementById('aiQuestionStatus');
  const drafts = document.getElementById('aiQuestionDrafts');
  const list = document.getElementById('aiQuestionDraftList');
  const csrf = document.getElementById('aiQuestionCsrf')?.value || '';
  if (!content || !generate || !list) return;

  let currentContentId = null;
  let draftQuestions = [];

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));

  function render() {
    drafts.hidden = draftQuestions.length === 0;
    list.innerHTML = draftQuestions.map((q, index) => `
      <article class="publisher-card ai-question-draft" data-index="${index}">
        <div class="material-meta"><span class="badge">🤖 RASCUNHO IA</span><span class="badge subtle-badge">${esc(q.difficulty)}</span></div>
        <label class="field-block full">Enunciado<textarea class="draft-question" maxlength="1000">${esc(q.question)}</textarea></label>
        <div class="options question-bank-options">${q.options.map((option, oi) => `<label>${'ABCD'[oi]}<input class="draft-option" data-option="${oi}" maxlength="500" value="${esc(option)}"></label>`).join('')}</div>
        <label class="field-block" style="display:block;max-width:280px;">Resposta correta<select class="draft-correct">${q.options.map((_, oi) => `<option value="${oi}" ${oi === Number(q.correct_index) ? 'selected' : ''}>${'ABCD'[oi]}</option>`).join('')}</select></label>
        <label class="field-block">Categoria<input class="draft-category" maxlength="100" value="${esc(q.category)}"></label>
        <label class="field-block">Tags<input class="draft-tags" maxlength="1000" value="${esc(q.tags)}"></label>
        <label class="field-block full">Código (opcional)<textarea class="draft-code" maxlength="8000">${esc(q.code)}</textarea></label>
        <div class="button-row"><button class="btn admin-primary save-draft" type="button">Salvar no banco</button><button class="btn danger discard-draft" type="button">Descartar</button></div>
      </article>`).join('');
  }

  function readDraft(card) {
    const options = [...card.querySelectorAll('.draft-option')].map(x => x.value.trim());
    return {
      question: card.querySelector('.draft-question')?.value.trim() || '',
      options,
      correct_index: Number(card.querySelector('.draft-correct')?.value || 0),
      difficulty: draftQuestions[Number(card.dataset.index)]?.difficulty || 'medio',
      category: card.querySelector('.draft-category')?.value.trim() || 'Geral',
      tags: card.querySelector('.draft-tags')?.value.trim() || '',
      code: card.querySelector('.draft-code')?.value || ''
    };
  }

  generate.addEventListener('click', async () => {
    currentContentId = content.value;
    if (!currentContentId) { status.textContent = 'Selecione um conteúdo primeiro.'; return; }
    generate.disabled = true;
    status.textContent = 'Gerando rascunhos...';
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 30000);
    try {
      const response = await fetch(`/ai/questions/generate/${currentContentId}`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        signal: controller.signal,
        body: JSON.stringify({csrf_token: csrf, count: Math.max(1, Math.min(10, Number(count.value) || 3))})
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Não foi possível gerar as questões.');
      draftQuestions = data.questions || [];
      render();
      status.textContent = `${draftQuestions.length} rascunho(s) gerado(s). Revise antes de salvar.`;
    } catch (error) {
      status.textContent = error.name === 'AbortError'
        ? 'A geração demorou demais. Verifique a configuração do Gemini no Render e tente novamente.'
        : (error.message || 'Não foi possível gerar as questões.');
    } finally {
      clearTimeout(timeoutId);
      generate.disabled = false;
    }
  });

  list.addEventListener('click', async (event) => {
    const card = event.target.closest('.ai-question-draft');
    if (!card) return;
    const index = Number(card.dataset.index);
    if (event.target.closest('.discard-draft')) {
      draftQuestions.splice(index, 1); render(); status.textContent = 'Rascunho descartado.'; return;
    }
    if (!event.target.closest('.save-draft')) return;
    const question = readDraft(card);
    if (!question.question || question.options.length < 2 || question.options.some(x => !x)) {
      status.textContent = 'Preencha o enunciado e todas as alternativas antes de salvar.'; return;
    }
    const button = event.target.closest('.save-draft');
    button.disabled = true;
    try {
      const response = await fetch('/ai/questions/save', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({csrf_token: csrf, content_id: Number(currentContentId), question})
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Não foi possível salvar a questão.');
      draftQuestions.splice(index, 1); render(); status.textContent = 'Questão salva no banco após confirmação do professor.';
    } catch (error) {
      status.textContent = error.message || 'Não foi possível salvar a questão.';
      button.disabled = false;
    }
  });
})();
