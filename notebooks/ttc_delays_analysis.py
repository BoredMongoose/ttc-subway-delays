# ---
# jupyter:
#   jupytext:
#     text_representation:
#       extension: .py
#       format_name: percent
#   kernelspec:
#     display_name: Python 3
#     language: python
#     name: python3
# ---

# %% [markdown]
# # Toronto's subway delays nearly doubled. What's behind them?
#
# The TTC logs every incident on the subway: when, where, which code, and how many minutes it delayed service.
# This notebook works through 267,571 incidents from January 2014 to August 2026.
#
# **Short answer:**
# 1. **Delays nearly doubled.** The subway lost an average of **71,000 minutes a year** to delays in 2023–25,
#    up **85%** from 38,000 in 2014–16. Delays of 30 minutes or more went from about 92 a year to 223.
# 2. **Three-quarters of the increase involves passengers and the public:** disorderly behaviour, people on the
#    tracks, passenger alarms and medical emergencies. Disorderly behaviour alone went from 4,800 to 17,100 minutes a
#    year and is now the biggest single cause. **Train breakdowns fell 12%.**
# 3. **Line 1's new signal system worked.** After automatic train control (ATC) went live, Line 1 had about
#    **half the signal-failure delays** that Line 2's trend predicts (rate ratio 0.53, 95% CI 0.39–0.73).
# 4. **One-person train operation added a new kind of delay:** problems with the door-monitoring cameras cost
#    Line 1 about 3,600 minutes a year since 2022.
#
# Data: City of Toronto Open Data, TTC Subway Delay Data. Run `python src/fetch_data.py`,
# `python src/prepare_data.py` and `python src/run_sql.py` first. All modelling is in SQL (DuckDB): see `sql/`.

# %%
import sys
import warnings
from pathlib import Path

import duckdb
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from matplotlib.colors import LinearSegmentedColormap

warnings.filterwarnings("ignore", category=UserWarning)
ROOT = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(ROOT / "src"))
from style import (BLUE, BLUE_LIGHT, GRID, INK, INK_2, LINE_COLOURS, NEUTRAL, ORANGE, SURFACE, VIOLET,  # noqa: E402
                   footnote, save, titles)

con = duckdb.connect(str(ROOT / "data" / "ttc.duckdb"), read_only=True)
sql = lambda q: con.sql(q).df()
pd.set_option("display.precision", 1)
pd.set_option("display.width", 200)

# %% [markdown]
# ## 1. Data quality
#
# The raw data is 23 Excel sheets and one CSV, typed by hand over 12 years. The line field alone has
# **111 different spellings** ("YU/BD", "B/D", "YUS / BD", "BLOOR DANFORTH LINE 2", and bus routes typed into the
# subway log), and the location field has **2,221** ("ST GEORGE YUS STATION", "SCARBOROUGH CTR STATIO", typos like
# "GLENCARIN STATION"). `src/prepare_data.py` maps them to 5 line values and 75 stations, and records every mapping
# in `data/processed/station_mapping.csv`. The pipeline won't publish if any of the checks below fails.

# %%
sql("SELECT check_name, detail, passed FROM dq_results ORDER BY check_name")

# %%
sql("SELECT * FROM cleaning_log")

# %%
# how the free-text locations were resolved (rows = incidents)
sql("""
    SELECT location_type, method, COUNT(*) AS spellings, SUM(rows) AS incidents,
           ROUND(100 * SUM(rows) / SUM(SUM(rows)) OVER (), 2) AS pct_of_incidents
    FROM station_mapping GROUP BY ALL ORDER BY incidents DESC
""")

# %%
# a sample of the messiest spellings and where they went
sql("""
    SELECT location_raw, station, location_type, method, rows
    FROM station_mapping
    WHERE location_raw IN ('YONGE BD STATION', 'ST GEORGE YUS STATION', 'SCARB CTR STATION', 'GLENCARIN STATION',
                           'YUS/BD/SHEPPARD SUBWAY', 'UNION STATION TO KING', 'WILSON HOSTLER', 'TMU STATION',
                           'EGLINTON WEST STATION', 'VMC STATION PLATFORM 2')
    ORDER BY rows DESC
""")

# %% [markdown]
# **A note on the measure.** "Delay minutes" is the TTC's own `Min Delay` field: minutes of delay to subway
# service caused by an incident. 64% of logged incidents delay nothing (a passenger alarm with no trouble found, an
# incident at a yard), so most of the analysis adds up minutes rather than counting rows.

# %% [markdown]
# ## 2. Delays nearly doubled

# %%
yearly = sql("SELECT * FROM mart_yearly WHERE line_name = 'All lines' ORDER BY year")
yearly

# %%
base = yearly.query("2014 <= year <= 2016")
recent = yearly.query("2023 <= year <= 2025")
summary = pd.DataFrame({
    "2014-16 (avg year)": base[["delay_minutes", "delays", "disruptions_30min"]].mean(),
    "2023-25 (avg year)": recent[["delay_minutes", "delays", "disruptions_30min"]].mean(),
})
summary["change %"] = 100 * (summary.iloc[:, 1] / summary.iloc[:, 0] - 1)
summary.loc["minutes per delay"] = [base.delay_minutes.sum() / base.delays.sum(),
                                    recent.delay_minutes.sum() / recent.delays.sum(), np.nan]
summary.loc["minutes per delay", "change %"] = 100 * (summary.iloc[3, 1] / summary.iloc[3, 0] - 1)
summary.round(1)

# %% [markdown]
# Both things got worse: there are **more delays** (+57%) and each lasts **longer** (6.7 → 8.0 minutes on average).
#
# Is this just a bigger network? Line 1 grew by six stations in December 2017 and Line 3 closed in July 2023.
# Line 2 has had the same 31 stations the whole time, and its delay minutes still rose 65%:

# %%
by_line = sql("""
    SELECT line_name,
           ROUND(AVG(delay_minutes) FILTER (WHERE year BETWEEN 2014 AND 2016)) AS minutes_2014_16,
           ROUND(AVG(delay_minutes) FILTER (WHERE year BETWEEN 2023 AND 2025)) AS minutes_2023_25
    FROM mart_yearly WHERE line_name IN ('Line 1', 'Line 2', 'Line 4', 'All lines')
    GROUP BY line_name ORDER BY line_name
""")
by_line["change_pct"] = 100 * (by_line.minutes_2023_25 / by_line.minutes_2014_16 - 1)
by_line

# %%
monthly = sql("SELECT * FROM mart_monthly WHERE months_in_window = 12 ORDER BY month")
fig, ax = plt.subplots(figsize=(11, 5.6))
ax.plot(monthly.month, monthly.minutes_rolling_12m / 1000, color=BLUE, lw=2.4)
avg_base, avg_recent = base.delay_minutes.mean() / 1000, recent.delay_minutes.mean() / 1000
ax.hlines(avg_base, pd.Timestamp("2014-12-01"), pd.Timestamp("2016-12-31"), color=INK_2, lw=1.2, ls=(0, (4, 3)))
ax.hlines(avg_recent, pd.Timestamp("2023-01-01"), pd.Timestamp("2025-12-31"), color=INK_2, lw=1.2, ls=(0, (4, 3)))
ax.annotate(f"2014–16 average\n{avg_base:.0f}k minutes a year", (pd.Timestamp("2015-12-01"), avg_base),
            xytext=(0, -42), textcoords="offset points", ha="center", color=INK_2, fontsize=10)
ax.text(pd.Timestamp("2024-06-01"), 80.5,
        f"2023–25 average\n{avg_recent:.0f}k minutes a year (+{100 * (avg_recent / avg_base - 1):.0f}%)",
        ha="center", va="bottom", color=INK, fontsize=10, fontweight="bold")
for date, label, ha in [("2020-03-15", "COVID-19 ", "right"), ("2021-11-21", "One-person trains\non Line 1 ", "right"),
                        ("2022-09-24", " New signals on\n all of Line 1", "left")]:
    ax.axvline(pd.Timestamp(date), color=GRID, lw=1.2, zorder=0)
    ax.text(pd.Timestamp(date), 97, label, fontsize=8.8, color=INK_2, ha=ha, va="top",
            bbox=dict(fc=SURFACE, ec="none", pad=1))
ax.set_ylim(0, 100)
ax.set_ylabel("Delay minutes in the past 12 months (thousands)")
ax.xaxis.set_major_locator(mdates.YearLocator(2))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
titles(ax, "Toronto's subway delays have nearly doubled since 2014",
       "Minutes of subway delay in the previous 12 months, all lines, Dec 2014 – Aug 2026")
footnote(fig, "Source: TTC Subway Delay Data (City of Toronto Open Data). 267,571 logged incidents.", y=-0.02)
save(fig, "01_delays_trend.png")
plt.show()

# %% [markdown]
# ## 3. What changed: the causes
#
# Each incident has a TTC delay code (234 codes in use). I grouped them into 13 plain-English causes
# (`src/prepare_data.py`, `CAUSE`). 1,634 incidents (0.6%) use codes that appear in neither of the TTC's published
# code lists. Most are new in 2026; they were grouped by the code's first letter, which names the responsible
# department (S = security, T = transportation, E = equipment).

# %%
change = sql("SELECT * FROM mart_cause_change ORDER BY change DESC")
change

# %%
fam = change.groupby("cause_family")[["minutes_2014_16", "minutes_2023_25", "change"]].sum()
fam["share_of_net_increase_pct"] = 100 * fam.change / fam.change.sum()
fam.sort_values("change", ascending=False).round(1)

# %%
c = change.sort_values("change")
fig, ax = plt.subplots(figsize=(10.5, 6.6))
colours = [ORANGE if v > 0 else BLUE for v in c.change]
ax.barh(c.cause_group, c.change, color=colours, height=0.62)
for y, (v, pct) in enumerate(zip(c.change, c.change_pct)):
    label = f"{v:+,.0f}" + (f"  ({pct:+.0f}%)" if abs(pct) < 1000 else "  (new)")
    ax.text(v + (180 if v > 0 else -180), y, label, va="center", ha="left" if v > 0 else "right",
            fontsize=9.5, color=INK)
ax.axvline(0, color=INK_2, lw=1)
ax.set_xlim(-4200, 16500)
ax.grid(axis="y", visible=False)
ax.tick_params(axis="y", length=0)
ax.set_xlabel("Change in delay minutes per year")
ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
passenger = {"Disorderly behaviour & crime", "Alarms, doors & clean-ups", "People on the tracks", "Medical emergencies"}
for lab in ax.get_yticklabels():
    if lab.get_text() in passenger:
        lab.set_fontweight("bold")
titles(ax, "Passenger incidents drive three-quarters of the increase",
       "Change in average delay minutes per year, 2014–16 vs 2023–25. Bold: incidents involving passengers and the public")
footnote(fig, "Orange: more delay. Blue: less delay. Labels show the change and the % change.", y=-0.03)
save(fig, "02_what_changed.png")
plt.show()

# %%
cy = sql("SELECT * FROM mart_cause_year")
panels = ["Disorderly behaviour & crime", "People on the tracks", "Alarms, doors & clean-ups",
          "Medical emergencies", "Door cameras (one-person trains)", "Train equipment"]
fig, axes = plt.subplots(2, 3, figsize=(12, 6.4), sharex=True, sharey=True)
for ax, g in zip(axes.flat, panels):
    s = cy[cy.cause_group == g].set_index("year").delay_minutes.reindex(range(2014, 2026), fill_value=0) / 1000
    colour = BLUE if s.iloc[-3:].mean() < s.iloc[:3].mean() else ORANGE
    ax.fill_between(s.index, s.values, color=colour, alpha=0.12, lw=0)
    ax.plot(s.index, s.values, color=colour, lw=2.2)
    ax.set_title(g, fontsize=11.5, pad=6)
    ax.text(2025.3, s.iloc[-1], f"{s.iloc[-1]:.1f}k", ha="left", va="center", fontsize=9.5, color=INK)
    ax.set_xticks([2014, 2018, 2022, 2025])
    ax.set_xlim(2013.5, 2026.9)
axes[0, 0].set_ylabel("Delay minutes (thousands)")
axes[1, 0].set_ylabel("Delay minutes (thousands)")
fig.suptitle("Disorderly behaviour is now the subway's biggest cause of delay", x=0.01, ha="left",
             fontsize=15, fontweight="bold", y=1.03)
fig.text(0.01, 0.975, "Delay minutes per year by cause, 2014–2025. Train breakdowns (bottom right) are the one big cause that fell.",
         color=INK_2, fontsize=10.5)
fig.tight_layout()
save(fig, "03_causes_over_time.png")
plt.show()

# %% [markdown]
# Disorderly-behaviour delays peaked in 2023 (18,700 minutes) and fell 23% by 2025 (14,400). The city and the TTC
# added safety ambassadors, security guards and special constables in January 2023 after a string of violent
# incidents. The timing fits, but this data can't show that the extra staff caused the fall.
#
# The single biggest code is **SUDP, "disorderly patron"**, at 12% of all delay minutes in 2023–25:

# %%
sql("SELECT rank, code, description, cause_group, delay_minutes, delays, share_of_minutes_pct FROM mart_codes WHERE rank <= 12")

# %% [markdown]
# ## 4. Short incidents are common; long ones do the damage

# %%
sev = sql("SELECT * FROM mart_severity ORDER BY bucket_order")
sev

# %%
fig, ax = plt.subplots(figsize=(10, 5.2))
x = np.arange(len(sev))
ax.bar(x - 0.2, sev.share_of_incidents_pct, width=0.38, color=NEUTRAL, label="Share of incidents")
ax.bar(x + 0.2, sev.share_of_minutes_pct, width=0.38, color=BLUE, label="Share of delay minutes")
for i, (a, b) in enumerate(zip(sev.share_of_incidents_pct, sev.share_of_minutes_pct)):
    ax.text(i - 0.2, a + 1, f"{a:.1f}%", ha="center", fontsize=9.5, color=INK_2)
    ax.text(i + 0.2, b + 1, f"{b:.1f}%", ha="center", fontsize=9.5, color=INK, fontweight="bold")
ax.set_xticks(x, sev.bucket)
ax.set_ylim(0, 72)
ax.set_xlabel("How long the incident delayed service")
ax.grid(axis="x", visible=False)
ax.legend(loc="upper right")
titles(ax, "Most incidents delay nobody. 1 in 100 causes a fifth of all delay",
       "Logged incidents and the delay minutes they caused, by length of delay, 2023–25")
save(fig, "04_severity.png")
plt.show()

# %% [markdown]
# ## 5. Did Line 1's new signal system help?
#
# Line 1 switched from its old fixed-block signals to **automatic train control (ATC)** section by section from
# 2017, finishing on **24 September 2022**. Line 2 still runs on the old system, so it shows what probably would have
# happened to Line 1 without ATC (a *difference-in-differences* design).
#
# Outcome: signal-system failures (track circuits, train stops, signal and ATC equipment; not radios, cameras or
# weather) that delayed a train. Before = 2014–16, after = October 2022 – August 2026. The 2017–22 roll-out is left out.

# %%
sig = sql("SELECT * FROM mart_signal_monthly")
sig.groupby(["line_name", "atc_period"])[["signal_delays", "signal_minutes", "signal_events", "other_minutes"]].mean().round(1)

# %%
def did(outcome, control="Line 2"):
    """Poisson difference-in-differences on monthly counts; returns the Line 1 x after rate ratio and 95% CI."""
    x = sig[sig.line_name.isin(["Line 1", control]) & sig.atc_period.isin(["before", "after"])].copy()
    x["line1"] = (x.line_name == "Line 1").astype(int)
    x["after"] = (x.atc_period == "after").astype(int)
    fit = smf.glm(f"{outcome} ~ line1 * after", data=x, family=sm.families.Poisson()).fit(cov_type="HC1", scale="X2")
    lo, hi = fit.conf_int().loc["line1:after"]
    return pd.Series({"rate ratio": np.exp(fit.params["line1:after"]), "95% CI low": np.exp(lo),
                      "95% CI high": np.exp(hi), "p-value": fit.pvalues["line1:after"]})


results = pd.DataFrame({
    "signal delays vs Line 2": did("signal_delays"),
    "signal delay minutes vs Line 2": did("signal_minutes"),
    "all signal events vs Line 2": did("signal_events"),
    "signal delays vs Line 4": did("signal_delays", "Line 4"),
    "placebo: non-signal minutes vs Line 2": did("other_minutes"),
}).T
results.round(3)

# %% [markdown]
# - **Signal delays on Line 1 fell to about half (0.53×) of what Line 2's trend implies**, and the 95% interval
#   (0.39–0.73) excludes "no effect". Using Line 4 as the comparison instead gives 0.58.
# - **Placebo check:** if Line 1 had simply become better run in general, its *other* delays would have fallen too.
#   They rose faster than Line 2's (1.50×), so the improvement is specific to signals.
# - **Parallel trends:** before the upgrade, the two lines' signal delays moved together (test below).
# - Line 1 also gained six stations in 2017, built with ATC from the start. More track should mean more failures,
#   so if anything this understates the effect.

# %%
pre = sig[(sig.atc_period == "before") & sig.line_name.isin(["Line 1", "Line 2"])].copy()
pre["t"] = (pre.month.dt.year - 2014) * 12 + pre.month.dt.month
pre["line1"] = (pre.line_name == "Line 1").astype(int)
pt = smf.glm("signal_delays ~ line1 * t", data=pre, family=sm.families.Poisson()).fit(cov_type="HC1", scale="X2")
print(f"difference in monthly trend before the upgrade: {pt.params['line1:t']:+.4f} (p = {pt.pvalues['line1:t']:.2f})")

# %%
r = results.loc["signal delays vs Line 2"]
fig, ax = plt.subplots(figsize=(11, 5.4))
for line in ["Line 1", "Line 2"]:
    s = sig[sig.line_name == line].set_index("month").signal_delays.rolling(12).mean()
    ax.plot(s.index, s.values, color=LINE_COLOURS[line], lw=2.4)
    ax.text(s.index[-1] + pd.Timedelta(days=40), s.values[-1], line, color=LINE_COLOURS[line],
            fontweight="bold", va="center", fontsize=11)
ax.axvspan(pd.Timestamp("2017-01-01"), pd.Timestamp("2022-09-24"), color=GRID, alpha=0.45, lw=0)
ax.text(pd.Timestamp("2019-11-15"), 16.3, "ATC installed on Line 1,\nsection by section", ha="center",
        color=INK_2, fontsize=9.5, va="top")
ax.text(pd.Timestamp("2015-07-01"), 16.3, "Before", ha="center", color=INK_2, fontsize=10, va="top", fontweight="bold")
ax.text(pd.Timestamp("2024-09-01"), 16.3, "After", ha="center", color=INK_2, fontsize=10, va="top", fontweight="bold")
ax.text(pd.Timestamp("2024-09-01"), 0.5,
        f"After the upgrade, Line 1 had {100 * (1 - r['rate ratio']):.0f}% fewer\nsignal delays than Line 2's trend predicts\n"
        f"(95% CI {100 * (1 - r['95% CI high']):.0f}–{100 * (1 - r['95% CI low']):.0f}%)",
        ha="center", fontsize=9.6, color=INK, va="bottom", bbox=dict(fc=SURFACE, ec=GRID, boxstyle="round,pad=0.5"))
ax.set_ylim(0, 17)
ax.set_xlim(pd.Timestamp("2014-06-01"), pd.Timestamp("2027-04-01"))
ax.set_ylabel("Signal-failure delays per month")
ax.xaxis.set_major_locator(mdates.YearLocator(2))
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
titles(ax, "Line 1 went from more signal delays than Line 2 to fewer",
       "Signal-system failures that delayed a train, per month (12-month average). Line 2 kept the old signals")
footnote(fig, "Difference-in-differences, Poisson model on monthly counts, 2014–16 vs Oct 2022 – Aug 2026.", y=-0.02)
save(fig, "05_atc_signals.png")
plt.show()

# %% [markdown]
# Before the upgrade, Line 1 had *more* signal delays than Line 2 (12.3 vs 9.4 a month); since then it has had fewer
# (7.9 vs 11.3). The gap was widest in 2023. Both lines have been getting worse since, and Line 1's increase comes
# from the new system's own parts, mainly axle counters and track switches:

# %%
sql("""
    SELECT year(month) AS year, line_name, SUM(signal_delays) AS signal_delays, SUM(signal_minutes) AS signal_minutes
    FROM mart_signal_monthly
    WHERE month >= DATE '2023-01-01' AND month < DATE '2026-01-01' AND line_name IN ('Line 1', 'Line 2')
    GROUP BY ALL ORDER BY year, line_name
""")

# %%
sql("""
    SELECT year, description, COUNT(*) FILTER (WHERE is_delay) AS delays, SUM(min_delay) AS delay_minutes
    FROM incidents
    WHERE is_signal_failure AND line = '1' AND year BETWEEN 2023 AND 2025
    GROUP BY year, description QUALIFY ROW_NUMBER() OVER (PARTITION BY year ORDER BY SUM(min_delay) DESC) <= 3
    ORDER BY year, delay_minutes DESC
""")

# %% [markdown]
# The failure *types* changed completely. The old system's track circuits and train stops (trackside trip arms)
# almost never fail on Line 1 any more; ATC brought its own failures (axle counters, zone controllers).

# %%
codes = sql("""
    SELECT atc_period, code, description, events,
           events / CASE atc_period WHEN 'before' THEN 36 ELSE 47 END AS events_per_month
    FROM mart_signal_codes WHERE line_name = 'Line 1'
""")
names = {"PUSTS": "Train stops (trip arms)", "PUSI": "Signal failures", "PUSNT": "Signal problem, no fault found",
         "PUSO": "Other signal problems", "PUSTC": "Track circuits", "PUSSW": "Track switches",
         "PUSAC": "Axle counters (ATC)", "PUATC": "Other ATC signal problems", "PUCSC": "Signal control",
         "PUTTC": "Track-circuit bonding", "PUTSC": "Signal control (track)", "PUSZC": "Zone controllers (ATC)",
         "PUCSS": "Central signalling", "PUSIO": "Smart IO (ATC)"}
wide = codes.pivot_table(index="code", columns="atc_period", values="events_per_month", fill_value=0)
wide = wide[wide.max(axis=1) >= 0.25].copy()
wide["name"] = [names.get(c, c) for c in wide.index]
wide = wide.sort_values("before")
fig, ax = plt.subplots(figsize=(10, 6))
y = np.arange(len(wide))
ax.barh(y + 0.2, wide["before"], height=0.38, color=NEUTRAL, label="Before ATC (2014–16)")
ax.barh(y - 0.2, wide["after"], height=0.38, color=BLUE, label="After ATC (Oct 2022 – Aug 2026)")
for i, (b, a) in enumerate(zip(wide["before"], wide["after"])):
    ax.text(b + 0.08, i + 0.2, f"{b:.1f}", va="center", fontsize=9, color=INK_2)
    ax.text(a + 0.08, i - 0.2, f"{a:.1f}", va="center", fontsize=9, color=INK)
ax.set_yticks(y, wide["name"])
ax.tick_params(axis="y", length=0)
ax.grid(axis="y", visible=False)
ax.set_xlabel("Signal failures logged per month on Line 1")
ax.legend(loc="lower right")
titles(ax, "Old failure types vanished, and ATC brought its own",
       "Signal-system failures logged on Line 1 per month, by type, before and after the upgrade")
save(fig, "06_signal_failure_types.png")
plt.show()

# %% [markdown]
# ## 6. One-person trains: a new kind of delay
#
# With one-person train operation (OPTO), the guard at the back is gone and the operator watches the doors on
# camera screens. Line 4 switched in 2016; Line 1 started in November 2021 and was fully converted by late 2022.
# When the cameras or screens fail, trains are held. Those delays have their own code (PUOPO and related).

# %%
door = sql("SELECT * FROM mart_door_monitoring ORDER BY year, line_name")
door.pivot_table(index="year", columns="line_name", values="delay_minutes", fill_value=0)

# %%
dm = door[door.year <= 2025].pivot_table(index="year", columns="line_name", values="delay_minutes", fill_value=0)
dm = dm.reindex(range(2016, 2026), fill_value=0)
fig, ax = plt.subplots(figsize=(10, 5))
x = np.arange(len(dm))
ax.bar(x - 0.2, dm["Line 1"], width=0.38, color=BLUE)
ax.bar(x + 0.2, dm["Line 4"], width=0.38, color=VIOLET)
for i, v in enumerate(dm["Line 1"]):
    if v:
        ax.text(i - 0.2, v + 60, f"{v:,.0f}", ha="center", fontsize=9.5, color=INK)
ax.text(0.2, dm["Line 4"].iloc[0] + 60, "Line 4", ha="center", color=VIOLET, fontsize=10, fontweight="bold")
ax.text(5 - 0.2, dm["Line 1"].iloc[5] + 380, "Line 1", ha="center", color=BLUE, fontsize=10, fontweight="bold")
ax.annotate("Line 1 starts one-person\noperation, Nov 2021", (4.6, 900), xytext=(2.6, 3200),
            fontsize=9.5, color=INK_2, arrowprops=dict(arrowstyle="->", color=INK_2, lw=1))
ax.set_xticks(x, dm.index)
ax.grid(axis="x", visible=False)
ax.set_ylabel("Delay minutes per year")
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
titles(ax, "One-person trains brought a new kind of delay",
       "Delay minutes from door-monitoring camera and screen problems, by line, 2016–2025")
save(fig, "07_door_cameras.png")
plt.show()

# %% [markdown]
# Since 2022, Line 1 has lost **about 3,600 minutes a year** to door-camera problems: 5% of all subway delay minutes,
# and more than its signal failures after ATC. This doesn't say one-person operation was a bad trade (it frees up
# staff, and this analysis can't count the delays guards used to cause or prevent), but it's a cost worth tracking.

# %%
dm.loc[2022:2025, "Line 1"].mean()

# %% [markdown]
# ## 7. When and where

# %%
hw = sql("SELECT * FROM mart_hour_weekday")
order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
hours = [5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 0, 1]
grid = hw.pivot_table(index="weekday", columns="hour", values="minutes_per_day").reindex(index=order, columns=hours)
fig, ax = plt.subplots(figsize=(12, 4.8))
cmap = LinearSegmentedColormap.from_list("blues", ["#f4f8fd", BLUE_LIGHT, "#5b9be3", BLUE, "#123f78"])
vmax = 16                                       # one cell (Sunday 8am) reaches 22; capping keeps the rest readable
im = ax.imshow(grid.values, aspect="auto", cmap=cmap, vmin=0, vmax=vmax)
ax.set_xticks(range(len(hours)), [f"{h % 12 or 12}{'am' if h < 12 else 'pm'}" for h in hours], fontsize=9)
ax.set_yticks(range(7), [d[:3] for d in order])
ax.grid(False)
for spine in ax.spines.values():
    spine.set_visible(False)
sun8 = (order.index("Sunday"), hours.index(8))
ax.add_patch(plt.Rectangle((sun8[1] - 0.5, sun8[0] - 0.5), 1, 1, fill=False, ec=INK, lw=1.6))
cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01, extend="max")
cb.set_label("Delay minutes per hour, average day", color=INK_2)
cb.outline.set_visible(False)
titles(ax, "Delays peak when service starts and in the afternoon rush",
       "Average delay minutes in each hour of the day, 2023–25. Outlined: Sunday 8am, when Sunday service starts, "
       "the worst hour of the week")
save(fig, "08_when.png")
plt.show()

# %% [markdown]
# The first hour of service (5–6am on weekdays and Saturdays, 7–8am on Sundays) fails for different reasons from the
# rest of the day: trains, track, crews and overnight work that isn't finished on time, rather than passengers.

# %%
sql("""
    WITH x AS (
        SELECT min_delay,
               CASE WHEN (weekday = 'Sunday' AND hour IN (7, 8)) OR (weekday <> 'Sunday' AND hour IN (5, 6))
                         THEN 'first hour of service'
                    WHEN weekday NOT IN ('Saturday', 'Sunday') AND hour BETWEEN 15 AND 18 THEN 'weekday 3-7pm'
                    ELSE 'rest of the day' END AS slot,
               CASE WHEN cause_group IN ('Disorderly behaviour & crime', 'Medical emergencies', 'People on the tracks',
                                         'Alarms, doors & clean-ups') THEN 'passengers & public'
                    WHEN cause_group IN ('Fire & smoke', 'Weather', 'Other & unclassified') THEN 'other'
                    ELSE 'trains, track, crews & works' END AS who
        FROM incidents WHERE year BETWEEN 2023 AND 2025
    )
    SELECT slot, who, SUM(min_delay) AS delay_minutes,
           ROUND(100 * SUM(min_delay) / SUM(SUM(min_delay)) OVER (PARTITION BY slot), 1) AS pct_of_slot
    FROM x GROUP BY slot, who ORDER BY slot, delay_minutes DESC
""")

# %%
st = sql("SELECT * FROM mart_station WHERE rank <= 15 ORDER BY rank DESC")
fig, ax = plt.subplots(figsize=(10, 6.4))
ax.barh(st.station, st.minutes_per_year, color=BLUE, height=0.62)
short = {"Disorderly behaviour & crime": "disorderly behaviour", "People on the tracks": "people on the tracks",
         "Signals & communications": "signals", "Weather": "weather", "Crew & operations": "crew & operations",
         "Train equipment": "train equipment", "Fire & smoke": "fire & smoke", "Medical emergencies": "medical"}
for y, (v, cause, share) in enumerate(zip(st.minutes_per_year, st.top_cause, st.top_cause_share_pct)):
    ax.text(v + 30, y, f"{v:,.0f}   top cause: {short.get(cause, cause.lower())} ({share:.0f}%)",
            va="center", fontsize=9.3, color=INK)
ax.set_xlim(0, st.minutes_per_year.max() * 1.62)
ax.grid(axis="y", visible=False)
ax.tick_params(axis="y", length=0)
ax.set_xlabel("Delay minutes per year, 2023–25")
ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
titles(ax, "The interchanges and the ends of the lines lose the most time",
       "15 stations with the most delay minutes, 2023–25 average, and their most common cause")
save(fig, "09_stations.png")
plt.show()
print("top cause is disorderly behaviour at", (st.top_cause == "Disorderly behaviour & crime").sum(), "of the top 15")

# %% [markdown]
# Bloor-Yonge and St George (where Lines 1 and 2 meet), Kennedy, Kipling and Finch (the ends of the lines) top the
# list, and disorderly behaviour is the leading cause at 12 of the top 15. Line ends collect incidents partly because
# trains are checked and cleared there, so this ranks where incidents are *logged*, not necessarily where they begin.

# %% [markdown]
# ## 8. Limitations
#
# - **Delay minutes measure service, not passengers.** A 10-minute delay at 8am affects far more riders than one at
#   11pm. The data has no ridership.
# - **Logging can change.** Some of the rise in disorderly-behaviour incidents may be better reporting (the city and TTC added
#   safety staff in January 2023 after a string of violent incidents), and new codes appeared in 2026.
# - **The ATC comparison assumes Line 2 shows what Line 1 would have done without the upgrade.** The two lines
#   tracked each other before the upgrade, which supports this, but it can't be proven.
# - **Line ends and interchanges** log incidents that may have started elsewhere on the line.
# - 2026 covers January to August only and is left out of yearly comparisons.
