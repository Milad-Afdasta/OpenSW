"""Regenerate cost tables and figures from docs/cost-assumptions.json.

Run: uv run --with matplotlib python tools/render_cost_charts.py
"""
from pathlib import Path
from datetime import date
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "docs/cost-assumptions.json").read_text())
OUT = ROOT / "assets/charts"
OUT.mkdir(parents=True, exist_ok=True)
PAID = sum(plan["per_seat_month"] for plan in DATA["reference_plans"]) * DATA["paid_seats"] * 12
INFRA = sum(DATA["illustrative_hosting_month"].values()) * 12
ADMIN = DATA["admin_hours_month"] * DATA["admin_hourly_rate"] * 12
SEATS = DATA["paid_seats"]
YEARS = DATA["scenario_years"]
COLORS = ["#6554c0", "#9b8ad4", "#c5b9e7", "#e1dbf0"]
INK, MUTED, MINT = "#172033", "#667085", "#20a58d"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                     "svg.fonttype": "none", "axes.labelcolor": INK,
                     "text.color": INK, "xtick.color": MUTED, "ytick.color": INK})


def readme_cost_section():
    """The README is a rendered view; prices and sizing live only in JSON."""
    monthly = DATA["illustrative_hosting_month"]
    sources = DATA["hosting_sources"]
    server = DATA["illustrative_server"]
    storage = DATA["storage_assumptions"]
    profile = DATA["controlled_test_comparison"]
    checked = date.fromisoformat(DATA["checked_on"]).strftime("%B %-d, %Y")
    lines = [f"## What could a {SEATS}-person team save?", "",
             f"This illustration assumes **{SEATS} paid seats in each of {len(DATA['reference_plans'])} products**. Prices are US dollars checked **{checked}**, excluding tax, promotions and add-ons.", "",
             "| Subscription | Listed price per person/month | Billing basis | Annualized cost for " + str(SEATS) + " |",
             "| --- | ---: | --- | ---: |"]
    for plan in DATA["reference_plans"]:
        annual = plan["per_seat_month"] * SEATS * 12
        lines.append(f"| [{plan['name']}]({plan['source']}) | ${plan['per_seat_month']:.2f} | {plan['billing']} | ${annual:,.0f} |")
    per_month = sum(plan["per_seat_month"] for plan in DATA["reference_plans"])
    lines += [f"| **Total** | **${per_month:.2f}** | Mixed billing as stated | **${PAID:,.0f}/year** |", "",
              "This is a spending example, **not a feature-parity or storage-capacity comparison**. Annual Jira terms, free plans, bundles, negotiated pricing, fewer paid seats or using only one product can lower your actual bill. Replacing a free plan creates no subscription saving.", "",
              "For a separate hosting illustration, four isolated apps could share a server while keeping their own containers and volumes:", "",
              "| Illustrative infrastructure budget | Monthly | Annual |", "| --- | ---: | ---: |",
              f"| [{server['name']}: {server['memory_gib']} GiB RAM, {server['vcpus']} vCPUs, {server['included_ssd_gib']} GiB SSD]({sources['server']}) | ${monthly['server']:,.0f} | ${monthly['server'] * 12:,.0f} |",
              f"| [{storage['local_volume_gib']} GiB additional storage for live data, backup workspace and growth]({sources['local_storage_and_workspace']}) | ${monthly['local_storage_and_workspace']:,.0f} | ${monthly['local_storage_and_workspace'] * 12:,.0f} |",
              f"| {storage['remote_repository_capacity_gib']} GiB encrypted backup repository capacity, rounded budget at [B2's starting ${storage['b2_starting_rate_usd_per_tb_month']:.2f}/TB/month rate]({sources['backup_repository_capacity_budget']}) | ${monthly['backup_repository_capacity_budget']:,.0f} | ${monthly['backup_repository_capacity_budget'] * 12:,.0f} |",
              f"| **Infrastructure illustration** | **${INFRA / 12:,.0f}** | **${INFRA:,.0f}** |",
              f"| Optional administration: {DATA['admin_hours_month']} hours/month at ${DATA['admin_hourly_rate']:,.0f}/hour | ${ADMIN / 12:,.0f} | ${ADMIN:,.0f} |",
              f"| **Including that administration allowance** | **${(INFRA + ADMIN) / 12:,.0f}** | **${INFRA + ADMIN:,.0f}** |", "",
              f"The local-storage example assumes **at most {storage['maximum_total_app_data_gib']} GiB of total app data**, including Trash, across all four apps. Allow approximately **{storage['verification_free_workspace_multiplier']}× the uncompressed snapshot size as free backup-verification workspace**, in addition to live data and Docker images; see [backup workspace](docs/BACKUP-AND-RECOVERY.md). The {storage['local_volume_gib']} GiB volume budget covers that working space and growth margin. The operator must make this storage available to Docker's app and backup volumes; it is not a promise of {storage['local_volume_gib']} GiB of live-file capacity.", "",
              f"The remote {storage['remote_repository_capacity_gib']} GiB is **repository capacity for retained snapshots**, not unlimited history or an additional {storage['remote_repository_capacity_gib']} GiB live dataset. Actual change rate, retention and deduplication determine usage. Additional transfer, domains, taxes, setup, migration and growth can change costs.", "",
              f"The {server['vcpus']}-vCPU/{server['memory_gib']}-GiB server is in a similar CPU/memory class to the controlled {profile['cpu_limit']}-CPU/{profile['memory_gib']}-GiB test fixture. This is **not a benchmark or capacity guarantee for that provider plan**. Full capacity, recovery and release conclusions depend on the [validation record](docs/VALIDATION.md); no paid provider account was created for this illustration.", "",
              f"With those assumptions, annual infrastructure-only savings are **${PAID - INFRA:,.0f} ({(PAID - INFRA) / PAID:.1%})**. Including the administration allowance gives **${PAID - INFRA - ADMIN:,.0f} ({(PAID - INFRA - ADMIN) / PAID:.1%})**. Neither percentage measures feature coverage.", "",
              "![Annual cost illustration: paid seats across four subscriptions versus OpenSW infrastructure and administration](assets/charts/annual-costs.svg)", "",
              f"Keeping the same prices and usage for {YEARS} years produces **${PAID * YEARS:,.0f}** in subscriptions versus **${(INFRA + ADMIN) * YEARS:,.0f}** for the illustrated infrastructure and administration: a **${(PAID - INFRA - ADMIN) * YEARS:,.0f} difference**, before migration. This is simple arithmetic, not a price forecast.", "",
              "![Cumulative cost illustration using constant prices and the same stated assumptions](assets/charts/five-year-costs.svg)", "",
              "Use your invoices and measured operating costs to calculate your own result. A solo user with inexpensive subscriptions may spend more maintaining a replacement; a team paying many seat fees has more room to save.", ""]
    return "\n".join(lines)


readme = ROOT / "README.md"
before, section = readme.read_text().split("<!-- COST-ILLUSTRATION:START -->\n", 1)
_, after = section.split("<!-- COST-ILLUSTRATION:END -->", 1)
readme.write_text(before + "<!-- COST-ILLUSTRATION:START -->\n" + readme_cost_section() + "\n<!-- COST-ILLUSTRATION:END -->" + after)


def style(ax):
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.spines["bottom"].set_color("#d7dce5")
    ax.tick_params(axis="both", length=0, pad=10)
    ax.set_axisbelow(True)
    ax.grid(axis="x", color="#e9ecf2", linewidth=0.8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value:,.0f}"))


fig, ax = plt.subplots(figsize=(11, 5.7), facecolor="white")
style(ax)
left = 0
for plan, color in zip(DATA["reference_plans"], COLORS):
    value = plan["per_seat_month"] * DATA["paid_seats"] * 12
    ax.barh(2, value, left=left, height=.55, color=color, label=plan["name"])
    left += value
ax.barh(1, INFRA, height=.55, color=MINT, label="Example infrastructure")
ax.barh(0, INFRA, height=.55, color=MINT)
ax.barh(0, ADMIN, left=INFRA, height=.55, color="#aeb7c7", label="Modeled administration")
ax.set_yticks([2, 1, 0], ["Four paid plans", "OpenSW: infrastructure", "OpenSW: infrastructure + admin"])
ax.set_xlim(0, PAID * 1.12)
for y, amount in [(2, PAID), (1, INFRA), (0, INFRA + ADMIN)]:
    ax.text(amount + PAID * .015, y, f"${amount:,.0f}/yr", va="center", fontweight="bold")
ax.set_ylim(-.65, 2.8)
fig.suptitle("The economics of owning everyday software", x=.03, y=.97, ha="left", fontsize=19, fontweight="bold")
fig.text(.03, .89, f"{SEATS} paid seats · annualized US reference prices · illustrative OpenSW budget", color=MUTED)
ax.legend(ncols=3, loc="lower left", bbox_to_anchor=(-.34, -.43), frameon=False, fontsize=9)
fig.text(.03, .025, f"No feature/storage parity. Admin: {DATA['admin_hours_month']} h/month × ${DATA['admin_hourly_rate']:,.0f}. Excludes setup, migration, tax, growth and extra retention.", fontsize=8.5, color=MUTED)
fig.subplots_adjust(left=.32, right=.96, top=.82, bottom=.27)
fig.savefig(OUT / "annual-costs.svg", metadata={"Title": "Illustrative annual cost of four paid plans and OpenSW", "Description": f"{SEATS} paid users: subscriptions ${PAID:,.0f}; illustrative infrastructure ${INFRA:,.0f}; infrastructure plus modeled administration ${INFRA + ADMIN:,.0f}."})
fig.savefig(OUT / "annual-costs.png", dpi=150)
plt.close(fig)

fig, ax = plt.subplots(figsize=(11, 5.8), facecolor="white")
years = list(range(1, YEARS + 1))
paid = [PAID * year for year in years]
owned = [(INFRA + ADMIN) * year for year in years]
ax.plot(years, paid, color=COLORS[0], marker="o", linewidth=3, label="Four paid plans")
ax.plot(years, owned, color=MINT, marker="o", linewidth=3, label="OpenSW + modeled administration")
ax.fill_between(years, owned, paid, color="#e7f6f1", zorder=0)
ax.spines[["top", "right", "left"]].set_visible(False)
ax.spines["bottom"].set_color("#d7dce5")
ax.grid(axis="y", color="#e9ecf2")
ax.set_axisbelow(True)
ax.tick_params(axis="both", length=0, pad=10)
ax.set_xticks(years, [f"Year {year}" for year in years])
ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${value/1000:.0f}k"))
ax.set_xlim(.85, YEARS + .7)
ax.set_ylim(0, PAID * YEARS * 1.11)
ax.text(YEARS + .08, paid[-1], f"${paid[-1]:,.0f}", va="center", color=COLORS[0], fontweight="bold")
ax.text(YEARS + .08, owned[-1], f"${owned[-1]:,.0f}", va="center", color=MINT, fontweight="bold")
ax.legend(loc="upper left", frameon=False)
fig.suptitle("Recurring fees compound. Ownership changes the curve.", x=.03, y=.96, ha="left", fontsize=18, fontweight="bold")
fig.text(.03, .885, f"{YEARS}-year scenario at constant seats, {DATA['checked_on'][:4]} prices, storage and administration effort", color=MUTED)
fig.text(.03, .025, "Scenario, not forecast. Hosting is an illustrative budget, not a capacity guarantee. See cost-assumptions.json for sources and limits.", fontsize=8.5, color=MUTED)
fig.subplots_adjust(left=.1, right=.94, top=.81, bottom=.14)
fig.savefig(OUT / "five-year-costs.svg", metadata={"Title": f"{YEARS}-year constant-cost scenario", "Description": f"At constant prices and {SEATS} seats, paid subscriptions total ${PAID * YEARS:,.0f} and the illustrative OpenSW plus administration scenario totals ${(INFRA + ADMIN) * YEARS:,.0f} over {YEARS} years."})
fig.savefig(OUT / "five-year-costs.png", dpi=150)
plt.close(fig)
for svg in OUT.glob("*.svg"):
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines()).rstrip() + "\n")
print(json.dumps({"annual_reference": PAID, "annual_infrastructure": INFRA, "annual_with_admin": INFRA + ADMIN, "annual_saving_with_admin": PAID - INFRA - ADMIN, "charts": str(OUT)}, indent=2))
