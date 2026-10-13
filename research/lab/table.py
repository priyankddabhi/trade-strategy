import sys; sys.stdout.reconfigure(encoding="utf-8")
import json, math
R = json.load(open("results.json"))
order = ["SEPA","SuperPerf","TurtleS1","TurtleS2","Whipsaw","Donchian","AbsoluteMom","RelativeMom","GEM","DualMomentum","NewYearlyHigh","HundredDayHigh","GoldenCross","TrendPilot","ClenowTF","Momentum","WeeklyRot","MeanRevLong","MeanRevShort","Trend200","MeanReversion","NiftyMom30"]
cap = {"us":5000,"india":400000}
sym = {"us":"$","india":"₹"}
def fmt(x):
    return "-" if x is None or (isinstance(x,float) and math.isnan(x)) else ("∞" if x==float("inf") else f"{x:.2f}")
summary = {}
for mk in ("us","india"):
    d = R[mk]; print(f"\n### {mk.upper()}  ({d['nsym']} shares, start {sym[mk]}{cap[mk]:,} each window)")
    print("| # | Strategy | W1 16-18 | W2 18-20 | W3 20-22 | W4 22-24 | W5 24-26 | Won | 10-yr PF | Worst DD | Verdict |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    print("| – | *Index buy & hold* | " + " | ".join(f"{b*100:+.0f}%" for b in d["bh"]) + " | | | | |")
    for i,s in enumerate(order,1):
        ws = d["res"][s]; cells=[]; won=0; gw=gl=0; dd=0; tr=0
        for r in ws:
            ok = r["pnl"]>0 and r["dd"]>-0.10 and r["n"]>=5
            won += ok
            gw += r["gw"]; gl += r["gl"]; dd=min(dd,r["dd"]); tr+=r["n"]
            mark = "✅" if ok else ("⚪" if r["n"]<5 else "❌")
            cells.append(f"{mark} {r['ret']*100:+.1f}% · PF {fmt(r['pf'])} · {r['n']}t")
        pf = gw/gl if gl>0 else float("nan")
        v = "PASS" if (pf>1.3 and dd>-0.10 and won>=3) else ("WEAK" if pf>1.0 else "FAIL")
        if s=="MeanRevShort": v+=" (short, banned)"
        summary[(mk,s)] = (won,pf,dd,v,tr)
        print(f"| {i} | {s} | " + " | ".join(cells) + f" | {won}/5 | {fmt(pf)} | {dd*100:.1f}% | **{v}** |")
