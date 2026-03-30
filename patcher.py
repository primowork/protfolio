#!/usr/bin/env python3
"""
Patches app.html to add:
1. Sortable columns in the holdings table (click header to sort)
2. Day-gain toggle: click "יום זה" header to switch between $ and %
"""

import sys, os, re

SRC = "app.html"
DST = "app.html"

OLD = r'yb=({holdings:e})=>{let[t,r]=(0,z.useState)(null),n=[...e].sort((e,t)=>(t.value??0)-(e.value??0));return(0,R.jsx)("div",{className:"overflow-x-auto",children:(0,R.jsxs)("table",{className:"w-full text-sm border-collapse",children:[(0,R.jsx)("thead",{children:(0,R.jsx)("tr",{className:"border-b border-border text-right",children:["נייר","סוג","מניות","מחיר עלות","מחיר נוכחי","שווי","רווח/הפסד","יום זה"].map(e=>(0,R.jsx)("th",{className:"py-2 px-2 text-[10px] text-muted-foreground uppercase tracking-wider font-medium whitespace-nowrap",children:e},e))})}),(0,R.jsx)("tbody",{children:n.map(e=>(0,R.jsxs)(R.Fragment,{children:[(0,R.jsxs)("tr",{className:"border-b border-border/50 hover:bg-muted/20 transition-colors cursor-default",onMouseEnter:()=>r(e.ticker),onMouseLeave:()=>r(null),children:[(0,R.jsx)("td",{className:"py-2 px-2 font-semibold whitespace-nowrap",children:e.ticker}),(0,R.jsx)("td",{className:"py-2 px-2",children:(0,R.jsx)(yc,{isETF:e.isETF})}),(0,R.jsx)("td",{className:"py-2 px-2 num text-muted-foreground",children:yt(e.shares)}),(0,R.jsx)("td",{className:"py-2 px-2 num text-muted-foreground",children:h7(e.avgPrice)}),(0,R.jsx)("td",{className:"py-2 px-2 num",children:null!==e.currentPrice?h7(e.currentPrice):"—"}),(0,R.jsx)("td",{className:"py-2 px-2 num font-medium",children:null!==e.value?h7(e.value):"—"}),(0,R.jsx)("td",{className:"py-2 px-2",children:(0,R.jsx)(yu,{v:e.gain,pct:e.gainPct})}),(0,R.jsx)("td",{className:"py-2 px-2",children:(0,R.jsx)(yu,{v:e.dayGain})})]},e.ticker),t===e.ticker&&yn[e.ticker]&&(0,R.jsx)("tr",{className:"bg-violet-500/5 border-b border-violet-500/20",children:(0,R.jsx)("td",{colSpan:8,className:"py-1.5 px-3 text-xs text-violet-300/90 italic",children:yn[e.ticker]})},e.ticker+"_desc")]}))})]})})};'

NEW = r'''yb=({holdings:e})=>{
let[t,r]=(0,z.useState)(null),
[sc,ss]=(0,z.useState)("value"),
[sd,sds]=(0,z.useState)("desc"),
[dm,setDm]=(0,z.useState)("usd");

const hSort=col=>{
  if(col==="dayGain"){setDm(m=>m==="usd"?"pct":"usd");return;}
  if(col==="type")return;
  if(sc===col)sds(d=>d==="asc"?"desc":"asc");
  else{ss(col);sds("desc");}
};

const gv=(h,col)=>{
  const map={ticker:h.ticker,shares:h.shares,avgPrice:h.avgPrice,
    currentPrice:h.currentPrice??-Infinity,value:h.value??-Infinity,
    gain:h.gain??-Infinity,gainPct:h.gainPct??-Infinity,dayGain:h.dayGain??-Infinity};
  return map[col]??0;
};

let n=[...e].sort((a,b)=>{
  let va=gv(a,sc),vb=gv(b,sc);
  if(typeof va==="string")return sd==="asc"?va.localeCompare(vb):vb.localeCompare(va);
  return sd==="asc"?va-vb:vb-va;
});

const SI=({col})=>{
  if(col==="type")return null;
  if(col==="dayGain")return(0,R.jsx)("span",{className:"text-violet-400 ml-1 text-[10px]",children:dm==="usd"?"$→%":"%→$"});
  if(sc!==col)return(0,R.jsx)("span",{className:"opacity-20 ml-1",children:"⇅"});
  return(0,R.jsx)("span",{className:"text-violet-400 ml-1",children:sd==="asc"?"↑":"↓"});
};

const cols=[
  {key:"ticker",label:"נייר"},{key:"type",label:"סוג"},
  {key:"shares",label:"מניות"},{key:"avgPrice",label:"מחיר עלות"},
  {key:"currentPrice",label:"מחיר נוכחי"},{key:"value",label:"שווי"},
  {key:"gain",label:"רווח/הפסד"},{key:"dayGain",label:dm==="usd"?"יום $ ":"יום % "}
];

return(0,R.jsx)("div",{className:"overflow-x-auto",children:(0,R.jsxs)("table",{className:"w-full text-sm border-collapse",children:[
(0,R.jsx)("thead",{children:(0,R.jsx)("tr",{className:"border-b border-border text-right",children:
  cols.map(c=>(0,R.jsxs)("th",{
    className:"py-2 px-2 text-[10px] text-muted-foreground uppercase tracking-wider font-medium whitespace-nowrap"+(c.key!=="type"?" cursor-pointer select-none hover:text-foreground":""),
    onClick:()=>hSort(c.key),
    children:[c.label,(0,R.jsx)(SI,{col:c.key})]
  },c.key))
})}),
(0,R.jsx)("tbody",{children:n.map(e=>(0,R.jsxs)(R.Fragment,{children:[
  (0,R.jsxs)("tr",{
    className:"border-b border-border/50 hover:bg-muted/20 transition-colors cursor-default",
    onMouseEnter:()=>r(e.ticker),onMouseLeave:()=>r(null),
    children:[
      (0,R.jsx)("td",{className:"py-2 px-2 font-semibold whitespace-nowrap",children:e.ticker}),
      (0,R.jsx)("td",{className:"py-2 px-2",children:(0,R.jsx)(yc,{isETF:e.isETF})}),
      (0,R.jsx)("td",{className:"py-2 px-2 num text-muted-foreground",children:yt(e.shares)}),
      (0,R.jsx)("td",{className:"py-2 px-2 num text-muted-foreground",children:h7(e.avgPrice)}),
      (0,R.jsx)("td",{className:"py-2 px-2 num",children:null!==e.currentPrice?h7(e.currentPrice):"—"}),
      (0,R.jsx)("td",{className:"py-2 px-2 num font-medium",children:null!==e.value?h7(e.value):"—"}),
      (0,R.jsx)("td",{className:"py-2 px-2",children:(0,R.jsx)(yu,{v:e.gain,pct:e.gainPct})}),
      (0,R.jsx)("td",{className:"py-2 px-2",children:
        dm==="usd"
          ?(0,R.jsx)(yu,{v:e.dayGain})
          :(0,R.jsx)("span",{className:"num "+(null!==e.dayGain&&(e.dayGain??0)>=0?"text-emerald-400":"text-red-400"),
              children:null===e.dayGain||null===e.prevClose||null===e.currentPrice?"—"
                :ye((e.currentPrice-e.prevClose)/e.prevClose*100,!0)})
      })
    ]
  },e.ticker),
  t===e.ticker&&yn[e.ticker]&&(0,R.jsx)("tr",{className:"bg-violet-500/5 border-b border-violet-500/20",
    children:(0,R.jsx)("td",{colSpan:8,className:"py-1.5 px-3 text-xs text-violet-300/90 italic",children:yn[e.ticker]})},
  e.ticker+"_desc")
]}))})]})})};'''

def patch(path):
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    if OLD not in html:
        print("ERROR: Could not find the yb function to replace.")
        print("Make sure you're running this against the correct app.html")
        return False
    patched = html.replace(OLD, NEW, 1)
    with open(DST, "w", encoding="utf-8") as f:
        f.write(patched)
    print(f"✓ Patched successfully → {DST}")
    return True

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else SRC
    if not os.path.exists(src):
        print(f"File not found: {src}")
        sys.exit(1)
    patch(src)
