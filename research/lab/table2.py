import sys, json, math
sys.stdout.reconfigure(encoding="utf-8")
R = json.load(open("results2.json"))
NAMES = {"Flipper":"20% Flipper","Bollinger":"Bollinger Breakout","Longboard":"Longboard (all-time high)","FearPullback":"Fear Pullback",
 "ETF75":"ETF 75-day filter","ETF_TrendPilot":"ETF TrendPilot","ETF_AbsMom":"ETF AbsoluteMom","ETF_GEM":"ETF GEM"}
sym={"us":"$","india":"₹"}
def f(x): return "-" if x is None or (isinstance(x,float) and math.isnan(x)) else ("∞" if x in (float("inf"),) or x is None else f"{x:.2f}")
for mk in ("us","india"):
    d=R[mk]; print(f"\n### {mk.upper()} ({d['nsym']} shares)")
    print("| # | Strategy | 2016–18 | 2018–20 | 2020–22 | 2022–24 | 2024–26 | Won | 10-yr PF | Worst DD | Verdict |\n|---|---|---|---|---|---|---|---|---|---|---|")
    print("| – | *Index buy & hold* | "+" | ".join(f"{b*100:+.0f}%" for b in d["bh"])+" | | | | |")
    i=0
    for k in [k for k in NAMES if k in d["res"]]:
        i+=1; ws=d["res"][k]; cells=[]; won=0; gw=gl=0; dd=0
        etf=k.startswith("ETF")
        for r in ws:
            pf=r["pf"]; pf=float("inf") if pf is None else pf
            thin=(r["n"]==0) if etf else (r["n"]<5)
            ok=(not thin) and r["pnl"]>0 and r["dd"]>-0.10
            won+=ok; gw+=r["gw"]; gl+=r["gl"]; dd=min(dd,r["dd"])
            mark="⚪" if thin else ("✅" if ok else "❌")
            cells.append(f"{mark} {r['ret']*100:+.1f}% · PF {f(pf)} · {r['n']}t")
        pf=gw/gl if gl>0 else float('inf')
        v="PASS" if (pf>1.3 and dd>-0.10 and won>=3) else ("WEAK" if pf>1.0 else "FAIL")
        print(f"| {i} | {NAMES[k]} | "+" | ".join(cells)+f" | {won}/5 | {f(pf)} | {dd*100:.1f}% | **{v}** |")
print("\nT-bill used:", R["us"]["res"].get("_tbill"))
