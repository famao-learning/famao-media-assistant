/* 保存到本机数据文件，独立于桌面启动端口与浏览器缓存。 */
window.createUISettings=function(initial){
  const state=structuredClone(initial),client=crypto.randomUUID();
  let revision=0,pending={},retryTimer=null;
  const requests=new Set();
  const clone=value=>JSON.parse(JSON.stringify(value));
  function report(failed){
    const status=document.getElementById('settingsStatus');
    status.hidden=!failed;
    status.textContent=failed?'设置未保存，正在重试…':'';
  }
  function snapshot(){return {settings:clone(state),client,revision}}
  function send(){
    clearTimeout(retryTimer);
    const settings=clone(pending),version=revision;
    if(!Object.keys(settings).length)return Promise.resolve();
    const request=fetch('/api/ui-settings',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({settings,client,revision:version}),keepalive:true})
      .then(response=>{if(!response.ok)throw new Error('设置保存失败');
        for(const [key,value] of Object.entries(settings))if(JSON.stringify(pending[key])===JSON.stringify(value))delete pending[key];
        if(!Object.keys(pending).length)report(false);
      }).catch(()=>{const failed=!!Object.keys(pending).length;report(failed);if(failed)retryTimer=setTimeout(send,1500)}).finally(()=>requests.delete(request));
    requests.add(request);return request;
  }
  const api={
    get(key,fallback){
      if(Object.hasOwn(state,key))return clone(state[key]);
      // 迁移当前页面能读取到的旧版缓存。
      try{const value=JSON.parse(localStorage.getItem(key));if(value!==null){api.set(key,value);return clone(value)}}catch(e){}
      return fallback;
    },
    set(key,value){
      if(JSON.stringify(state[key])===JSON.stringify(value))return;
      state[key]=clone(value);pending[key]=clone(value);revision++;
      // 立即提交，轮询重绘的相同设置不会重复写入。
      send();
    },
    snapshot,
    async flush(){await Promise.all([...requests]);await send();if(Object.keys(pending).length)throw new Error('设置尚未保存，请稍后重试')}
  };
  window.addEventListener('pagehide',()=>{
    if(Object.keys(pending).length)navigator.sendBeacon('/api/ui-settings',new Blob([JSON.stringify(snapshot())],{type:'application/json'}));
  });
  return api;
};
