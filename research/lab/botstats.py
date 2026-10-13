import sys, json, math, logging, sqlite3
sys.path.insert(0, r"D:\dabhim\imp\RJ\office\us.in treding\trendstrategy")
import os; os.chdir(r"D:\dabhim\imp\RJ\office\us.in treding\trendstrategy")
import pandas as pd
logging.disable(logging.CRITICAL)
from tbot import study, walkforward as wf
from tbot.config import load_events
W=[("2016-10-10","2018-10-09"),("2018-10-10","2020-10-09"),("2020-10-10","2022-10-09"),("2022-10-10","2024-10-09"),("2024-10-10","2026-10-07")]
CAP={"us":5000,"india":400000}
out={}
for mk in ("us","india"):
    cfg=study.evidence_cfg(mk); cfg["capital"]=CAP[mk]
    frames=wf.load_frames(cfg,"2016-01-01",offline=True); ev=load_events()[mk]
    rows=[]
    for a,b in W:
        s=wf.run_once(cfg,frames,ev,a,b); rows.append(s)
    full=wf.run_once(cfg,frames,ev,"2016-10-10","2026-10-07")
    out[mk]=dict(rows=rows,full=full,nsym=len(cfg["universe"]["symbols"]),maxpos=cfg["risk"]["max_positions"],strategy=cfg["strategy"]["name"])
    for r in rows: print(mk,{k:(round(v,3) if isinstance(v,float) else v) for k,v in r.items() if k in("total_return","profit_factor","trades","win_rate","max_drawdown","halts")} if r else None)
    print(mk,"FULL",{k:(round(v,3) if isinstance(v,float) else v) for k,v in full.items() if k in("total_return","profit_factor","trades","win_rate","max_drawdown","halts","cagr")})
json.dump(out,open(r"C:\Users\priya\AppData\Local\Temp\claude\d--dabhim-imp-RJ-office-us-in-treding\e103e35f-c7d6-4e20-8069-109f2bb0a0ef\scratchpad\botstats.json","w"),default=lambda x:None)
# the live practice account: what has it really done?
con=sqlite3.connect("state/sim.sqlite")
print([r[0] for r in con.execute("select name from sqlite_master where type='table'")])
for t in ("trades","events"):
    try:
        print(t,con.execute(f"select count(*) from {t}").fetchone())
    except Exception as e: print(t,e)
try:
    for r in con.execute("select market,count(*),round(sum(pnl),2),min(exit_date),max(exit_date) from trades group by market"): print("live trades",r)
except Exception as e: print(e)
