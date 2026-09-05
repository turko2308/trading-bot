"""
tools/compare3.py — Method 2 vs Method 3, head-to-head, clean methodology
============================================================================
Rebuilt 03/09/2026. Same ground for both: 0.75oz (REPORT_LOT_OZ), spread
0.77$, same H1-derived period, same cost-only slippage overlay (rule 29 --
signal generation runs once with zero cost, cost applied only as a $
deduction at close, never shifting entry/BE/pyramid/exit timing).

Method 2: clean_simulate_slow() from execution_slippage_consistency_test.py
Method 3: tf_engine_v2.py (4H with backup, matches live behavior)

Metrics: trades, win%, net P&L, Profit Factor, Max Drawdown, Sharpe-like
ratio (mean/std of per-trade $ pnl, NOT annualized -- see note).
"""
import sys, os, math
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import execution_slippage_consistency_test as m2
import tf_engine_v2 as m3
import trading_bot as tb


def max_drawdown(pnls_with_time):
    """pnls_with_time: list of (close_time, pnl), any order -- sorted here."""
    running = 0.0
    peak = 0.0
    dd = 0.0
    for _, pnl in sorted(pnls_with_time, key=lambda x: x[0]):
        running += pnl
        peak = max(peak, running)
        dd = max(dd, peak - running)
    return dd


def sharpe_like(pnls):
    """Mean/stdev of per-trade $ pnl. NOT annualized -- trade frequency
    differs hugely between methods (45 vs 413 trades/2.5y), so this is
    a per-trade risk-adjusted return, not a comparable Sharpe ratio in
    the classical sense. Reported for relative ranking only, per §20
    DSR/PBO cautions already on file -- do not over-interpret a single
    point estimate."""
    n = len(pnls)
    if n < 2:
        return None
    mean = sum(pnls) / n
    var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
    sd = math.sqrt(var)
    return mean / sd if sd > 1e-9 else None


def profit_factor(pnls):
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = sum(p for p in pnls if p <= 0)
    return gross_win / abs(gross_loss) if gross_loss != 0 else None


def run_method2(h4, slip):
    kw = dict(entry_days=tb.SLOW_ENTRY_DAYS, trail_days=tb.SLOW_TRAIL_DAYS,
              risk_ils=tb.SLOW_RISK_ILS, pyramid_units=tb.SLOW_PYRAMID_UNITS,
              pyramid_step_n=tb.SLOW_PYRAMID_STEP_N, be_trigger=tb.SLOW_BE_TRIGGER,
              be_offset=tb.SLOW_BE_OFFSET)
    r = m2.clean_simulate_slow(h4, extra_cost_points=slip, **kw)
    pnls = [t["pnl"] for t in r["detail"]]
    times = [(t["ct"], t["pnl"]) for t in r["detail"]]
    return {
        "trades": r["trades"], "win_rate": r["win_rate"], "pnl": r["pnl"],
        "pf": profit_factor(pnls), "max_dd": max_drawdown(times),
        "sharpe_like": sharpe_like(pnls),
    }


def run_method3(h1, all_4h, all_6h, slip, backed_only=True):
    sigs = [s for s in all_4h if s["backup"]] if backed_only else all_4h
    closed = m3.simulate_exits(sigs, h1, 4, slip)
    pnls = [c["pnl"] for c in closed]
    times = [(c["exit_t"], c["pnl"]) for c in closed]
    n = len(closed)
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trades": n, "win_rate": round(100 * wins / n, 1) if n else 0,
        "pnl": round(sum(pnls), 2),
        "pf": profit_factor(pnls), "max_dd": max_drawdown(times),
        "sharpe_like": sharpe_like(pnls),
    }


if __name__ == "__main__":
    h4 = m2.load_h4()
    h1 = m3.load_h1()

    bars_4h = m3.tf_aggregate(h1, 4)
    bars_6h = m3.tf_aggregate(h1, 6)
    ema4 = tb.calc_ema_series([b["c"] for b in bars_4h], m3.TF_EMA_PERIOD)
    ema6 = tb.calc_ema_series([b["c"] for b in bars_6h], m3.TF_EMA_PERIOD)
    atr4 = m3.tf_atr_series(bars_4h, m3.TF_ATR_PERIOD)
    atr6 = m3.tf_atr_series(bars_6h, m3.TF_ATR_PERIOD)
    sig4 = m3.generate_signals(bars_4h, ema4, atr4, "4H")
    sig6 = m3.generate_signals(bars_6h, ema6, atr6, "6H")
    m3.tag_backup(sig4, sig6)

    print(f"קרקע משותפת: 0.75oz, ספרד {tb.SPREAD_POINTS}$, מתודולוגיה נקייה (כלל 29)")
    print(f"Method 2: entry={tb.SLOW_ENTRY_DAYS} trail={tb.SLOW_TRAIL_DAYS} "
          f"BE={tb.SLOW_BE_TRIGGER}/+{tb.SLOW_BE_OFFSET} pyramid={tb.SLOW_PYRAMID_UNITS}x{tb.SLOW_PYRAMID_STEP_N}")
    print(f"Method 3: 4H עם גיבוי 6H בלבד (הקבוצה הרווחית, ר' §4.4/§21.6)")
    print()

    header = f"{'עלות':>6} | {'M':>2} {'עסקאות':>7} {'win%':>6} {'PnL':>8} {'PF':>6} {'MaxDD':>7} {'Sharpe~':>8}"
    print(header)
    print("-" * len(header))
    for slip in (0.0, 2.0, 3.0):
        a = run_method2(h4, slip)
        b = run_method3(h1, sig4, sig6, slip, backed_only=True)
        for label, r in (("2", a), ("3", b)):
            pf = f"{r['pf']:.2f}" if r['pf'] is not None else "—"
            sh = f"{r['sharpe_like']:.3f}" if r['sharpe_like'] is not None else "—"
            print(f"{slip:>5.0f}$ | {label:>2} {r['trades']:>7} {r['win_rate']:>5.1f}% "
                  f"{r['pnl']:>+8.0f} {pf:>6} {r['max_dd']:>7.0f} {sh:>8}")
        print()

    print("הערה: Sharpe~ הוא ממוצע/סטיית-תקן של P&L לעסקה בודדת, לא מנורמל")
    print("שנתי — התדירות שונה מאוד בין השיטות (45 מול 413 עסקאות/2.5 שנה),")
    print("לכן זה יחס תשואה-לסיכון פר-עסקה, לא Sharpe ratio קלאסי בר-השוואה")
    print("ישירה. לדירוג יחסי בלבד, לא כמספר עצמאי (ר' §20 אזהרות DSR/PBO).")
