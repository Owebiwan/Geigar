"""Daily, monthly and yearly metrics from a backtest (BT-2) and special days (BT-4)."""

from __future__ import annotations

import pandas as pd

from bdo.config import Config

_SUMS = ["revenue_eur", "cash_flow_eur", "degradation_eur", "charge_kwh", "discharge_kwh", "cycles",
         "energy_eur", "fcr_eur", "afrr_up_eur", "afrr_dn_eur", "reserve_wear_eur"]
_MEANS = ["fcr_kw", "afrr_up_kw", "afrr_dn_kw"]


def monthly(daily: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    d = daily.assign(month=daily["date"].str[:7])
    g = d.groupby(["month", "strategy"], sort=True)
    out = g[_SUMS].sum()
    out["peak_import_kw"] = g["peak_import_kw"].max()
    out["spread_captured_eur_mwh"] = g["spread_captured_eur_mwh"].mean()
    out[_MEANS] = g[_MEANS].mean()
    out = out.reset_index()
    idle_peak = out[out["strategy"] == "idle"].set_index("month")["peak_import_kw"]
    # Peak shaving only means something behind the meter of a site with its own load.
    reduction = out["month"].map(idle_peak) - out["peak_import_kw"]
    out["peak_reduction_kw"] = reduction if cfg.site.has_load else 0.0
    out["peak_savings_eur"] = (
        out["peak_reduction_kw"] * cfg.site.demand_charge_eur_per_kw_year / 12.0
    )
    out["total_eur"] = out["revenue_eur"] + out["peak_savings_eur"]
    return out


def yearly(monthly_df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    g = monthly_df.groupby("strategy", sort=True)
    out = g[[*_SUMS, "peak_savings_eur", "total_eur"]].sum()
    out["peak_reduction_kw"] = g["peak_reduction_kw"].mean()
    out["spread_captured_eur_mwh"] = g["spread_captured_eur_mwh"].mean()
    out[_MEANS] = g[_MEANS].mean()
    out["eur_per_kwh_capacity"] = out["total_eur"] / cfg.battery.capacity_kwh
    out["eur_per_mwh_capacity"] = out["eur_per_kwh_capacity"] * 1000.0
    return out.reset_index()


def headline(yearly_df: pd.DataFrame) -> dict[str, float | None]:
    """Headline numbers. A ratio is None when its base is not positive, e.g. when the
    peak-blind naive rule loses money on a site with a demand charge."""
    total = {str(k): float(v) for k, v in yearly_df.set_index("strategy")["total_eur"].items()}
    opt_row = yearly_df[yearly_df["strategy"] == "optimizer"].iloc[0]
    opt, naive, pers = total["optimizer"], total["naive"], total["persistence"]
    return {
        "optimizer_eur": opt,
        "naive_eur": naive,
        "persistence_eur": pers,
        "uplift_vs_naive_pct": (opt / naive - 1.0) * 100.0 if naive > 0 else None,
        "uplift_vs_naive_eur": opt - naive,
        "persistence_share_pct": pers / opt * 100.0 if opt > 0 else None,
        "eur_per_mwh_capacity": float(opt_row["eur_per_mwh_capacity"]),
        "cycles": float(opt_row["cycles"]),
        "peak_savings_eur": float(opt_row["peak_savings_eur"]),
        "peak_reduction_kw": float(opt_row["peak_reduction_kw"]),
    }


def markets(yearly_df: pd.DataFrame) -> dict[str, float | None] | None:
    """Revenue of the multi-market strategy by source, against day-ahead only."""
    if "multi_market" not in set(yearly_df["strategy"]):
        return None
    multi = yearly_df[yearly_df["strategy"] == "multi_market"].iloc[0]
    base = float(yearly_df[yearly_df["strategy"] == "optimizer"].iloc[0]["total_eur"])
    total = float(multi["total_eur"])
    out: dict[str, float | None] = {
        "total_eur": total,
        "day_ahead_only_eur": base,
        "uplift_vs_day_ahead_pct": (total / base - 1.0) * 100.0 if base > 0 else None,
    }
    for key in ("energy_eur", "degradation_eur", "fcr_eur", "afrr_up_eur", "afrr_dn_eur",
                "reserve_wear_eur", "peak_savings_eur", "fcr_kw", "afrr_up_kw", "afrr_dn_kw",
                "cycles"):
        out[key] = float(multi[key])
    return out


def special_days(daily: pd.DataFrame) -> list[dict[str, object]]:
    wide = daily.pivot(index="date", columns="strategy", values="revenue_eur")
    opt = daily[daily["strategy"] == "optimizer"].set_index("date")
    spread = opt["price_max"] - opt["price_min"]
    picks = [
        ("MOST NEGATIVE PRICE", opt["price_min"], str(opt["price_min"].idxmin()), "EUR/MWh"),
        ("HIGHEST SPREAD", spread, str(spread.idxmax()), "EUR/MWh"),
        ("WORST DAY FOR NAIVE RULE", wide["naive"], str(wide["naive"].idxmin()), "EUR"),
        ("BEST DAY FOR OPTIMIZER", wide["optimizer"], str(wide["optimizer"].idxmax()), "EUR"),
    ]
    return [
        {
            "label": label,
            "date": day,
            "value": float(series[day]),
            "unit": unit,
            "optimizer_eur": float(wide["optimizer"][day]),
            "naive_eur": float(wide["naive"][day]),
            "price_min": float(opt["price_min"][day]),
            "price_max": float(opt["price_max"][day]),
        }
        for label, series, day, unit in picks
    ]
