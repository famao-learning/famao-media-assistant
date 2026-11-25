/* 粘贴只读取文本，移除 Markdown 语法，不把 HTML 放进编辑器。 */
function plainMarkdown(value){
  return String(value??'').replace(/\r\n?/g,'\n').replace(/[\u200b\ufeff]/g,'')
    .replace(/^[ \t]*```[^\n]*\n?/gm,'').replace(/^[ \t]*~~~[^\n]*\n?/gm,'')
    .replace(/!\[([^\]]*)\]\([^\n)]*\)/g,'$1').replace(/\[([^\]]+)\]\([^\n)]*\)/g,'$1')
    .replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm,'').replace(/^[ \t]*>[ \t]?/gm,'')
    .replace(/^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)/gm,'').replace(/\[([ xX])\]\s+/g,'')
    .replace(/(`+)([^`]+)\1/g,'$2').replace(/\*\*([^*]+)\*\*/g,'$1').replace(/__([^_]+)__/g,'$1')
    .replace(/~~([^~]+)~~/g,'$1').replace(/(^|\s)\*([^*\n]+)\*(?=\s|$)/g,'$1$2')
    .replace(/(^|\s)_([^_\n]+)_(?=\s|$)/g,'$1$2').replace(/\\([#`*_\[\]()>])/g,'$1');
}
function parseTags(value){
  const text=plainMarkdown(Array.isArray(value)?value.join(' '):value).replace(/^\s*(?:话题|标签|关键词|话题\s*[/／]\s*关键词)\s*[:：]?\s*(?:\n|(?=#))/gm,'').replace(/[＃]/g,'#').replace(/[`*~]/g,'');
  return [...new Set(text.split(/[\s#,，;；、]+/u).map(tag=>tag.trim()).filter(Boolean))];
}
for(const id of ['title','desc'])$(id).addEventListener('blur',()=>{$(id).value=plainMarkdown($(id).value);updateTextPreview()});
function formatTags(value){const tags=parseTags(value);return tags.length?tags.map(tag=>'#'+tag).join(' ')+' ':''}
function normalizeKeywordInput(){const input=$('tags');input.value=input.value.split('\n').map(line=>formatTags(line)).join('\n');resizeKeywordInput();updateTextPreview()}
function insertPlainText(input,text){
  const start=input.selectionStart??input.value.length,end=input.selectionEnd??start;
  input.setRangeText(text,start,end,'end');input.dispatchEvent(new Event('input',{bubbles:true}));
}
for(const id of ['title','desc','tags']){
  const input=$(id);
  input.addEventListener('paste',event=>{
    const text=event.clipboardData?.getData('text/plain');if(text===undefined)return;
    event.preventDefault();insertPlainText(input,id==='tags'?formatTags(text):plainMarkdown(text));
    if(id==='tags')normalizeKeywordInput();
  });
}
$('tags').addEventListener('input',event=>{
  const input=$('tags');
  if(!event.isComposing&&input.selectionStart===input.value.length&&/[\s,，;；、]$/.test(input.value))normalizeKeywordInput();else updateTextPreview();resizeKeywordInput();
});
$('tags').addEventListener('compositionend',()=>updateTextPreview());
$('tags').addEventListener('blur',normalizeKeywordInput);
function resizeKeywordInput(){const input=$('tags');input.style.height='auto';input.style.height=Math.max(40,input.scrollHeight+2)+'px'}
window.addEventListener('resize',resizeKeywordInput);resizeKeywordInput();
