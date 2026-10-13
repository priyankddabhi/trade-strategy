"""Speed check: Kronos-small on this PC (CPU), one batch of daily candles."""
import sys, time
from pathlib import Path
import pandas as pd, torch
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "research" / "kronos"))
from model import Kronos, KronosTokenizer, KronosPredictor
torch.manual_seed(0); torch.set_num_threads(12)
t0 = time.time()
tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
mdl = Kronos.from_pretrained("NeoQuasar/Kronos-small")
pred = KronosPredictor(mdl, tok, device="cpu", max_context=512)
print(f"load {time.time()-t0:.1f}s")
files = sorted(p for p in (ROOT / "data" / "us").glob("*.csv") if not p.name.startswith("_"))[:int(sys.argv[1])]
dfs, xs, ys = [], [], []
for f in files:
    d = pd.read_csv(f, parse_dates=["Date"]).dropna().tail(420).reset_index(drop=True)
    d.columns = [c.lower() for c in d.columns]
    dfs.append(d.loc[:399, ["open", "high", "low", "close", "volume"]]); xs.append(d.loc[:399, "date"]); ys.append(d.loc[400:419, "date"])
for sc in (1, 5):
    t0 = time.time()
    out = pred.predict_batch(dfs, xs, ys, pred_len=20, T=1.0, top_p=0.9, sample_count=sc, verbose=False)
    print(f"{len(dfs)} shares x 20 days, sample_count {sc}: {time.time()-t0:.1f}s")
print(out[0].head(3))
