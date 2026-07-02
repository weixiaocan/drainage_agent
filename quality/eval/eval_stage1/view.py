"""把一次 eval 的 results.jsonl 渲染成一个可扫读、可判分、可导出的 open-coding 网页。

用法:
    python quality/eval/eval_stage1/view.py  # 默认读 quality/eval/results.jsonl，写 quality/eval/report.html
    python quality/eval/eval_stage1/view.py path/to/results.jsonl  out.html

设计取向：Swiss/极简。工具调用序列是每张卡的主角——open coding 判断的就是
"agent 选的路径对不对"。run_python 单独标出（你已知的失败模式），但只作提示，
判断权在你。判分用 window.storage 自动保存（在支持的环境里）；任何环境都可
"导出 annotations.csv" 落盘，也可"导入"已有标注续标。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_results(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        # args 可能是 JSON 字符串，解析成对象方便前端展示
        for tc in r.get("tool_calls") or []:
            a = tc.get("args")
            if isinstance(a, str):
                try:
                    tc["args"] = json.loads(a)
                except Exception:
                    pass
        rows.append(r)
    return rows


HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Eval Open Coding</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
  :root{
    --bg:#FAFAF8; --ink:#1A1A18; --muted:#6E6E68; --line:#E4E3DD;
    --card:#FFFFFF; --accent:#B0461F; --accent-soft:#F4E7E0;
    --ok:#2F6E4F; --ok-soft:#E6F0EA; --bad:#A8311F; --bad-soft:#F6E5E2;
    --mono:"SF Mono",ui-monospace,"Cascadia Mono",Menlo,Consolas,monospace;
    --sans:"Helvetica Neue",Helvetica,Arial,"Noto Sans SC",sans-serif;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
       line-height:1.6;-webkit-font-smoothing:antialiased}
  .wrap{max-width:920px;margin:0 auto;padding:0 28px 120px}

  header{position:sticky;top:0;z-index:20;background:rgba(250,250,248,.92);
         backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
         padding:18px 0 14px;margin-bottom:8px}
  .hd-row{max-width:920px;margin:0 auto;padding:0 28px;display:flex;
          align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}
  h1{font-size:15px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;margin:0}
  .counts{font-size:13px;color:var(--muted);font-variant-numeric:tabular-nums}
  .counts b{color:var(--ink)}
  .save{font-size:12px;font-variant-numeric:tabular-nums;white-space:nowrap}
  .save.ok{color:var(--ok)} .save.warn{color:var(--accent);font-weight:600}
  .bar{height:4px;background:var(--line);border-radius:2px;margin:12px 28px 0;
       max-width:864px;overflow:hidden}
  .bar > i{display:block;height:100%;background:var(--accent);width:0;transition:width .25s}

  .toolbar{max-width:920px;margin:12px auto 0;padding:0 28px;display:flex;
           gap:8px;flex-wrap:wrap;align-items:center}
  .chip-btn{font-family:var(--mono);font-size:11.5px;border:1px solid var(--line);
            background:var(--card);color:var(--muted);padding:5px 11px;border-radius:999px;
            cursor:pointer;letter-spacing:.02em}
  .chip-btn.on{border-color:var(--ink);color:var(--ink);font-weight:600}
  .spacer{flex:1}
  .act{font-size:12px;border:1px solid var(--ink);background:var(--ink);color:#fff;
       padding:6px 13px;border-radius:6px;cursor:pointer;font-family:var(--sans)}
  .act.ghost{background:transparent;color:var(--ink)}

  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:22px 24px;margin:18px 0;scroll-margin-top:120px}
  .card.graded-ok{border-left:3px solid var(--ok)}
  .card.graded-bad{border-left:3px solid var(--bad)}
  .c-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:6px}
  .cid{font-family:var(--mono);font-size:13px;font-weight:700;letter-spacing:.05em}
  .flags{display:flex;gap:6px;align-items:center}
  .flag{font-family:var(--mono);font-size:10.5px;padding:2px 8px;border-radius:999px;
        background:var(--accent-soft);color:var(--accent);letter-spacing:.03em}
  .flag.err{background:var(--bad-soft);color:var(--bad)}

  .prompt{font-size:16px;color:var(--ink);margin:2px 0 16px;font-weight:500}

  .lbl{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);
       margin:0 0 8px}

  /* 工具序列：每张卡的主角 */
  .pipe{display:flex;flex-wrap:wrap;align-items:center;gap:0;margin-bottom:18px}
  .step{display:flex;align-items:center}
  .tool{font-family:var(--mono);font-size:12px;padding:5px 10px;border:1px solid var(--line);
        border-radius:6px;background:#fff;cursor:pointer;white-space:nowrap}
  .tool:hover{border-color:var(--ink)}
  .tool.py{background:var(--accent-soft);border-color:var(--accent);color:var(--accent);font-weight:600}
  .arrow{color:var(--line);margin:0 7px;font-size:13px}
  .args{font-family:var(--mono);font-size:11.5px;background:#F3F2EE;border:1px solid var(--line);
        border-radius:6px;padding:8px 10px;margin:-8px 0 16px;white-space:pre-wrap;
        word-break:break-all;display:none;color:#4A4A45}

  .out{font-size:14px;border-top:1px solid var(--line);padding-top:14px;position:relative;
       max-height:280px;overflow:hidden}
  .out.open{max-height:none}
  .out::after{content:"";position:absolute;left:0;right:0;bottom:0;height:48px;
       background:linear-gradient(transparent,var(--card));pointer-events:none}
  .out.open::after{display:none}
  .out :first-child{margin-top:0}
  .out h3{font-size:14px;margin:14px 0 6px}
  .out table{border-collapse:collapse;font-size:12.5px;margin:8px 0}
  .out th,.out td{border:1px solid var(--line);padding:4px 9px;text-align:right;font-variant-numeric:tabular-nums}
  .out th:first-child,.out td:first-child{text-align:left}
  .out blockquote{margin:8px 0;padding:4px 12px;border-left:3px solid var(--accent-soft);color:var(--muted)}
  .out pre{white-space:pre-wrap;font-family:var(--mono);font-size:12px}
  .more{display:inline-block;margin-top:10px;font-size:12px;color:var(--accent);
        cursor:pointer;border:none;background:none;padding:0;font-family:var(--sans)}

  .trace{font-family:var(--mono);font-size:11px;color:var(--muted);margin:14px 0 0;
         word-break:break-all}

  .grade{border-top:1px dashed var(--line);margin-top:18px;padding-top:16px;
         display:grid;gap:10px}
  .g-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .verdict{display:flex;gap:6px}
  .vbtn{font-size:12.5px;padding:6px 16px;border:1px solid var(--line);background:#fff;
        border-radius:6px;cursor:pointer;color:var(--muted)}
  .vbtn.pass.on{background:var(--ok-soft);border-color:var(--ok);color:var(--ok);font-weight:600}
  .vbtn.fail.on{background:var(--bad-soft);border-color:var(--bad);color:var(--bad);font-weight:600}
  .g-row input{flex:1;min-width:200px;font-size:13px;font-family:var(--sans);
       border:1px solid var(--line);border-radius:6px;padding:8px 11px;background:#fff;color:var(--ink)}
  .g-row input.cls{flex:0 0 200px;min-width:140px;font-family:var(--mono);font-size:12px}
  .g-row .tag{font-size:10.5px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);min-width:64px}
  .empty{color:var(--muted);font-size:13px;padding:40px 0;text-align:center}
</style>
</head>
<body>
<header>
  <div class="hd-row">
    <h1>Eval · Open Coding</h1>
    <div class="counts" id="counts"></div>
  </div>
  <div class="bar"><i id="barfill"></i></div>
  <div class="toolbar">
    <button class="chip-btn on" data-f="all">全部</button>
    <button class="chip-btn" data-f="ungraded">未判</button>
    <button class="chip-btn" data-f="fail">判失败</button>
    <button class="chip-btn" data-f="py">用了 run_python</button>
    <span class="spacer"></span>
    <span class="save warn" id="save">○ 仅在内存</span>
    <button class="act ghost" id="imp">导入 CSV</button>
    <button class="act" id="exp">导出 annotations.csv</button>
    <input type="file" id="impfile" accept=".csv" style="display:none">
  </div>
</header>

<div class="wrap"><div id="cards"></div></div>

<script>
const DATA = __DATA__;
const KEY = "eval-anno-v1";
let anno = {};   // id -> {pass:'pass'|'fail'|'', first:'', cls:''}
let filter = "all";
let saveMode = "memory";   // cloud | local | memory
const hasCloud = typeof window !== "undefined" && window.storage && window.storage.get;

function setStatus(){
  const el = document.getElementById("save"); if(!el) return;
  const map = {cloud:["●","已自动保存"], local:["●","已存到本地浏览器"], memory:["○","仅在内存，请及时导出"]};
  const [dot,txt] = map[saveMode] || map.memory;
  el.textContent = dot + " " + txt;
  el.className = "save " + (saveMode==="memory" ? "warn" : "ok");
}
async function loadAnno(){
  if(hasCloud){
    try{ const r = await window.storage.get(KEY); if(r&&r.value){ anno=JSON.parse(r.value); saveMode="cloud"; setStatus(); return; } }catch(e){}
  }
  try{ const v = localStorage.getItem(KEY); if(v){ anno=JSON.parse(v); saveMode="local"; } }catch(e){}
  setStatus();
}
async function saveAnno(){
  if(hasCloud){
    try{ await window.storage.set(KEY, JSON.stringify(anno), false); saveMode="cloud"; setStatus(); return; }catch(e){}
  }
  try{ localStorage.setItem(KEY, JSON.stringify(anno)); saveMode="local"; setStatus(); return; }catch(e){}
  saveMode="memory"; setStatus();
}
window.addEventListener("beforeunload", e=>{
  const dirty = Object.values(anno).some(g=>g.pass||g.first||g.cls);
  if(saveMode==="memory" && dirty){ e.preventDefault(); e.returnValue=""; }
});
function a(id){ return anno[id] || (anno[id]={pass:"",first:"",cls:""}); }

function usesPy(r){ return (r.tool_calls||[]).some(t=>t.tool==="run_python"); }
function pyCount(r){ return (r.tool_calls||[]).filter(t=>t.tool==="run_python").length; }

function md(text){
  if(window.marked){ try{ return window.marked.parse(text||""); }catch(e){} }
  const esc=(text||"").replace(/&/g,"&amp;").replace(/</g,"&lt;");
  return "<pre>"+esc+"</pre>";
}

function render(){
  const host = document.getElementById("cards");
  host.innerHTML = "";
  const list = DATA.filter(r=>{
    if(filter==="ungraded") return !a(r.id).pass;
    if(filter==="fail") return a(r.id).pass==="fail";
    if(filter==="py") return usesPy(r);
    return true;
  });
  if(!list.length){ host.innerHTML='<div class="empty">没有符合筛选的用例。</div>'; updateCounts(); return; }

  for(const r of list){
    const g = a(r.id);
    const card = document.createElement("div");
    card.className = "card" + (g.pass==="pass"?" graded-ok":g.pass==="fail"?" graded-bad":"");

    const flags = [];
    if(usesPy(r)) flags.push(`<span class="flag">run_python ×${pyCount(r)}</span>`);
    if(r.error) flags.push(`<span class="flag err">报错</span>`);

    const pipe = (r.tool_calls||[]).map((t,i)=>{
      const py = t.tool==="run_python" ? " py":"";
      const arr = i? '<span class="arrow">›</span>':"";
      const args = JSON.stringify(t.args ?? {}, null, 2);
      return `${arr}<span class="step"><span class="tool${py}" data-args='${encodeURIComponent(args)}'>${t.tool}</span></span>`;
    }).join("");

    card.innerHTML = `
      <div class="c-top"><span class="cid">${r.id}</span><span class="flags">${flags.join("")}</span></div>
      <div class="prompt">${(r.prompt||"").replace(/</g,"&lt;")}</div>
      <p class="lbl">工具序列（${(r.tool_calls||[]).length} 步 · 点击看参数）</p>
      <div class="pipe">${pipe || '<span class="tool">（无工具调用）</span>'}</div>
      <div class="args"></div>
      <p class="lbl">最终回答</p>
      <div class="out">${md(r.output)}</div>
      <button class="more">展开全文</button>
      <p class="trace">trace: ${r.trace||"—"}</p>
      <div class="grade">
        <div class="g-row">
          <span class="tag">判定</span>
          <div class="verdict">
            <button class="vbtn pass ${g.pass==='pass'?'on':''}">通过</button>
            <button class="vbtn fail ${g.pass==='fail'?'on':''}">失败</button>
          </div>
        </div>
        <div class="g-row">
          <span class="tag">第一处失败</span>
          <input class="first" placeholder="大白话写：哪一步先错了" value="${(g.first||'').replace(/"/g,'&quot;')}">
          <input class="cls" placeholder="failure_class（后填）" value="${(g.cls||'').replace(/"/g,'&quot;')}">
        </div>
      </div>`;

    // 工具参数展开
    const argsBox = card.querySelector(".args");
    card.querySelectorAll(".tool[data-args]").forEach(el=>{
      el.onclick = ()=>{
        const v = decodeURIComponent(el.dataset.args);
        if(argsBox.style.display==="block" && argsBox.textContent===v){ argsBox.style.display="none"; return; }
        argsBox.textContent = v; argsBox.style.display="block";
      };
    });
    // 输出展开
    const out = card.querySelector(".out"), more = card.querySelector(".more");
    const sync = ()=>{ const open=out.classList.contains("open"); more.textContent=open?"收起":"展开全文";
                       more.style.display = (out.scrollHeight>280||open)?"inline-block":"none"; };
    more.onclick=()=>{ out.classList.toggle("open"); sync(); };
    setTimeout(sync,0);
    // 判分
    card.querySelector(".vbtn.pass").onclick=()=>setV(r.id,"pass");
    card.querySelector(".vbtn.fail").onclick=()=>setV(r.id,"fail");
    card.querySelector(".first").oninput=e=>{a(r.id).first=e.target.value;saveAnno();updateCounts();};
    card.querySelector(".cls").oninput=e=>{a(r.id).cls=e.target.value;saveAnno();};

    host.appendChild(card);
  }
  updateCounts();
}

function setV(id,v){ const g=a(id); g.pass = g.pass===v?"":v; saveAnno(); render(); }

function updateCounts(){
  const total=DATA.length;
  let graded=0,pass=0,fail=0,py=0;
  DATA.forEach(r=>{ const g=a(r.id); if(g.pass)graded++; if(g.pass==="pass")pass++; if(g.pass==="fail")fail++; if(usesPy(r))py++; });
  document.getElementById("counts").innerHTML =
    `已判 <b>${graded}</b>/<b>${total}</b> · 通过 <b>${pass}</b> · 失败 <b>${fail}</b> · 用 run_python <b>${py}</b>`;
  document.getElementById("barfill").style.width = (total? graded/total*100:0)+"%";
}

// 导出 / 导入 CSV
function exportCsv(){
  const esc=s=>{ s=(s??"").toString(); return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s; };
  const lines=["id,pass,first_failure,failure_class,note"];
  DATA.forEach(r=>{ const g=a(r.id);
    lines.push([r.id, g.pass==="pass"?"yes":g.pass==="fail"?"no":"", esc(g.first), esc(g.cls), ""].join(","));});
  const blob=new Blob(["\ufeff"+lines.join("\n")],{type:"text/csv;charset=utf-8"});
  const url=URL.createObjectURL(blob), a2=document.createElement("a");
  a2.href=url; a2.download="annotations.csv"; a2.click(); URL.revokeObjectURL(url);
}
function importCsv(text){
  const rows=text.replace(/^\ufeff/,"").split(/\r?\n/).slice(1);
  rows.forEach(line=>{ if(!line.trim())return;
    const m=line.match(/(".*?"|[^,]*)(,|$)/g).map(x=>x.replace(/,$/,"").replace(/^"|"$/g,"").replace(/""/g,'"'));
    const [id,p,first,cls]=m;
    if(!id)return; const g=a(id);
    g.pass = p==="yes"?"pass":p==="no"?"fail":""; g.first=first||""; g.cls=cls||"";
  });
  saveAnno(); render();
}

document.querySelectorAll(".chip-btn").forEach(b=>b.onclick=()=>{
  document.querySelectorAll(".chip-btn").forEach(x=>x.classList.remove("on"));
  b.classList.add("on"); filter=b.dataset.f; render();
});
document.getElementById("exp").onclick=exportCsv;
document.getElementById("imp").onclick=()=>document.getElementById("impfile").click();
document.getElementById("impfile").onchange=e=>{ const f=e.target.files[0]; if(!f)return;
  const rd=new FileReader(); rd.onload=()=>importCsv(rd.result); rd.readAsText(f); };

(async()=>{ await loadAnno(); render(); })();
</script>
</body>
</html>
"""


def main():
    args = sys.argv[1:]
    src = Path(args[0]) if args else Path("quality/eval/results.jsonl")
    out = Path(args[1]) if len(args) > 1 else src.with_name("report.html")
    rows = load_results(src)
    html = HTML.replace("__DATA__", json.dumps(rows, ensure_ascii=False))
    out.write_text(html, encoding="utf-8")
    print(f"{len(rows)} 条 → {out}")


if __name__ == "__main__":
    main()
