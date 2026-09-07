let deferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault(); deferredInstallPrompt = event;
  document.querySelectorAll('#installApp').forEach(btn => { btn.hidden = false; });
});
document.querySelectorAll('#installApp').forEach(btn => btn.addEventListener('click', async () => {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  await deferredInstallPrompt.userChoice;
  deferredInstallPrompt = null;
  btn.hidden = true;
}));
window.addEventListener('appinstalled', () => document.querySelectorAll('#installApp').forEach(btn => btn.hidden = true));

function selectSource(source) {
  document.querySelectorAll('.source-card').forEach(card => card.classList.toggle('active', card.dataset.source === source));
  document.querySelectorAll('.source-panel').forEach(panel => panel.classList.remove('visible'));
  const panel = document.getElementById(source + 'Source'); if (panel) panel.classList.add('visible');
  const selectId = {device:'deviceKind',drive:'driveKind',link:'linkKind'}[source];
  const kind = selectId ? document.getElementById(selectId).value : 'explanation';
  document.getElementById('kind').value = kind;
  const editor = document.getElementById('textEditorCard'); if (editor) editor.style.display = source === 'text' ? 'block' : 'none';
  if (source === 'device') document.getElementById('externalUrl').value = '';
}
function syncExternal(value) { const el=document.getElementById('externalUrl'); if(el) el.value=value; }
function updateAcceptedTypes() { const input=document.getElementById('fileInput'); const pdf=document.getElementById('deviceKind').value==='pdf'; if(input) input.accept=pdf?'.pdf':'.pdf,.png,.jpg,.jpeg,.webp,.gif,.ppt,.pptx,.doc,.docx,.txt'; }

document.addEventListener('DOMContentLoaded', () => {
  const input=document.getElementById('fileInput'), fileName=document.getElementById('fileName'), dropzone=document.getElementById('dropzone');
  if(input && fileName) input.addEventListener('change',()=>fileName.textContent=input.files[0]?'✓ '+input.files[0].name:'Nenhum arquivo escolhido');
  if(dropzone && input) {
    ['dragenter','dragover'].forEach(e=>dropzone.addEventListener(e,ev=>{ev.preventDefault();dropzone.classList.add('dragging')}));
    ['dragleave','drop'].forEach(e=>dropzone.addEventListener(e,ev=>{ev.preventDefault();dropzone.classList.remove('dragging')}));
    dropzone.addEventListener('drop',ev=>{if(ev.dataTransfer.files.length){input.files=ev.dataTransfer.files;fileName.textContent='✓ '+input.files[0].name;}});
  }
  const form=document.querySelector('.publisher');
  if(form && form.dataset.existingKind) {
    const k=form.dataset.existingKind, u=form.dataset.existingUrl||'';
    let source=k==='explanation'?'text':(k==='file'||k==='pdf'?'device':(u.includes('drive.google.com')?'drive':'link'));
    const selectId={device:'deviceKind',drive:'driveKind',link:'linkKind'}[source];
    if(selectId){const sel=document.getElementById(selectId);if(sel&&[...sel.options].some(o=>o.value===k))sel.value=k;}
    selectSource(source);
    if(u){const field=document.getElementById(source==='drive'?'driveUrl':'linkUrl');if(field)field.value=u;}
    if(k==='file'||k==='pdf') updateAcceptedTypes();
    if(input && form.dataset.existingFile) fileName.textContent='✓ Arquivo já enviado (envie um novo para substituir)';
  }
});
