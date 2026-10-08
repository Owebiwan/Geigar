"""Command line entry point: bdo fetch | optimize | backtest | sweep | sim | control | serve."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from bdo.config import load_config
from bdo.timeutil import BERLIN, day_bounds_utc

app = typer.Typer(add_completion=False, no_args_is_help=True)
ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Path to the YAML config")]


def _day(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return (datetime.now(BERLIN) + timedelta(days=1)).date()


@app.command()
def fetch(start: str, end: str, config: ConfigOpt = None) -> None:
    """Fetch and cache day-ahead prices for the Berlin days START..END (inclusive)."""
    from bdo.data.prices import ensure_prices, resolve_source
    from bdo.data.quality import check_prices

    cfg = load_config(config)
    a, _ = day_bounds_utc(date.fromisoformat(start))
    _, b = day_bounds_utc(date.fromisoformat(end))
    prices = ensure_prices(cfg, a, b)
    report = check_prices(prices, a, b)
    typer.echo(f"source={resolve_source(cfg)} rows={report.rows} gaps={len(report.gaps)} "
               f"duplicates={len(report.duplicates)}")
    for gap_start, gap_end in report.gaps:
        typer.echo(f"  gap {gap_start.isoformat()} -> {gap_end.isoformat()}")


@app.command()
def optimize(day: Annotated[str | None, typer.Argument()] = None, config: ConfigOpt = None,
             soc: float | None = None) -> None:
    """Optimise one delivery day (default: tomorrow) and print the schedule."""
    from bdo.data.prices import ensure_prices
    from bdo.optimizer.rolling import build_inputs, plan_day

    cfg = load_config(config)
    target = _day(day)
    a, b = day_bounds_utc(target)
    inputs = build_inputs(cfg, ensure_prices(cfg, a, b), target)
    if inputs is None:
        typer.echo(f"no complete price data for {target}", err=True)
        raise typer.Exit(1)
    schedule = plan_day(cfg, inputs, cfg.battery.soc_start if soc is None else soc)
    frame = schedule.frame.assign(local=schedule.frame["ts_utc"].dt.tz_convert(BERLIN))
    cols = ["local", "price_eur_mwh", "power_kw", "soc", "cash_flow_eur"]
    with pd.option_context("display.max_rows", None, "display.width", 160):
        typer.echo(frame[cols].round(3).to_string(index=False))
    typer.echo(f"cash flow {schedule.cash_flow_eur:.2f} EUR, solved in "
               f"{schedule.solve_seconds * 1000:.0f} ms")


@app.command()
def backtest(year: int = 2025, config: ConfigOpt = None,
             duration: Annotated[float | None, typer.Option(
                 help="Capacity as hours at full power (dashboard 1h/2h/4h)")] = None,
             markets: Annotated[bool, typer.Option(
                 help="Also sell FCR and aFRR capacity (needs ENTSOE_TOKEN)")] = False,
             ) -> None:
    """Replay a full year and write CSV, JSON and an HTML report."""
    from bdo.backtest.report import uplift_text
    from bdo.backtest.results import run_and_save
    from bdo.backtest.sweep import scenario, with_markets

    cfg = load_config(config)
    if duration is not None:
        cfg = scenario(cfg, duration, cfg.battery.round_trip_efficiency)
    if markets:
        cfg = with_markets(cfg)
    summary, out = run_and_save(cfg, year)
    h = summary["headline"]
    typer.echo(f"{year}: optimizer {h['optimizer_eur']:,.0f} EUR, naive {h['naive_eur']:,.0f} EUR, "
               f"uplift {uplift_text(h['uplift_vs_naive_pct'])}, "
               f"{h['eur_per_mwh_capacity']:,.0f} EUR per MWh capacity")
    typer.echo(f"violations={len(summary['violations'])} "
               f"skipped_days={len(summary['skipped_days'])} config={summary['config_hash']}")
    m = summary["markets"]
    if m:
        typer.echo(f"multi-market {m['total_eur']:,.0f} EUR "
                   f"({m['uplift_vs_day_ahead_pct']:+.0f}% vs day-ahead only, "
                   f"{m['eur_per_mw_year']:,.0f} EUR per MW-year): energy {m['energy_eur']:,.0f}, "
                   f"FCR {m['fcr_eur']:,.0f}, aFRR up {m['afrr_up_eur']:,.0f}, "
                   f"aFRR down {m['afrr_dn_eur']:,.0f}, wear "
                   f"-{m['degradation_eur'] + m['reserve_wear_eur']:,.0f}")
    typer.echo(f"written to {out}")
    if summary["violations"]:
        raise typer.Exit(1)


@app.command()
def init(year: int = 2025, config: ConfigOpt = None, durations: str = "1,2,4",
         with_forecast: Annotated[bool, typer.Option(
             envvar="BDO_INIT_FORECAST",
             help="Also train and score the price forecaster (downloads five more years)")] = False,
         ) -> None:
    """First-start job: cache prices and run the backtests the dashboard shows, if missing."""
    import os

    from bdo.backtest.report import uplift_text
    from bdo.backtest.results import load_run, run_and_save
    from bdo.backtest.sweep import scenario, with_markets

    cfg = load_config(config)
    scenarios = [cfg] + [scenario(cfg, float(d), cfg.battery.round_trip_efficiency)
                         for d in durations.split(",")]
    for sc in {s.hash(): s for s in scenarios}.values():
        if load_run(sc, year) is None:
            summary, out = run_and_save(sc, year)
            uplift = uplift_text(summary["headline"]["uplift_vs_naive_pct"])
            typer.echo(f"backtest {out.name}: {uplift} vs naive, "
                       f"{len(summary['violations'])} violations")
        else:
            typer.echo(f"backtest {year}-{sc.hash()}: cached")
        # Reserve prices only exist on ENTSO-E, so the multi-market run needs the token.
        if os.environ.get("ENTSOE_TOKEN") and load_run(with_markets(sc), year) is None:
            try:
                mm, _ = run_and_save(with_markets(sc), year)
                typer.echo(f"markets {year}-{sc.hash()}: "
                           f"{mm['markets']['uplift_vs_day_ahead_pct']:+.0f}% vs day-ahead only")
            except Exception as exc:  # noqa: BLE001 - v1 results must not depend on this
                typer.echo(f"markets {year}-{sc.hash()}: skipped ({exc})", err=True)
        if with_forecast:
            from bdo.forecast import evaluate

            if evaluate.load_run(sc, year) is None:
                fc, _ = evaluate.run_and_save(sc, year)
                share = next(r["share_of_optimizer_pct"] for r in fc["strategies"]
                             if r["strategy"] == evaluate.MAIN)
                typer.echo(f"forecast {year}-{sc.hash()}: {share:.1f}% of perfect foresight")


@app.command()
def forecast(year: int = 2025, config: ConfigOpt = None,
             duration: Annotated[float | None, typer.Option(
                 help="Capacity as hours at full power (dashboard 1h/2h/4h)")] = None,
             ) -> None:
    """Train the price forecaster walk-forward and score it in EUR against the baselines."""
    from bdo.backtest.sweep import scenario
    from bdo.forecast.evaluate import run_and_save

    cfg = load_config(config)
    if duration is not None:
        cfg = scenario(cfg, duration, cfg.battery.round_trip_efficiency)
    summary, out = run_and_save(cfg, year)
    typer.echo(f"{'strategy':<24}{'MAE':>8}{'shape':>8}{'EUR':>10}{'% of perfect':>14}")
    for row in summary["strategies"]:
        mae = "" if row["mae"] is None else f"{row['mae']:.2f}"
        shape = "" if row["shape_corr"] is None else f"{row['shape_corr']:.3f}"
        typer.echo(f"{row['strategy']:<24}{mae:>8}{shape:>8}{row['total_eur']:>10,.0f}"
                   f"{row['share_of_optimizer_pct']:>13.1f}%")
    for quarter, shares in summary["quarterly_share_pct"].items():
        typer.echo(f"  {quarter}: " + ", ".join(f"{k} {v:.1f}%" for k, v in shares.items()))
    typer.echo(f"{summary['days']} days to {summary['last_day']}, "
               f"violations={len(summary['violations'])}, written to {out}")


@app.command()
def registers() -> None:
    """Print the Modbus register map as Markdown (source of docs/register-map.md)."""
    from bdo.device.registers import markdown

    typer.echo(markdown())


@app.command()
def sweep(year: int = 2025, durations: str = "1,2,4", efficiencies: str = "0.85,0.90,0.95",
          config: ConfigOpt = None) -> None:
    """Compare battery durations and round-trip efficiencies in one run."""
    from bdo.backtest.sweep import run_sweep

    table = run_sweep(load_config(config), year, [float(x) for x in durations.split(",")],
                      [float(x) for x in efficiencies.split(",")])
    cols = ["duration_h", "round_trip_efficiency", "optimizer_eur", "naive_eur",
            "uplift_vs_naive_pct", "eur_per_mwh_capacity", "cycles", "violations"]
    typer.echo(table[cols].round(2).to_string(index=False))


@app.command()
def sim(config: ConfigOpt = None, host: str = "0.0.0.0", speed: float = 1.0) -> None:
    """Run the simulated battery inverter as a Modbus TCP server."""
    from bdo.device.simulator import serve

    asyncio.run(serve(load_config(config), host=host, speed=speed))


@app.command()
def control(config: ConfigOpt = None, speed: float = 1.0, day: str | None = None,
            host: str = "0.0.0.0", port: int = 8100) -> None:
    """Run the dispatch controller. With --speed > 1 a chosen day is replayed in a loop."""
    from bdo.device.service import run

    run(load_config(config), speed=speed, day=date.fromisoformat(day) if day else None,
        host=host, port=port)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the public API and the daily price job."""
    import uvicorn

    uvicorn.run("bdo.api.main:app", host=host, port=port)


if __name__ == "__main__":
    app()
