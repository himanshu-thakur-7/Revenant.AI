"""`revenant-eval` — CLI entry point for the T1 deterministic check suite.

Usage:
    revenant-eval check --merchant Meesho        # from a saved/live bundle
    revenant-eval check --merchant Meesho --from-disk   # reconstruct from out/
    revenant-eval list-bundles
"""

from __future__ import annotations

import sys

import typer

from evals.bundle import BUNDLES_DIR, Bundle, from_disk, slug
from evals.runner import artifact_pass, run_t1, summarize

app = typer.Typer(add_completion=False)


@app.command()
def check(merchant: str = typer.Option(..., "--merchant", "-m"),
          from_disk_: bool = typer.Option(False, "--from-disk",
                                          help="Reconstruct the bundle from out/ + "
                                               "~/.revenant/last_campaign.json instead "
                                               "of a saved evals bundle.")):
    """Run the T1 deterministic checks for one merchant's bundle."""
    b = from_disk(merchant) if from_disk_ else Bundle.load(slug(merchant))
    claimed = b.artifacts()
    if not any(claimed.values()):
        typer.echo(f"No artifacts found for '{merchant}' (bundle_id={b.bundle_id}). "
                   f"Nothing to check.")
        raise typer.Exit(2)

    results = run_t1(b)
    typer.echo(summarize(results))
    all_pass = all(artifact_pass(c) for c in results.values())
    typer.echo(f"\n{'PASS' if all_pass else 'FAIL'} — {b.bundle_id}")
    raise typer.Exit(0 if all_pass else 1)


@app.command()
def score(merchant: str = typer.Option(..., "--merchant", "-m"),
         from_disk_: bool = typer.Option(False, "--from-disk"),
         accept: bool = typer.Option(False, "--accept-baseline",
                                     help="Write this run as the new baseline for this golden.")):
    """T1 + T2 (LLM judge, gated behind T1) for one merchant's bundle."""
    from evals.report import accept_baseline, diff_against_baseline, render_report, save_report
    from evals.runner import bundle_pass, score_bundle, score_summary

    b = from_disk(merchant) if from_disk_ else Bundle.load(slug(merchant))
    if not any(b.artifacts().values()):
        typer.echo(f"No artifacts found for '{merchant}' (bundle_id={b.bundle_id}).")
        raise typer.Exit(2)

    scored = score_bundle(b)
    typer.echo(score_summary(scored))

    golden_id = slug(merchant)
    regressions = diff_against_baseline("agents_fleet", golden_id, scored)
    if regressions:
        typer.echo("\n⚠ Regressions vs baseline:")
        for r in regressions:
            typer.echo(f"  - {r}")

    report_path = save_report(golden_id, render_report(golden_id, scored, regressions))
    typer.echo(f"\nReport saved: {report_path}")

    if accept:
        accept_baseline("agents_fleet", golden_id, scored)
        typer.echo(f"Baseline accepted for '{golden_id}'.")

    ok = bundle_pass(scored) and not regressions
    typer.echo(f"\n{'PASS' if ok else 'FAIL'} — {b.bundle_id}")
    raise typer.Exit(0 if ok else 1)


@app.command()
def calibrate():
    """Run the labeled judge-calibration set (evals/golden/labeled/) —
    confirms the LLM judge still separates a real build from a
    deliberately generic one. Costs a handful of live API calls."""
    from evals.judge import calibrate as _calibrate

    ok, report = _calibrate()
    typer.echo(report)
    typer.echo(f"\n{'PASS' if ok else 'FAIL'} — judge calibration")
    raise typer.Exit(0 if ok else 1)


@app.command("list-bundles")
def list_bundles():
    """List every saved bundle id."""
    if not BUNDLES_DIR.exists():
        typer.echo("(no bundles recorded yet)")
        return
    for p in sorted(BUNDLES_DIR.glob("*.json")):
        typer.echo(p.stem)


if __name__ == "__main__":
    app()
