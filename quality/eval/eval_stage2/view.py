"""Render eval results as a compact, single-page human review report."""
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


def checks_path_for(results_path: Path) -> Path:
    return results_path.with_name(f"{results_path.stem}_checks.json")


def load_checks(results_path: Path) -> list[dict]:
    path = checks_path_for(results_path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("checks", []) if isinstance(payload, dict) else payload


HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Drainage Agent Eval 评审</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.0/marked.min.js"></script>
<style>
:root{--bg:#f7f7f4;--card:#fff;--ink:#20201d;--muted:#74746d;--line:#deded7;--accent:#9b3e1d;--soft:#f5e8e1;--ok:#2f6e4f;--okbg:#e6f0ea;--bad:#a8311f;--badbg:#f6e5e2;--warn:#8a6518;--warnbg:#f8efd8;--mono:"Cascadia Mono",Consolas,monospace;--sans:Arial,"Noto Sans SC",sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 var(--sans)}
header{position:sticky;top:0;z-index:10;background:rgba(247,247,244,.96);border-bottom:1px solid var(--line);backdrop-filter:blur(8px)}
.head,.toolbar,.wrap{max-width:1080px;margin:auto;padding-left:26px;padding-right:26px}.head{padding-top:16px;padding-bottom:10px;display:flex;gap:18px;align-items:baseline;justify-content:space-between}
h1{font-size:15px;letter-spacing:.06em;margin:0}.counts,.meta{color:var(--muted);font-size:12px}.bar{height:4px;background:var(--line)}.bar i{display:block;height:100%;width:0;background:var(--accent)}
.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding-top:10px;padding-bottom:12px}.spacer{flex:1}button{font:inherit;cursor:pointer}.chip,.action{border:1px solid var(--line);background:#fff;border-radius:7px;padding:5px 11px}.chip.on{border-color:var(--ink);font-weight:700}.action{background:var(--ink);color:#fff;border-color:var(--ink)}.action.ghost{background:#fff;color:var(--ink)}
.wrap{padding-top:8px;padding-bottom:100px}.case{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:18px 0;overflow:hidden}.case.pass{border-left:4px solid var(--ok)}.case.fail{border-left:4px solid var(--bad)}.case.uncertain{border-left:4px solid var(--warn)}
.case-head{padding:16px 21px 13px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.cid{font:700 14px var(--mono)}.category{color:var(--muted);margin-left:10px}.scenario{font-size:15px;font-weight:600;margin-top:5px}.dims{margin-top:7px;display:flex;gap:5px;flex-wrap:wrap}.badge{font:11px var(--mono);padding:2px 7px;border-radius:99px;background:var(--soft);color:var(--accent)}.badge.ok{background:var(--okbg);color:var(--ok)}.badge.err{background:var(--badbg);color:var(--bad)}.badge.warn{background:var(--warnbg);color:var(--warn)}
.turn{padding:17px 21px;border-bottom:1px solid var(--line)}.turn.key{background:#fffdfb}.turn-top{display:flex;justify-content:space-between;gap:12px}.tn{font:700 12px var(--mono);color:var(--muted)}.prompt{font-size:16px;font-weight:600;margin:6px 0 10px}.label{font-size:10px;letter-spacing:.08em;color:var(--muted);margin:10px 0 4px}.expect{border-left:3px solid #d8b5a5;background:#faf4f1;padding:8px 11px;margin:5px 0 12px;color:#51433d}.output{border-top:1px dashed var(--line);margin-top:7px;padding-top:9px;max-height:260px;overflow:hidden}.output.open{max-height:none}.output pre{white-space:pre-wrap}.output table{border-collapse:collapse;font-size:12px}.output td,.output th{border:1px solid var(--line);padding:3px 7px}.more{border:0;background:transparent;color:var(--accent);padding:5px 0;font-size:12px}
details.evidence{margin-top:10px;color:var(--muted)}details.evidence summary{cursor:pointer;font-size:12px}.evidence-body{padding:9px 11px;background:#f6f6f2;border-radius:7px;margin-top:6px}.pipe{display:flex;gap:6px;flex-wrap:wrap}.tool{font:12px var(--mono);border:1px solid var(--line);border-radius:6px;padding:3px 7px;background:#fff}.args,.trace{white-space:pre-wrap;word-break:break-all;font:11px var(--mono)}.args{display:none;background:#eee;padding:7px;border-radius:6px;margin-top:6px}.auto-list{margin:5px 0 0;padding-left:18px}.auto-list .fail{color:var(--bad)}
.case-grade{padding:14px 21px;background:#fafaf7}.grade{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.grade input{flex:1;min-width:250px;border:1px solid var(--line);border-radius:6px;padding:7px}.v{border:1px solid var(--line);background:#fff;border-radius:6px;padding:5px 12px}.v.pass.on{background:var(--okbg);color:var(--ok);border-color:var(--ok)}.v.fail.on{background:var(--badbg);color:var(--bad);border-color:var(--bad)}.v.uncertain.on{background:var(--warnbg);color:var(--warn);border-color:var(--warn)}.empty{text-align:center;color:var(--muted);padding:50px}
</style></head><body>
<header><div class="head"><div><h1>Drainage Agent Eval 评审</h1><div class="meta" id="meta"></div></div><div class="counts" id="counts"></div></div><div class="bar"><i id="bar"></i></div>
<div class="toolbar"><button class="chip on" data-filter="all">全部</button><button class="chip" data-filter="ungraded">未判</button><button class="chip" data-filter="fail">人工失败</button><button class="chip" data-filter="uncertain">人工不确定</button><button class="chip" data-filter="auto-fail">自动失败</button><span class="spacer"></span><button class="action ghost" id="import">导入 CSV</button><button class="action" id="export">导出标注</button><input id="file" type="file" accept=".csv" hidden></div></header>
<main class="wrap"><div id="cases"></div></main>
<script>
const META=__META__,DATA=__DATA__,CHECKS=__CHECKS__,STORE="drainage-eval-annotations-v2";let notes={},filter="all";
try{notes=JSON.parse(localStorage.getItem(STORE)||"{}")}catch(e){}
const esc=s=>(s??"").toString().replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;");const md=s=>window.marked?marked.parse(s||""):"<pre>"+esc(s)+"</pre>";
const get=id=>notes[id]||(notes[id]={verdict:"",note:""});function save(){try{localStorage.setItem(STORE,JSON.stringify(notes))}catch(e){}}
function caseChecks(id){return CHECKS.filter(x=>x.case_id===id)}function autoState(id){const xs=caseChecks(id);return !xs.length?"none":xs.some(x=>x.status==="fail")?"fail":xs.some(x=>x.status==="pass")?"pass":"skip"}
function autoBadge(id){const xs=caseChecks(id),s=autoState(id);if(s==="none")return '<span class="badge warn">未运行自动检查</span>';const p=xs.filter(x=>x.status==="pass").length,f=xs.filter(x=>x.status==="fail").length,k=xs.filter(x=>x.status==="skip").length;return `<span class="badge ${s==='fail'?'err':s==='pass'?'ok':'warn'}">自动 ${p}✓ ${f}✕ ${k}跳过</span>`}
function expected(t){const e=t.expected||{};return t.expect||e.response||e.answer||e.behavior||"-"}function dims(r){const d=r.dimensions||{};return Object.entries(d).map(([k,v])=>`<span class="badge">${esc(k)}: ${esc(Array.isArray(v)?v.join(' / '):v)}</span>`).join("")}
function tools(t){return (t.tool_calls||[]).map(x=>`<button class="tool" data-args="${encodeURIComponent(JSON.stringify(x.args??{},null,2))}">${esc(x.tool)}</button>`).join("")||'<span class="tool">无工具调用</span>'}
function artifacts(t){const a=[];(t.trace_events||[]).forEach(e=>(e.artifacts||[]).forEach(x=>a.push(typeof x==='string'?x:JSON.stringify(x))));return [...new Set(a)]}
function evidence(r,t){const xs=caseChecks(r.id).filter(x=>x.turn==null||Number(x.turn)===Number(t.n)),as=artifacts(t);return `<details class="evidence"><summary>查看证据 · 工具 ${(t.tool_calls||[]).length} · 产物 ${as.length} · 自动检查 ${xs.length}</summary><div class="evidence-body"><div class="pipe">${tools(t)}</div><pre class="args"></pre>${as.length?'<div class="label">产物</div><div class="trace">'+as.map(esc).join('\n')+'</div>':''}${xs.length?'<div class="label">自动检查</div><ul class="auto-list">'+xs.map(x=>`<li class="${x.status}">${esc(x.check)}: ${esc(x.status)} — ${esc(x.reason)}</li>`).join('')+'</ul>':''}<div class="trace">run_id: ${esc(t.run_id||'-')}</div></div></details>`}
function buttons(g){return `<button class="v pass ${g.verdict==='pass'?'on':''}" data-v="pass">通过</button><button class="v fail ${g.verdict==='fail'?'on':''}" data-v="fail">失败</button><button class="v uncertain ${g.verdict==='uncertain'?'on':''}" data-v="uncertain">不确定</button><input class="note" placeholder="失败原因或评审备注（可选）" value="${esc(g.note)}">`}
function render(){const host=document.getElementById("cases");host.innerHTML="";const shown=DATA.filter(r=>{const g=get(r.id);if(filter==="ungraded")return !g.verdict;if(filter==="fail")return g.verdict==="fail";if(filter==="uncertain")return g.verdict==="uncertain";if(filter==="auto-fail")return autoState(r.id)==="fail";return true});if(!shown.length){host.innerHTML='<div class="empty">没有符合筛选条件的用例</div>';count();return}
for(const r of shown){const g=get(r.id),card=document.createElement("section");card.className="case "+g.verdict;let turns="";(r.turns||[]).forEach(t=>{turns+=`<article class="turn ${t.key?'key':''}"><div class="turn-top"><span class="tn">TURN ${t.n}</span>${t.key?'<span class="badge">KEY</span>':''}</div><div class="prompt">${esc(t.prompt)}</div><div class="label">预期</div><div class="expect">${esc(expected(t))}</div><div class="label">实际回答</div><div class="output">${md(t.output)}</div><button class="more">展开全文</button>${evidence(r,t)}</article>`});
card.innerHTML=`<div class="case-head"><div><span class="cid">${esc(r.id)}</span><span class="category">${esc(r.category||'')}</span><div class="scenario">${esc(r.scenario||r.conversation_goal||'')}</div><div class="dims">${dims(r)}</div></div><div>${autoBadge(r.id)} <span class="badge">${(r.turns||[]).length} 轮</span></div></div>${turns}<div class="case-grade"><div class="label">整例人工判定</div><div class="grade">${buttons(g)}</div>${r.error?'<div class="trace">error: '+esc(r.error)+'</div>':''}</div>`;
card.querySelectorAll(".tool[data-args]").forEach(b=>b.onclick=()=>{const box=b.closest(".evidence-body").querySelector(".args"),v=decodeURIComponent(b.dataset.args),close=box.style.display==="block"&&box.textContent===v;box.textContent=v;box.style.display=close?"none":"block"});card.querySelectorAll(".more").forEach(b=>b.onclick=()=>{const o=b.previousElementSibling;o.classList.toggle("open");b.textContent=o.classList.contains("open")?"收起":"展开全文"});card.querySelectorAll(".v").forEach(b=>b.onclick=()=>{g.verdict=g.verdict===b.dataset.v?"":b.dataset.v;save();render()});card.querySelector(".note").oninput=e=>{g.note=e.target.value;save()};host.appendChild(card)}count()}
function count(){let done=0,p=0,f=0,u=0;DATA.forEach(r=>{const v=get(r.id).verdict;if(v)done++;if(v==="pass")p++;if(v==="fail")f++;if(v==="uncertain")u++});document.getElementById("counts").innerHTML=`整例已判 <b>${done}/${DATA.length}</b> / 通过 ${p} / 失败 ${f} / 不确定 ${u}`;document.getElementById("bar").style.width=(DATA.length?done/DATA.length*100:0)+"%"}
document.getElementById("meta").textContent=`${META.model||'未知模型'} / ${META.cases_file||''} / ${DATA.length} cases`;document.querySelectorAll(".chip").forEach(b=>b.onclick=()=>{document.querySelectorAll(".chip").forEach(x=>x.classList.remove("on"));b.classList.add("on");filter=b.dataset.filter;render()});
function csvCell(v){v=(v??"").toString();return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v}function exportCsv(){const rows=[["case_id","verdict","note"]];DATA.forEach(r=>{const g=get(r.id);rows.push([r.id,g.verdict,g.note])});const text="\ufeff"+rows.map(x=>x.map(csvCell).join(",")).join("\n"),url=URL.createObjectURL(new Blob([text],{type:"text/csv;charset=utf-8"})),a=document.createElement("a");a.href=url;a.download="eval_annotations.csv";a.click();URL.revokeObjectURL(url)}
function parseCsv(text){const rows=[];let row=[],cell="",q=false;for(let i=0;i<text.length;i++){const c=text[i];if(c==='"'&&q&&text[i+1]==='"'){cell+='"';i++}else if(c==='"')q=!q;else if(c===','&&!q){row.push(cell);cell=""}else if((c==='\n'||c==='\r')&&!q){if(c==='\r'&&text[i+1]==='\n')i++;row.push(cell);rows.push(row);row=[];cell=""}else cell+=c}if(cell||row.length){row.push(cell);rows.push(row)}return rows}
document.getElementById("export").onclick=exportCsv;document.getElementById("import").onclick=()=>document.getElementById("file").click();document.getElementById("file").onchange=e=>{const f=e.target.files[0];if(!f)return;const rd=new FileReader();rd.onload=()=>{parseCsv(rd.result.replace(/^\ufeff/,"")).slice(1).forEach(([id,v,note])=>{if(id){const g=get(id);g.verdict=v||"";g.note=note||""}});save();render()};rd.readAsText(f)};render();
</script></body></html>'''


def render_report(source: Path, destination: Path) -> int:
    meta, rows = load_results(source)
    html = (
        HTML.replace("__META__", json.dumps(meta, ensure_ascii=False))
        .replace("__DATA__", json.dumps(rows, ensure_ascii=False))
        .replace("__CHECKS__", json.dumps(load_checks(source), ensure_ascii=False))
    )
    destination.write_text(html, encoding="utf-8")
    return len(rows)


def main() -> None:
    args = sys.argv[1:]
    stage_dir = Path(__file__).resolve().parent
    source = Path(args[0]) if args else stage_dir / "results.jsonl"
    destination = Path(args[1]) if len(args) > 1 else source.with_name("report.html")
    count = render_report(source, destination)
    print(f"{count} 条评测用例 -> {destination}")


if __name__ == "__main__":
    main()
