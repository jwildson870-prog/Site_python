(() => {
  const root = document.querySelector('[data-ai-class-summary]');
  if (!root) return;

  const button = root.querySelector('[data-ai-summary-generate]');
  const card = root.querySelector('[data-ai-summary-card]');
  const text = root.querySelector('[data-ai-summary-text]');
  const status = root.querySelector('[data-ai-summary-status]');
  const time = root.querySelector('[data-ai-summary-time]');
  const csrf = root.querySelector('[data-ai-summary-csrf]')?.value || '';
  let metrics = {};

  try {
    metrics = JSON.parse(root.querySelector('[data-ai-summary-metrics]')?.textContent || '{}');
  } catch (_) {
    metrics = {};
  }

  if (!button || !card || !text) return;

  button.addEventListener('click', async () => {
    button.disabled = true;
    if (status) status.textContent = 'Gerando resumo...';

    try {
      const response = await fetch('/ai/class-summary', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({csrf_token: csrf, metrics})
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Não foi possível gerar o resumo.');
      }

      // textContent mantém o retorno como texto mesmo se a IA devolver HTML.
      text.textContent = data.text || 'A IA não encontrou informações suficientes para resumir.';
      if (time) {
        const date = data.timestamp ? new Date(data.timestamp) : new Date();
        time.textContent = `Gerado em ${date.toLocaleString('pt-BR')}`;
      }
      card.hidden = false;
      if (status) status.textContent = 'Resumo atualizado.';
    } catch (error) {
      if (status) status.textContent = error.message || 'Resumo indisponível no momento, tente novamente em instantes.';
    } finally {
      button.disabled = false;
    }
  });
})();
