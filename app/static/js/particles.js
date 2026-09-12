(function () {
  'use strict';

  const canvas = document.getElementById('spaceParticles');
  if (!canvas) return;

  const ctx = canvas.getContext('2d', { alpha: true });
  if (!ctx) return;

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  const mobile = window.matchMedia('(max-width: 680px)');
  let particles = [];
  let width = 0;
  let height = 0;
  let animationFrame = 0;
  let lastTime = 0;

  function count() {
    if (reduceMotion.matches) return 28;
    if (mobile.matches) return 42;
    return Math.min(90, Math.max(55, Math.round((width * height) / 18000)));
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
    createParticles();
  }

  function createParticles() {
    particles = Array.from({ length: count() }, function () {
      const angle = Math.random() * Math.PI * 2;
      const speed = 0.06 + Math.random() * 0.16;
      return {
        x: Math.random() * width,
        y: Math.random() * height,
        r: 0.55 + Math.random() * 1.35,
        alpha: 0.16 + Math.random() * 0.48,
        vx: Math.cos(angle) * speed,
        vy: Math.sin(angle) * speed,
        twinkle: Math.random() * Math.PI * 2
      };
    });
  }

  function draw(time) {
    const delta = Math.min(32, time - lastTime || 16);
    lastTime = time;
    ctx.clearRect(0, 0, width, height);

    particles.forEach(function (p) {
      if (!reduceMotion.matches) {
        p.x += p.vx * delta;
        p.y += p.vy * delta;
        p.twinkle += delta * 0.0012;
        if (p.x < -10) p.x = width + 10;
        if (p.x > width + 10) p.x = -10;
        if (p.y < -10) p.y = height + 10;
        if (p.y > height + 10) p.y = -10;
      }

      const pulse = 0.78 + Math.sin(p.twinkle) * 0.22;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(125, 211, 252, ' + (p.alpha * pulse) + ')';
      ctx.fill();
    });

    // Conecta apenas partículas próximas para criar uma rede espacial discreta.
    if (!reduceMotion.matches && !mobile.matches) {
      for (let i = 0; i < particles.length; i += 1) {
        for (let j = i + 1; j < particles.length; j += 1) {
          const a = particles[i];
          const b = particles[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const distance = Math.sqrt(dx * dx + dy * dy);
          if (distance < 115) {
            const opacity = (1 - distance / 115) * 0.075;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.strokeStyle = 'rgba(34, 211, 238, ' + opacity + ')';
            ctx.lineWidth = 0.7;
            ctx.stroke();
          }
        }
      }
    }

    animationFrame = window.requestAnimationFrame(draw);
  }

  window.addEventListener('resize', resize, { passive: true });
  reduceMotion.addEventListener?.('change', resize);
  mobile.addEventListener?.('change', resize);
  resize();
  animationFrame = window.requestAnimationFrame(draw);

  window.addEventListener('pagehide', function () {
    window.cancelAnimationFrame(animationFrame);
  });
})();
