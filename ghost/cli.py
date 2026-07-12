"""``ghost`` / ``revenant`` CLI — the operator's entry point.

    ghost run --seller ring-ai --limit 3     # full loop, offline by default
    ghost onboard "we sell ..."              # dictated blurb → new seller
    ghost sellers                            # list built-in configs
    ghost approve <campaign_id> --to a@b.com # send an awaiting_review campaign
"""

from __future__ import annotations

import typer
from rich.console import Console

from . import outreach
from .config import settings
from .ledger import ledger
from .models import CampaignState
from .pipeline import run_seller
from .sellers import get_seller, list_sellers
from .sellers import onboard as onboard_seller

app = typer.Typer(add_completion=False, help="Revenant — the autonomous outbound engineer.")
console = Console()


@app.command()
def run(
    seller: str = typer.Option("ring-ai", help="Seller config slug (see `ghost sellers`)."),
    limit: int = typer.Option(3, help="Max leads to process."),
):
    """Run the full hunt → review loop for a seller."""
    console.print(f"[dim]mode={settings.mode} dry_run={settings.dry_run}[/dim]")
    try:
        cfg = get_seller(seller)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)
    run_seller(cfg, limit=limit)
    console.print("\n[dim]ledger → out/ledger.json  ·  sites → out/sites/[/dim]")


@app.command()
def onboard(
    blurb: str = typer.Argument(..., help="Dictated company description."),
    slug: str = typer.Option("custom", help="Slug for the new seller."),
    limit: int = typer.Option(3),
):
    """Onboard a new seller from a free-text blurb, then run the loop."""
    cfg = onboard_seller(blurb, slug=slug)
    console.print(f"[green]Onboarded[/green] {cfg.name} — hunting: {', '.join(cfg.pain_keywords)}")
    run_seller(cfg, limit=limit)


@app.command()
def sellers():
    """List built-in seller configs."""
    for s in list_sellers():
        cfg = get_seller(s)
        console.print(f"  [bold]{s}[/bold] — {cfg.one_liner}")


@app.command()
def approve(
    campaign_id: str = typer.Argument(...),
    to: str = typer.Option(..., "--to", help="Recipient (team-owned inbox during buildathon)."),
):
    """Approve & send an awaiting_review campaign (honors DRY_RUN)."""
    match = [c for c in ledger.campaigns() if c.id == campaign_id]
    if not match:
        console.print(f"[red]no campaign {campaign_id} in this process's ledger[/red]")
        raise typer.Exit(1)
    camp = match[0]
    sent = outreach.send(camp, to)
    if sent:
        ledger.set_state(camp, CampaignState.SENT)
        console.print(f"[green]sent[/green] → {to}")
    else:
        console.print("[yellow]not sent (DRY_RUN or missing key)[/yellow]")


if __name__ == "__main__":
    app()
