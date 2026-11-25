/* User-configured AI, invoked only by explicit actions. Keys never enter browser storage. */
(() => {
  const el=id=>document.getElementById(id),root='/api/topic-radar';
  let config=null,registry=null,configBusy=false,topicBusy=false;
  const fields={base_url:'radarAIBaseURL',model:'radarAIModel',protocol:'radarAIProtocol',
    max_tokens:'radarAITokens',timeout_seconds:'radarAITimeout',candidate_limit:'radarAICandidates',
    system_prompt:'radarAISystemPrompt',token_parameter:'radarAITokenParameter'};
  const send=(method,url,body)=>api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function fillConfig(value){
    config=value;
    for(const [key,id] of Object.entries(fields))el(id).value=value[key];
    el('radarAIEnabled').checked=value.enabled;el('radarAITemperature').value=value.temperature??'';
    el('radarAIExtra').value=JSON.stringify(value.extra_body,null,2);el('radarAIKey').value='';
    el('radarAIKey').placeholder=value.api_key_set?'已保存 Key；留空保留原值':'填写 API Key，本机无鉴权模型可留空';
    el('radarAIClearKey').checked=false;
  }
  function body(){
    const result={revision:config.revision,registry_revision:registry.revision,enabled:el('radarAIEnabled').checked,api_key:el('radarAIKey').value,
      clear_api_key:el('radarAIClearKey').checked,temperature:el('radarAITemperature').value===''?null:Number(el('radarAITemperature').value)};
    for(const [key,id] of Object.entries(fields))result[key]=el(id).value;
    for(const key of ['max_tokens','timeout_seconds','candidate_limit'])result[key]=Number(result[key]);
    try{result.extra_body=JSON.parse(el('radarAIExtra').value||'{}')}catch{throw new Error('额外请求参数不是有效的 JSON')}
    return result;
  }
  function configLock(busy){configBusy=busy;el('radarAIConfigForm').querySelectorAll('button,input,textarea,select').forEach(input=>input.disabled=busy)}
  function selectProvider(){
    const selected=registry.profiles.find(item=>item.id===el('radarAIProvider').value);
    fillConfig(selected.config);if(el('radarAIModelHint'))el('radarAIModelHint').textContent=selected.id==='deepseek'?'官方 deepseek-flash 对应 DeepSeek-V4.1-Flash-1017；API 使用 deepseek-flash 这个模型 ID。':'';
    const active=registry.profiles.find(item=>item.id===registry.active_id);
    el('radarAIProviderStatus').textContent=`当前使用：${active.name}。各服务独立保存 Key；切换选择后需保存才生效。未保存的修改会在切换选择时清除。`;
    el('radarAIConfigError').textContent='';el('radarAIConfigMessage').textContent='密钥由本机 Windows 账号加密保存。测试只请求当前所选服务，不自动切换服务。';
  }
  function fillProviders(value){
    registry=value;const select=el('radarAIProvider');select.replaceChildren();
    for(const item of value.profiles){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;select.append(option)}
    select.value=value.active_id;selectProvider();
  }
  el('radarAIProvider').onchange=selectProvider;
  el('radarAIConfigButton').onclick=async()=>{
    try{fillProviders(await api(root+'/ai/providers'));el('radarAIConfigDialog').showModal()}catch(error){publisherToast(error.message)}
  };
  el('radarAIConfigDialog').addEventListener('close',()=>{el('radarAIKey').value=''});
  el('radarAIConfigDialog').addEventListener('cancel',event=>{if(configBusy)event.preventDefault()});
  el('radarAITest').onclick=async()=>{
    if(configBusy||!el('radarAIConfigForm').reportValidity())return;
    try{const data=body();configLock(true);el('radarAIConfigError').textContent='';el('radarAIConfigMessage').textContent='正在测试连接…';
      const result=await send('POST',root+'/ai/providers/'+el('radarAIProvider').value+'/test',data);el('radarAIConfigMessage').textContent=`${result.message} · ${result.model} · ${result.elapsed_ms} ms（尚未保存配置）`;
    }catch(error){el('radarAIConfigMessage').textContent='';el('radarAIConfigError').textContent=error.message}finally{configLock(false)}
  };
  el('radarAIConfigForm').onsubmit=async event=>{
    event.preventDefault();if(configBusy)return;
    try{const data=body(),id=el('radarAIProvider').value;configLock(true);fillProviders(await send('PUT',root+'/ai/providers/'+id,data));el('radarAIConfigDialog').close();publisherToast('AI 配置已保存并设为当前服务')}
    catch(error){el('radarAIConfigError').textContent=error.message}finally{configLock(false)}
  };
  el('radarAIAnalyze').onclick=()=>window.topicRadar.loadAI();
  function show(item){
    const research=item.research,box=el('radarResearchResult');
    if(box){box.classList.toggle('hidden',!research);if(research){
      const index=new Map(research.sources.map(source=>[source.id,source]));
      const labels={supported:'多网站支持（AI判断）',single_source:'单网站支持',conflicting:'说法存在冲突',unverified:'证据不足'};
      const links=ids=>ids.map(id=>{const source=index.get(id);return source?`<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(id)} · ${esc(source.domain)}</a>`:''}).join(' · ');
      box.innerHTML=`<div class="radar-outline"><h3>联网研究</h3><p class="hint">${esc(research.provider)} · ${esc(research.model)} · ${esc(research.generated_at)}<br>AI 对检索证据的判断，仍有检索范围与来源本身的限制。</p><p>${esc(research.summary)}</p><p>事件时间：${esc(research.event_time||'未知')}</p><ul>${research.claims.map(claim=>`<li><b>${esc(labels[claim.status]||claim.status)}</b>：${esc(claim.text)}<br>${links(claim.source_ids)}</li>`).join('')}</ul><b>待核实与冲突</b><ul>${research.research_gaps.map(gap=>`<li>${esc(gap)}</li>`).join('')}</ul><details><summary>查看 ${research.sources.length} 个资料出处与摘录</summary>${research.sources.map(source=>`<p><a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.id)} · ${esc(source.title)}</a><br><small>${source.evidence_kind==='retrieved_text'?'已取得页面正文':'仅搜索摘要'} · 报道时间：${esc(source.published_at||'未知')}</small><br>${esc(source.excerpt.slice(0,600))}${source.fetch_error?`<br><small>${esc(source.fetch_error)}</small>`:''}</p>`).join('')}</details></div>`;
    }}
    const s=item.ai_suggestion;el('radarAIResult').classList.toggle('hidden',!s);
    if(box&&research){
      // Keep the report available without pushing generated copy below a long evidence list.
      const report=document.createElement('details'),summary=document.createElement('summary');
      summary.textContent=`联网研究 · ${research.sources.length} 个资料来源（展开查看证据）`;
      report.append(summary,...box.childNodes);box.replaceChildren(report);
    }
    if(!s)return;
    el('radarAIResultTitle').textContent=s.mode==='draft'?'AI 生成结果 · 文案':'AI 生成结果 · 提纲预览';
    el('radarAIResultState').textContent=s.mode==='draft'?(item.draft_description===s.draft_description?'文案已填入下方草稿并保存，可直接编辑。':'该结果尚未填入当前草稿，点击“填入标题和文案”采用。'):'提纲可预览，点击“采用此提纲”保存。';
    el('radarAISuggestion').innerHTML=`<p class="hint">${esc(s.model)} · AI 建议 · 待核实</p><h3>${esc(s.angle)}</h3><ol>${s.steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol><b>待核实</b><ul>${s.research_gaps.map(gap=>`<li>${esc(gap)}</li>`).join('')}</ul><h3>${esc(s.draft_title)}</h3>${s.mode==='draft'?`<p class="radar-ai-draft">${esc(s.draft_description)}</p>`:''}`;
    el('radarAIAdoptDraft').disabled=s.mode!=='draft';
  }
  function lockTopic(busy){topicBusy=busy;el('radarTopicForm').querySelectorAll('button,input,select,textarea').forEach(input=>input.disabled=busy);if(!busy&&window.topicRadar.getTopic())show(window.topicRadar.getTopic())}
  async function generate(mode){
    if(topicBusy)return;
    const current=window.topicRadar.getTopic();if(!current)return;
    const replaceDraft=mode==='draft'&&!!el('radarDraftDescription').value.trim();
    if(replaceDraft&&!confirm('当前已有文案。重新生成后将替换标题和文案，继续吗？'))return;
    let saved=false;
    el('radarAIMessage').textContent='';
    lockTopic(true);el('radarTopicError').textContent='';el('radarAIMessage').textContent=mode==='research'?'正在联网搜索、读取来源并整理证据…':mode==='draft'?'正在准备文案；缺少资料时会先自动联网研究…':'正在生成 AI 提纲…';
    el('radarTopicForm').setAttribute('aria-busy','true');el('radarAIMessage').scrollIntoView({block:'center'});
    try{
      const item=await window.topicRadar.saveCurrent();
      saved=true;
      const path=`${root}/accounts/${encodeURIComponent(item.account_id)}/topics/${encodeURIComponent(item.id)}`;
      const generated=await send('POST',path+(mode==='research'?'/research':'/ai'),{revision:item.revision,mode,replace_draft:replaceDraft});
      if(window.topicRadar.getTopic()?.id===item.id&&el('radarTopicDialog').open){
        window.topicRadar.acceptTopic(generated);
        if(mode==='draft'){
          const message=generated.draft_applied?'文案已填入下方草稿并保存，可继续编辑或带入发布页。':'文案已生成，已有草稿已保留；可点击“填入标题和文案”替换。';
          el('radarAIMessage').textContent=message;publisherToast(message);
          if(generated.draft_applied)el('radarDraftDescription').scrollIntoView({block:'center'});
        }else if(mode==='outline'){
          el('radarAIMessage').textContent='AI 提纲已生成，请查看下方“AI 生成结果”。';
          el('radarAIResultTitle').scrollIntoView({block:'start'});
        }else if(mode==='research'){
          el('radarAIMessage').textContent='联网研究已完成，展开“联网研究”查看证据，也可直接起草文案。';
          el('radarAIMessage').scrollIntoView({block:'center'});
        }
      }
    }catch(error){
      // Research may already be saved when drafting fails; refresh its revision for a retry.
      if(saved)try{const updated=await api(`${root}/accounts/${encodeURIComponent(current.account_id)}/topics/${encodeURIComponent(current.id)}`);if(window.topicRadar.getTopic()?.id===current.id)window.topicRadar.acceptTopic(updated)}catch{}
      el('radarTopicError').textContent=error.message;
      el('radarAIMessage').textContent='生成未完成，已保存的资料和文案会保留。';el('radarTopicError').scrollIntoView({block:'center'});
    }finally{el('radarTopicForm').removeAttribute('aria-busy');lockTopic(false)}
  }
  el('radarTopicDialog').addEventListener('cancel',event=>{if(topicBusy)event.preventDefault()});
  el('radarAIGenerateOutline').onclick=()=>generate('outline');
  el('radarAIGenerateDraft').onclick=()=>generate('draft');
  el('radarAIResearch')?.addEventListener('click',()=>generate('research'));
  el('radarAIAdoptOutline').onclick=async()=>{
    if(topicBusy)return;
    try{lockTopic(true);window.topicRadar.acceptTopic(await window.topicRadar.saveCurrent(true));publisherToast('已采用 AI 提纲')}
    catch(error){el('radarTopicError').textContent=error.message}finally{lockTopic(false)}
  };
  el('radarAIAdoptDraft').onclick=async()=>{
    const s=window.topicRadar.getTopic()?.ai_suggestion;if(!s||s.mode!=='draft'||topicBusy)return;
    if((el('radarDraftDescription').value.trim()||el('radarDraftTitle').value.trim())&&!confirm('将用 AI 建议替换当前标题和文案并保存，继续吗？'))return;
    try{
      lockTopic(true);window.topicRadar.acceptTopic(await window.topicRadar.saveCurrent(false,true));
      el('radarAIMessage').textContent='已填入 AI 标题和文案并保存。';publisherToast('已填入 AI 标题和文案并保存');
      el('radarDraftDescription').scrollIntoView({block:'center'});
    }catch(error){el('radarTopicError').textContent=error.message}finally{lockTopic(false)}
  };
  window.topicAI={show,isBusy:()=>topicBusy};
})();
