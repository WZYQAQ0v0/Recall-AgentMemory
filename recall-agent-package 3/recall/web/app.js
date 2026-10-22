"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const paths = {
  grid:'<rect x="3" y="3" width="6.5" height="6.5" rx="1.4"/><rect x="14.5" y="3" width="6.5" height="6.5" rx="1.4"/><rect x="3" y="14.5" width="6.5" height="6.5" rx="1.4"/><rect x="14.5" y="14.5" width="6.5" height="6.5" rx="1.4"/>',
  star:'<path d="m12 3 2.8 5.7 6.3.9-4.6 4.5 1.1 6.3-5.6-3-5.6 3 1.1-6.3L3 9.6l6.3-.9L12 3Z"/>',
  layers:'<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4.5 4.5"/>',
  refresh:'<path d="M20 7V3l-3 3A8 8 0 0 0 4 9M4 17v4l3-3a8 8 0 0 0 13-3"/><path d="M20 3h-4M4 21h4"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  x:'<path d="m6 6 12 12M6 18 18 6"/>',
  folder:'<path d="M3 6a1.5 1.5 0 0 1 1.5-1.5h5l2 2H20a1.5 1.5 0 0 1 1.5 1.5v10a1.5 1.5 0 0 1-1.5 1.5H4.5A1.5 1.5 0 0 1 3 18V6Z"/>',
  calendar:'<rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4M16 3v4M4 10h16M8 14h2M14 14h2"/>',
  list:'<path d="M9 5h12M9 12h12M9 19h12"/><circle cx="4" cy="5" r=".7"/><circle cx="4" cy="12" r=".7"/><circle cx="4" cy="19" r=".7"/>',
  down:'<path d="m7 10 5 5 5-5"/>',
  up:'<path d="m7 14 5-5 5 5"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"/>',
  message:'<path d="M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 3V6a2 2 0 0 1 2-2Z"/><path d="M7 8h10M7 12h7"/>',
  copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  terminal:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  spark:'<path d="m12 3 2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1L12 3Z"/>',
  settings:'<path d="m9 4 1-2h4l1 2 3 2 2 1v4l-2 1-1 3-1 3h-4l-1-2-3-2-2-1V9l2-1 1-4Z"/><circle cx="12" cy="11" r="3"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'
};
const labels = {codex:"Codex",claude:"Claude",trae:"Trae",chatgpt:"ChatGPT",other:"其他 AI",cursor:"Cursor",gemini:"Gemini",copilot:"GitHub Copilot",opencode:"OpenCode",doubao:"豆包"};
function knownProvider(provider) { return Object.prototype.hasOwnProperty.call(labels,provider); }
function providerLabel(provider) { return knownProvider(provider) ? labels[provider] : provider; }
const symbols = {codex:"↗",claude:"✳",trae:"T",chatgpt:"◎",other:"✧"};
const urlState = new URL(location.href);
const state = {token:"",q:urlState.searchParams.get("q")||"",provider:urlState.searchParams.get("provider")||"",project:urlState.searchParams.get("project")||"",days:0,
  favorite:urlState.searchParams.get("favorite")==="1",sort:"recent",view:localStorage.getItem("recall-view")||"grid",sessions:[],stats:null,index:null,
  request:0,hasMore:false,total:0,detail:null,selected:urlState.searchParams.get("session")||"",importFile:null,lastScan:0,connected:false,loading:false};
let debounceTimer, toastTimer, pollingTimer;

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2),value);
    else if (key === "disabled") node.disabled = value;
    else node.setAttribute(key,value);
  }
  for (const child of children) if (child != null) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}
function icon(name) {
  const span = el("span", {class:"icon-slot"});
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.55" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]||paths.info}</svg>`;
  return span;
}
function paintIcons(root = document) { $$('[data-icon]',root).forEach(node => node.replaceChildren(icon(node.dataset.icon))); }
function providerSymbol(provider) { return el("span",{class:`provider-symbol ${provider}`,text:Object.prototype.hasOwnProperty.call(symbols,provider)?symbols[provider]:"✧","aria-hidden":"true"}); }
function button(text, name, callback, className = "button button-subtle") { return el("button",{class:className,onclick:callback},[name?icon(name):null,text]); }
function date(when, full = false) {
  if (!when) return "时间未知";
  return new Date(when*1000).toLocaleString("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit",...(full?{hour:"2-digit",minute:"2-digit"}:{})}).replaceAll("/",".");
}
function timeAgo(when) {
  if (!when) return "";
  const seconds = Math.max(0,Date.now()/1000-when);
  if (seconds<60) return "刚刚更新";
  if (seconds<3600) return `${Math.floor(seconds/60)} 分钟前更新`;
  return date(when);
}
function toast(text) { clearTimeout(toastTimer); $("#toast").textContent=text; $("#toast").classList.remove("hidden"); toastTimer=setTimeout(()=>$("#toast").classList.add("hidden"),3500); }
function highlight(text, terms = []) {
  const fragment = document.createDocumentFragment();
  const values = [...new Set(terms)].filter(Boolean).sort((a,b)=>b.length-a.length);
  if (!values.length) {fragment.append(document.createTextNode(text));return fragment;}
  const escaped = values.map(term=>term.replace(/[.*+?^${}()|[\]\\]/g,"\\$&"));
  const regex = new RegExp(escaped.join("|"),"gi");
  let last=0;
  for (const match of text.matchAll(regex)) {fragment.append(document.createTextNode(text.slice(last,match.index)));fragment.append(el("mark",{text:match[0]}));last=match.index+match[0].length;}
  fragment.append(document.createTextNode(text.slice(last)));
  return fragment;
}
async function api(path, body) {
  const response = await fetch(path,body===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-Atlas-Token":state.token},body:JSON.stringify(body)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error||"请求失败，请重试。");
  return result;
}
function syncURL() {
  const target=new URL(location.href);
  for (const [key,value] of Object.entries({q:state.q,provider:state.provider,project:state.project,favorite:state.favorite?"1":"",session:state.selected})) value?target.searchParams.set(key,value):target.searchParams.delete(key);
  history.replaceState({},"",target.pathname+target.search+target.hash);
}
function currentProviders() { return [...new Set(["codex","claude","trae",...(state.stats?.providers||[]).map(item=>item.provider)])]; }
function providerCount(provider) { return state.stats?.providers.find(item=>item.provider===provider)?.count||0; }

function renderNav() {
  $("#nav-total").textContent = state.stats?.total??"—";
  $("#nav-favorites").textContent = state.stats?.favorites??"—";
  $$('[data-nav]').forEach(node=>node.classList.toggle("active",node.dataset.nav===(state.favorite?"favorites":"all")&&!state.provider));
  $("#provider-nav").replaceChildren(...currentProviders().map(provider=>el("button",{class:`nav-item ${state.provider===provider?"active":""}`,"aria-label":`${providerLabel(provider)} · ${providerCount(provider)} 段会话`,onclick:()=>chooseProvider(provider)},[
    providerSymbol(provider),el("span",{text:providerLabel(provider)}),el("span",{class:"nav-count",text:providerCount(provider)})])));
  $("#provider-tabs").replaceChildren(...["",...currentProviders()].map(provider=>el("button",{class:`provider-tab ${state.provider===provider?"active":""}`,"aria-pressed":String(state.provider===provider),onclick:()=>chooseProvider(provider)},[
    provider?providerLabel(provider):"全部 AI",el("span",{class:"tab-count",text:provider?providerCount(provider):(state.stats?.total??"—")})])));
}
function renderStats() {
  if (!state.stats) return;
  for (const [id,value] of Object.entries({"stat-total":state.stats.total,"stat-providers":state.stats.providers.length,"stat-recent":state.stats.recent,"stat-favorites":state.stats.favorites})) $("#"+id).textContent=value.toLocaleString();
  const projects=$("#project-filter");
  projects.replaceChildren(el("option",{value:"",text:"全部项目"}),...state.stats.projects.map(project=>el("option",{value:project.project,text:`${project.project} · ${project.count}`})));
  projects.value=state.project;
  $("#suggested-tags").replaceChildren(...state.stats.tags.slice(0,6).map(tag=>el("button",{class:"suggestion",text:tag,onclick:()=>setQuery(tag)})));
  renderNav();
}
function chooseProvider(provider) {state.provider=provider;state.favorite=false;renderNav();search();}
function setQuery(value) {state.q=value;$("#search-input").value=value;$("#clear-search").classList.toggle("hidden",!value);state.sort=value?"relevance":"recent";$("#sort-filter").value=state.sort;search();}

function originalConversationButton(session, compact=false) {
  const navigation=session.navigation||{available:false,reason:"可以在详情中查看原文。"};
  const launch=el("button",{
    class:compact?"card-open-original":"button "+(navigation.available?"button-dark":"button-subtle"),
    title:navigation.reason||navigation.label+" · "+navigation.destination,
    "aria-label":compact?(navigation.available?navigation.label:"查看原文")+"："+session.title:(navigation.available?navigation.label:"查看原文"),
    onclick:async event=>{
      event.stopPropagation();
      if(!navigation.available){if(compact)await openDetail(session.key);else $("#conversation-section")?.scrollIntoView({behavior:"smooth"});return;}
      launch.disabled=true;
      try {
        const result=await api("/api/open-session",{id:session.key});
        toast(result.mirrored?"正在打开保存在 Codex 中的会话镜像":"正在打开 "+result.destination+" 中的原对话");
      }catch(error){toast(error.message);}finally{launch.disabled=false;}
    }
  },[icon(navigation.available?"arrow":"message"),navigation.available?navigation.label:"查看原文"]);
  return launch;
}

function createCard(session, terms) {
  const card=el("article",{class:"session-card",tabindex:"0","aria-label":`${providerLabel(session.provider)} 会话：${session.title}`,onclick:event=>{if(!event.target.closest("button")) openDetail(session.key);},onkeydown:event=>{if(event.target===card&&(event.key==="Enter"||event.key===" ")){event.preventDefault();openDetail(session.key);}}});
  const favorite=el("button",{class:`icon-button star-button ${session.favorite?"is-favorite":""}`,"aria-label":session.favorite?"取消收藏":"收藏会话","aria-pressed":String(session.favorite),onclick:async event=>{
    event.stopPropagation();favorite.disabled=true;
    try{await api("/api/annotate",{id:session.key,favorite:!session.favorite});session.favorite=!session.favorite;favorite.classList.toggle("is-favorite",session.favorite);favorite.setAttribute("aria-pressed",String(session.favorite));favorite.setAttribute("aria-label",session.favorite?"取消收藏":"收藏会话");await updateStatus();if(state.favorite)search();}
    catch(error){toast(error.message);}finally{favorite.disabled=false;}
  }},[icon("star")]);
  card.append(el("div",{class:"card-head"},[providerSymbol(session.provider),el("span",{class:"provider-name",text:providerLabel(session.provider)}),el("time",{class:"card-date",text:date(session.updated_at),title:date(session.updated_at,true)}),favorite]));
  const title=el("h3",{title:session.title});title.append(highlight(session.title,terms));card.append(title);
  card.append(el("p",{class:"card-summary",text:session.conclusion||session.summary||"会话刚刚开始，收到问题和回复后会生成摘要。"}));
  if(session.match){const match=el("div",{class:"match-snippet"},[el("span",{class:"match-label",text:"原文命中"})]);match.append(highlight(session.match.text,terms));card.append(match);}
  card.append(el("div",{class:"card-tags"},session.keywords.slice(0,4).map(tag=>el("button",{class:"tag",text:tag,onclick:event=>{event.stopPropagation();setQuery(tag);}}))));
  card.append(el("div",{class:"card-original-action"},[originalConversationButton(session,true)]));
  card.append(el("div",{class:"card-footer"},[icon("folder"),el("span",{class:"card-project",text:session.project,title:session.cwd||session.project}),session.archived?el("span",{class:"archive-label",text:"已归档"}):null,el("span",{class:"message-count"},[icon("message"),`${session.message_count} 条消息`])]));
  return card;
}
async function search(append = false) {
  const ticket=++state.request;
  state.loading=true;
  $("#load-more").disabled=true;
  syncURL();renderNav();
  const params=new URLSearchParams({q:state.q,provider:state.provider,project:state.project,days:state.days,favorite:state.favorite?"1":"0",sort:state.sort,offset:append?state.sessions.length:0,limit:24});
  try {
    const result=await api("/api/search?"+params);
    if(ticket!==state.request)return;
    state.sessions=append?[...state.sessions,...result.sessions]:result.sessions;state.total=result.total;state.hasMore=result.has_more;
    const grid=$("#session-grid");
    if(!append)grid.replaceChildren();
    grid.append(...result.sessions.map(session=>createCard(session,result.match_terms)));
    grid.classList.toggle("list-mode",state.view==="list");
    $("#empty-state").classList.toggle("hidden",result.total>0);
    const filtered=state.q||state.provider||state.project||state.favorite||state.days;
    $("#empty-title").textContent=filtered?"还没有找到这段经验":"等待第一段会话";
    $("#empty-text").textContent=filtered?"换一个关键词，或减少筛选条件。也可以导入尚未收录的历史会话。":"正在读取本机历史；其他 AI 的导出记录也可以加入这里。";
    $("#reset-filters").classList.toggle("hidden",!filtered);
    $("#results-title").textContent=state.q?"搜索结果":state.favorite?"经验收藏":state.provider?`${providerLabel(state.provider)} 会话`:"所有会话";
    $("#results-count").textContent=`${result.total.toLocaleString()} 段${state.q?"匹配":"记录"}`;
    $("#search-time").textContent=state.q?`${result.search_ms} ms · 搜索正文、摘要与笔记`:"";
    $("#load-more").classList.toggle("hidden",!result.has_more);
  }catch(error){if(ticket===state.request){toast(error.message);$("#results-count").textContent="暂时无法读取";}}
  finally{if(ticket===state.request){state.loading=false;$("#load-more").disabled=false;}}
}
async function updateStatus() {
  const result=await api("/api/status");
  const oldTotal=state.stats?.total;
  const wasRunning=state.index?.running;
  state.stats=result.stats;state.index=result.index;renderStats();
  const active=result.index.running;
  $("#sync-button").disabled=active;
  $("#sync-label").textContent=active?"正在同步":timeAgo(result.index.last_scan);
  $("#index-banner").classList.toggle("hidden",!active);
  $("#index-banner-text").textContent=result.index.total_files?`正在读取历史会话 · ${result.index.done} / ${result.index.total_files} 份记录` : "正在发现本机历史会话，最近的内容会先出现…";
  if(!$("#sources-modal").classList.contains("hidden"))renderSources();
  const scanChanged=result.index.last_scan!==state.lastScan;
  state.lastScan=result.index.last_scan;
  if((oldTotal!==undefined&&oldTotal!==state.stats.total)||(wasRunning&&!active)||(scanChanged&&state.connected))await search();
}
async function poll() {
  clearTimeout(pollingTimer);
  try{await updateStatus();}catch(error){$("#sync-label").textContent="索引服务未连接";}
  pollingTimer=setTimeout(poll,state.index?.running?4000:12000);
}

function showOverlay(kind) {
  $("#modal-backdrop").classList.remove("hidden");$("#"+kind+"-modal").classList.remove("hidden");document.body.classList.add("overlay-open");$("#"+kind+"-modal").focus();
}
function closeModal() {$("#modal-backdrop").classList.add("hidden");$$(".modal").forEach(node=>node.classList.add("hidden"));if($("#detail-drawer").classList.contains("hidden"))document.body.classList.remove("overlay-open");}
function renderSources() {
  const sources=[...(state.index?.sources||[])];
  for(const provider of currentProviders())if(!sources.some(item=>item.provider===provider))sources.push({provider,label:providerLabel(provider),mode:"import",description:"从导出文件接入的历史会话。",path:"本机导入索引"});
  $("#source-cards").replaceChildren(...sources.map(source=>{
    const count=providerCount(source.provider);
    const status=source.mode==="auto"?(source.available?"自动同步":"未发现记录"):source.mode==="hook"?(source.available?"事件同步":"已配置，等待新会话"):(source.provider==="trae"?"导出接入":"文件导入");
    const className=source.mode==="auto"?(source.available?"":"missing-status"):"import-status";
    return el("div",{class:"source-card"},[
      el("div",{class:"source-card-header"},[providerSymbol(source.provider),el("strong",{text:source.label}),el("span",{class:`source-status ${className}`,text:status})]),
      el("p",{text:source.description}),el("div",{class:"source-card-path",text:source.path||""}),
      el("div",{class:"source-card-bottom"},[el("span",{text:`已收录 ${count} 段会话`}),button("导入历史","upload",()=>{closeModal();openImport(source.provider);})])
    ]);
  }));
}
function openSources(){renderSources();showOverlay("sources");}
function openImport(provider="trae") {state.importFile=null;$("#import-file").value="";$("#file-name").textContent="选择导出文件，或拖到这里";$("#confirm-import").disabled=true;$("#import-error").classList.add("hidden");$("#import-provider").value=knownProvider(provider)?provider:"other";$("#import-custom-provider").value=knownProvider(provider)?"":provider;toggleCustomProvider();showOverlay("import");}
function selectFile(file) {
  if(!file)return;
  if(file.size>24*1024*1024){toast("请选择小于 24 MB 的导出文件。");return;}
  state.importFile=file;$("#file-name").textContent=file.name;$("#confirm-import").disabled=false;$("#import-error").classList.add("hidden");
}
function toggleCustomProvider() { $("#custom-provider-field").classList.toggle("hidden",$("#import-provider").value!=="other"); }
async function importFile() {
  const file=state.importFile;if(!file)return;
  const confirm=$("#confirm-import");confirm.disabled=true;
  try{const result=await api("/api/import",{provider:$("#import-provider").value==="other"?($("#import-custom-provider").value.trim()||"other"):$("#import-provider").value,filename:file.name,content:await file.text()});closeModal();toast(`已接收 ${result.imported} 段会话、${result.message_count} 条消息`);await updateStatus();await search();}
  catch(error){$("#import-error").textContent=error.message;$("#import-error").classList.remove("hidden");}
  finally{confirm.disabled=false;}
}

async function copy(text,label="已复制") {
  try{await navigator.clipboard.writeText(text);toast(label);}catch(error){toast("浏览器未允许复制，可以在详情中选中原会话 ID。");}
}
function detailSection(title,children,annotation="") {
  return el("section",{class:"detail-section"},[el("h3",{},[title,annotation?el("span",{class:"small-label",text:annotation}):null]),...children]);
}
async function openDetail(key,focus="",offset=null) {
  state.selected=key;syncURL();$("#drawer-backdrop").classList.remove("hidden");$("#detail-drawer").classList.remove("hidden");document.body.classList.add("overlay-open");
  const params=new URLSearchParams({id:key,q:state.q,focus,limit:24});if(offset!==null)params.set("offset",offset);
  try{const detail=await api("/api/session?"+params);if(state.selected!==key)return;state.detail=detail;renderDetail(focus);$("#detail-drawer").focus();}
  catch(error){toast(error.message);closeDetail();}
}
function closeDetail(){state.selected="";state.detail=null;syncURL();$("#detail-drawer").classList.add("hidden");$("#drawer-backdrop").classList.add("hidden");if($("#modal-backdrop").classList.contains("hidden"))document.body.classList.remove("overlay-open");}
function renderDetail(focus="") {
  const detail=state.detail,session=detail.session,terms=detail.match_terms;
  $("#drawer-favorite").classList.toggle("is-favorite",session.favorite);
  $("#drawer-favorite").setAttribute("aria-label",session.favorite?"取消收藏":"收藏会话");
  const content=$("#drawer-content");content.replaceChildren();
  content.append(el("div",{class:"detail-head"},[providerSymbol(session.provider),providerLabel(session.provider),el("span",{text:session.archived?"· 已归档":""})]));
  content.append(el("h2",{class:"detail-title",text:session.title}));
  content.append(el("div",{class:"detail-meta"},[el("span",{title:session.cwd},[icon("folder"),session.project]),el("span",{},[icon("clock"),date(session.updated_at,true)]),el("span",{},[icon("message"),`${session.message_count} 条消息`])]));
  const actions=el("div",{class:"detail-actions"},[
    originalConversationButton(session),
    session.navigation?.available?button("查看原文","message",()=>$("#conversation-section")?.scrollIntoView({behavior:"smooth"})):null,
    session.resume_command?button(session.resume_kind==="mirror"?"复制镜像继续命令":"复制继续命令","terminal",()=>copy(session.resume_command,session.resume_kind==="mirror"?"已复制；将在 Codex 中打开保存的 Claude 历史":"已复制，粘贴到终端即可继续原会话")):null,
    button("显示源文件","folder",async()=>{try{await api("/api/reveal",{id:session.key});}catch(error){toast(error.message);}}),
    button("复制会话 ID","copy",()=>copy(session.native_id,"已复制原会话 ID"))
  ]);content.append(actions);
  if(session.navigation&&!session.navigation.available)content.append(el("p",{class:"navigation-note",text:session.navigation.reason}));
  else if(session.navigation?.mirrored)content.append(el("p",{class:"navigation-note",text:"此入口打开保存在 Codex 中的 Claude 历史镜像。原 Claude Code 记录已不可用。"}));
  if(session.source_kind==="codex-import")content.append(el("p",{class:"partial-note",text:"这是一段 Claude 原始会话，历史镜像保存在 Codex 中。镜像内的消息时间可能是导入时间。"}));
  if(session.source_kind==="ui-mirror")content.append(el("p",{class:"partial-note",text:"这段历史来自 Trae 界面镜像，可检索已读取的文本。完整上下文可按原会话 ID 和标题回到 Trae 查看。"}));
  else if(session.partial)content.append(el("p",{class:"partial-note",text:"这段记录包含尚未完整读取的消息，请以原始会话为准。"}));
  content.append(detailSection("问题与核心内容",[el("p",{text:session.summary||"收到对话内容后，会生成可回溯的摘要。"})],"本地摘录"));
  if(session.conclusion)content.append(detailSection("最近一次回复",[el("p",{text:session.conclusion})]));
  content.append(detailSection("关键词",[el("div",{class:"detail-tags"},session.keywords.map(tag=>el("button",{class:"tag",text:tag,onclick:()=>{closeDetail();setQuery(tag);}})))]));
  const notes=el("textarea",{class:"notes-textarea",placeholder:"记下这个问题的关键经验、踩坑点，或下次会搜索的关键词…","aria-label":"经验笔记"});notes.value=session.notes;
  const save=button("保存笔记",null,async()=>{save.disabled=true;try{await api("/api/annotate",{id:session.key,notes:notes.value});session.notes=notes.value;toast("笔记已保存，也可以被搜索到");await search();}catch(error){toast(error.message);}finally{save.disabled=false;}});
  content.append(detailSection("你的经验笔记",[notes,el("div",{class:"notes-row"},[el("span",{text:"补充的线索也会参与搜索"}),save])]));
  if(detail.matches.length){
    content.append(detailSection("搜索命中的原文",detail.matches.map(match=>{
      const excerpt=el("p");excerpt.append(highlight(match.text,terms));
      return el("button",{class:"hit-item",onclick:()=>openDetail(session.key,match.id)},[el("div",{class:"hit-meta"},[match.role==="user"?"你的问题":"AI 回复",date(match.timestamp,true)]),excerpt]);
    })));
  }
  const conversation=el("section",{class:"detail-section",id:"conversation-section"});
  conversation.append(el("div",{class:"message-header"},[el("h3",{text:"对话原文"}),button("从开头查看",null,()=>openDetail(session.key,"",0))]));
  for(const message of detail.messages){
    const item=el("article",{class:`message-item ${message.role==="user"?"user-message":"ai-message"} ${message.id===focus?"focused-message":""}`,id:"message-"+message.id});
    item.append(el("div",{class:"message-meta"},[icon(message.role==="user"?"message":"spark"),message.role==="user"?"你":providerLabel(session.provider),el("time",{text:date(message.timestamp,true)})]));
    const text=el("pre",{class:"message-text"});text.append(highlight(message.text,terms));item.append(text);
    if(message.text.length>650||message.text.split("\n").length>10){const expand=button("展开全文","down",()=>{const expanded=text.classList.toggle("expanded");expand.replaceChildren(icon(expanded?"up":"down"),document.createTextNode(expanded?"收起":"展开全文"));},"expand-message");item.append(expand);}
    conversation.append(item);
  }
  const earlier=button("更早的消息","up",()=>openDetail(session.key,"",Math.max(0,detail.offset-detail.limit)));earlier.disabled=!detail.has_earlier;
  const later=button("后续消息","down",()=>openDetail(session.key,"",detail.offset+detail.limit));later.disabled=!detail.has_later;
  conversation.append(el("div",{class:"conversation-pager"},[earlier,el("span",{text:`${Math.min(detail.offset+1,detail.total_messages)}–${Math.min(detail.offset+detail.messages.length,detail.total_messages)} / ${detail.total_messages}`}),later]));
  content.append(conversation);
  content.append(detailSection("原始会话标识",[el("div",{class:"native-id",text:session.native_id}),el("div",{class:"source-file-list",text:session.source_paths.join("\n")})]));
  if(session.models.length)content.append(detailSection("会话中的模型",[el("div",{class:"detail-tags"},session.models.map(model=>el("span",{class:"tag",text:model})))]));
  if(focus)requestAnimationFrame(()=>$("#message-"+focus)?.scrollIntoView({block:"center"}));else content.scrollTop=0;
}

function bind() {
  paintIcons();$("#search-input").value=state.q;$("#clear-search").classList.toggle("hidden",!state.q);
  if(state.q){state.sort="relevance";$("#sort-filter").value="relevance";}
  $("#search-input").addEventListener("input",event=>{state.q=event.target.value;$("#clear-search").classList.toggle("hidden",!state.q);state.sort=state.q?"relevance":"recent";$("#sort-filter").value=state.sort;clearTimeout(debounceTimer);debounceTimer=setTimeout(()=>search(),190);});
  $("#clear-search").addEventListener("click",()=>{setQuery("");$("#search-input").focus();});
  $("#project-filter").addEventListener("change",event=>{state.project=event.target.value;search();});
  $("#period-filter").addEventListener("change",event=>{state.days=Number(event.target.value);search();});
  $("#sort-filter").addEventListener("change",event=>{state.sort=event.target.value;search();});
  $("#load-more").addEventListener("click",()=>search(true));
  $("#reset-filters").addEventListener("click",()=>{state.provider="";state.project="";state.days=0;state.favorite=false;$("#project-filter").value="";$("#period-filter").value="0";setQuery("");});
  $$("[data-nav]").forEach(node=>node.addEventListener("click",()=>{if(node.dataset.nav==="sources")return openSources();state.favorite=node.dataset.nav==="favorites";state.provider="";renderNav();search();}));
  $("#source-shortcut").addEventListener("click",openSources);
  $("#sync-button").addEventListener("click",async()=>{try{if(!state.token)return initialize();await api("/api/sync",{});toast("正在同步本机会话");await updateStatus();}catch(error){toast(error.message);}});
  $("#import-button").addEventListener("click",()=>openImport());
  $$("[data-close-modal]").forEach(node=>node.addEventListener("click",closeModal));
  $("#modal-backdrop").addEventListener("click",closeModal);
  $("#import-provider").addEventListener("change",toggleCustomProvider);
  $("#import-file").addEventListener("change",event=>selectFile(event.target.files[0]));
  $("#file-drop").addEventListener("dragover",event=>{event.preventDefault();$("#file-drop").classList.add("dragover");});
  $("#file-drop").addEventListener("dragleave",()=>$("#file-drop").classList.remove("dragover"));
  $("#file-drop").addEventListener("drop",event=>{event.preventDefault();$("#file-drop").classList.remove("dragover");selectFile(event.dataTransfer.files[0]);});
  $("#confirm-import").addEventListener("click",importFile);
  $("#close-drawer").addEventListener("click",closeDetail);$("#drawer-backdrop").addEventListener("click",closeDetail);
  $("#drawer-favorite").addEventListener("click",async()=>{if(!state.detail)return;const session=state.detail.session;try{await api("/api/annotate",{id:session.key,favorite:!session.favorite});session.favorite=!session.favorite;$("#drawer-favorite").classList.toggle("is-favorite",session.favorite);$("#drawer-favorite").setAttribute("aria-label",session.favorite?"取消收藏":"收藏会话");$("#drawer-favorite").setAttribute("aria-pressed",String(session.favorite));await updateStatus();await search();}catch(error){toast(error.message);}});
  for(const view of ["grid","list"]){$("#"+view+"-view").classList.toggle("selected",state.view===view);$("#"+view+"-view").addEventListener("click",()=>{state.view=view;localStorage.setItem("recall-view",view);$("#session-grid").classList.toggle("list-mode",view==="list");$("#grid-view").classList.toggle("selected",view==="grid");$("#list-view").classList.toggle("selected",view==="list");});}
  document.addEventListener("keydown",event=>{if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){event.preventDefault();closeModal();closeDetail();$("#search-input").focus();}if(event.key==="Escape"){if(!$("#modal-backdrop").classList.contains("hidden"))closeModal();else closeDetail();}});
}
async function initialize() {
  $("#session-grid").replaceChildren(...Array.from({length:6},()=>el("div",{class:"session-card skeleton","aria-hidden":"true"})));
  try{const boot=await api("/api/bootstrap");state.token=boot.token;await updateStatus();await search();state.connected=true;if(state.selected)await openDetail(state.selected);poll();}
  catch(error){$("#session-grid").replaceChildren();$("#empty-state").classList.remove("hidden");$("#empty-title").textContent="本机索引尚未连接";$("#empty-text").textContent="运行 start.command，然后点击同步会话。";$("#results-count").textContent="未连接";toast(error.message);}
}
bind();initialize();
