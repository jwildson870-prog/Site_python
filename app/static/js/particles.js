(function () {
  'use strict';

  const canvas = document.getElementById('spaceParticles');
  if (!canvas) return;
  const ctx = canvas.getContext('2d', { alpha: true });
  if (!ctx) return;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const mobile = window.matchMedia('(max-width: 680px)');
  let width = 0, height = 0, animationFrame = 0, lastTime = 0;
  let snippets = [];

  const code = [
    'def calcular_media(notas):',
    '    return sum(notas) / len(notas)',
    'class Aluno:',
    '    def __init__(self, nome):',
    '        self.nome = nome',
    'for aluno in alunos:',
    '    print(aluno.nome)',
    'if nota >= 7:',
    '    aprovado = True',
    'import random',
    'import datetime',
    'while True:',
    '    escolha = input("> ")',
    'lista.append(valor)',
    'def estudar(topico):',
    '    return aprender(topico)',
    'try:',
    '    resultado = calcular()',
    'except ValueError:',
    '    resultado = 0',
    'print("Portal Python")',
    'for i in range(10):',
    '    print(i)',
    'dados = {"nome": "Aluno"}',
    'with open("dados.json") as arquivo:',
    '    dados = json.load(arquivo)'
  ];

  function amount() {
    if (reduceMotion.matches) return mobile.matches ? 5 : 8;
    return mobile.matches ? 7 : 13;
  }

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    createSnippets();
  }

  function createSnippets() {
    snippets = Array.from({ length: amount() }, function (_, index) {
      const text = code[(index * 3 + Math.floor(Math.random() * 4)) % code.length];
      return {
        text: text,
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.018,
        vy: -0.012 - Math.random() * 0.022,
        size: mobile.matches ? 10 + Math.random() * 2 : 12 + Math.random() * 3,
        alpha: mobile.matches ? 0.045 + Math.random() * 0.025 : 0.055 + Math.random() * 0.045,
        phase: Math.random() * Math.PI * 2,
        drift: 0.0005 + Math.random() * 0.0008
      };
    });
  }

  function draw(time) {
    const delta = Math.min(32, time - lastTime || 16);
    lastTime = time;
    ctx.clearRect(0, 0, width, height);

    snippets.forEach(function (s) {
      if (!reduceMotion.matches) {
        s.x += s.vx * delta;
        s.y += s.vy * delta;
        s.phase += delta * s.drift;
        s.x += Math.sin(s.phase) * 0.06;
      }

      const w = ctx.measureText(s.text).width;
      if (s.y < -40) s.y = height + 35;
      if (s.x < -w - 30) s.x = width + 30;
      if (s.x > width + 30) s.x = -w - 30;

      ctx.font = '600 ' + s.size + 'px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
      ctx.fillStyle = 'rgba(103, 211, 255, ' + s.alpha + ')';
      ctx.shadowColor = 'rgba(34, 211, 238, 0.18)';
      ctx.shadowBlur = 7;
      ctx.fillText(s.text, s.x, s.y);
      ctx.shadowBlur = 0;
    });

    animationFrame = window.requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize, { passive: true });
  reduceMotion.addEventListener?.('change', resize);
  mobile.addEventListener?.('change', resize);
  resize();
  animationFrame = window.requestAnimationFrame(draw);
  window.addEventListener('pagehide', function () { window.cancelAnimationFrame(animationFrame); });
})();
