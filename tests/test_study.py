import math
import pandas as pd
from tbot import stages, study

from test_engine import scenario


def test_windows_cover_ten_years_without_gaps():
    s, e = pd.Timestamp("2016-10-08"), pd.Timestamp("2026-10-07")
    w1, w2 = study.windows(s, e, 1), study.windows(s, e, 2)
    assert len(w1) == 10 and len(w2) == 5
    assert w1[0][0] == "2016-10-08" and w1[-1][1] == "2026-10-07"
    for a, b in zip(w2, w2[1:]):
        assert pd.Timestamp(b[0]) - pd.Timestamp(a[1]) == pd.Timedelta(days=1)


def test_score_ignores_thin_or_losing_results():
    good = {"trades": 80, "pf": 1.3, "ret_dd": 2.0}
    assert study.score(good) == 2.0
    assert study.score(dict(good, trades=5)) == float("-inf")
    assert study.score(dict(good, pf=0.9)) == float("-inf")


def test_bundle_compare():
    f = {"trades": 10, "ret": 0.05, "dd": -0.10}
    h = {"trades": 8, "ret": 0.05, "dd": -0.05}
    assert study.score_cmp(f, h) == "hier" and study.score_cmp(h, f) == "flat"


def test_bundle_compare_with_losses():
    flat = {"trades": 9, "ret": -0.121, "dd": -0.139}
    hier = {"trades": 5, "ret": -0.044, "dd": -0.047}
    assert study.score_cmp(flat, hier) == "hier"            # smaller loss and smaller drop wins


def test_a_test_saves_where_it_started_even_if_the_world_changes(monkeypatch, us_cfg, tmp_path):
    """A test started in the time machine once finished after Exit and overwrote the real result."""
    from tbot import config
    frames = scenario(n=2700)
    monkeypatch.setattr(study, "evidence_cfg", lambda m: us_cfg)
    monkeypatch.setattr(study, "load_market", lambda m: us_cfg)
    monkeypatch.setattr(study, "load_events", lambda: {"us": set()})
    monkeypatch.setattr(study.wf, "load_frames", lambda cfg, start, offline=False: frames)
    start_dir, real = config.RUNS_DIR / "timemachine", config.RUNS_DIR
    monkeypatch.setattr(config, "RUNS_DIR", start_dir)
    run_once = study.wf.run_once
    monkeypatch.setattr(study.wf, "run_once", lambda *a, **k: (setattr(config, "RUNS_DIR", real), run_once(*a, **k))[1])
    study.passport("us")
    assert (start_dir / "passport" / "us.json").exists() and not (real / "passport" / "us.json").exists()


def test_pass_marks_decide_the_verdict():
    ok = {"trades": 330, "profit_factor": 1.31, "max_drawdown": -0.09}
    assert stages.judge(ok)["passed"]
    assert not stages.judge(dict(ok, profit_factor=1.23))["passed"]            # profit factor under 1.3
    assert not stages.judge(dict(ok, max_drawdown=-0.12))["passed"]            # drop over 10%
    assert not stages.judge(dict(ok, trades=10))["passed"]                      # too few trades to judge
    assert not stages.judge(dict(ok, profit_factor=float("nan")))["passed"]
    assert not stages.judge(None)["passed"]


def test_passport_tests_the_live_settings_and_saves_them(monkeypatch, us_cfg):
    frames = scenario(n=2700)                            # about ten years of days
    monkeypatch.setattr(study, "evidence_cfg", lambda m: us_cfg)
    monkeypatch.setattr(study, "load_market", lambda m: us_cfg)
    monkeypatch.setattr(study, "load_events", lambda: {"us": set()})
    monkeypatch.setattr(study.wf, "load_frames", lambda cfg, start, offline=False: frames)
    p = study.passport("us")
    assert len(p["parts"]) == 5 and p["shares"] == 2 and p["years_test"] == 5
    assert p["fingerprint"] == stages.fingerprint(us_cfg)
    assert p["passed"] is False                          # a handful of trades proves nothing
    assert stages.load_passport("us")["test"]["trades"] == p["test"]["trades"]
