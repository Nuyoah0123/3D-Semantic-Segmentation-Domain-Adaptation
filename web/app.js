"use strict";
const D=window.ATLAS;
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const validURL=s=>{try{const u=new URL(s);return ["https:","http:"].includes(u.protocol)&&!u.username?u.href:"";}catch{return "";}};
const date=s=>s?String(s).slice(0,10):"日期未提供";
function time(s){if(!s)return "尚无记录";try{return new Intl.DateTimeFormat("zh-CN",{dateStyle:"medium",timeStyle:"short",timeZone:D.site.timezone}).format(new Date(s));}catch{return s;}}
const states={candidate:"自动发现 · 待审核","review-stale":"版本已变化 · 需重新核对","imported-unreviewed":"既有题录 · 待审核",selected:"人工精选","metadata-checked":"题录已核对","abstract-checked":"摘要已核对","full-text-read":"人工全文阅读"};
const tasks={semantic:"语义分割",adjacent:"邻近任务","segmentation-unspecified":"分割任务待核对",other:"其他 / 支撑"};
let favorites=new Set();try{favorites=new Set(JSON.parse(localStorage.getItem("3d-seg-favorites")||"[]"));}catch{}
let shown=24,filtered=[];
const cutoff=Date.now()-7*86400000;
const recent=new Set(D.events.filter(e=>e.kind==="discovered"&&Date.parse(e.at)>=cutoff).map(e=>e.paper_id));
const revised=new Set(D.events.filter(e=>e.kind==="revised"&&Date.parse(e.at)>=cutoff).map(e=>e.paper_id));
const selected=p=>["selected","metadata-checked","abstract-checked","full-text-read"].includes(p.review_status);
const statusNames={"not-run":"等待首次自动发现",ok:"最近一轮抓取成功",partial:"部分来源需要检查",failed:"抓取失败 · 保留已有库"};
$("health-title").textContent=statusNames[D.status.status]||D.status.status;
if(D.status.status==="ok" && D.last_full_success && Date.now()-Date.parse(D.last_full_success)>48*3600000){$("health-title").textContent="抓取记录已过期 · 请检查 Actions";}
$("health-time").textContent="最近尝试："+time(D.status.at);
$("last-success").textContent="上次完整成功："+time(D.last_full_success);
$("new-count").textContent=D.status.counts?.new??"—";
$("rev-count").textContent=D.status.counts?.revised??"—";
$("library-count").textContent=D.papers.length;
$("seed-count").textContent=D.index_count;
$("core-count").textContent=D.papers.filter(p=>p.classification.bucket==="core").length;
$("reviewed-count").textContent=D.papers.filter(selected).length;
$("built").textContent="页面构建于 "+time(D.built_at);
if(D.site.preview){$("preview-note").hidden=false;$("preview-note").textContent="本地预览：额外展示既有题录；不代表已完成联网抓取，也不代表已经发布。";}
function link(p,text){const u=validURL(p.url);return u?`<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${text}</a>`:"";}
function render(){
 const query=$("search").value.trim().toLocaleLowerCase();const track=$("track").value,bucket=$("bucket").value,scope=$("scope").value;
 filtered=D.papers.filter(p=>{
  const c=p.classification;
  if(track&&!(c.tracks||[]).includes(track))return false;
  if(bucket&&c.bucket!==bucket)return false;
  if(scope==="new"&&!recent.has(p.id))return false;
  if(scope==="revised"&&!revised.has(p.id))return false;
  if(scope==="selected"&&!selected(p))return false;
  if(scope==="favorites"&&!favorites.has(p.id))return false;
  return !query||[p.title,p.id,p.abstract,...(p.authors||[]),...(c.methods||[]),...(p.identifiers?.arxiv||[]),...(p.identifiers?.doi||[])].join(" ").toLocaleLowerCase().includes(query);
 });
 const sort=$("sort").value;
 filtered.sort((a,b)=>sort==="title"?a.title.localeCompare(b.title):String(b[sort]||b.published||"").localeCompare(String(a[sort]||a.published||""))||a.id.localeCompare(b.id));
 $("results").textContent=`找到 ${filtered.length} 篇 · 显示 ${Math.min(shown,filtered.length)} 篇`;
 $("empty").hidden=filtered.length!==0;$("more").hidden=shown>=filtered.length;
 $("papers").innerHTML=filtered.slice(0,shown).map(p=>`<article class="card"><div class="cardtop">${(p.classification.tracks||[]).map(t=>`<span class="chip track">${esc(t)}</span>`).join("")}<span class="chip">${esc(tasks[p.classification.task]||"待核对")}</span><span class="muted">${esc(date(p.published))}</span></div><h3>${esc(p.title)}</h3><p class="authors">${esc((p.authors||[]).slice(0,3).join(" · "))}${(p.authors||[]).length>3?" et al.":""}</p><div>${(p.classification.methods||[]).slice(0,3).map(t=>`<span class="chip">${esc(t)}</span> `).join("")}</div><p class="review">${esc(states[p.review_status]||p.review_status)}${recent.has(p.id)?" · 近七天新收录":""}${revised.has(p.id)?" · 新版本":""}</p><div class="cardfoot"><button data-open="${esc(p.id)}">查看题录与依据 ↗</button>${link(p,"原文 ↗")}<button class="star" data-star="${esc(p.id)}" aria-label="收藏论文">${favorites.has(p.id)?"★":"☆"}</button></div></article>`).join("");
}
function showPaper(id){
 const p=D.papers.find(x=>x.id===id);if(!p)return;
 const c=p.classification;
 $("detail-body").innerHTML=`<p class="eyebrow">${esc(p.id)} / PAPER RECORD</p><h2>${esc(p.title)}</h2><p>${esc((p.authors||[]).join(" · "))}</p><div class="detailmeta"><b>研究方向：</b>${esc((c.tracks||[]).join(" / ")||"待确认")}　<b>任务：</b>${esc(tasks[c.task]||"待确认")}<br><b>题录日期：</b>${esc(p.published||"未提供")}　<b>版本：</b>${esc(p.version||"待核对")}<br><b>本站首次发现：</b>${esc(p.first_seen?time(p.first_seen):"既有导入题录")}<br><b>阅读状态：</b>${esc(states[p.review_status]||p.review_status)}<br>${link(p,"打开论文来源 ↗")}</div><h3>分类依据与边界</h3><p>${esc(c.note||"题录级分类；尚未核对全文。")}</p><p class="muted">来源范围：${esc(c.basis)}。数据集、类别映射、训练与推理模态、目标域监督及模型选择协议仍需原文核对。</p><h3>原始摘要</h3><p>${esc(p.abstract||"当前公开策略不复制摘要全文。请通过论文来源阅读；此限制不影响抓取时的自动初筛。")}</p>${p.ai_draft?`<h3>中文摘要级草稿</h3><p class="disclaimer">AI 生成，未人工核验；没有进行全文精读。</p><p>${esc(p.ai_draft.summary_zh)}</p>`:""}${p.public_note?`<h3>人工公开笔记</h3><p>${esc(p.public_note)}</p>`:""}<p class="disclaimer">DA、DG、TTA、OV 可以交叉。关键词命中不证明实验设置；不同数据集、类别空间与监督条件下的指标不能直接排名。</p><button class="ghost" id="single-ris">导出这篇题录 RIS ↓</button>`;
 $("single-ris").onclick=()=>downloadRIS([p],p.id.replace(/[^a-zA-Z0-9-]/g,"_")+".ris");
 if(!$("detail").open)$("detail").showModal();
}
function downloadRIS(papers,name){
 const clean=s=>String(s??"").replace(/[\r\n]+/g," ");
 const ris=papers.map(p=>["TY  - JOUR","TI  - "+clean(p.title),...(p.authors||[]).map(a=>"AU  - "+clean(a)),...(p.published?["PY  - "+clean(p.published.slice(0,4))]:[]),...(p.identifiers?.doi||[]).slice(0,1).map(d=>"DO  - "+clean(d)),...(validURL(p.url)?["UR  - "+validURL(p.url)]:[]),"ER  - ",""].join("\n")).join("\n");
 const u=URL.createObjectURL(new Blob([ris],{type:"application/x-research-info-systems;charset=utf-8"}));const a=document.createElement("a");a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),1000);
}
$("papers").addEventListener("click",e=>{const open=e.target.closest("[data-open]");if(open)showPaper(open.dataset.open);const star=e.target.closest("[data-star]");if(star){const id=star.dataset.star;favorites.has(id)?favorites.delete(id):favorites.add(id);try{localStorage.setItem("3d-seg-favorites",JSON.stringify([...favorites]));}catch{}render();}});
for(const id of ["search","track","bucket","scope","sort"])$(id).addEventListener(id==="search"?"input":"change",()=>{shown=24;render();});
$("reset").onclick=()=>{$("search").value="";$("track").value="";$("bucket").value="core";$("scope").value="all";$("sort").value="first_seen";shown=24;render();};
$("more").onclick=()=>{shown+=24;render();};$("export").onclick=()=>downloadRIS(filtered,"3d-seg-selection.ris");
$("close").onclick=()=>$("detail").close();$("detail").addEventListener("click",e=>{if(e.target===$("detail"))$("detail").close();});
$("sources").innerHTML=(D.status.sources||[]).map(s=>`<div class="source"><b>${esc(s.source)}</b><span class="${s.status==="ok"?"good":"bad"}">${s.status==="ok"?"成功":"失败"}${s.backlog_days?" · 仍有日期待回查":""}</span></div>${s.error?`<p class="muted">${esc(s.error)}</p>`:""}`).join("")||'<p class="muted">还没有抓取记录。运行 GitHub Actions 后，这里会显示每个查询的状态。</p>';
$("events").innerHTML=D.events.slice(-8).reverse().map(e=>`<div class="event"><span class="chip">${esc({discovered:"新收录",revised:"新版本","metadata-updated":"题录更新"}[e.kind]||e.kind)}</span> ${esc(e.title)}<small>${esc(time(e.at))}</small></div>`).join("")||'<p class="muted">没有已发布的发现或版本事件。既有文献导入不会被伪装成今天的新论文。</p>';
render();
const initial=new URLSearchParams(location.search).get("paper");if(initial)showPaper(initial);
