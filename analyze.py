"""
Tesla Model Y aanbod tracker — analyse

Leest tesla_model_y.db en genereert report.html met:
  - huidig aanbod per prijsklasse
  - nieuwe advertenties per dag (laatste 30 dagen)
  - nieuwe advertenties per week (laatste 12 weken)
  - totaal actief aanbod over tijd

Gebruik:
    python analyze.py
Open daarna report.html in je browser.
"""

import json
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "tesla_model_y.db"
DOCS_DIR = Path(__file__).parent / "docs"
OUT_PATH = DOCS_DIR / "index.html"

PRICE_BUCKET_SIZE = 5000  # pas aan indien gewenst


def load_rows():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT * FROM listings").fetchall()
    con.close()
    return [dict(r) for r in rows]


def bucket_label(price):
    if price is None:
        return "onbekend"
    low = (price // PRICE_BUCKET_SIZE) * PRICE_BUCKET_SIZE
    high = low + PRICE_BUCKET_SIZE
    return f"€{low//1000}k - €{high//1000}k"


def build_data(rows):
    today = date.today()

    active = [r for r in rows if not r["removed_on"]]

    # Prijsklasse verdeling (huidig actief aanbod) + gemiddelde kilometerstand per klasse
    price_counts = defaultdict(int)
    price_km_sum = defaultdict(int)
    price_km_count = defaultdict(int)
    for r in active:
        label = bucket_label(r["price"])
        price_counts[label] += 1
        if r["mileage"] is not None:
            price_km_sum[label] += r["mileage"]
            price_km_count[label] += 1
    # sorteer op ondergrens van de klasse (onbekend laatste)
    def sort_key(label):
        return (9_999_999,) if label == "onbekend" else (int(label.split("k")[0].replace("€", "")),)
    price_labels = sorted(price_counts.keys(), key=sort_key)
    price_values = [price_counts[l] for l in price_labels]
    price_avg_km = [
        round(price_km_sum[l] / price_km_count[l]) if price_km_count[l] else None
        for l in price_labels
    ]

    # Variant-verdeling (Long Range / Performance / RWD / AWD / Standard Range)
    variant_counts = defaultdict(int)
    variant_price_sum = defaultdict(int)
    variant_price_count = defaultdict(int)
    for r in active:
        v = r["variant"] or "Onbekend"
        variant_counts[v] += 1
        if r["price"] is not None:
            variant_price_sum[v] += r["price"]
            variant_price_count[v] += 1
    variant_order = ["Long Range", "Performance", "RWD", "AWD", "Standard Range", "Onbekend"]
    variant_labels = [v for v in variant_order if variant_counts.get(v)]
    variant_values = [variant_counts[v] for v in variant_labels]
    variant_avg_price = [
        round(variant_price_sum[v] / variant_price_count[v]) if variant_price_count[v] else None
        for v in variant_labels
    ]

    # Nieuwe advertenties per dag, laatste 30 dagen
    daily_new = defaultdict(int)
    for r in rows:
        daily_new[r["first_seen"]] += 1
    last_30_days = [(today - timedelta(days=i)).isoformat() for i in range(29, -1, -1)]
    daily_values = [daily_new.get(d, 0) for d in last_30_days]

    # Nieuwe advertenties per week (ISO-week), laatste 12 weken
    weekly_new = defaultdict(int)
    for r in rows:
        d = datetime.fromisoformat(r["first_seen"]).date()
        iso = d.isocalendar()
        weekly_new[f"{iso[0]}-W{iso[1]:02d}"] += 1
    week_keys = sorted(weekly_new.keys())[-12:]
    weekly_values = [weekly_new[w] for w in week_keys]

    # Totaal actief aanbod over tijd (cumulatief first_seen - cumulatief removed_on)
    all_dates = sorted({r["first_seen"] for r in rows} | {r["removed_on"] for r in rows if r["removed_on"]})
    running = 0
    active_over_time = []
    added_by_date = defaultdict(int)
    removed_by_date = defaultdict(int)
    for r in rows:
        added_by_date[r["first_seen"]] += 1
        if r["removed_on"]:
            removed_by_date[r["removed_on"]] += 1
    for d in all_dates:
        running += added_by_date.get(d, 0) - removed_by_date.get(d, 0)
        active_over_time.append((d, running))

    return {
        "price_labels": price_labels,
        "price_values": price_values,
        "price_avg_km": price_avg_km,
        "variant_labels": variant_labels,
        "variant_values": variant_values,
        "variant_avg_price": variant_avg_price,
        "daily_labels": last_30_days,
        "daily_values": daily_values,
        "weekly_labels": week_keys,
        "weekly_values": weekly_values,
        "active_over_time_labels": [d for d, _ in active_over_time],
        "active_over_time_values": [v for _, v in active_over_time],
        "total_active": len(active),
        "total_ever_seen": len(rows),
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "by_source": {
            src: len([r for r in active if r["source"] == src])
            for src in sorted({r["source"] for r in rows})
        },
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="nl">
<head>
<meta charset="UTF-8">
<title>Tesla Model Y aanbod — rapport</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#0f172a; color:#e2e8f0; margin:0; padding:24px; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .meta {{ color:#94a3b8; font-size:0.85rem; margin-bottom: 24px;}}
  .stats {{ display:flex; gap:16px; margin-bottom: 32px; flex-wrap:wrap; }}
  .stat {{ background:#1e293b; padding:16px 20px; border-radius:10px; min-width:140px; }}
  .stat .num {{ font-size:1.6rem; font-weight:700; color:#38bdf8; }}
  .stat .label {{ font-size:0.8rem; color:#94a3b8; }}
  .chart-box {{ background:#1e293b; border-radius:12px; padding:20px; margin-bottom:28px; }}
  .chart-box h2 {{ font-size:1.05rem; margin-top:0; }}
  canvas {{ max-height: 360px; }}
</style>
</head>
<body>
  <h1>🚗 Tesla Model Y aanbod — Nederland</h1>
  <div class="meta">Gegenereerd op {generated_at} · Marktplaats + AutoTrack</div>

  <div class="stats">
    <div class="stat"><div class="num">{total_active}</div><div class="label">actief aanbod nu</div></div>
    <div class="stat"><div class="num">{total_ever_seen}</div><div class="label">totaal ooit gezien</div></div>
    {source_stats}
  </div>

  <div class="chart-box">
    <h2>Aanbod per prijsklasse (huidig) + gemiddelde km-stand</h2>
    <canvas id="priceChart"></canvas>
  </div>

  <div class="chart-box">
    <h2>Aanbod per variant (Long Range / Performance / RWD / AWD)</h2>
    <canvas id="variantChart"></canvas>
  </div>

  <div class="chart-box">
    <h2>Nieuwe advertenties per dag (laatste 30 dagen)</h2>
    <canvas id="dailyChart"></canvas>
  </div>

  <div class="chart-box">
    <h2>Nieuwe advertenties per week (laatste 12 weken)</h2>
    <canvas id="weeklyChart"></canvas>
  </div>

  <div class="chart-box">
    <h2>Totaal actief aanbod over tijd</h2>
    <canvas id="totalChart"></canvas>
  </div>

<script>
const data = {data_json};

new Chart(document.getElementById('priceChart'), {{
  data: {{
    labels: data.price_labels,
    datasets: [
      {{ type: 'bar', label: 'Aantal auto\\'s', data: data.price_values, backgroundColor: '#38bdf8', yAxisID: 'y' }},
      {{ type: 'line', label: 'Gem. km-stand', data: data.price_avg_km, borderColor: '#fbbf24', backgroundColor: '#fbbf24', yAxisID: 'y1', tension: 0.2 }}
    ]
  }},
  options: {{
    scales: {{
      y: {{ position: 'left', title: {{ display: true, text: 'Aantal auto\\'s' }} }},
      y1: {{ position: 'right', title: {{ display: true, text: 'Gem. km-stand' }}, grid: {{ drawOnChartArea: false }} }}
    }}
  }}
}});

new Chart(document.getElementById('variantChart'), {{
  type: 'bar',
  data: {{
    labels: data.variant_labels,
    datasets: [{{ label: 'Aantal auto\\'s', data: data.variant_values, backgroundColor: '#f472b6' }}]
  }},
  options: {{
    plugins: {{
      legend: {{ display:false }},
      tooltip: {{
        callbacks: {{
          afterLabel: function(ctx) {{
            const avg = data.variant_avg_price[ctx.dataIndex];
            return avg ? 'Gem. prijs: €' + avg.toLocaleString('nl-NL') : '';
          }}
        }}
      }}
    }}
  }}
}});

new Chart(document.getElementById('dailyChart'), {{
  type: 'bar',
  data: {{ labels: data.daily_labels, datasets: [{{ label: 'Nieuwe advertenties', data: data.daily_values, backgroundColor: '#a78bfa' }}] }},
  options: {{ plugins: {{ legend: {{ display:false }} }} }}
}});

new Chart(document.getElementById('weeklyChart'), {{
  type: 'bar',
  data: {{ labels: data.weekly_labels, datasets: [{{ label: 'Nieuwe advertenties', data: data.weekly_values, backgroundColor: '#34d399' }}] }},
  options: {{ plugins: {{ legend: {{ display:false }} }} }}
}});

new Chart(document.getElementById('totalChart'), {{
  type: 'line',
  data: {{ labels: data.active_over_time_labels, datasets: [{{ label: 'Actief aanbod', data: data.active_over_time_values, borderColor: '#fbbf24', tension: 0.2 }}] }},
  options: {{ plugins: {{ legend: {{ display:false }} }} }}
}});
</script>
</body>
</html>
"""


def render(data):
    DOCS_DIR.mkdir(exist_ok=True)
    source_stats = "".join(
        f'<div class="stat"><div class="num">{count}</div><div class="label">actief op {src}</div></div>'
        for src, count in data["by_source"].items()
    )
    html = HTML_TEMPLATE.format(
        generated_at=data["generated_at"],
        total_active=data["total_active"],
        total_ever_seen=data["total_ever_seen"],
        source_stats=source_stats,
        data_json=json.dumps(data),
    )
    OUT_PATH.write_text(html, encoding="utf-8")


def main():
    if not DB_PATH.exists():
        print("Geen database gevonden. Draai eerst scraper.py (minstens 1x, liever een paar dagen achter elkaar).")
        return
    rows = load_rows()
    if not rows:
        print("Database is leeg. Draai eerst scraper.py.")
        return
    data = build_data(rows)
    render(data)
    print(f"Rapport gegenereerd: {OUT_PATH}")
    print(f"  Actief aanbod nu: {data['total_active']}")
    print(f"  Totaal ooit gezien: {data['total_ever_seen']}")


if __name__ == "__main__":
    main()
