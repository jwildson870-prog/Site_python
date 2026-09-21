(() => {
  const box = document.getElementById('ai-feedback');
  if (!box) return;

  const attemptId = box.dataset.attemptId;
  const generate = box.querySelector('[data-ai-feedback-generate]');
  const loading = box.querySelector('[data-ai-feedback-loading]');
  const error = box.querySelector('[data-ai-feedback-error]');
  const text = box.querySelector('[data-ai-feedback-text]');
  const rating = box.querySelector('[data-ai-feedback-rating]');
  const rated = box.querySelector('[data-ai-feedback-rated]');

  const csrf = document.querySelector('meta[name="csrf-token"]')?.content ||
    document.querySelector('input[name="csrf_token"]')?.value || '';

  async function load() {
    if (!generate) return;
    generate.disabled = true;
    generate.hidden = true;
    loading.hidden = false;
    error.hidden = true;
    try {
      const response = await fetch(`/ai/feedback/${attemptId}`, {
        headers: { 'Accept': 'application/json' },
        credentials: 'same-origin'
      });
      if (!response.ok) throw new Error('feedback-unavailable');
      const data = await response.json();
      if (!data.has_errors) {
        error.textContent = 'Não há erros objetivos para gerar feedback.';
        error.hidden = false;
        return;
      }
      loading.hidden = true;
      text.textContent = data.feedback || '';
      text.hidden = false;
      rating.hidden = false;
    } catch (err) {
      loading.hidden = true;
      error.hidden = false;
      generate.hidden = false;
      generate.disabled = false;
    }
  }

  if (generate) generate.addEventListener('click', load);

  box.querySelectorAll('[data-ai-feedback-rate]').forEach(button => {
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      const buttons = box.querySelectorAll('[data-ai-feedback-rate]');
      buttons.forEach(item => { item.disabled = true; });
      try {
        const response = await fetch(`/ai/feedback/${attemptId}/rating`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrf,
            'Accept': 'application/json'
          },
          credentials: 'same-origin',
          body: JSON.stringify({ rating: button.dataset.aiFeedbackRate })
        });
        if (!response.ok) throw new Error('rating-failed');
        rated.hidden = false;
      } catch (err) {
        buttons.forEach(item => { item.disabled = false; });
      }
    });
  });

})();
