/* 矩媒账号显式绑定；每次发布都重新核对本次内容。 */
let matrixAccounts=[],matrixConfirmationResolver=null;
const accountBackend=account=>account?.publish_backend||(account?.platform==='xiaohongshu'?'matrixmedia':'assistant');

async function showMatrix(){
  $('matrixDialog').showModal();$('matrixStatus').textContent='正在读取连接设置…';
  try{const settings=await api('/api/matrixmedia/settings');$('matrixExecutable').value=settings.executable;$('matrixPort').value=settings.port;$('matrixStatus').textContent='请打开矩媒后测试连接；测试不会上传或发布作品。'}catch(error){$('matrixStatus').textContent=error.message}
}
async function saveMatrixSettings(){
  try{await jsend('POST','/api/matrixmedia/settings',{port:Number($('matrixPort').value),executable:$('matrixExecutable').value});matrixAccounts=[];$('matrixStatus').textContent='设置已保存，请重新测试连接。';renderMatrixBinding()}catch(error){$('matrixStatus').textContent=error.message}
}
async function openMatrix(){
  try{const result=await jsend('POST','/api/matrixmedia/open',{});publisherToast(result.message)}catch(error){publisherToast(error.message);if($('matrixDialog').open)$('matrixStatus').textContent=error.message}
}
async function refreshMatrixAccounts(){
  $('matrixBindingStatus').textContent='正在读取矩媒账号…';$('matrixStatus').textContent='正在测试连接…';
  try{
    const result=await api('/api/matrixmedia/probe');matrixAccounts=result.accounts;
    $('matrixStatus').textContent=result.message+`，读取到 ${matrixAccounts.length} 个账号。`;
    $('matrixAccountList').innerHTML=matrixAccounts.map(account=>`<p><b>${esc(PN[account.platform]?.[0]||account.platform)}</b> · ${esc(account.name)} <small>${esc(account.partition)}</small></p>`).join('')||'<p class="hint">暂未发现这三个平台的账号，请先在矩媒添加并登录。</p>';
    $('matrixBindingStatus').textContent='账号列表已刷新，请按实际账号选择。';renderMatrixBinding();
  }catch(error){$('matrixStatus').textContent=error.message;$('matrixBindingStatus').textContent=error.message}
}
function renderMatrixBinding(){
  const platform=$('accPlat').value,account=accs.find(item=>item.id===editing);
  const supported=['douyin','bilibili','xiaohongshu'].includes(platform);
  $('matrixAccountFields').classList.toggle('hidden',!supported);
  const provider=$('accBackend');
  provider.querySelector('[value=assistant]').disabled=platform==='xiaohongshu';
  if(platform==='xiaohongshu')provider.value='matrixmedia';
  const enabled=supported&&provider.value==='matrixmedia';
  $('matrixBindingFields').classList.toggle('hidden',!enabled);
  const previous=$('accMatrixAccount').value||account?.matrix_partition||'';
  const choices=matrixAccounts.filter(item=>item.platform===platform);
  $('accMatrixAccount').innerHTML='<option value="">暂不绑定（发布前必须选择）</option>'+choices.map(item=>`<option value="${esc(item.partition)}">${esc(item.name)} · ${esc(item.partition)}</option>`).join('');
  if(previous&&!choices.some(item=>item.partition===previous)&&account?.matrix_partition===previous){
    const option=new Option((account.matrix_name||'原绑定账号')+'（请刷新核对）',previous);$('accMatrixAccount').add(option);
  }
  $('accMatrixAccount').value=previous;
  if(!$('accMatrixAccount').value)$('accMatrixAccount').value='';
  if(enabled){$('accHint').style.display='';$('accHint').textContent='先保存账号，再在矩媒中登录并刷新绑定；助手不复制旧浏览器登录状态。';$('accOk').textContent='保存账号';}
  else{$('accHint').textContent='保存后在助手内登录，每个账号使用原有独立浏览器。';$('accOk').textContent=editing?'保存':'确定并去登录';}
}
function matrixAccountValues(){
  const supported=['douyin','bilibili','xiaohongshu'].includes($('accPlat').value);
  return {publish_backend:supported?$('accBackend').value:'assistant',matrix_partition:supported?$('accMatrixAccount').value:''};
}
const originalShowAcc=showAcc;
showAcc=function(id){
  if(accountSaving)return;
  originalShowAcc(id);const account=accs.find(item=>item.id===id);
  $('accBackend').value=accountBackend(account||{platform:$('accPlat').value});
  $('accMatrixAccount').innerHTML='';$('matrixBindingStatus').textContent='';renderMatrixBinding();
};
$('accBackend').addEventListener('change',renderMatrixBinding);
$('accPlat').addEventListener('change',()=>{$('accBackend').value=$('accPlat').value==='xiaohongshu'?'matrixmedia':'assistant';$('accMatrixAccount').innerHTML='';renderMatrixBinding()});

async function loadMatrixHistory(){
  $('matrixHistory').textContent='正在读取…';
  try{const result=await api('/api/matrixmedia/history');$('matrixHistory').innerHTML=result.records.slice(-50).reverse().map(row=>`<p><b>${esc(row.pt||'')}</b> · ${esc(row.title||row.bookName||'未命名作品')}<br><small>${esc(row.status||row.publishStatus||'状态请到矩媒查看')} ${esc(row.message||'')}</small></p>`).join('')||'<p class="hint">暂无矩媒发布记录。</p>'}catch(error){$('matrixHistory').textContent=error.message}
}
function confirmMatrixPublication(check){
  if(!check.matrix_confirmation)return Promise.resolve(true);
  $('matrixConfirmChecked').checked=false;$('matrixConfirmSubmit').disabled=true;
  const draft=checkedValue('mode')==='0';
  $('matrixConfirmSubmit').textContent=draft?'确认并保存平台草稿':'确认并交给矩媒提交';
  $('matrixConfirmDetails').innerHTML=`<p><b>${esc(mediaFiles[0]?.name||'')}</b> · ${draft?'保存平台草稿，稍后人工提交':'确认后自动提交'}${check.targets.some(t=>t.schedule_at)?' · 按北京时间在矩媒本机定时执行':''}</p>`+
    check.matrix_targets.map(binding=>`<p><b>${esc(binding.platform)} · ${esc(binding.account)}</b> → 矩媒 ${esc(binding.binding)}<br><small>${esc(binding.partition)}</small></p>`).join('')+
    check.targets.filter(target=>check.matrix_targets.some(binding=>binding.account_id===target.account_id)).map(target=>`<details open><summary>${esc(target.label)}</summary><p><b>${esc(target.title)}</b></p><p style="white-space:pre-wrap">${esc(target.desc)}</p><p>${esc(target.tags.map(t=>'#'+t).join(' '))}</p><p>${esc(target.publish_time)}</p>${target.warnings.map(message=>`<p class="hint">${esc(message)}</p>`).join('')}</details>`).join('');
  $('matrixConfirmDialog').showModal();
  return new Promise(resolve=>matrixConfirmationResolver=resolve);
}
function closeMatrixConfirmation(accepted){
  if(accepted&&!$('matrixConfirmChecked').checked)return;
  const resolve=matrixConfirmationResolver;matrixConfirmationResolver=null;$('matrixConfirmDialog').close();if(resolve)resolve(accepted);
}
$('matrixConfirmChecked').addEventListener('change',()=>{$('matrixConfirmSubmit').disabled=!$('matrixConfirmChecked').checked});
$('matrixConfirmDialog').addEventListener('cancel',event=>{event.preventDefault();closeMatrixConfirmation(false)});
$('matrixConfirmDialog').addEventListener('close',()=>{if(matrixConfirmationResolver)closeMatrixConfirmation(false)});
