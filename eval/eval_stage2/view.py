"""Render Stage 2 multi-turn eval results as a single-page review report."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def load_results(path: Path) -> tuple[dict, list[dict]]:
    meta: dict = {}
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "_meta" in record:
            meta = record["_meta"] or {}
            continue
        for turn in record.get("turns") or []:
            for call in turn.get("tool_calls") or []:
                if isinstance(call.get("args"), str):
                    try:
                        call["args"] = json.loads(call["args"])
                    except (TypeError, ValueError):
                        pass
        rows.append(record)
    return meta, rows


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stage 2 多轮对话评估</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
:root{--bg:#f7f7f4;--card:#fff;--ink:#20201d;--muted:#74746d;--line:#deded7;
--accent:#9b3e1d;--soft:#f5e8e1;--ok:#2f6e4f;--okbg:#e6f0ea;--bad:#a8311f;
--badbg:#f6e5e2;--mono:"Cascadia Mono",Consolas,monospace;--sans:Arial,"Noto Sans SC",sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 var(--sans)}
header{position:sticky;top:0;z-index:10;background:rgba(247,247,244,.95);border-bottom:1px solid var(--line);backdrop-filter:blur(8px)}
.head,.toolbar,.wrap{max-width:1080px;margin:auto;padding-left:26px;padding-right:26px}.head{padding-top:16px;padding-bottom:10px;display:flex;gap:18px;align-items:baseline;justify-content:space-between}
h1{font-size:15px;letter-spacing:.08em;margin:0}.counts,.meta{color:var(--muted);font-size:12px}.bar{height:4px;background:var(--line)}.bar i{display:block;height:100%;width:0;background:var(--accent)}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding-top:10px;padding-bottom:12px}.spacer{flex:1}
button{font:inherit;cursor:pointer}.chip,.action{border:1px solid var(--line);background:#fff;border-radius:7px;padding:5px 11px}.chip.on{border-color:var(--ink);font-weight:700}.action{background:var(--ink);color:#fff;border-color:var(--ink)}.action.ghost{background:#fff;color:var(--ink)}
.wrap{padding-top:8px;padding-bottom:100px}.case{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:18px 0;overflow:hidden}.case.pass{border-left:4px solid var(--ok)}.case.fail{border-left:4px solid var(--bad)}
.case-head{padding:18px 21px 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.cid{font:700 14px var(--mono)}.category{color:var(--muted);margin-left:10px}.badge{font:11px var(--mono);padding:2px 7px;border-radius:99px;background:var(--soft);color:var(--accent)}.badge.err{background:var(--badbg);color:var(--bad)}
.turn{padding:18px 21px;border-bottom:1px solid var(--line)}.turn:last-of-type{border-bottom:0}.turn.key{background:#fffdfb}.turn-top{display:flex;justify-content:space-between;gap:12px}.tn{font:700 12px var(--mono);color:var(--muted)}
.prompt{font-size:16px;font-weight:600;margin:7px 0 10px}.expect{border-left:3px solid #d8b5a5;background:#faf4f1;padding:8px 11px;margin:8px 0 14px;color:#51433d}.label{font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin:12px 0 6px}
.pipe{display:flex;gap:6px;flex-wrap:wrap}.tool{font:12px var(--mono);border:1px solid var(--line);border-radius:6px;padding:4px 8px;background:#fff}.tool.py{color:var(--accent);border-color:var(--accent)}.args{display:none;white-space:pre-wrap;word-break:break-all;background:#f2f2ee;border:1px solid var(--line);padding:8px;border-radius:6px;font:11px var(--mono);margin-top:6px}
.output{border-top:1px dashed var(--line);margin-top:13px;padding-top:12px;max-height:260px;overflow:hidden;position:relative}.output.open{max-height:none}.output pre{white-space:pre-wrap}.output table{border-collapse:collapse;font-size:12px}.output td,.output th{border:1px solid var(--line);padding:3px 7px}.more{border:0;background:transparent;color:var(--accent);padding:5px 0;font-size:12px}
.grade{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-top:12px}.grade input{flex:1;min-width:250px;border:1px solid var(--line);border-radius:6px;padding:7px}.v{border:1px solid var(--line);background:#fff;border-radius:6px;padding:5px 12px}.v.pass.on{background:var(--okbg);color:var(--ok);border-color:var(--ok)}.v.fail.on{background:var(--badbg);color:var(--bad);border-color:var(--bad)}
.case-grade{padding:14px 21px;background:#fafaf7;border-top:1px solid var(--line)}.trace{font:11px var(--mono);color:var(--muted);word-break:break-all}.empty{text-align:center;color:var(--muted);padding:50px}
</style>
</head>
<body>
<header><div class="head"><div><h1>Stage 2 多轮对话评估</h1><div class="meta" id="meta"></div></div><div class="counts" id="counts"></div></div><div class="bar"><i id="bar"></i></div>
<div class="toolbar"><button class="chip on" data-filter="all">全部</button><button class="chip" data-filter="ungraded">未判</button><button class="chip" data-filter="fail">失败</button><button class="chip" data-filter="key">含关键轮</button><span class="spacer"></span><button class="action ghost" id="import">导入 CSV</button><button class="action" id="export">导出 annotations.csv</button><input id="file" type="file" accept=".csv" hidden></div></header>
<main class="wrap"><div id="cases"></div></main>
<script>
const META=__META__, DATA=__DATA__, STORE="eval-stage2-annotations-v1";
let notes={}, filter="all";
try{notes=JSON.parse(localStorage.getItem(STORE)||"{}")}catch(e){}
const esc=s=>(s??"").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");
const md=s=>window.marked?marked.parse(s||""):"<pre>"+esc(s)+"</pre>";
const key=(id,n)=>id+"::"+(n===null?"case":n), get=(id,n)=>notes[key(id,n)]||(notes[key(id,n)]={verdict:"",note:""});
function save(){try{localStorage.setItem(STORE,JSON.stringify(notes))}catch(e){}}
function verdictButtons(id,n,g){return `<button class="v pass ${g.verdict==='pass'?'on':''}" data-v="pass">通过</button><button class="v fail ${g.verdict==='fail'?'on':''}" data-v="fail">失败</button><input class="note" placeholder="失败点或评估备注" value="${esc(g.note)}">`}
function tools(turn){return (turn.tool_calls||[]).map(t=>`<button class="tool ${t.tool==='run_python'?'py':''}" data-args="${encodeURIComponent(JSON.stringify(t.args??{},null,2))}">${esc(t.tool)}</button>`).join("")||'<span class="tool">无工具调用</span>'}
function render(){const host=document.getElementById("cases");host.innerHTML="";const shown=DATA.filter(r=>{const cg=get(r.id,null);if(filter==="ungraded")return !cg.verdict;if(filter==="fail")return cg.verdict==="fail";if(filter==="key")return (r.turns||[]).some(t=>t.key);return true});if(!shown.length){host.innerHTML='<div class="empty">没有符合筛选的用例</div>';count();return}
for(const r of shown){const cg=get(r.id,null), card=document.createElement("section");card.className="case "+cg.verdict;const flags=[r.error?'<span class="badge err">报错</span>':'',`<span class="badge">${(r.turns||[]).length} 轮</span>`].join("");let turns="";
for(const t of r.turns||[]){const tg=get(r.id,t.n);turns+=`<article class="turn ${t.key?'key':''}" data-n="${t.n}"><div class="turn-top"><span class="tn">TURN ${t.n}</span><span>${t.key?'<span class="badge">KEY</span>':''}</span></div><div class="prompt">${esc(t.prompt)}</div><div class="label">人工判定标准</div><div class="expect">${esc(t.expect||'-')}</div><div class="label">工具序列 / 点击查看参数</div><div class="pipe">${tools(t)}</div><pre class="args"></pre><div class="label">回答</div><div class="output">${md(t.output)}</div><button class="more">展开全文</button><div class="grade" data-grade="turn">${verdictButtons(r.id,t.n,tg)}</div><div class="trace">run_id: ${esc(t.run_id||'-')}</div></article>`}
card.innerHTML=`<div class="case-head"><div><span class="cid">${esc(r.id)}</span><span class="category">${esc(r.category||'')}</span></div><div>${flags}</div></div>${turns}<div class="case-grade"><div class="label">整例判定</div><div class="grade" data-grade="case">${verdictButtons(r.id,null,cg)}</div><div class="trace">trace: ${esc(r.trace||'-')}${r.error?' / '+esc(r.error):''}</div></div>`;
card.querySelectorAll(".tool[data-args]").forEach(b=>b.onclick=()=>{const box=b.closest(".turn").querySelector(".args"),v=decodeURIComponent(b.dataset.args),close=box.style.display==="block"&&box.textContent===v;box.textContent=v;box.style.display=close?"none":"block"});
card.querySelectorAll(".more").forEach(b=>b.onclick=()=>{const o=b.previousElementSibling;o.classList.toggle("open");b.textContent=o.classList.contains("open")?"收起":"展开全文"});
card.querySelectorAll(".grade").forEach(gr=>{const turn=gr.closest(".turn"),n=turn?Number(turn.dataset.n):null,g=get(r.id,n);gr.querySelectorAll(".v").forEach(b=>b.onclick=()=>{g.verdict=g.verdict===b.dataset.v?"":b.dataset.v;save();render()});gr.querySelector(".note").oninput=e=>{g.note=e.target.value;save()}});host.appendChild(card)}count()}
function count(){let total=0,done=0,pass=0,fail=0;DATA.forEach(r=>(r.turns||[]).forEach(t=>{total++;const v=get(r.id,t.n).verdict;if(v)done++;if(v==="pass")pass++;if(v==="fail")fail++}));document.getElementById("counts").innerHTML=`逐轮已判 <b>${done}/${total}</b> / 通过 ${pass} / 失败 ${fail}`;document.getElementById("bar").style.width=(total?done/total*100:0)+"%"}
document.getElementById("meta").textContent=`${META.model||'未知模型'} / ${META.cases_file||''} / ${DATA.length} cases`;
document.querySelectorAll(".chip").forEach(b=>b.onclick=()=>{document.querySelectorAll(".chip").forEach(x=>x.classList.remove("on"));b.classList.add("on");filter=b.dataset.filter;render()});
function csvCell(v){v=(v??"").toString();return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}function exportCsv(){const rows=[["case_id","turn","key","verdict","note"]];DATA.forEach(r=>{const cg=get(r.id,null);rows.push([r.id,"case","",cg.verdict,cg.note]);(r.turns||[]).forEach(t=>{const g=get(r.id,t.n);rows.push([r.id,t.n,t.key?"yes":"no",g.verdict,g.note])})});const text="\ufeff"+rows.map(x=>x.map(csvCell).join(",")).join("\n"),url=URL.createObjectURL(new Blob([text],{type:"text/csv;charset=utf-8"})),a=document.createElement("a");a.href=url;a.download="annotations_stage2.csv";a.click();URL.revokeObjectURL(url)}
function parseCsv(text){const rows=[];let row=[],cell="",quoted=false;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'&&quoted&&text[i+1]==='"'){cell+='"';i++}else if(c==='"'){quoted=!quoted}else if(c===','&&!quoted){row.push(cell);cell=""}else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);rows.push(row);row=[];cell=""}else cell+=c}if(cell||row.length){row.push(cell);rows.push(row)}return rows}
document.getElementById("export").onclick=exportCsv;document.getElementById("import").onclick=()=>document.getElementById("file").click();document.getElementById("file").onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{parseCsv(rd.result.replace(/^\ufeff/,"")).slice(1).forEach(cols=>{const [id,n,,v,note]=cols;if(!id)return;const g=get(id,n==="case"?null:Number(n));g.verdict=v||"";g.note=note||""});save();render()};rd.readAsText(f)};
render();
</script></body></html>"""


def render_report(source: Path, destination: Path) -> int:
    meta, rows = load_results(source)
    html = HTML.replace("__META__", json.dumps(meta, ensure_ascii=False)).replace(
        "__DATA__", json.dumps(rows, ensure_ascii=False)
    )
    destination.write_text(html, encoding="utf-8")
    return len(rows)


def main() -> None:
    args = sys.argv[1:]
    stage_dir = Path(__file__).resolve().parent
    source = Path(args[0]) if args else stage_dir / "results.jsonl"
    destination = Path(args[1]) if len(args) > 1 else source.with_name("report.html")
    count = render_report(source, destination)
    print(f"{count} 条多轮用例 -> {destination}")


if __name__ == "__main__":
    main()
