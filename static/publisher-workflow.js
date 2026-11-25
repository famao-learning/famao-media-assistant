/* 本机自动保存、异常恢复与各平台内容检查，共用发布表单。 */
let autosaveRunning=null,autosaveTimer=0,savedFingerprint='',boundDraftId='';
let fileKeys=new WeakMap(),fileTokens=new WeakMap(),nextFileToken=0,savedCovers={};
let preflightTimer=0,preflightVersion=0,lastPreflightSignature='';
const token=file=>{if(!file)return '';if(!fileTokens.has(file))fileTokens.set(file,++nextFileToken);return fileTokens.get(file)};
function workflowFields(){
  const form=publicationForm(),fields={};
  for(const [key,value] of form)if(typeof value==='string'&&key!=='accounts')fields[key]=value;
  return fields;
}
function workflowFingerprint(){return JSON.stringify([workflowFields(),mediaFiles.map(token),['h','v'].map(kind=>token($('cover_'+kind).files[0]))])}
function autosaveStatus(text,failed=false){
  $('autosaveStatus').textContent=text;$('autosaveStatus').classList.toggle('save-error',failed);
  $('autosaveStatus').title=failed?'点击重试保存':'';$('autosaveStatus').onclick=failed?()=>flushAutosave().catch(()=>{}):null;
}
async function saveAutosaveSnapshot(){
  const files=[...mediaFiles],covers=Object.fromEntries(['h','v'].map(kind=>[kind,$('cover_'+kind).files[0]]));
  if(!files.length)return;
  const fields=workflowFields(),fingerprint=workflowFingerprint(),id=activeDraftId;
  const reuse=id&&id===boundDraftId&&files.every(file=>fileKeys.has(file));
  const form=new FormData();for(const [key,value] of Object.entries(fields))form.append(key,value);
  form.append('autosaved','1');form.append('editing','1');
  if(reuse){
    form.append('file_order',JSON.stringify(files.map(file=>fileKeys.get(file))));
    const removed=[];
    for(const kind of ['h','v'])if(covers[kind]!==savedCovers[kind]){if(covers[kind])form.append('cover_'+kind,covers[kind]);else removed.push(kind)}
    form.append('remove_covers',JSON.stringify(removed));
  }else{
    for(const file of files)form.append('files',file);
    for(const kind of ['h','v'])if(covers[kind])form.append('cover_'+kind,covers[kind]);
    if(id)form.append('draft_id',id);
  }
  autosaveStatus(reuse?'正在保存修改…':'正在保存作品文件到本机…');
  const response=await fetch(reuse?'/api/drafts/'+id:'/api/drafts',{method:reuse?'PATCH':'POST',body:form});
  const draft=await response.json();if(!response.ok)throw new Error(draft.error||'自动保存失败');
  if(mediaFiles.length&&mediaFiles.every(file=>files.includes(file))&&selectedType()===fields.type){
    activeDraftId=draft.id;boundDraftId=draft.id;
    files.forEach((file,index)=>fileKeys.set(file,draft.files[index].key));
    savedCovers=covers;savedFingerprint=fingerprint;
  }
  await updateDraftCount();
}
function flushAutosave(){
  clearTimeout(autosaveTimer);
  if(autosaveRunning)return autosaveRunning;
  autosaveRunning=(async()=>{
    try{
      while(mediaFiles.length&&workflowFingerprint()!==savedFingerprint)await saveAutosaveSnapshot();
      if(mediaFiles.length)autosaveStatus('已自动保存到本机');
      return activeDraftId;
    }catch(error){autosaveStatus('保存失败，点击重试',true);$('autosaveStatus').title=error.message;throw error}
  })().finally(()=>{autosaveRunning=null});
  return autosaveRunning;
}
function bindDraftSnapshot(draft){
  boundDraftId=draft.id;fileKeys=new WeakMap();
  mediaFiles.forEach((file,index)=>fileKeys.set(file,draft.files[index].key));
  savedCovers=Object.fromEntries(['h','v'].map(kind=>[kind,$('cover_'+kind).files[0]]));
  savedFingerprint=workflowFingerprint();autosaveStatus('草稿已加载，修改会自动保存');
}
async function finishDraftEditing(autosaved=false){
  if(!activeDraftId)return;
  const fields={...workflowFields(),autosaved,editing:false,file_order:mediaFiles.map(file=>fileKeys.get(file))};
  await jsend('PATCH','/api/drafts/'+activeDraftId,fields);
  savedFingerprint=workflowFingerprint();await updateDraftCount();
}
function publisherChanged(){
  if(!mediaFiles.length)return;
  $('draftRecovery').classList.add('hidden');
  if(workflowFingerprint()!==savedFingerprint){
    clearTimeout(autosaveTimer);
    if(!publisherBusy)autosaveTimer=setTimeout(()=>flushAutosave().catch(()=>{}),activeDraftId?900:150);
  }
  const signature=JSON.stringify([workflowFingerprint(),accs.map(a=>[a.id,a.busy,a.status,a.collections_at,a.publish_backend,a.matrix_partition])]);
  if(signature!==lastPreflightSignature){lastPreflightSignature=signature;preflightVersion++;clearTimeout(preflightTimer);preflightTimer=setTimeout(()=>checkPreflight().catch(()=>{}),300)}
}
async function refreshDraftRecovery(){
  const records=await updateDraftCount(),draft=records.find(record=>record.editing);
  if(!draft||mediaFiles.length)return;
  $('recoveryName').textContent=(draft.title||draft.desc||'未命名作品')+' · '+draft.updated_at;
  $('recoverDraftBtn').onclick=()=>restoreDraft(draft.id);$('draftRecovery').classList.remove('hidden');
}
async function checkPreflight(){
  clearTimeout(preflightTimer);const version=++preflightVersion;
  const fields=workflowFields(),payload={...fields,accounts:selectedPlatforms().map(p=>tg[p].acc),
    files:mediaFiles.map(f=>({name:f.name,size:f.size})),covers:['h','v'].filter(kind=>$('cover_'+kind).files.length)};
  $('preflightSummary').textContent='正在检查…';
  try{
    const result=await jsend('POST','/api/publish/preflight',payload);
    if(version!==preflightVersion)return {ok:false,errors:['内容已修改，请重新检查']};
    const errors=[...(result.errors||[]),...result.targets.flatMap(t=>t.errors)];
    $('preflightSummary').textContent=errors.length?`有 ${errors.length} 项需要处理`:result.targets.some(t=>t.warnings.length)?'有提示，请核对各平台内容':'检查通过';
    $('preflightResults').innerHTML=result.targets.map(target=>`<details class="preflight-target" ${target.errors.length?'open':''}><summary><b>${esc(target.label)}</b><span class="${target.errors.length?'check-error':target.warnings.length?'check-warning':'check-ok'}">${target.errors.length?'需要调整':target.warnings.length?'请留意':'可发布'}</span></summary>${[...target.errors.map(e=>`<p class="check-error">${esc(e)}</p>`),...target.warnings.map(e=>`<p class="check-warning">${esc(e)}</p>`)].join('')}<dl><dt>标题</dt><dd>${esc(target.title||'无独立标题')}</dd><dt>描述</dt><dd>${esc(target.desc||'未填写')}</dd><dt>标签</dt><dd>${esc(target.tags.map(t=>'#'+t).join(' ')||'未填写')}</dd><dt>封面</dt><dd>${esc(target.cover)}</dd><dt>合集</dt><dd>${esc(target.collection)}</dd>${target.platform==='bilibili'?`<dt>投稿分区</dt><dd>${esc(target.partition)}</dd>`:''}${['douyin','bilibili'].includes(target.platform)?`<dt>${target.platform==='douyin'?'自主声明':'创作声明'}</dt><dd>${esc(target.declaration)}</dd>`:''}<dt>发布时间</dt><dd>${esc(target.publish_time||'立即发布')}</dd></dl></details>`).join('')||(errors.length?`<p class="hint">${esc(errors.join('；'))}</p>`:'');
    return {...result,errors};
  }catch(error){if(version===preflightVersion){$('preflightSummary').textContent='检查未通过';$('preflightResults').innerHTML=`<p class="check-error">${esc(error.message)}</p>`}return {ok:false,errors:[error.message]}}
}
function editorChanged(){publisherError('');publisherChanged()}
$('publisher').addEventListener('input',editorChanged);$('publisher').addEventListener('change',editorChanged);
window.addEventListener('pagehide',()=>{
  // 视频已保存后，最后一刻的文案修改可用小请求补存；大文件由正常自动保存负责。
  if(activeDraftId===boundDraftId&&mediaFiles.length&&mediaFiles.every(f=>fileKeys.has(f))&&!autosaveRunning&&workflowFingerprint()!==savedFingerprint){
    fetch('/api/drafts/'+activeDraftId,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({...workflowFields(),file_order:mediaFiles.map(f=>fileKeys.get(f)),autosaved:true,editing:true}),keepalive:true}).catch(()=>{});
  }
});
refreshDraftRecovery();
setInterval(()=>{
  if(activeDraftId&&mediaFiles.length)fetch('/api/drafts/'+activeDraftId+'/active',{method:'POST'}).catch(()=>{});
  updateDraftCount();
},60000);
