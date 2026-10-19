const $ = (id) => document.getElementById(id);
const active = new Set(['queued', 'starting', 'parsing', 'rendering', 'converting_azw3']);
const labels = {queued:'等待转换',starting:'加载引擎',parsing:'正在解析',rendering:'生成 EPUB',converting_azw3:'生成 AZW3',completed:'转换完成',failed:'转换失败',cancelled:'已取消'};
const quality = {flash:'快速',basic:'基础',standard:'标准',advanced:'精细'};
const modeLabel = {auto:'自动',ocr:'强制 OCR',txt:'文字层'};
let selected = null, uploading = false, ready = false, azw3Ready = false, maxMB = 200, logId = null;
let ids;
try { ids = JSON.parse(localStorage.getItem('minepub-jobs') || '[]'); } catch { ids = []; }
if (!Array.isArray(ids)) ids = [];
ids = ids.filter(x => typeof x === 'string' && /^[a-f0-9]{32}$/.test(x)).slice(0,50);
const jobs = new Map();
const empty = $('tasks').innerHTML;
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const size = bytes => bytes < 1048576 ? `${Math.max(1,Math.round(bytes / 1024))} KB` : `${(bytes / 1048576).toFixed(1)} MB`;
function save() { try { localStorage.setItem('minepub-jobs',JSON.stringify(ids)); } catch {} }
function notice(message='') { $('notice').textContent=message; $('notice').hidden=!message; }
function buttonState() { $('submit').disabled=!selected || !ready || uploading || ($('output-format').value==='both' && !azw3Ready); }
function outputHint() {
  $('output-hint').textContent=$('output-format').value==='epub'
    ? '通用 EPUB 格式；Kindle USB 阅读请选择 AZW3。'
    : azw3Ready ? '同时保留 EPUB；AZW3 可通过 USB 导入兼容的 Kindle。' : 'AZW3 工具尚未就绪，可先选择仅 EPUB。安装方法见使用说明。';
  buttonState();
}
$('output-format').addEventListener('change',outputHint);
function selectFile(file) {
  if (uploading || !file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) { notice('请选择 PDF 文件。'); return; }
  if (file.size > maxMB*1048576) { notice(`文件超过 ${maxMB} MB，请拆分后上传。`); return; }
  if (!file.size) { notice('文件为空，请重新选择。'); return; }
  selected=file; notice(); $('file-title').textContent=file.name;
  $('file-subtitle').textContent=`${size(file.size)} · 点击可更换文件`;
  $('file-limit').textContent='已选择 · 准备转换'; buttonState();
}
$('pdf-file').addEventListener('change', e => selectFile(e.target.files[0]));
for (const name of ['dragenter','dragover']) $('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.add('dragging');});
for (const name of ['dragleave','drop']) $('dropzone').addEventListener(name,e=>{e.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',e=>selectFile(e.dataTransfer.files[0]));
window.addEventListener('dragover',e=>e.preventDefault());
window.addEventListener('drop',e=>e.preventDefault());
$('tier').addEventListener('change',()=>{
  $('tier-hint').textContent={standard:'结合版面分析与视觉模型，适合书籍转换。',basic:'使用 OCR 和小模型，可在 CPU 上运行。',flash:'速度优先；简单文字文档可搭配“读取文字层”。',advanced:'投入更多计算处理复杂页面，耗时和内存需求更高。'}[$('tier').value];
});
$('mode').addEventListener('change',()=>{
  $('mode-hint').textContent={auto:'由 MinerU 判断文本读取或 OCR 识别方式。',ocr:'对扫描页或异常文字层重新识别，需要 OCR 模型。',txt:'仅适合有正常文字层的 PDF；扫描页可能没有文字输出。'}[$('mode').value];
});
async function api(url, options) {
  const response = await fetch(url,options);
  if (!response.ok) {
    const body = await response.json().catch(()=>({}));
    const error = new Error(typeof body.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
    error.status=response.status; throw error;
  }
  return response.status === 204 ? null : response.json();
}
async function health() {
  try {
    const result=await api('/api/health'); ready=result.engine.ready; maxMB=result.max_upload_mb;
    const changed=azw3Ready !== !!result.azw3?.ready;
    azw3Ready=!!result.azw3?.ready; outputHint();
    if(changed) renderJobs();
    $('engine-status').innerHTML=`<span class="dot"></span>${ready ? `MinerU ${escape(result.engine.version)} · 已安装` : '引擎尚未就绪'}`;
    $('engine-status').title=result.engine.message;
    $('engine-status').classList.toggle('offline',!ready);
    if (!selected) $('file-limit').textContent=`支持扫描版与文字版 PDF · 最大 ${maxMB} MB`;
    if (!ready) notice(result.engine.message);
  } catch { ready=false; $('engine-status').textContent='服务连接中断'; $('engine-status').classList.add('offline'); }
  buttonState();
}
$('convert-form').addEventListener('submit', async e=>{
  e.preventDefault(); if (!selected || uploading || !ready || ($('output-format').value==='both' && !azw3Ready)) return;
  uploading=true; buttonState(); notice();
  $('pdf-file').disabled=true; $('tier').disabled=true; $('mode').disabled=true; $('output-format').disabled=true;
  const file=selected;
  try {
    const params=new URLSearchParams({filename:file.name,tier:$('tier').value,mode:$('mode').value,output_format:$('output-format').value});
    const job=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest(); xhr.open('POST',`/api/jobs?${params}`); xhr.setRequestHeader('Content-Type','application/pdf');
      xhr.upload.onprogress=e=>{ if(e.lengthComputable) $('submit').textContent=e.loaded===e.total ? '检查 PDF…' : `上传中 ${Math.round(e.loaded/e.total*100)}%`; };
      xhr.onerror=()=>reject(new Error('上传连接中断，请检查本地服务后重试。'));
      xhr.onload=()=>{let body;try{body=JSON.parse(xhr.responseText);}catch{reject(new Error('服务器响应异常'));return;}if(xhr.status>=200&&xhr.status<300)resolve(body);else reject(new Error(typeof body.detail==='string'?body.detail:'上传失败，请检查文件与选项。'));};
      xhr.send(file);
    });
    ids=[job.id,...ids].slice(0,50); jobs.set(job.id,job); save(); renderJobs();
    selected=null; $('pdf-file').value=''; $('file-title').textContent='把 PDF 放在这里';
    $('file-subtitle').innerHTML='拖入文件，或 <u>点击选择</u>';
    $('file-limit').textContent=`支持扫描版与文字版 PDF · 最大 ${maxMB} MB`;
  } catch(error) { notice(error.message); }
  finally { uploading=false; $('pdf-file').disabled=false; $('tier').disabled=false; $('mode').disabled=false; $('output-format').disabled=false; $('submit').innerHTML='开始转换 <span>↗</span>'; buttonState(); }
});
function renderJobs() {
  $('task-count').textContent=String(ids.length).padStart(2,'0');
  if (!ids.length) { $('tasks').innerHTML=empty; return; }
  $('tasks').innerHTML=ids.map(id=>{
    const j=jobs.get(id);
    if(!j) return `<article class="task-card"><span class="task-message">正在恢复任务状态…</span></article>`;
    const running=active.has(j.status);
    const downloads=j.downloads || {};
    const formats=downloads.azw3 ? 'EPUB + AZW3' : 'EPUB';
    const messages={queued:j.kind==='azw3'?'已排队，将使用现有 EPUB 生成 AZW3，无需重新解析 PDF。':'已加入队列，前面的任务结束后自动开始。',starting:j.kind==='azw3'?'正在启动 AZW3 转换工具。':'正在启动 MinerU，首次使用可能需要下载模型。',parsing:'正在识别文字与阅读顺序。长文档可能需要较长时间，可查看日志。',rendering:'文档解析完成，正在生成 EPUB。',converting_azw3:'EPUB 已就绪，正在生成 Kindle AZW3；EPUB 可以先下载。',completed:`${formats} 已生成 · 本次用时 ${Math.round(j.elapsed)} 秒 · 保存至 ${new Date((j.updated+86400)*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})}`,failed:j.error,cancelled:'任务已取消，已生成的文件仍可下载，也可删除任务。'};
    const epubLink=downloads.epub?`<a class="${downloads.azw3?'':'download'}" href="/api/jobs/${id}/download?format=epub">下载 EPUB ↓</a>`:'';
    const azw3Link=downloads.azw3?`<a class="download" href="/api/jobs/${id}/download?format=azw3">下载 AZW3 ↓</a>`:downloads.epub&&!running?`<button data-action="azw3" data-id="${id}" ${azw3Ready?'':'disabled'} title="使用现有 EPUB 生成 Kindle 格式，无需重新解析">${j.output_format==='both'?'重试 AZW3':'生成 AZW3'}</button>`:'';
    return `<article class="task-card"><div class="task-top"><span class="task-icon">${downloads.azw3?'AZW3':downloads.epub?'EPUB':'PDF'}</span><div class="task-meta"><div class="task-name">${escape(j.filename)}</div><div class="task-details">${j.pages} 页 · ${size(j.size)} · ${quality[j.tier]} / ${modeLabel[j.mode]}${j.output_format==='both'?' · EPUB + AZW3':''}</div></div><span class="status-pill ${escape(j.status)}">${labels[j.status]||'未知状态'}</span></div>${running?'<div class="task-progress" role="progressbar" aria-label="转换进行中"><span></span></div>':''}<div class="task-bottom"><div class="task-message">${escape(messages[j.status])}${j.offline?' · 状态连接中断，正在重连':''}</div><div class="task-actions">${epubLink}${azw3Link}<button data-action="log" data-id="${id}">日志</button>${running?`<button data-action="cancel" data-id="${id}">取消</button>`:`<button data-action="delete" data-id="${id}" aria-label="删除 ${escape(j.filename)} 的文件和记录">删除</button>`}</div></div></article>`;
  }).join('');
}
async function showLog(id) {
  logId=id; $('log-content').textContent='正在读取日志…';
  if (!$('log-dialog').open) $('log-dialog').showModal();
  try { const result=await api(`/api/jobs/${id}/log`); $('log-content').textContent=result.text || '引擎正在启动，暂无日志。'; }
  catch(error){$('log-content').textContent=error.message;}
}
$('tasks').addEventListener('click',async e=>{
  const button=e.target.closest('button[data-action]');if(!button)return;
  const {id,action}=button.dataset;button.disabled=true;
  try {
    if(action==='log') await showLog(id);
    else if(action==='azw3') {jobs.set(id,await api(`/api/jobs/${id}/azw3`,{method:'POST'}));notice();renderJobs();}
    else if(action==='cancel') {jobs.set(id,await api(`/api/jobs/${id}/cancel`,{method:'POST'}));renderJobs();}
    else if(action==='delete') {await api(`/api/jobs/${id}`,{method:'DELETE'});ids=ids.filter(x=>x!==id);jobs.delete(id);save();renderJobs();}
  } catch(error){notice(error.message);}finally{button.disabled=false;}
});
$('close-log').addEventListener('click',()=>$('log-dialog').close());
$('refresh-log').addEventListener('click',()=>showLog(logId));
async function poll() {
  const before=JSON.stringify([...jobs]);
  await Promise.all(ids.map(async id=>{
    try { jobs.set(id,await api(`/api/jobs/${id}`)); }
    catch(error) { if(error.status===404){ids=ids.filter(x=>x!==id);jobs.delete(id);save();}else if(jobs.has(id)){jobs.get(id).offline=true;} }
  }));
  if(before!==JSON.stringify([...jobs]) || !ids.length) renderJobs();
  setTimeout(poll,2500);
}
renderJobs(); health(); poll(); setInterval(health,30000);
