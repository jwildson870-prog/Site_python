// Mostrar/ocultar senha no login do Portal Python.
// Apenas alterna o atributo "type" do campo; não interfere no envio do
// formulário nem no valor digitado pelo usuário.
(function () {
  document.querySelectorAll('.password-toggle').forEach(function (button) {
    var input = document.getElementById(button.getAttribute('data-target'));
    if (!input) return;

    var icon = button.querySelector('img');
    var eyeIcon = icon ? icon.getAttribute('src') : '';
    var eyeOffIcon = button.getAttribute('data-icon-off') || eyeIcon;

    button.addEventListener('click', function () {
      var isHidden = input.type === 'password';
      input.type = isHidden ? 'text' : 'password';

      var label = isHidden ? 'Ocultar senha' : 'Mostrar senha';
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.setAttribute('aria-pressed', String(isHidden));

      if (icon) icon.src = isHidden ? eyeOffIcon : eyeIcon;
    });
  });
})();
