"""Interactive mind map viewer + exports. No external JS libraries are loaded (works offline).

All AI-provided text is inserted with DOM APIs (textContent), never innerHTML, so generated content cannot inject script.
"""
import json
from html import escape

PALETTE = ["#8b7bff", "#2dd4bf", "#f59e0b", "#f472b6", "#60a5fa", "#a3e635", "#fb7185", "#c084fc"]


def component_html(data, height=640):
    payload = json.dumps(data).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", payload).replace("__PALETTE__", json.dumps(PALETTE)).replace("__H__", str(height))


# ---- Server-side exports -----------------------------------------------------------------------
def to_markdown(data):
    lines = []

    def walk(n, d):
        lines.append(f"{'  ' * d}- **{n['label']}**" + (f": {n['definition']}" if n.get("definition") else ""))
        for k in ("example", "formula", "revision"):
            if n.get(k):
                lines.append(f"{'  ' * (d + 1)}- _{k.title()}_: {n[k]}")
        for c in n.get("children", []):
            walk(c, d + 1)
    walk(data["root"], 0)
    if data.get("links"):
        names = {}

        def idx(n):
            names[n["id"]] = n["label"]
            [idx(c) for c in n["children"]]
        idx(data["root"])
        lines += ["", "## Relationships"]
        lines += [f"- {names.get(l['from'], l['from'])} -> {names.get(l['to'], l['to'])}: {l['relation']}" for l in data["links"]]
    return "\n".join(lines)


def to_svg(data):
    """Static, fully expanded left-to-right tree as a standalone SVG file."""
    rows = [0]
    pos = {}
    colw, rowh = 250, 44

    def lay(n, depth, branch):
        n["_b"] = branch
        if not n["children"]:
            y = rows[0] * rowh
            rows[0] += 1
        else:
            ys = [lay(c, depth + 1, (i if depth == 0 else branch)) for i, c in enumerate(n["children"])]
            y = sum(ys) / len(ys)
        pos[n["id"]] = (depth * colw + 20, y + 20)
        return y
    root = json.loads(json.dumps(data["root"]))
    lay(root, 0, -1)
    parts, w, h = [], 0, rows[0] * rowh + 40

    def draw(n):
        nonlocal w
        x, y = pos[n["id"]]
        col = "#6c5ce7" if n["_b"] == -1 else PALETTE[n["_b"] % len(PALETTE)]
        label = n["label"] if len(n["label"]) <= 30 else n["label"][:29] + "…"
        bw = max(70, len(label) * 7.4 + 24)
        w = max(w, x + bw + 20)
        for c in n["children"]:
            cx, cy = pos[c["id"]]
            parts.append(f'<path d="M{x + bw},{y} C{x + bw + 40},{y} {cx - 40},{cy} {cx},{cy}" fill="none" stroke="{col}" stroke-width="1.6" opacity=".7"/>')
            draw(c)
        parts.append(f'<rect x="{x}" y="{y - 16}" width="{bw}" height="32" rx="9" fill="{col}" fill-opacity=".22" stroke="{col}"/>'
                     f'<text x="{x + 12}" y="{y + 5}" fill="#f4f7ff" font-family="Arial,sans-serif" font-size="13">{escape(label)}</text>')
    draw(root)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{int(w + 20)}" height="{int(h)}" viewBox="0 0 {int(w + 20)} {int(h)}">'
            f'<rect width="100%" height="100%" fill="#0b1020"/>' + "".join(parts) + "</svg>")


_TEMPLATE = r"""<!doctype html><html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}body{margin:0;background:#0b1020;color:#edf3ff;font-family:Arial,Helvetica,sans-serif}
#wrap{display:flex;height:__H__px;border:1px solid #304064;border-radius:14px;overflow:hidden}
#stage{flex:1;position:relative;background:radial-gradient(circle at 20% 0,#1b2550,#0b1020 60%)}
svg{width:100%;height:100%;cursor:grab;touch-action:none}svg.drag{cursor:grabbing}
#bar{position:absolute;top:8px;left:8px;display:flex;gap:6px;flex-wrap:wrap;z-index:2}
button{background:#29264a;color:#e7e3ff;border:1px solid #4b447d;border-radius:8px;padding:6px 10px;font-size:12px;cursor:pointer}
button:hover{background:#3a3570}label{font-size:12px;color:#b7c5df;align-self:center;margin-left:4px}
#panel{width:290px;border-left:1px solid #304064;background:#111a30;padding:14px;overflow:auto;font-size:13px;line-height:1.45}
#panel h3{margin:0 0 4px;font-size:16px}#panel .path{color:#9aa8c7;font-size:11px;margin-bottom:10px}
#panel b{color:#cbc5ff;display:block;margin-top:9px;font-size:11px;letter-spacing:.06em;text-transform:uppercase}
.hint{color:#9aa8c7}.msg{color:#f59e0b;font-size:11px;margin-top:8px}
@media (max-width:640px){#wrap{flex-direction:column}#panel{width:100%;height:190px;border-left:0;border-top:1px solid #304064}}
@media print{#bar,#panel{display:none}#wrap{border:0;height:auto}svg{height:90vh}}
</style></head><body>
<div id="wrap"><div id="stage"><div id="bar">
<button id="zin">＋</button><button id="zout">－</button><button id="fit">Fit</button>
<button id="expand">Expand all</button><button id="collapse">Collapse</button>
<button id="png">Download PNG</button><button id="pdf">Save as PDF</button>
<label><input type="checkbox" id="links" checked> relationships</label></div>
<svg id="svg"><g id="vp"></g></svg></div>
<div id="panel"><div class="hint">Click a node to see its definition, example, formula and revision point. Click the +/- badge to expand or collapse a branch. Scroll to zoom, drag to pan.</div></div></div>
<script>
const DATA=__DATA__, PAL=__PALETTE__, NS="http://www.w3.org/2000/svg";
const svg=document.getElementById("svg"), vp=document.getElementById("vp"), panel=document.getElementById("panel");
let collapsed=new Set(), selected=null, scale=1, tx=20, ty=20, showLinks=true;
const byId={}, parent={};
(function idx(n,p,d){byId[n.id]=n;parent[n.id]=p;n._d=d;n.children.forEach((c,i)=>{c._b=d===0?i:n._b;idx(c,n,d+1)})})(DATA.root,null,0);
Object.values(byId).forEach(n=>{if(n._d>=2&&n.children.length)collapsed.add(n.id)});
function el(tag,attrs,parentEl){const e=document.createElementNS(NS,tag);for(const k in attrs)e.setAttribute(k,attrs[k]);if(parentEl)parentEl.appendChild(e);return e}
function color(n){return n._d===0?"#6c5ce7":PAL[n._b%PAL.length]}
function shortLabel(s){return s.length>30?s.slice(0,29)+"…":s}
function widthOf(n){return Math.max(70,shortLabel(n.label).length*7.4+24)+(n.children.length?24:0)}
const COLW=260,ROWH=46;
function layout(){const pos={};let row=0;(function go(n,d){const kids=collapsed.has(n.id)?[]:n.children;let y;
 if(!kids.length){y=row*ROWH;row++}else{const ys=kids.map(c=>go(c,d+1));y=ys.reduce((a,b)=>a+b,0)/ys.length}
 pos[n.id]={x:d*COLW,y:y};return y})(DATA.root,0);return pos}
function draw(){
 vp.replaceChildren();const pos=layout();const gl=el("g",{},vp),gk=el("g",{},vp),gn=el("g",{},vp);
 (function go(n){if(collapsed.has(n.id))return;const p=pos[n.id],w=widthOf(n);
  n.children.forEach(c=>{const q=pos[c.id];el("path",{d:`M${p.x+w},${p.y} C${p.x+w+45},${p.y} ${q.x-45},${q.y} ${q.x},${q.y}`,fill:"none",stroke:color(c),"stroke-width":1.8,opacity:.65},gl);go(c)})})(DATA.root);
 if(showLinks)(DATA.links||[]).forEach(l=>{const a=pos[l.from],b=pos[l.to];if(!a||!b)return;
  const A=byId[l.from],B=byId[l.to];const x1=a.x+widthOf(A)/2,y1=a.y,x2=b.x+widthOf(B)/2,y2=b.y;const mx=(x1+x2)/2+60;
  const path=el("path",{d:`M${x1},${y1} Q${mx},${(y1+y2)/2} ${x2},${y2}`,fill:"none",stroke:"#f59e0b","stroke-width":1.3,"stroke-dasharray":"5 4",opacity:.8},gk);
  const t=el("title",{},path);t.textContent=A.label+" ↔ "+B.label+(l.relation?": "+l.relation:"")});
 Object.keys(pos).forEach(id=>{const n=byId[id],p=pos[id],w=widthOf(n),c=color(n);
  const g=el("g",{style:"cursor:pointer"},gn);
  el("rect",{x:p.x,y:p.y-17,width:w,height:34,rx:10,fill:c,"fill-opacity":(id===selected)?0.45:0.2,stroke:c,"stroke-width":id===selected?2.5:1.3},g);
  const t=el("text",{x:p.x+12,y:p.y+5,fill:"#f4f7ff","font-size":13,"font-family":"Arial,sans-serif"},g);t.textContent=shortLabel(n.label);
  const tt=el("title",{},g);tt.textContent=n.label;
  g.addEventListener("click",()=>{selected=id;show(n);draw()});
  if(n.children.length){const bx=p.x+w-20;const b=el("g",{},gn);el("circle",{cx:bx+8,cy:p.y,r:9,fill:c,"fill-opacity":.9},b);
   const bt=el("text",{x:bx+8,y:p.y+4,"text-anchor":"middle",fill:"#0b1020","font-size":12,"font-weight":"bold","font-family":"Arial,sans-serif"},b);
   bt.textContent=collapsed.has(id)?"+":"−";b.style.cursor="pointer";
   b.addEventListener("click",e=>{e.stopPropagation();collapsed.has(id)?collapsed.delete(id):collapsed.add(id);draw()})}});
 apply()}
function apply(){vp.setAttribute("transform",`translate(${tx},${ty}) scale(${scale})`)}
function addRow(label,text){if(!text)return;const b=document.createElement("b");b.textContent=label;const d=document.createElement("div");d.textContent=text;panel.append(b,d)}
function show(n){panel.replaceChildren();const h=document.createElement("h3");h.textContent=n.label;h.style.color=color(n);
 const path=[];for(let x=n;x;x=parent[x.id])path.unshift(x.label);const p=document.createElement("div");p.className="path";p.textContent=path.join(" › ");
 panel.append(h,p);addRow("Definition",n.definition);addRow("Example",n.example);addRow("Formula",n.formula);addRow("Revision point",n.revision);
 const rel=(DATA.links||[]).filter(l=>l.from===n.id||l.to===n.id);
 if(rel.length){const b=document.createElement("b");b.textContent="Relationships";panel.append(b);rel.forEach(l=>{const o=byId[l.from===n.id?l.to:l.from];const d=document.createElement("div");d.textContent="↔ "+o.label+(l.relation?" — "+l.relation:"");panel.append(d)})}}
function fit(){const b=vp.getBBox();if(!b.width)return;const r=svg.getBoundingClientRect();scale=Math.min(1.2,Math.min((r.width-40)/b.width,(r.height-60)/b.height));tx=20-b.x*scale;ty=50-b.y*scale+Math.max(0,(r.height-60-b.height*scale)/2);apply()}
function zoomAt(f,cx,cy){const ns=Math.max(.2,Math.min(3,scale*f));tx=cx-(cx-tx)*(ns/scale);ty=cy-(cy-ty)*(ns/scale);scale=ns;apply()}
document.getElementById("zin").onclick=()=>{const r=svg.getBoundingClientRect();zoomAt(1.2,r.width/2,r.height/2)};
document.getElementById("zout").onclick=()=>{const r=svg.getBoundingClientRect();zoomAt(1/1.2,r.width/2,r.height/2)};
document.getElementById("fit").onclick=fit;
document.getElementById("expand").onclick=()=>{collapsed.clear();draw();fit()};
document.getElementById("collapse").onclick=()=>{collapsed=new Set(DATA.root.children.map(c=>c.id).filter(i=>byId[i].children.length));draw();fit()};
document.getElementById("links").onchange=e=>{showLinks=e.target.checked;draw()};
svg.addEventListener("wheel",e=>{e.preventDefault();const r=svg.getBoundingClientRect();zoomAt(e.deltaY<0?1.1:1/1.1,e.clientX-r.left,e.clientY-r.top)},{passive:false});
let drag=null;svg.addEventListener("pointerdown",e=>{if(e.target.closest("g[style]"))return;drag={x:e.clientX,y:e.clientY,tx,ty};svg.classList.add("drag")});
window.addEventListener("pointermove",e=>{if(!drag)return;tx=drag.tx+e.clientX-drag.x;ty=drag.ty+e.clientY-drag.y;apply()});
window.addEventListener("pointerup",()=>{drag=null;svg.classList.remove("drag")});
document.getElementById("pdf").onclick=()=>window.print();
document.getElementById("png").onclick=()=>{try{
 const b=vp.getBBox(),pad=30,W=Math.ceil(b.width+pad*2),H=Math.ceil(b.height+pad*2);
 const s=new XMLSerializer().serializeToString(vp);
 const full=`<svg xmlns="${NS}" width="${W}" height="${H}"><rect width="100%" height="100%" fill="#0b1020"/><g transform="translate(${pad-b.x},${pad-b.y})">${s.replace(/^<g[^>]*>/,"").replace(/<\/g>$/,"")}</g></svg>`;
 const img=new Image();img.onload=()=>{const c=document.createElement("canvas");c.width=W*2;c.height=H*2;const x=c.getContext("2d");x.scale(2,2);x.drawImage(img,0,0);
  c.toBlob(bl=>{const a=document.createElement("a");a.href=URL.createObjectURL(bl);a.download="mindmap.png";a.click()})};
 img.onerror=()=>{msg("PNG export failed in this browser. Use the SVG download below the map.")};
 img.src="data:image/svg+xml;charset=utf-8,"+encodeURIComponent(full)}catch(e){msg("PNG export failed. Use the SVG download below the map.")}};
function msg(t){const d=document.createElement("div");d.className="msg";d.textContent=t;panel.append(d)}
draw();setTimeout(fit,50);
</script></body></html>"""
