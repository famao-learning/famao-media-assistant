(() => {
  const el=id=>document.getElementById(id),root='/api/topic-radar';
  let owner='',data=null,result=null,busy=false,bootstrapState=null;
  const path=part=>`${root}/accounts/${encodeURIComponent(owner)}/profile/${part}`;
  const send=(method,url,body)=>api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function lock(value){busy=value;el('radarPositionDialog').querySelectorAll('button,input,textarea,select').forEach(node=>node.disabled=value);if(!value&&bootstrapState)showBootstrap(bootstrapState)}
  function failure(error){el('radarPositionError').textContent=error.message||String(error)}
  function showBootstrap(value){
    bootstrapState=value;
    el('radarBootstrapStatus').textContent=value.message;
    el('radarBootstrapDetails').textContent=[value.imported_at?'首次导入：'+new Date(value.imported_at).toLocaleString():'尚未完成导入',value.completed_at?'定位流程完成：'+new Date(value.completed_at).toLocaleString():'',value.basis||'',value.scope||''].filter(Boolean).join(' · ');
    el('radarBootstrapRetry').disabled=['completed','ready_for_review','importing','analysing'].includes(value.status);
  }
  function top(){return [...(data?.works||[])].sort((a,b)=>b.views-a.views||a.id.localeCompare(b.id)).slice(0,Number(el('radarWorksTop').value))}
  function render(){
    const selected=top();el('radarWorksSummary').textContent=`导入 ${data.works.length} 个作品，本次选择其中${selected[0]?.metric||'播放量'}最高的 ${selected.length} 个。范围：${data.scope||'尚未填写'}。`;
    el('radarWorksList').innerHTML=selected.length?selected.map((item,index)=>`<article class="radar-lead"><b>${index+1}. ${esc(item.title)}</b><p class="hint">${esc(item.metric||'播放量')} ${item.approximate_views?'约 ':''}${item.views.toLocaleString()} · ID ${esc(item.id)}${item.published_at?' · '+esc(item.published_at):''}${item.content_source?' · '+esc(item.content_source):''}</p><label class="f">字幕 / 口播全文或内容摘要（至少30字）</label><textarea rows="4" maxlength="20000" data-work-content="${index}">${esc(item.transcript||item.description||'')}</textarea><input type="file" accept=".txt,.srt,.vtt" data-work-subtitle="${index}" aria-label="导入本作品字幕"></article>`).join(''):'<div class="radar-empty">下次打开后台或检查账号时会尝试首次读取；也可以手动导入。</div>';
    el('radarWorksList').querySelectorAll('[data-work-content]').forEach(input=>input.oninput=()=>{selected[Number(input.dataset.workContent)].transcript=input.value});
    el('radarWorksList').querySelectorAll('[data-work-subtitle]').forEach(input=>input.onchange=async()=>{
      try{const file=input.files[0];if(!file)return;if(file.size>100000)throw new Error('字幕文件需小于100KB');
        const text=(await file.text()).replace(/^\uFEFF|^WEBVTT[^\n]*\n/g,'').split(/\r?\n/).filter(line=>!/^\s*\d+\s*$/.test(line)&&!line.includes('-->')).join('\n').trim();
        if(text.length>20000)throw new Error('字幕超过20000字，请整理后导入');
        const index=Number(input.dataset.workSubtitle);selected[index].transcript=text;el('radarWorksList').querySelector(`[data-work-content="${index}"]`).value=text;
      }catch(error){failure(error)}
    });
    if(busy)el('radarWorksList').querySelectorAll('input,textarea').forEach(node=>node.disabled=true);
  }
  function show(value,stale=false){
    result=value;el('radarPositionResult').classList.toggle('hidden',!value);if(!value)return;
    const indexed=Object.fromEntries(value.samples.map(item=>[item.id,item]));
    el('radarPositionEvidence').innerHTML=`<p class="hint">${esc(value.model)} · ${esc(value.scope)}${stale?' · 样本已更新，请重新分析':''}</p><h3>内容共性</h3><ul>${value.patterns.map(text=>`<li>${esc(text)}</li>`).join('')}</ul><h3>作品依据</h3><ul>${value.evidence.map(item=>`<li><b>${esc(indexed[item.video_id].title)}</b>：${esc(item.finding)}</li>`).join('')}</ul><h3>判断边界</h3><ul>${value.limitations.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`;
    for(const [key,id] of Object.entries({direction:'radarPositionDirection',audience:'radarPositionAudience',domains:'radarPositionDomains',keywords:'radarPositionKeywords'}))el(id).value=Array.isArray(value.suggestion[key])?value.suggestion[key].join(' '):value.suggestion[key];
    el('radarPositionInherit').checked=false;el('radarPositionAdopt').disabled=stale;
  }
  async function save(text){
    data=await send('PUT',path('works'),{revision:data.revision,scope:el('radarWorksScope').value,text});render();return data;
  }
  el('radarPositionButton').onclick=async()=>{
    owner=el('radarAccount').value;if(!owner){publisherToast('请先选择账号');return}
    try{const responses=await Promise.all([api(path('works')),api(path('analysis')),api(path('bootstrap'))]);data=responses[0];
      el('radarPositionTitle').textContent=el('radarAccount').selectedOptions[0].textContent+' · 高流量作品定位';
      el('radarWorksScope').value=data.scope;el('radarWorksText').value='';el('radarWorksFile').value='';el('radarWorksImport').open=!data.works.length;
      el('radarPositionError').textContent='';el('radarPositionMessage').textContent='';render();show(responses[1].result,responses[1].stale);showBootstrap(responses[2]);el('radarPositionDialog').showModal();
    }catch(error){publisherToast(error.message)}
  };
  el('radarWorksTop').onchange=()=>render();
  el('radarWorksFile').onchange=async()=>{try{const file=el('radarWorksFile').files[0];if(!file)return;if(file.size>500000)throw new Error('作品文件需小于500KB');el('radarWorksText').value=await file.text()}catch(error){failure(error)}};
  el('radarWorksTemplate').onclick=()=>{
    const url=URL.createObjectURL(new Blob(['\uFEFFid,title,views,url,published_at,transcript,description\r\n'],{type:'text/csv;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download='账号作品数据模板.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
  el('radarWorksSave').onclick=async()=>{
    if(busy)return;try{lock(true);el('radarPositionError').textContent='';await save(el('radarWorksText').value);show(null);el('radarWorksImport').open=false;el('radarPositionMessage').textContent='样本已保存，请补充高播放作品的内容后分析。'}catch(error){failure(error)}finally{lock(false)}
  };
  el('radarPositionAnalyze').onclick=async()=>{
    if(busy||!data)return;
    try{lock(true);el('radarPositionError').textContent='';el('radarPositionMessage').textContent='正在保存内容并分析…';
      await save(JSON.stringify(data.works.map(item=>({...item,views:item.approximate_views?String(item.views)+'+':item.views}))));
      const generated=await send('POST',path('analysis'),{revision:data.revision,count:Number(el('radarWorksTop').value)});
      show(generated);el('radarPositionMessage').textContent='分析完成。请查看作品依据，编辑后采用。';
    }catch(error){failure(error);el('radarPositionMessage').textContent=''}finally{lock(false)}
  };
  el('radarPositionAdopt').onclick=async()=>{
    if(busy||!result)return;
    try{lock(true);await send('POST',path('adopt'),{analysis_id:result.id,inherit_tags:el('radarPositionInherit').checked,
      edits:{direction:el('radarPositionDirection').value,audience:el('radarPositionAudience').value,domains:el('radarPositionDomains').value,keywords:el('radarPositionKeywords').value}});
      el('radarPositionDialog').close();await window.topicRadar.open();publisherToast('已根据作品分析更新账号定位');
    }catch(error){failure(error)}finally{lock(false)}
  };
  el('radarPositionDialog').addEventListener('cancel',event=>{if(busy)event.preventDefault()});
  async function bootstrapAction(action){
    if(busy)return;
    if(action==='restart'&&!confirm('将允许下次打开后台或检查账号时重新读取作品并更新定位，继续吗？'))return;
    try{lock(true);showBootstrap(await send('POST',path('bootstrap'),{action}));el('radarPositionError').textContent=''}catch(error){failure(error)}finally{lock(false)}
  }
  el('radarBootstrapRetry').onclick=()=>bootstrapAction('retry');
  el('radarBootstrapRestart').onclick=()=>bootstrapAction('restart');
  setInterval(async()=>{
    if(busy||!owner||!el('radarPositionDialog').open)return;
    try{const status=await api(path('bootstrap'));showBootstrap(status);
      if(!data.works.length&&status.imported_at){data=await api(path('works'));el('radarWorksScope').value=data.scope;render()}
      if(!result&&status.analysis_id){const analysis=await api(path('analysis'));show(analysis.result,analysis.stale)}
    }catch{}
  },5000);
  el('radarPositionClose').onclick=()=>{if(!busy)el('radarPositionDialog').close()};
})();
