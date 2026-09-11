(() => {
  const text = document.getElementById('smartText');
  const analyze = document.getElementById('smartAnalyze');
  const clear = document.getElementById('smartClear');
  if (!text || !analyze) return;
  const saved = localStorage.getItem('portalPythonStudyText');
  if (saved) { text.value = saved; localStorage.removeItem('portalPythonStudyText'); }
  const stop = new Set('a o e de do da dos das em um uma uns umas para por com sem no na nos nas ao aos as os que se é ou como mais menos muito sua seu suas seus sobre entre também já ser são foi foram este esta esse essa isso aquilo pelo pela pelos pelas'.split(' '));
  const clean = s => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/[^a-z0-9\s]/g,' ');
  const sentences = s => s.replace(/\s+/g,' ').match(/[^.!?]+[.!?]+|[^.!?]+$/g)?.map(x=>x.trim()).filter(x=>x.length>35) || [];
  const words = s => clean(s).split(/\s+/).filter(w=>w.length>3 && !stop.has(w));
  const escape = s => s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));
  function analyzeText(s){
    const ss = sentences(s); const freq={}; words(s).forEach(w=>freq[w]=(freq[w]||0)+1);
    const ranked = Object.entries(freq).sort((a,b)=>b[1]-a[1]).slice(0,10).map(x=>x[0]);
    const selected = ss.map((x,i)=>({x,i,score:words(x).reduce((n,w)=>n+(freq[w]||0),0)})).sort((a,b)=>b.score-a.score).slice(0,5).sort((a,b)=>a.i-b.i).map(x=>x.x);
    const title = ranked.slice(0,3).map(w=>w[0].toUpperCase()+w.slice(1)).join(' · ') || 'Conteúdo analisado';
    return {ranked, selected, title};
  }
  function render(data){
    const sum=document.getElementById('smartPanel-summary');
    sum.innerHTML=`<div class="smart-result"><span class="badge">RESUMO INTELIGENTE</span><h2>${escape(data.title)}</h2><p class="smart-lead">Principais ideias identificadas no conteúdo:</p><ol class="smart-list">${data.selected.map(x=>`<li>${escape(x)}</li>`).join('')}</ol><div class="smart-keywords"><strong>Palavras-chave</strong>${data.ranked.map(x=>`<span>${escape(x)}</span>`).join('')}</div></div>`;
    const map=document.getElementById('smartPanel-mindmap');
    map.innerHTML=`<div class="smart-result"><span class="badge">MAPA MENTAL</span><h2>${escape(data.title)}</h2><div class="mindmap"><div class="mindmap-center">${escape(data.title.split(' · ')[0]||'Tema')}</div>${data.ranked.slice(0,8).map((x,i)=>`<div class="mindmap-node node-${i%4}">${escape(x)}</div>`).join('')}</div></div>`;
    const quiz=document.getElementById('smartPanel-quiz');
    quiz.innerHTML=`<div class="smart-result"><span class="badge">REVISÃO</span><h2>Questões para praticar</h2><div class="quiz-list">${data.selected.slice(0,4).map((x,i)=>`<div class="quiz-item"><strong>${i+1}.</strong><span>Explique com suas palavras a ideia central deste trecho: <em>${escape(x)}</em></span><details><summary>Ver orientação</summary><p>Identifique o conceito principal, explique sua função e relacione-o às palavras-chave: ${escape(data.ranked.slice(0,4).join(', '))}.</p></details></div>`).join('')}</div></div>`;
    const tutor=document.getElementById('smartPanel-tutor');
    tutor.innerHTML=`<div class="smart-result"><span class="badge">TUTOR</span><h2>Pergunte ao conteúdo</h2><div class="tutor-box"><input id="tutorQuestion" type="text" placeholder="Ex.: Qual é a ideia principal?" aria-label="Pergunta sobre o conteúdo"><button class="btn" type="button" id="askTutor">Perguntar</button></div><div id="tutorAnswer" class="tutor-answer">Faça uma pergunta para receber uma orientação baseada nas ideias identificadas.</div></div>`;
    document.getElementById('askTutor').addEventListener('click',()=>{
      const q=document.getElementById('tutorQuestion').value.trim(); const a=document.getElementById('tutorAnswer');
      if(!q){a.textContent='Digite uma pergunta primeiro.';return;}
      a.innerHTML=`<strong>Orientação:</strong> sua pergunta está relacionada ao tema <strong>${escape(data.title.split(' · ')[0]||'do conteúdo')}</strong>. Revise estes pontos: ${escape(data.ranked.slice(0,6).join(', '))}. Depois tente explicar a resposta com suas próprias palavras.`;
    });
  }
  analyze.addEventListener('click',()=>{const s=text.value.trim(); if(s.length<80){text.focus(); return;} render(analyzeText(s));});
  clear.addEventListener('click',()=>{text.value=''; ['summary','mindmap','quiz','tutor'].forEach((p,i)=>{const el=document.getElementById('smartPanel-'+p); if(i===0){el.classList.add('active');el.hidden=false;} else el.classList.remove('active');});});
  document.querySelectorAll('.smart-tab').forEach(tab=>tab.addEventListener('click',()=>{const p=tab.dataset.panel;document.querySelectorAll('.smart-tab').forEach(x=>x.classList.toggle('active',x===tab));document.querySelectorAll('.smart-panel').forEach(x=>{x.hidden=x.id!==`smartPanel-${p}`;x.classList.toggle('active',x.id===`smartPanel-${p}`);});}));
})();
