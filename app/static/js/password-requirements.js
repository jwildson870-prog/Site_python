// Feedback visual de requisitos de senha e conferência de confirmação de
// senha no cadastro e na redefinição de senha do Portal Python.
//
// Importante: isto é só uma conveniência para o usuário ver o progresso
// antes de enviar o formulário. A validação que realmente decide se a
// senha é aceita acontece sempre no servidor (auth/routes.py,
// password_errors()) — este script nunca bloqueia o envio do formulário.
(function () {
  var passwordInput = document.getElementById('registerPassword');
  var confirmInput = document.getElementById('registerConfirmPassword');
  var requirementsList = document.getElementById('passwordRequirements');
  var confirmHint = document.getElementById('confirmPasswordHint');

  function checkRequirements() {
    if (!passwordInput || !requirementsList) return;
    var value = passwordInput.value || '';
    var checks = {
      length: value.length >= (parseInt(passwordInput.getAttribute('minlength'), 10) || 10),
      letter: /[A-Za-z]/.test(value),
      digit: /[0-9]/.test(value)
    };
    requirementsList.querySelectorAll('li[data-requirement]').forEach(function (li) {
      var key = li.getAttribute('data-requirement');
      var met = !!checks[key];
      li.classList.toggle('met', met);
      li.classList.toggle('pending', !met);
    });
  }

  function checkConfirm() {
    if (!passwordInput || !confirmInput || !confirmHint) return;
    if (!confirmInput.value) { confirmHint.textContent = ''; confirmHint.classList.remove('ok'); return; }
    if (confirmInput.value === passwordInput.value) {
      confirmHint.textContent = 'As senhas coincidem.';
      confirmHint.classList.add('ok');
    } else {
      confirmHint.textContent = 'As senhas ainda não coincidem.';
      confirmHint.classList.remove('ok');
    }
  }

  if (passwordInput) passwordInput.addEventListener('input', function () { checkRequirements(); checkConfirm(); });
  if (confirmInput) confirmInput.addEventListener('input', checkConfirm);
  checkRequirements();
})();
