/* flashlight.js — atualiza as variáveis --mouse-x / --mouse-y dos elementos .flashlight-target conforme o cursor (extraído de src/App.jsx) */
(function () {
  function handleMouseMove(e) {
    document.querySelectorAll('.flashlight-target').forEach(function (target) {
      var rect = target.getBoundingClientRect();
      target.style.setProperty('--mouse-x', (e.clientX - rect.left) + 'px');
      target.style.setProperty('--mouse-y', (e.clientY - rect.top) + 'px');
    });
  }
  window.addEventListener('mousemove', handleMouseMove);
})();
