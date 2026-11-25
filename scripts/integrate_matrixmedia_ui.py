"""为原发布页面增加矩媒连接、逐账号绑定和人工确认界面。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace(file, old, new, count=1):
    path = ROOT / file
    text = path.read_text(encoding='utf-8')
    if text.count(old) != count:
        raise RuntimeError(f'{file} 定位失败：{text.count(old)}，预期 {count}')
    path.write_text(text.replace(old, new), encoding='utf-8')


replace('templates/index.html', '<div class="header-tools">', '<div class="header-tools"><button class="b" type="button" onclick="showMatrix()">矩媒连接</button>')
replace('templates/index.html', '<div class="hint" id="accHint">', '''<div id="matrixAccountFields"><label class="f" for="accBackend">发布工具</label><select id="accBackend"><option value="assistant">助手原发布（保留原高级设置）</option><option value="matrixmedia">矩媒发布（每次先人工确认）</option></select><div id="matrixBindingFields" class="hidden"><label class="f" for="accMatrixAccount">对应的矩媒账号</label><select id="accMatrixAccount"></select><button class="b" type="button" onclick="refreshMatrixAccounts()">刷新矩媒账号</button><p id="matrixBindingStatus" class="hint"></p><p class="hint">请按实际账号绑定；账号名称相似不会自动绑定。未绑定的账号可以先保存，暂不能发布。</p></div></div>
  <div class="hint" id="accHint">''')
replace('templates/index.html', '<script src="{{ url_for(\'static\', filename=\'ui-settings.js\') }}"></script>', '''{% include "matrixmedia.html" %}
<script src="{{ url_for('static', filename='ui-settings.js') }}"></script>''')
replace('templates/index.html', '<script src="{{ url_for(\'static\', filename=\'topic-radar.js\') }}"></script>', '''<script src="{{ url_for('static', filename='topic-radar.js') }}"></script>
<script src="{{ url_for('static', filename='matrixmedia.js') }}"></script>''')
replace('templates/index.html', "function colSelect(p,a,t){", "function colSelect(p,a,t){\n  if(a&&(a.publish_backend||(p==='xiaohongshu'?'matrixmedia':'assistant'))==='matrixmedia')return `<div class=\"hint\">矩媒 · ${esc(a.matrix_name||'未绑定，请编辑账号')}</div>`;")
replace('templates/index.html', "${p==='bilibili'?`<input aria-label=", "${p==='bilibili'&&cur.publish_backend!=='matrixmedia'?`<input aria-label=")
replace('templates/index.html', "${PC[p].collections?`<button class=\"b\" title=\"刷新此账号的合集\"", "${PC[p].collections&&(cur?.publish_backend||'assistant')!=='matrixmedia'?`<button class=\"b\" title=\"刷新此账号的合集\"")
replace('templates/index.html', "const status=manual?'手动登录':account.busy?'使用中':account.status;", "const status=(account.publish_backend||(account.platform==='xiaohongshu'?'matrixmedia':'assistant'))==='matrixmedia'?(account.busy?'矩媒处理中':account.matrix_partition?'矩媒中核对':'矩媒未绑定'):manual?'手动登录':account.busy?'使用中':account.status;")
replace('templates/index.html', "async function act(id,a,button){if(button)", "async function act(id,a,button){if(accountBackend(accs.find(item=>item.id===id))==='matrixmedia'){if(a==='check'){publisherToast('请在矩媒内检查账号登录状态');return}await openMatrix();return}if(button)")
replace('templates/index.html', "const b={name:$('accName').value,tags:$('accTags').value,profile_url:$('accProfile').value};", "const b={name:$('accName').value,tags:$('accTags').value,profile_url:$('accProfile').value,...matrixAccountValues()};")
replace('templates/index.html', "if(platform==='bilibili')", "if(platform==='bilibili')", 0) if False else None
replace('static/publisher.js', "if(platform==='bilibili'){const account=accs.find(a=>a.id===target.acc);", "if(platform==='bilibili'&&accountBackend(accs.find(a=>a.id===target.acc))!=='matrixmedia'){const account=accs.find(a=>a.id===target.acc);")
replace('static/publisher.js', "const form=publicationForm();form.append('submission_key',publicationRequestKey);await postMultipart('/api/publish',form);", """if(!await confirmMatrixPublication(check))return;
    if(fingerprint!==workflowFingerprint())throw new Error('内容已修改，请重新确认');
    const form=publicationForm();form.append('submission_key',publicationRequestKey);
    if(check.matrix_confirmation){form.append('matrix_confirmed','1');form.append('matrix_confirmation',check.matrix_confirmation)}
    await postMultipart('/api/publish',form);""")
replace('static/publisher-workflow.js', 'const signature=JSON.stringify([workflowFingerprint(),accs.map(a=>[a.id,a.busy,a.status,a.collections_at])]);', 'const signature=JSON.stringify([workflowFingerprint(),accs.map(a=>[a.id,a.busy,a.status,a.collections_at,a.publish_backend,a.matrix_partition])]);')
replace('templates/publisher.html', '<span>发布方式</span>', '<span>发布方式</span>', 0) if False else None
replace('templates/publisher.html', '<span>核对后发布</span>', '<span>核对后发布 / 矩媒存草稿</span>')
replace('templates/publisher.html', '<section class="publish-card preflight-card">', '<p class="hint">矩媒账号每次都会先确认绑定和文案。矩媒接口暂不接收此处的自定义封面、合集与 B站分区；这些高级设置可继续用助手原发布。</p><section class="publish-card preflight-card">')
print('矩媒界面整合完成')
