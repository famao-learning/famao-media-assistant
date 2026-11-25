/* 上传、预览、封面、草稿与发布均使用同一份真实表单数据。 */
let mediaFiles=[],mediaURLs=[],activeDraftId='',primaryPlatform='',publisherBusy=false,disabledPublisherControls=[];
let coverKind='h',cropImage=null,cropOffset={x:0,y:0},cropPending={},cropGeneration=0,coverWorking=false;
const coverURLs={h:'',v:''};
let previewGeneration=0,previewJob='',previewState='original',previewWantsPlay=false;
let publicationRequestKey='',publicationRequestFingerprint='',lastSubmittedFingerprint='';
const selectedType=()=>document.querySelector('input[name=type]:checked').value;
const checkedValue=name=>document.querySelector(`input[name=${name}]:checked`).value;
const selectedPlatforms=()=>Object.keys(PN).filter(p=>!PC[p].backend_only&&tg[p]?.on&&tg[p]?.acc&&!(selectedType()==='image'&&!PC[p].image));
const MAX_MEDIA_SIZE=16*1024*1024*1024;
let toastTimer;
function publisherToast(message){clearTimeout(toastTimer);$('publisherToast').textContent=message;$('publisherToast').classList.remove('hidden');toastTimer=setTimeout(()=>$('publisherToast').classList.add('hidden'),4500)}
function publisherError(message){$('publishError').textContent=message;$('publishError').classList.toggle('hidden',!message);if(message)$('publishError').scrollIntoView({block:'nearest',behavior:'smooth'})}
function pickRadio(name,value){const input=document.querySelector(`input[name=${name}][value="${value}"]`);if(input)input.checked=true}
function assignFiles(input,files){const transfer=new DataTransfer();for(const file of files)transfer.items.add(file);input.files=transfer.files}
function chooseMedia(){$('files').value='';$('files').click()}
function formatSize(bytes){return bytes>=1024**3?`${(bytes/1024**3).toFixed(2)} GB`:bytes>=1024**2?`${(bytes/1024**2).toFixed(1)} MB`:`${Math.ceil(bytes/1024)} KB`}
function updateStage(edit){$('uploadStage').classList.toggle('hidden',edit);$('editorStage').classList.toggle('hidden',!edit);publisherError('');resizeKeywordInput()}
async function backToUpload(){if(publisherBusy)return;try{await flushAutosave();$('mediaPreview').pause();updateStage(false)}catch(error){publisherToast(error.message)}}
function clearMedia(){
  $('mediaPreview').pause();$('mediaPreview').removeAttribute('src');$('mediaPreview').load();releaseVideoPreview();$('imagePreview').removeAttribute('src');
  for(const url of mediaURLs)URL.revokeObjectURL(url);mediaURLs=[];mediaFiles=[];assignFiles($('files'),[]);
  for(const kind of ['h','v']){assignFiles($('cover_'+kind),[]);updateCoverPreview(kind)}
  $('imageGrid').innerHTML='';activeDraftId='';
  for(const key of ['title','desc','tags'])$(key).value='';
  $('scheduleAt').value='';pickRadio('publish_time','now');syncPublishTime();
  publicationRequestKey='';publicationRequestFingerprint='';lastSubmittedFingerprint='';
  resizeKeywordInput();updateTextPreview();
}
function changeMediaType(){
  clearMedia();updateStage(false);const image=selectedType()==='image';
  $('files').accept=image?'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp':'video/*,.mp4,.mov,.m4v';$('files').multiple=image;
  $('fileLabel').textContent=image?'上传图片':'上传视频';$('dropTitle').textContent=image?'点击上传 或直接将图片文件拖入此区域':'点击上传 或直接将视频文件拖入此区域';
  $('fileHint').textContent=image?'支持 JPG、PNG、WebP，可一次选择多张图片，编辑页中可调整顺序。':'推荐使用清晰的原始视频。选择后可预览作品、填写描述并设置横竖封面。';
  $('guideFormatTitle').textContent=image?'图片大小和格式':'视频大小和格式';$('guideFormat').textContent=image?'支持 JPG、PNG、WebP 格式，保留原始图片清晰度':'支持 1 小时以内、16GB 以内的视频，推荐 MP4、MOV 格式';
  $('guideQualityTitle').textContent=image?'图片画质':'视频画质';$('guideQuality').textContent=image?'上传清晰的原图，避免过度压缩，首张图片作为图文预览':'支持 4K 视频，使用清晰的原始文件，让好内容被更清晰地看见';
  $('guideRatioTitle').textContent=image?'图片数量':'视频画幅';$('guideRatio').textContent=image?'X 最多 4 张，抖音和快手最多 35 张，可自由调整图片顺序':'建议上传 16:9、9:16、3:4、4:3 比例的视频，兼顾横竖屏观看';
  $('qualityBadge').textContent=image?'支持多图':'支持 4K';$('imageLimitHint').classList.toggle('hidden',!image);$('coverBox').classList.toggle('hidden',image);renderTargets();
}
function validateMedia(files){
  if(!files.length)return '请选择作品文件';
  const image=selectedType()==='image';
  if(!image&&files.length!==1)return '视频作品一次只能选择一个视频';
  if(image&&files.length>35)return '图文作品最多选择 35 张图片';
  if(image&&selectedPlatforms().includes('x')&&files.length>4)return 'X 图文最多选择 4 张图片';
  if(files.some(file=>file.size===0))return '不能选择空文件';
  if(files.some(file=>file.size>MAX_MEDIA_SIZE))return '单个文件不能超过 16GB';
  if(files.reduce((size,file)=>size+file.size,0)>MAX_MEDIA_SIZE)return '本次作品的文件总大小不能超过 16GB';
  if(image&&files.some(file=>!/^image\/(jpeg|png|webp)$/.test(file.type)&&!(/\.(jpe?g|png|webp)$/i.test(file.name))))return '图片请选择 JPG、PNG 或 WebP 文件';
  if(!image&&files.some(file=>!file.type.startsWith('video/')&&!/\.(mp4|mov|m4v|webm|mkv|avi|wmv|flv)$/i.test(file.name)))return '请选择视频文件';
  return '';
}
async function acceptMedia(files){
  if(publisherBusy)return;
  files=Array.from(files);const error=validateMedia(files);if(error){publisherToast(error);return false}
  try{if(mediaFiles.length||autosaveRunning){setPublisherBusy(true);await flushAutosave()}}catch(error){publisherToast(error.message);return false}finally{if(publisherBusy)setPublisherBusy(false)}
  clearMedia();mediaFiles=files;assignFiles($('files'),files);renderMedia();updateStage(true);renderTargets();window.scrollTo({top:0,behavior:'smooth'});return true;
}
function renderMedia(){
  for(const url of mediaURLs)URL.revokeObjectURL(url);mediaURLs=mediaFiles.map(file=>URL.createObjectURL(file));
  const image=selectedType()==='image';$('mediaPreview').classList.toggle('hidden',image);$('imagePreview').classList.toggle('hidden',!image);$('imageGrid').classList.toggle('hidden',!image);$('previewPlay').textContent=image?'查看首图':'预览视频';
  if(image){$('mediaPreview').pause();$('mediaPreview').removeAttribute('src');$('mediaPreview').load();$('imagePreview').src=mediaURLs[0];$('mediaName').textContent=`${mediaFiles.length} 张图片`;$('mediaDetails').textContent=`共 ${formatSize(mediaFiles.reduce((total,file)=>total+file.size,0))}`;renderImageGrid()}
  else{$('imagePreview').removeAttribute('src');$('mediaPreview').src=mediaURLs[0];$('mediaPreview').load();$('mediaName').textContent=mediaFiles[0].name;$('mediaDetails').textContent=formatSize(mediaFiles[0].size)}
  $('mediaReady').textContent=image?`${mediaFiles.length} 张图片已就绪`:'视频文件已就绪';updateTextPreview();
  if(typeof publisherChanged==='function')publisherChanged();
}
function renderImageGrid(){
  $('imageGrid').innerHTML=mediaFiles.map((file,index)=>`<div class="image-tile"><img src="${mediaURLs[index]}" alt="${esc(file.name)}"><small>${index+1}${index===0?' · 首图':''}</small><div class="image-tile-tools"><button type="button" aria-label="向前移动第 ${index+1} 张图片" ${index===0?'disabled':''} onclick="moveImage(${index},-1)">←</button><button type="button" aria-label="移除第 ${index+1} 张图片" onclick="removeImage(${index})">移除</button><button type="button" aria-label="向后移动第 ${index+1} 张图片" ${index===mediaFiles.length-1?'disabled':''} onclick="moveImage(${index},1)">→</button></div></div>`).join('');
}
function moveImage(index,direction){const next=index+direction;if(next<0||next>=mediaFiles.length)return;[mediaFiles[index],mediaFiles[next]]=[mediaFiles[next],mediaFiles[index]];assignFiles($('files'),mediaFiles);renderMedia()}
function removeImage(index){if(publisherBusy)return;mediaFiles.splice(index,1);if(!mediaFiles.length){clearMedia();updateStage(false);return}assignFiles($('files'),mediaFiles);renderMedia()}
function setPreviewStatus(message){$('previewStatus').textContent=message;$('previewStatus').classList.toggle('hidden',!message)}
function releaseVideoPreview(){previewGeneration++;if(previewJob)fetch('/api/media/preview/'+previewJob,{method:'DELETE',keepalive:true}).catch(()=>{});previewJob='';previewState='original';previewWantsPlay=false;setPreviewStatus('')}
async function ensureVideoPreview(play=false){
  if(!mediaFiles.length||selectedType()!=='video')return;
  previewWantsPlay=previewWantsPlay||play;if(previewState==='processing'||previewState==='converted')return;
  const generation=previewGeneration;previewState='processing';setPreviewStatus('正在生成可播放预览…');$('previewPlay').textContent='生成预览中…';
  let job='';
  try{
    const form=new FormData();form.append('file',mediaFiles[0]);const response=await fetch('/api/media/preview',{method:'POST',body:form});const result=await response.json();if(!response.ok)throw new Error(result.error||'预览生成失败');job=result.id;
    if(generation!==previewGeneration){fetch('/api/media/preview/'+job,{method:'DELETE'}).catch(()=>{});return}previewJob=job;
    while(generation===previewGeneration){
      const response=await fetch('/api/media/preview/'+job);const result=await response.json();if(!response.ok||result.status==='error')throw new Error(result.error||'预览生成失败');
      if(result.status==='ready'){previewState='converted';const video=$('mediaPreview');video.src='/api/media/preview/'+job+'/video';video.load();setPreviewStatus('');$('previewPlay').textContent='预览视频';if(previewWantsPlay)video.play().catch(()=>publisherToast('预览已就绪，点击播放即可'));return}
      await new Promise(resolve=>setTimeout(resolve,700));
    }
  }catch(error){if(generation!==previewGeneration)return;previewState='failed';setPreviewStatus(error.message);$('previewPlay').textContent='重试预览';if(job){fetch('/api/media/preview/'+job,{method:'DELETE'}).catch(()=>{});previewJob=''}}
}
function toggleVideoPlayback(){
  if(selectedType()==='image'){$('imagePreview').scrollIntoView({block:'center'});return}
  const video=$('mediaPreview');if(previewState==='processing'){previewWantsPlay=true;return}
  if(previewState==='failed'||video.error){ensureVideoPreview(true);return}
  if(video.paused){if(video.ended)video.currentTime=0;video.play().catch(error=>{if(error.name==='NotSupportedError')ensureVideoPreview(true);else publisherToast('点击视频播放按钮开始预览')})}else video.pause();
}
function combinedDescription(){return [plainMarkdown($('desc').value).trim(),...parseTags($('tags').value).map(tag=>'#'+tag)].filter(Boolean).join(' ')}
function updateTextPreview(){
  const limit=$('title').maxLength;setStableText($('titleCounter'),`${$('title').value.length}/${limit}`);
  $('titleCounter').classList.toggle('over-limit',$('title').value.length>limit);
  const count=combinedDescription().length;setStableText($('descCounter'),`${count} / 1000`);$('descCounter').classList.toggle('over-limit',count>1000);
  setStableText($('previewAccount'),plainMarkdown($('title').value).trim()||'我的作品');setStableText($('previewCaption'),combinedDescription());$('previewPhone').classList.toggle('show-caption',$('showCaption').checked);
}

function currentPrimary(){const selected=selectedPlatforms();if(!selected.includes(primaryPlatform))primaryPlatform=selected.includes('douyin')?'douyin':selected[0]||'';return accs.find(account=>account.id===tg[primaryPlatform]?.acc)}
function syncPublisherTargets(){
  const selected=selectedPlatforms();$('title').maxLength=selected.length?Math.min(...selected.map(p=>PC[p].title_limit)):30;updateTextPreview();
}
function syncPublishTime(){
  const scheduled=checkedValue('publish_time')==='scheduled';
  $('scheduleFields').classList.toggle('hidden',!scheduled);$('scheduleAt').required=scheduled;
  const now=new Date(Date.now()+60000),parts=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(now);
  $('scheduleAt').min=parts.replace(' ','T');
  $('scheduleHint').textContent=scheduled?'点击发布后立即上传，并设置各平台自有的定时发布；平台确认提交后可关闭助手。定时设置失败时不会改为立即发布。':'统一公开；抖音禁止下载。其他平台如需设置下载权限，可在核对页补充。';
}
function updateCoverPreview(kind){const file=$('cover_'+kind).files[0];if(coverURLs[kind])URL.revokeObjectURL(coverURLs[kind]);coverURLs[kind]=file?URL.createObjectURL(file):'';const suffix=kind.toUpperCase();if(file)$('coverPreview'+suffix).src=coverURLs[kind];else $('coverPreview'+suffix).removeAttribute('src');$('coverSlot'+suffix).classList.toggle('has-cover',!!file);if(typeof publisherChanged==='function')publisherChanged()}
function loadCropImage(src){const generation=++cropGeneration;return new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>{if(generation!==cropGeneration){resolve(false);return}cropImage=image;cropOffset={x:0,y:0};$('cropZoom').value='1';drawCrop();resolve(true)};image.onerror=()=>reject(new Error('无法读取封面图片'));image.src=src})}
function drawCrop(){const canvas=$('cropCanvas'),context=canvas.getContext('2d');canvas.width=coverKind==='h'?960:720;canvas.height=coverKind==='h'?720:960;context.fillStyle='#252630';context.fillRect(0,0,canvas.width,canvas.height);if(!cropImage)return;const scale=Math.max(canvas.width/cropImage.width,canvas.height/cropImage.height)*Number($('cropZoom').value);const width=cropImage.width*scale,height=cropImage.height*scale;cropOffset.x=Math.max(-(width-canvas.width)/2,Math.min((width-canvas.width)/2,cropOffset.x));cropOffset.y=Math.max(-(height-canvas.height)/2,Math.min((height-canvas.height)/2,cropOffset.y));context.drawImage(cropImage,(canvas.width-width)/2+cropOffset.x,(canvas.height-height)/2+cropOffset.y,width,height)}
async function captureCoverFrame(){
  const video=$('mediaPreview');if(video.readyState<2){publisherToast('视频画面尚未加载，也可以直接上传封面图片');return false}
  const frame=document.createElement('canvas');frame.width=video.videoWidth;frame.height=video.videoHeight;frame.getContext('2d').drawImage(video,0,0);await loadCropImage(frame.toDataURL('image/jpeg',.92));return true;
}
async function openCover(kind){
  if(!mediaFiles.length||selectedType()!=='video')return;coverKind=kind;cropImage=null;cropPending={};$('mediaPreview').pause();$('coverDialog').showModal();setCoverStep();
  const video=$('mediaPreview');$('frameSeek').max=Number.isFinite(video.duration)?video.duration:1;$('frameSeek').value=video.currentTime||0;
  try{const file=$('cover_'+kind).files[0];if(file){const src=URL.createObjectURL(file);try{await loadCropImage(src)}finally{URL.revokeObjectURL(src)}}else await captureCoverFrame()}catch(error){publisherToast(error.message)}
}
function setCoverStep(){document.querySelectorAll('[data-cover-kind]').forEach(button=>button.classList.toggle('active',button.dataset.coverKind===coverKind));$('coverNext').textContent=coverKind==='h'?'设置竖封面':'设置横封面';drawCrop()}
function cropAsFile(kind){return new Promise((resolve,reject)=>{if(!cropImage){resolve(null);return}$('cropCanvas').toBlob(blob=>{if(!blob){reject(new Error('封面生成失败，请重新选择图片'));return}resolve(new File([blob],`cover_${kind}.png`,{type:'image/png'}))},'image/png')})}
async function switchCover(kind){if(coverWorking||kind===coverKind)return;coverWorking=true;try{const current=await cropAsFile(coverKind);if(current)cropPending[coverKind]=current;coverKind=kind;setCoverStep();const file=cropPending[kind]||$('cover_'+kind).files[0];if(file){const src=URL.createObjectURL(file);try{await loadCropImage(src)}finally{URL.revokeObjectURL(src)}}else if(cropImage){cropOffset={x:0,y:0};$('cropZoom').value='1';drawCrop()}else await captureCoverFrame()}catch(error){publisherToast(error.message)}finally{coverWorking=false}}
async function commitCover(){if(coverWorking)return;coverWorking=true;$('coverDone').disabled=true;try{const file=await cropAsFile(coverKind);if(!file){publisherToast('请选择视频画面或上传一张封面图片');return}cropPending[coverKind]=file;for(const [kind,cover] of Object.entries(cropPending)){assignFiles($('cover_'+kind),[cover]);updateCoverPreview(kind)}$('coverDialog').close();publisherToast('封面已设置')}catch(error){publisherToast(error.message)}finally{coverWorking=false;$('coverDone').disabled=false}}
function cancelCover(){cropGeneration++;cropPending={};cropImage=null;$('coverDialog').close()}
function validatePublication(){
  const platforms=selectedPlatforms();if(!platforms.length)return '请先选择至少一个发布账号';
  const mediaError=validateMedia(mediaFiles);if(mediaError)return mediaError;
  const selected=platforms.map(platform=>accs.find(account=>account.id===tg[platform].acc));const busy=selected.find(account=>account?.busy);if(busy)return `「${busy.name}」正在使用，请先关闭它的账号页面或等待当前任务结束`;
  if(parseTags($('tags').value).some(tag=>tag.length>50))return '单个话题不能超过 50 字';
  if(combinedDescription().length>1000)return '简介和话题合计不能超过 1000 字';
  if(platforms.includes('douyin')&&$('title').value.length>30)return '抖音作品标题不能超过 30 字';
  if(checkedValue('publish_time')==='scheduled'){
    if(!$('scheduleAt').value)return '请选择定时发布时间（北京时间）';
    if(new Date($('scheduleAt').value+':00+08:00').getTime()<=Date.now())return '定时发布时间必须晚于当前北京时间';
  }
  return '';
}
function publicationForm(){
  const fd=new FormData();fd.append('type',selectedType());fd.append('auto_publish',checkedValue('mode'));
  for(const file of mediaFiles)fd.append('files',file);
  for(const key of ['title','desc','category'])fd.append(key,key==='category'?$(key).value:plainMarkdown($(key).value));
  fd.append('tags',parseTags($('tags').value).join(' '));
  if(selectedType()==='video')for(const kind of ['h','v'])if($('cover_'+kind).files[0])fd.append('cover_'+kind,$('cover_'+kind).files[0]);
  const collections={},partitions={},targets={};for(const platform of selectedPlatforms()){const target=tg[platform];fd.append('accounts',target.acc);if(target.col?.[target.acc])collections[target.acc]=target.col[target.acc];if(platform==='bilibili'&&accountBackend(accs.find(a=>a.id===target.acc))!=='matrixmedia'){const account=accs.find(a=>a.id===target.acc);const zone=target.zone?.[target.acc]??(account?.name?.trim()==='星河切片号'?'知识':'');if(zone)partitions[target.acc]=zone}targets[platform]=target}
  fd.append('collections',JSON.stringify(collections));fd.append('partitions',JSON.stringify(partitions));fd.append('targets',JSON.stringify(targets));fd.append('visibility','public');fd.append('allow_download','0');fd.append('schedule_at',checkedValue('publish_time')==='scheduled'?$('scheduleAt').value:'');return fd;
}
function postMultipart(url,body){return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('POST',url);xhr.responseType='json';xhr.upload.onprogress=event=>{if(event.lengthComputable){const percent=Math.round(event.loaded/event.total*100);$('transferBar').value=percent;$('transferLabel').textContent=`正在保存作品文件 ${percent}%`}};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve(xhr.response);else reject(new Error(xhr.response?.error||(xhr.status===413?'作品文件总大小超过限制':'提交失败，请重试')))};xhr.onerror=()=>reject(new Error('无法连接本地服务，请确认软件正在运行'));xhr.onabort=()=>reject(new Error('提交已中断'));xhr.send(body)})}
function setPublisherBusy(busy){publisherBusy=busy;if(busy){disabledPublisherControls=[...$('editorStage').querySelectorAll('input,textarea,select,button')].map(el=>[el,el.disabled]);disabledPublisherControls.forEach(([el])=>el.disabled=true)}else{disabledPublisherControls.forEach(([el,disabled])=>el.disabled=disabled);disabledPublisherControls=[];renderTargets();renderCategoryOptions()}$('pubBtn').disabled=busy;$('saveDraftBtn').disabled=busy;$('transferProgress').classList.toggle('hidden',!busy);if(busy){$('transferBar').value=0;$('transferLabel').textContent='正在保存作品文件…'}}
async function publish(){
  if(publisherBusy)return;normalizeKeywordInput();const error=validatePublication();if(error){publisherError(error);return}
  const fingerprint=workflowFingerprint();if(fingerprint===lastSubmittedFingerprint){publisherToast('这份作品已创建发布任务，请在右侧查看结果或重试失败账号');return}
  publisherError('');setPublisherBusy(true);$('pubBtn').textContent='提交中…';
  try{
    await flushAutosave();const check=await checkPreflight();if(!check.ok)throw new Error(check.errors.join('；'));
    if(fingerprint!==publicationRequestFingerprint){publicationRequestFingerprint=fingerprint;publicationRequestKey=crypto.randomUUID()}
    if(!await confirmMatrixPublication(check))return;
    if(fingerprint!==workflowFingerprint())throw new Error('内容已修改，请重新确认');
    const form=publicationForm();form.append('submission_key',publicationRequestKey);
    if(check.matrix_confirmation){form.append('matrix_confirmed','1');form.append('matrix_confirmation',check.matrix_confirmation)}
    await postMultipart('/api/publish',form);lastSubmittedFingerprint=fingerprint;
    try{await finishDraftEditing(true)}catch(error){autosaveStatus('任务已创建，草稿状态保存失败',true)}
    publisherToast(checkedValue('mode')==='0'?'已开始准备作品，请在账号页面核对并发布':'发布任务已创建，请查看右侧任务进度');loadTasks();loadAccounts();
  }catch(error){publisherError(error.message)}finally{setPublisherBusy(false);$('pubBtn').textContent='发布'}
}
async function updateDraftCount(){try{const drafts=await api('/api/drafts');$('draftCount').textContent=drafts.length?`(${drafts.length})`:'';$('editDraftCount').textContent=$('draftCount').textContent;return drafts}catch(error){publisherToast(error.message);return []}}
async function saveDraft(){
  if(publisherBusy)return;normalizeKeywordInput();const error=validateMedia(mediaFiles);if(error){publisherError(error);return}setPublisherBusy(true);$('saveDraftBtn').textContent='保存中…';
  try{await flushAutosave();await finishDraftEditing(false);clearMedia();updateStage(false);publisherToast('已暂存到本机草稿箱，可以稍后继续编辑')}catch(error){publisherError(error.message)}finally{setPublisherBusy(false);$('saveDraftBtn').textContent='暂存离开'}
}
function showDraftRetention(){const days=ls.get('draftRetentionDays',7);$('draftRetentionDays').value=String(days);$('draftRetentionHint').textContent=days?`${days} 天未修改且未在编辑的草稿会自动删除。长期筹备的作品建议选择“不自动清理”。`:'草稿会一直保留，直到你主动删除。'}
async function changeDraftRetention(){
  const previous=ls.get('draftRetentionDays',7),days=Number($('draftRetentionDays').value);
  if(days!==0&&(previous===0||days<previous)&&!confirm(`改为 ${days} 天后，超过期限且未在编辑的草稿将被清理，继续吗？`)){showDraftRetention();return}
  $('draftRetentionDays').disabled=true;
  try{ls.set('draftRetentionDays',days);await ls.flush();await openDrafts();publisherToast('草稿清理设置已保存')}
  catch(error){publisherToast(error.message)}finally{$('draftRetentionDays').disabled=false}
}
async function openDrafts(){const dialog=$('draftDialog');if(!dialog.open)dialog.showModal();showDraftRetention();$('draftList').innerHTML='<div class="draft-empty">正在读取草稿…</div>';const drafts=await updateDraftCount();$('draftList').innerHTML=drafts.length?drafts.map(draft=>`<div class="draft-row"><div><b>${esc(draft.title||draft.desc||'未命名作品')}</b><small>${draft.autosaved?'自动保存 · ':''}${draft.type==='image'?'图文':'视频'} · ${esc(draft.updated_at)} · ${draft.file_count} 个文件</small></div><button class="text-button" type="button" onclick="restoreDraft('${draft.id}')">继续编辑</button><button class="text-button" type="button" onclick="deleteDraft('${draft.id}')">删除</button></div>`).join(''):'<div class="draft-empty">暂无草稿，选择作品后会自动保存到本机。</div>'}
async function restoreDraft(id){
  if(publisherBusy)return;setPublisherBusy(true);try{
    await flushAutosave();
    const draft=await api('/api/drafts/'+id);const loadFile=async file=>{const response=await fetch(file.url);if(!response.ok)throw new Error('草稿文件已丢失，请重新选择');return new File([await response.blob()],file.name,{type:file.mime})};
    const files=await Promise.all(draft.files.map(loadFile));const covers={};for(const [kind,file] of Object.entries(draft.covers||{}))covers[kind]=await loadFile(file);
    pickRadio('type',draft.type);changeMediaType();mediaFiles=files;assignFiles($('files'),files);renderMedia();updateStage(true);
    for(const key of ['title','desc','category'])$(key).value=draft[key]||'';$('tags').value=formatTags(draft.tags||[]);resizeKeywordInput();updateTextPreview();
    pickRadio('mode',draft.auto_publish?'1':'0');ls.set('mediaType',draft.type);ls.set('publishMode',checkedValue('mode'));ls.set('category',$('category').value);
    $('scheduleAt').value=draft.schedule_at||'';pickRadio('publish_time',draft.schedule_at?'scheduled':'now');syncPublishTime();
    for(const [kind,file] of Object.entries(covers)){assignFiles($('cover_'+kind),[file]);updateCoverPreview(kind)}
    for(const platform of Object.keys(PN))tg[platform]={...(draft.targets?.[platform]||{}),on:!!draft.targets?.[platform]?.on};renderTargets();activeDraftId=id;bindDraftSnapshot(draft);$('draftRecovery').classList.add('hidden');$('draftDialog').close();publisherToast('草稿已恢复，可继续编辑');
  }catch(error){publisherToast(error.message)}finally{setPublisherBusy(false)}
}
async function deleteDraft(id){if(publisherBusy||!confirm('删除这份草稿和它保存的文件？'))return;setPublisherBusy(true);try{await flushAutosave();await api('/api/drafts/'+id,{method:'DELETE'});if(activeDraftId===id){clearMedia();updateStage(false)}await openDrafts();await refreshDraftRecovery()}catch(error){publisherToast(error.message)}finally{setPublisherBusy(false)}}


$('chooseMedia').addEventListener('click',event=>{event.stopPropagation();chooseMedia()});
$('dropZone').addEventListener('click',chooseMedia);$('dropZone').addEventListener('keydown',event=>{if(event.target===$('dropZone')&&['Enter',' '].includes(event.key)){event.preventDefault();chooseMedia()}});
for(const type of ['dragenter','dragover'])$('dropZone').addEventListener(type,event=>{event.preventDefault();$('dropZone').classList.add('drag-over')});
for(const type of ['dragleave','drop'])$('dropZone').addEventListener(type,event=>{event.preventDefault();$('dropZone').classList.remove('drag-over')});
$('dropZone').addEventListener('drop',event=>acceptMedia(event.dataTransfer.files));
document.addEventListener('dragover',event=>{if(event.dataTransfer?.types.includes('Files'))event.preventDefault()});document.addEventListener('drop',event=>{if(event.dataTransfer?.types.includes('Files'))event.preventDefault()});
$('files').addEventListener('change',event=>{if(event.target.files.length)acceptMedia(event.target.files)});
document.querySelectorAll('input[name=type]').forEach(input=>input.addEventListener('change',()=>{ls.set('mediaType',selectedType());changeMediaType()}));
document.querySelectorAll('input[name=mode]').forEach(input=>input.addEventListener('change',()=>ls.set('publishMode',checkedValue('mode'))));
document.querySelectorAll('input[name=publish_time]').forEach(input=>input.addEventListener('change',syncPublishTime));
syncPublishTime();
for(const id of ['title','desc'])$(id).addEventListener('input',updateTextPreview);$('showCaption').addEventListener('change',()=>{ls.set('showCaption',$('showCaption').checked);updateTextPreview()});
$('mediaPreview').addEventListener('loadedmetadata',()=>{const video=$('mediaPreview');if(video.duration>3600){publisherToast('视频时长不能超过 1 小时');clearMedia();updateStage(false);return}$('mediaDetails').textContent=`${formatSize(mediaFiles[0]?.size||0)} · ${video.videoWidth} × ${video.videoHeight} · ${Math.floor(video.duration/60)}:${String(Math.floor(video.duration%60)).padStart(2,'0')}`});
$('mediaPreview').addEventListener('error',()=>{if(mediaFiles.length&&selectedType()==='video'&&previewState==='original'&&$('mediaPreview').getAttribute('src')===mediaURLs[0])ensureVideoPreview()});
$('mediaPreview').addEventListener('playing',()=>{$('previewPlay').textContent='暂停预览';setPreviewStatus('')});
for(const event of ['pause','ended'])$('mediaPreview').addEventListener(event,()=>{if(previewState!=='processing')$('previewPlay').textContent=selectedType()==='image'?'查看首图':'预览视频'});
window.addEventListener('pagehide',releaseVideoPreview);
for(const kind of ['h','v'])$('cover_'+kind).addEventListener('change',()=>updateCoverPreview(kind));
$('cropZoom').addEventListener('input',drawCrop);$('cropImageInput').addEventListener('change',async event=>{const file=event.target.files[0];if(!file)return;const src=URL.createObjectURL(file);try{await loadCropImage(src)}catch(error){publisherToast(error.message)}finally{URL.revokeObjectURL(src);event.target.value=''}});
$('frameSeek').addEventListener('input',()=>{const video=$('mediaPreview');if(video.readyState<2)return;video.pause();const time=Number($('frameSeek').value);if(Math.abs(video.currentTime-time)<.01)captureCoverFrame();else{video.addEventListener('seeked',()=>captureCoverFrame().catch(error=>publisherToast(error.message)),{once:true});video.currentTime=time}});
let cropDrag=null;$('cropCanvas').addEventListener('pointerdown',event=>{if(!cropImage)return;cropDrag={x:event.clientX,y:event.clientY,offset:{...cropOffset}};event.target.setPointerCapture(event.pointerId)});
$('cropCanvas').addEventListener('pointermove',event=>{if(!cropDrag)return;const rect=event.target.getBoundingClientRect();cropOffset={x:cropDrag.offset.x+(event.clientX-cropDrag.x)*event.target.width/rect.width,y:cropDrag.offset.y+(event.clientY-cropDrag.y)*event.target.height/rect.height};drawCrop()});
for(const event of ['pointerup','pointercancel'])$('cropCanvas').addEventListener(event,()=>cropDrag=null);
$('coverDialog').addEventListener('cancel',()=>{cropGeneration++;cropPending={};cropImage=null});
pickRadio('type',ls.get('mediaType','video'));pickRadio('mode',ls.get('publishMode','1'));$('showCaption').checked=ls.get('showCaption',true);
changeMediaType();updateDraftCount();updateTextPreview();

for(const kind of ['h','v']){
  const slot=$('coverSlot'+kind.toUpperCase());
  for(const event of ['dragenter','dragover'])slot.addEventListener(event,e=>{e.preventDefault();e.stopPropagation();slot.classList.add('drag-over')});
  slot.addEventListener('dragleave',()=>slot.classList.remove('drag-over'));
  slot.addEventListener('drop',async e=>{
    e.preventDefault();e.stopPropagation();slot.classList.remove('drag-over');if(publisherBusy||coverWorking)return;
    const files=Array.from(e.dataTransfer.files);if(files.length!==1||!/^image\/(jpeg|png|webp)$/.test(files[0].type)&&!(/\.(jpe?g|png|webp)$/i.test(files[0].name))){publisherToast('请拖入一张 JPG、PNG 或 WebP 封面图片');return}
    const src=URL.createObjectURL(files[0]);coverWorking=true;
    try{
      const image=new Image();image.src=src;await image.decode();
      const canvas=document.createElement('canvas');canvas.width=kind==='h'?960:720;canvas.height=kind==='h'?720:960;
      const scale=Math.max(canvas.width/image.width,canvas.height/image.height),width=image.width*scale,height=image.height*scale;
      canvas.getContext('2d').drawImage(image,(canvas.width-width)/2,(canvas.height-height)/2,width,height);
      const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));if(!blob)throw new Error('无法生成封面');
      assignFiles($('cover_'+kind),[new File([blob],`cover_${kind}.png`,{type:'image/png'})]);updateCoverPreview(kind);publisherToast((kind==='h'?'横':'竖')+'封面已设置，点击可调整裁剪');
    }catch(error){publisherToast('封面设置失败：'+error.message)}finally{coverWorking=false;URL.revokeObjectURL(src)}
  });
}
