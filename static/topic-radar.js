/* Account-aware discovery; never invokes publishing or account browser tasks. */
(() => {
  const root='/api/topic-radar', el=id=>document.getElementById(id);
  const styleNames={explain:'知识科普',story:'人物故事',business:'行业经营',general:'自定义方向'};
  const stateNames={watching:'待观察',selected:'已选题',making:'制作中',published:'已发布',dropped:'已放弃'};
  const laneNames={matched:'适合这个账号',douyin:'抖音热点',rising:'正在上升',all:'探索全部热点',saved:'我的选题库',ai:'AI 选题'};
  let accounts=[],accountId=ls.get('radarAccount',''),lane=ls.get('radarLane','matched'),profile=null,health=null,currentTopic=null,topicAccount=null,handoff=null,sequence=0,poll=null,opening=null,profileOwner='';
  const date=value=>value?new Date(value).toLocaleString():'尚未取得';
  const route=(suffix,id=accountId)=>`${root}/accounts/${encodeURIComponent(id)}/${suffix}`;
  async function send(method,url,data){return api(url,{method,headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})})}
  function errorAt(id,error){el(id).textContent=error.message||String(error)}
  function selectedAccount(){return accounts.find(account=>account.id===accountId)}
  function sourceGroups(sources){
    const groups=new Map();for(const source of sources){const name=source.group||'信息来源';if(!groups.has(name))groups.set(name,[]);groups.get(name).push(source)}return [...groups];
  }

  async function loadStatus(){
    health=await api(root+'/status');
    if(el('radarSourceCoverage'))el('radarSourceCoverage').textContent=`${health.sources.length} 个来源频道 · ${health.publisher_count||'多个'} 个网站`;
    if(el('radarSourceUpgrade'))el('radarSourceUpgrade').textContent=health.catalog_version===2?'':'检测到后台仍在运行旧版本，请先保存编辑，再完全退出并重新启动平台以加载全部来源。';
    el('radarSources').innerHTML=sourceGroups(health.sources.filter(source=>health.settings.sources.includes(source.id))).map(([name,sources])=>`<details class="radar-source-group" open><summary>${esc(name)} · ${sources.length}</summary>${sources.map(source=>`<div class="radar-source"><b>${esc(source.name)}</b><small class="${source.error?'source-failed':''}">${source.error?'暂不可用，保留上次数据':source.last_success?'最近取得 '+source.item_count+' 条':'尚未采集'}</small><small>采集：${esc(date(source.last_success))}</small>${source.error?`<small>${esc(source.error)}</small>`:''}</div>`).join('')}</details>`).join('');
    el('radarRefreshStatus').textContent=health.refresh.message+(health.settings.enabled?` · 每 ${health.settings.interval_minutes} 分钟监控`:' · 手动刷新');
    el('radarRefresh').disabled=health.refresh.running;
    el('radarRefresh').textContent=health.refresh.running?'正在刷新…':'刷新热点';
    return health;
  }

  async function open(){
    if(opening)return opening;
    opening=(async()=>{
      try{
        const data=await api(root+'/accounts');accounts=data;
        if(!accounts.some(account=>account.id===accountId))accountId=accounts[0]?.id||'';
        ls.set('radarAccount',accountId);
        document.querySelectorAll('[data-radar-lane]').forEach(button=>button.classList.toggle('active',button.dataset.radarLane===lane));
        el('radarAccount').innerHTML=accounts.length?accounts.map(account=>`<option value="${esc(account.id)}">${esc(PN[account.platform]?.[0]||account.platform)} · ${esc(account.name)}</option>`).join(''):'<option value="">请先在账号中心添加账号</option>';
        el('radarAccount').value=accountId;el('radarProfileButton').disabled=!accountId;el('radarPositionButton').disabled=!accountId;el('radarAIAnalyze').disabled=!accountId;
        await Promise.all([loadStatus(),loadFeed()]);
        if(health.refresh.running)watchRefresh();
      }catch(error){el('radarResults').innerHTML=`<div class="radar-empty">${esc(error.message)}</div>`}
    })().finally(()=>opening=null);
    return opening;
  }

  async function loadFeed(analyze=false){
    const ticket=++sequence,id=accountId,requestedLane=lane;
    el('radarAIToolbar').classList.toggle('hidden',requestedLane!=='ai');
    el('radarListTitle').textContent=laneNames[lane];el('radarCount').textContent='';
    el('radarNotice').textContent='';
    el('radarAccountSummary').textContent=selectedAccount()?.name||'尚未选择账号';
    if(!id){el('radarResults').innerHTML='<div class="radar-empty">先添加创作账号，再为它选择内容方向。</div>';return}
    el('radarResults').innerHTML='<div class="radar-empty">正在匹配账号方向…</div>';
    try{
      if(requestedLane==='saved'){
        const items=await api(route('topics',id));if(ticket!==sequence)return;
        el('radarNotice').textContent='每个账号独立保存选题。同一热点可用不同表达方式创建多个选题。';
        el('radarCount').textContent=items.length+' 个选题';
        el('radarResults').innerHTML=items.length?items.map(item=>`<article class="radar-lead"><div class="radar-lead-top"><span>${esc(styleNames[item.style])}</span><span class="radar-tag">${esc(stateNames[item.status])}</span></div><h3>${esc(item.draft_title||item.outline.title)}</h3><p class="hint">${esc(item.outline.angle)}</p><div class="radar-lead-footer"><time>${esc(date(item.updated_at))}</time><button class="b p" data-open-topic="${esc(item.id)}">编辑选题</button></div></article>`).join(''):'<div class="radar-empty">还没有收藏选题。找到感兴趣的热点后，选择表达方式并保存。</div>';
        el('radarResults').querySelectorAll('[data-open-topic]').forEach(button=>button.onclick=async()=>{
          button.disabled=true;
          try{const item=await api(route('topics/'+button.dataset.openTopic,id));if(accountId===id)showTopic(item,id)}
          catch(error){publisherToast(error.message)}finally{button.disabled=false}
        });
        return;
      }
      let data;
      if(requestedLane==='ai'){
        el('radarAIAnalyze').disabled=true;
        el('radarResults').innerHTML='<div class="radar-empty">'+(analyze?'AI 正在分析账号定位与候选热点…':'正在读取上次 AI 分析…')+'</div>';
        try{data=await (analyze?send('POST',route('ai/recommendations',id),{}):api(route('ai/recommendations',id)))}finally{el('radarAIAnalyze').disabled=false}
        data.keywords=[...data.profile.inherited_tags,...data.profile.domains,...data.profile.keywords];
        data.notice=data.generated_at?`AI 分析于 ${date(data.generated_at)} · ${data.model} · ${data.candidate_count} 条候选。${data.stale?'定位、配置或热点已变化，建议重新分析。':''}`:'先在“AI 配置”填写并启用服务，再点击“分析当前热点”。仅在手动点击时调用模型。';
      }else{data=await api(route('feed',id)+'?lane='+requestedLane)}
      if(ticket!==sequence)return;
      profile=data.profile;
      el('radarAccountSummary').textContent=data.keywords.length?'筛选方向：'+data.keywords.join('、'):'这个账号还没有明确的选题关键词，可先编辑定位。';
      el('radarNotice').textContent=data.notice;
      el('radarCount').textContent=data.items.length+' 条线索';
      const empty=requestedLane==='douyin'?'尚无抖音热点。请在“信息源与监控”启用抖音热榜、抖音上升推荐，再刷新热点；来源异常会保留上次数据并提示。':requestedLane==='ai'?'暂无 AI 推荐。点击“分析当前热点”；若无合适选题，AI 可能返回空结果。':requestedLane==='rising'?'尚无匹配且有上升证据的热点。至少取得两次不同的榜单快照后才能比较。':requestedLane==='all'?'还没有热点。点击“刷新热点”取得真实榜单；来源异常可在右侧查看。':'暂时没有匹配线索。可以刷新热点、补充关键词，或切换“探索全部热点”。';
      el('radarResults').innerHTML=data.items.length?data.items.map(item=>`<article class="radar-lead"><div class="radar-lead-top"><span>${esc(item.source_name)}${item.rank?' · 第 '+item.rank+' 位':''}</span><span class="radar-score">${requestedLane==='ai'?'AI 相关性 '+item.ai_score:'匹配分 '+item.score}</span></div><h3>${esc(item.title)}</h3>${item.source==='douyin-rising'?'<span class="radar-tag">平台上升推荐 · 非增长率</span> ':''}${item.heat?`<span class="radar-tag">来源热度 ${Number(item.heat).toLocaleString()} · 非播放量</span> `:''}${item.stale?'<span class="radar-tag warn">旧快照 / 来源延迟</span>':item.rank_delta>0?`<span class="radar-tag up" title="对比 ${esc(date(item.comparison_at))}">快照排名 ↑ ${item.rank_delta}</span>`:'<span class="radar-tag">标题线索 · 待核实</span>'}<ul class="radar-reasons">${(requestedLane==='ai'?[item.ai_reason,'切入角度：'+item.ai_angle]:item.reasons).map(reason=>`<li>${esc(reason)}</li>`).join('')}</ul><time>首次发现 ${esc(date(item.first_seen))}${item.published_at?` · 来源发布 ${esc(date(item.published_at))}`:""}</time><div class="radar-lead-footer"><a href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">查看来源 ↗</a><select aria-label="创作表达方式">${profile.styles.map(style=>`<option value="${style}">${styleNames[style]}</option>`).join('')}</select><button class="b p" data-save-lead="${esc(item.id)}">收藏并生成提纲</button></div></article>`).join(''):`<div class="radar-empty">${empty}</div>`;
      el('radarResults').querySelectorAll('[data-save-lead]').forEach(button=>button.onclick=async()=>{
        button.disabled=true;
        try{const item=await send('POST',route('topics',id),{lead_id:button.dataset.saveLead,style:button.parentElement.querySelector('select').value});showTopic(item,id)}catch(error){publisherToast(error.message)}finally{button.disabled=false}
      });
    }catch(error){if(ticket===sequence)el('radarResults').innerHTML=`<div class="radar-empty">${esc(error.message)}</div>`}
  }

  async function watchRefresh(){
    if(poll)return;
    const tick=async()=>{
      try{await loadStatus();if(!health.refresh.running){poll=null;if(!el('radar').classList.contains('hidden'))await loadFeed();return}}catch(error){el('radarRefreshStatus').textContent=error.message;poll=null;return}
      poll=setTimeout(tick,1200);
    };poll=setTimeout(tick,800);
  }
  el('radarRefresh').onclick=async()=>{el('radarRefresh').disabled=true;try{await send('POST',root+'/refresh');await loadStatus();watchRefresh()}catch(error){publisherToast(error.message);el('radarRefresh').disabled=false}};
  el('radarAccount').onchange=()=>{accountId=el('radarAccount').value;ls.set('radarAccount',accountId);profile=null;loadFeed()};
  document.querySelectorAll('[data-radar-lane]').forEach(button=>button.onclick=()=>{lane=button.dataset.radarLane;ls.set('radarLane',lane);document.querySelectorAll('[data-radar-lane]').forEach(other=>other.classList.toggle('active',other===button));loadFeed()});
  document.querySelectorAll('[data-radar-close]').forEach(button=>button.onclick=()=>button.dataset.radarClose==='radarTopicDialog'?closeTopic():el(button.dataset.radarClose).close());

  el('radarProfileButton').onclick=async()=>{
    try{
      const id=accountId,p=await api(route('profile',id));if(id!==accountId)return;profile=p;profileOwner=id;
      el('radarProfileTitle').textContent=selectedAccount().name+' · 账号定位';
      el('radarInherited').textContent='账号中心标签：'+(selectedAccount().tags?.join('、')||'暂无')+'。保存定位不会改动这些标签。';
      el('radarInherit').checked=p.inherit_tags;
      for(const [key,input] of Object.entries({domains:'radarDomains',direction:'radarDirection',audience:'radarAudience',region:'radarRegion',keywords:'radarKeywords',exclude:'radarExclude'}))el(input).value=Array.isArray(p[key])?p[key].join(' '):p[key];
      document.querySelectorAll('[name=radarStyle]').forEach(input=>input.checked=p.styles.includes(input.value));
      el('radarProfileError').textContent='';el('radarProfileDialog').showModal();
    }catch(error){publisherToast(error.message)}
  };
  el('radarProfileForm').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try{
      const body={revision:profile.revision,inherit_tags:el('radarInherit').checked,styles:[...document.querySelectorAll('[name=radarStyle]:checked')].map(input=>input.value)};
      for(const [key,input] of Object.entries({domains:'radarDomains',direction:'radarDirection',audience:'radarAudience',region:'radarRegion',keywords:'radarKeywords',exclude:'radarExclude'}))body[key]=el(input).value;
      profile=await send('PUT',route('profile',profileOwner),body);el('radarProfileDialog').close();await loadFeed();
    }catch(error){errorAt('radarProfileError',error)}finally{button.disabled=false}
  };
  el('radarSettingsButton').onclick=async()=>{
    try{await loadStatus();el('radarSourceOptions').innerHTML=sourceGroups(health.sources).map(([name,sources])=>`<fieldset><legend>${esc(name)}</legend>${sources.map(source=>`<label class="radar-check"><input type="checkbox" name="radarSource" value="${source.id}" ${health.settings.sources.includes(source.id)?'checked':''}>${esc(source.name)}</label>`).join('')}</fieldset>`).join('');el('radarAuto').checked=health.settings.enabled;el('radarInterval').value=health.settings.interval_minutes;el('radarSettingsError').textContent='';el('radarSettingsDialog').showModal()}catch(error){publisherToast(error.message)}
  };
  el('radarRecommendedSources')?.addEventListener('click',()=>{document.querySelectorAll('[name=radarSource]').forEach(input=>input.checked=(health.recommended_sources||health.sources.map(source=>source.id)).includes(input.value))});
  el('radarAllSources')?.addEventListener('click',()=>{document.querySelectorAll('[name=radarSource]').forEach(input=>input.checked=true)});
  el('radarSettingsForm').onsubmit=async event=>{
    event.preventDefault();const button=event.submitter;button.disabled=true;
    try{await send('PUT',root+'/settings',{enabled:el('radarAuto').checked,interval_minutes:Number(el('radarInterval').value),sources:[...document.querySelectorAll('[name=radarSource]:checked')].map(input=>input.value)});el('radarSettingsDialog').close();await loadStatus();await loadFeed()}catch(error){errorAt('radarSettingsError',error)}finally{button.disabled=false}
  };

  function showTopic(item,id){
    currentTopic=item;topicAccount=accounts.find(account=>account.id===id);const outline=item.outline;
    el('radarTopicTitle').textContent=outline.title;
    el('radarTopicMeta').textContent=(topicAccount?.name||'当前账号')+' · '+styleNames[item.style]+' · '+outline.method;
    el('radarOutline').innerHTML=`<h3>${esc(outline.angle)}</h3>${outline.direction?`<p>定位：${esc(outline.direction)}</p>`:''}${outline.audience?`<p>受众：${esc(outline.audience)}</p>`:''}<ol>${outline.steps.map(step=>`<li>${esc(step)}</li>`).join('')}</ol><b>${esc(outline.evidence_status)}</b><ul>${outline.research_gaps.map(gap=>`<li>${esc(gap)}</li>`).join('')}</ul><a href="${esc(outline.source_url)}" target="_blank" rel="noopener noreferrer">${esc(outline.source_name)} · 查看来源</a>`;
    el('radarTopicState').value=item.status;el('radarDraftTitle').value=item.draft_title;el('radarDraftDescription').value=item.draft_description;el('radarTopicNotes').value=item.notes;
    window.topicAI?.show(item);el('radarAIMessage').textContent='';el('radarTopicError').textContent='';if(!el('radarTopicDialog').open)el('radarTopicDialog').showModal();
  }
  async function saveCurrent(applyAIOutline=false,applyAIDraft=false){
    currentTopic=await send('PATCH',route('topics/'+currentTopic.id,currentTopic.account_id),{apply_ai_outline:applyAIOutline,apply_ai_draft:applyAIDraft,revision:currentTopic.revision,status:el('radarTopicState').value,draft_title:el('radarDraftTitle').value,draft_description:el('radarDraftDescription').value,notes:el('radarTopicNotes').value});
    return currentTopic;
  }
  let closingTopic=false;
  function topicDirty(){
    if(!currentTopic||!el('radarTopicDialog').open)return false;
    return Object.entries({status:'radarTopicState',draft_title:'radarDraftTitle',draft_description:'radarDraftDescription',notes:'radarTopicNotes'})
      .some(([key,id])=>el(id).value!==currentTopic[key]);
  }
  async function flushTopic(){
    if(window.topicAI?.isBusy()||closingTopic)throw new Error('选题正在生成或保存，请完成后再退出');
    if(topicDirty())await saveCurrent();
  }
  async function closeTopic(){
    if(closingTopic||window.topicAI?.isBusy())return;
    closingTopic=true;
    const controls=[...el('radarTopicForm').querySelectorAll('button,input,select,textarea')].map(node=>[node,node.disabled]);
    controls.forEach(([node])=>node.disabled=true);
    try{if(topicDirty())await saveCurrent();el('radarTopicDialog').close()}
    catch(error){errorAt('radarTopicError',error);el('radarTopicError').scrollIntoView({block:'center'})}
    finally{closingTopic=false;controls.forEach(([node,disabled])=>node.disabled=disabled)}
  }
  el('radarTopicDialog').addEventListener('cancel',event=>{event.preventDefault();closeTopic()});
  el('radarTopicDialog').addEventListener('close',()=>{if(lane==='saved')loadFeed()});
  el('radarTopicForm').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await saveCurrent();el('radarTopicDialog').close();if(lane==='saved')await loadFeed();publisherToast('选题已保存到本机')}catch(error){errorAt('radarTopicError',error)}finally{button.disabled=false}};
  el('radarCopyOutline').onclick=async()=>{try{const o=currentTopic.outline;await navigator.clipboard.writeText([o.title,o.angle,o.method,...o.steps.map((step,index)=>`${index+1}. ${step}`),'待核实：'+o.research_gaps.join('；'),o.source_url].join('\n'));publisherToast('创作提纲已复制')}catch(error){errorAt('radarTopicError',new Error('无法读取剪贴板权限，请直接选择提纲文字复制'))}};
  el('radarUseTopic').onclick=async()=>{
    const button=el('radarUseTopic');button.disabled=true;
    try{const item=await saveCurrent();handoff={...item,account:topicAccount};el('radarHandoffTitle').textContent=`待使用选题：${item.draft_title} · ${topicAccount.name}`;el('radarHandoff').classList.remove('hidden');el('radarTopicDialog').close();document.querySelector('[data-tab=pub]').click();el('radarHandoff').scrollIntoView({block:'start'});}catch(error){errorAt('radarTopicError',error)}finally{button.disabled=false}
  };
  el('radarCancelHandoff').onclick=()=>{handoff=null;el('radarHandoff').classList.add('hidden')};
  el('radarApplyHandoff').onclick=async()=>{
    const button=el('radarApplyHandoff');if(!handoff||publisherBusy)return;
    if(!mediaFiles.length){publisherToast('请先选择作品文件，再点击填入当前作品');return}
    button.disabled=true;
    try{
      const current=await api('/api/accounts');const account=current.find(item=>item.id===handoff.account_id);
      if(!account)throw new Error('原账号已删除，请重新选择选题所属账号');
      if(selectedType()==='image'&&!PC[account.platform].image)throw new Error('这个账号的平台不支持当前图文类型，请先选择视频作品');
      const hasContent=['title','desc','tags'].some(id=>el(id).value.trim());
      const backendOnly=PC[account.platform].backend_only;
      if(hasContent&&!confirm(backendOnly?'将替换工具内的标题、文案和话题；内容请在后台手动填写。继续吗？':`将替换当前标题、文案和话题，并仅选择“${account.name}”作为发布账号。继续吗？`))return;
      el('title').value=handoff.draft_title;el('desc').value=handoff.draft_description;el('tags').value='';
      for(const platform of Object.keys(PN))tg[platform]={...(tg[platform]||{}),on:platform===account.platform&&!backendOnly,acc:platform===account.platform?account.id:(tg[platform]?.acc||'')};
      ls.set('targets',tg);renderTargets();resizeKeywordInput();updateTextPreview();publisherChanged();
      handoff=null;el('radarHandoff').classList.add('hidden');publisherToast(backendOnly?'文案已保存在工具内；请打开后台手动填写':'已填入文案并选择对应账号，请核对后再发布');
    }catch(error){publisherToast(error.message)}finally{button.disabled=false}
  };
  window.topicRadar={open,saveCurrent,flush:flushTopic,getTopic:()=>currentTopic,acceptTopic:item=>showTopic(item,item.account_id),loadAI:()=>{
    lane='ai';ls.set('radarLane',lane);document.querySelectorAll('[data-radar-lane]').forEach(button=>button.classList.toggle('active',button.dataset.radarLane===lane));return loadFeed(true);
  }};
  setInterval(async()=>{
    if(!health?.settings.enabled||poll||opening||el('radar').classList.contains('hidden')||document.querySelector('.radar-dialog[open]'))return;
    const finished=health.refresh.finished_at;
    try{await loadStatus();if(health.refresh.running)watchRefresh();else if(finished!==health.refresh.finished_at)await loadFeed()}catch(error){el('radarRefreshStatus').textContent=error.message}
  },15000);
  if(!el('radar').classList.contains('hidden'))open();
})();
