/* Search keys stay independent from model-provider keys. */
(() => {
  const el=id=>document.getElementById(id), root='/api/topic-radar/search';
  let settings=null,busy=false;
  function select(clearKey=true){
    const provider=el('radarSearchProvider').value,item=settings.providers.find(item=>item.id===provider),official=provider==='deepseek';
    if(clearKey){el('radarSearchKey').value='';el('radarSearchClear').checked=false}
    el('radarSearchKey').disabled=official;el('radarSearchClear').disabled=official;
    el('radarSearchKey').placeholder=item?.api_key_set?'已保存该服务的 Key，留空保留':'填写该搜索服务自己的 Key';
    el('radarSearchKeyHint').textContent=official?'复用“DeepSeek 官方”配置中已有的 Key；不会使用无垠视界的 Key。':'需要此搜索服务独立的 API Key，不会自动使用其他服务的密钥。';
  }
  function fill(value){settings=value;el('radarSearchProvider').value=value.provider;el('radarSearchEnabled').checked=value.enabled;el('radarSearchAuto').checked=value.auto_research;el('radarSearchResults').value=value.max_results;el('radarSearchPages').value=value.max_pages;el('radarSearchTimeout').value=value.timeout_seconds;select()}
  function lock(value){busy=value;el('radarSearchForm').querySelectorAll('input,button,select').forEach(input=>input.disabled=value);if(!value)select(false)}
  el('radarSearchConfigButton').onclick=async()=>{try{fill(await api(root+'/config'));el('radarSearchError').textContent='';el('radarSearchMessage').textContent='';el('radarSearchDialog').showModal()}catch(error){publisherToast(error.message)}};
  el('radarSearchProvider').onchange=select;
  el('radarSearchDialog').addEventListener('close',()=>{el('radarSearchKey').value=''});
  el('radarSearchDialog').addEventListener('cancel',event=>{if(busy)event.preventDefault()});
  el('radarSearchForm').onsubmit=async event=>{
    event.preventDefault();if(busy)return;
    const payload={revision:settings.revision,provider:el('radarSearchProvider').value,enabled:el('radarSearchEnabled').checked,auto_research:el('radarSearchAuto').checked,max_results:Number(el('radarSearchResults').value),max_pages:Number(el('radarSearchPages').value),timeout_seconds:Number(el('radarSearchTimeout').value),api_key:el('radarSearchKey').value,clear_api_key:el('radarSearchClear').checked};
    try{lock(true);fill(await api(root+'/config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));el('radarSearchDialog').close();publisherToast('联网搜索配置已保存')}
    catch(error){el('radarSearchError').textContent=error.message}finally{lock(false)}
  };
  el('radarSearchTest').onclick=async()=>{
    if(busy)return;lock(true);el('radarSearchError').textContent='';el('radarSearchMessage').textContent='正在测试已保存的配置…';
    try{const result=await api(root+'/test',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});el('radarSearchMessage').textContent=result.message+' · '+result.result_count+' 条结果'}
    catch(error){el('radarSearchError').textContent=error.message;el('radarSearchMessage').textContent=''}finally{lock(false)}
  };
})();
