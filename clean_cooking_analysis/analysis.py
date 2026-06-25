import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
from pathlib import Path

sns.set_theme(style="whitegrid", font_scale=1.1)
COLORS = ["#2E86AB", "#A23B72", "#F18F01", "#C73E1D", "#3B1F2B", "#44BBA4", "#E94F37", "#393E41"]
OUT = Path("/home/user/Influx/clean_cooking_analysis")

df = pd.read_excel("/root/.claude/uploads/6d0b1bc2-69c5-5420-89b9-14fc3bc32d23/619c8215-prospect_meters_1.xlsx")

# Normalize case inconsistencies in location columns
df['county'] = df['customer_location_area_1'].str.strip().str.title()
df['sub_county'] = df['customer_location_area_2'].str.strip().str.title()
df['installation_date'] = pd.to_datetime(df['installation_date'])
df['install_month'] = df['installation_date'].dt.to_period('M')

# ── 1. Deployment Timeline ──────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 5))
monthly = df.groupby('install_month').size()
monthly.index = monthly.index.astype(str)
bars = ax.bar(monthly.index, monthly.values, color=COLORS[0], edgecolor='white')
ax.bar_label(bars, fontsize=10, fontweight='bold')
ax.set_title("Monthly Clean Cooking Meter Deployments", fontsize=15, fontweight='bold')
ax.set_xlabel("Month")
ax.set_ylabel("Meters Installed")
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.savefig(OUT / "1_deployment_timeline.png", dpi=150)
plt.close()

# Cumulative
fig, ax = plt.subplots(figsize=(12, 5))
cumulative = monthly.cumsum()
ax.plot(cumulative.index, cumulative.values, marker='o', color=COLORS[1], linewidth=2.5, markersize=8)
ax.fill_between(range(len(cumulative)), cumulative.values, alpha=0.15, color=COLORS[1])
ax.set_xticks(range(len(cumulative)))
ax.set_xticklabels(cumulative.index, rotation=45, ha='right')
ax.set_title("Cumulative Meter Deployments", fontsize=15, fontweight='bold')
ax.set_ylabel("Total Meters")
for i, v in enumerate(cumulative.values):
    ax.annotate(str(v), (i, v), textcoords="offset points", xytext=(0, 10), ha='center', fontsize=9, fontweight='bold')
plt.tight_layout()
plt.savefig(OUT / "2_cumulative_deployment.png", dpi=150)
plt.close()

# ── 2. Geographic Distribution ──────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 6))
county_counts = df['county'].value_counts().head(10)
bars = ax.barh(county_counts.index[::-1], county_counts.values[::-1], color=COLORS[0], edgecolor='white')
ax.bar_label(bars, fontsize=10, fontweight='bold', padding=5)
ax.set_title("Top 10 Counties by Meter Deployment", fontsize=15, fontweight='bold')
ax.set_xlabel("Number of Meters")
plt.tight_layout()
plt.savefig(OUT / "3_county_distribution.png", dpi=150)
plt.close()

# Sub-county top 15
fig, ax = plt.subplots(figsize=(12, 7))
sub_counts = df['sub_county'].value_counts().head(15)
bars = ax.barh(sub_counts.index[::-1], sub_counts.values[::-1], color=COLORS[2], edgecolor='white')
ax.bar_label(bars, fontsize=9, fontweight='bold', padding=5)
ax.set_title("Top 15 Sub-Counties by Meter Deployment", fontsize=15, fontweight='bold')
ax.set_xlabel("Number of Meters")
plt.tight_layout()
plt.savefig(OUT / "4_subcounty_distribution.png", dpi=150)
plt.close()

# ── 3. Gender Analysis ──────────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(13, 5))

gender = df['customer_gender'].value_counts()
gender_labels = {'F': 'Female', 'M': 'Male'}
labels = [gender_labels.get(g, g) for g in gender.index]
wedges, texts, autotexts = axes[0].pie(gender.values, labels=labels, autopct='%1.1f%%',
    colors=[COLORS[1], COLORS[0]], startangle=90, textprops={'fontsize': 12})
for t in autotexts:
    t.set_fontweight('bold')
axes[0].set_title("Gender Distribution\n(All Deployments)", fontsize=13, fontweight='bold')

# Gender by top counties
top5 = df[df['county'].isin(county_counts.head(5).index)]
gender_county = top5.groupby(['county', 'customer_gender']).size().unstack(fill_value=0)
gender_county.plot(kind='bar', ax=axes[1], color=[COLORS[1], COLORS[0]], edgecolor='white')
axes[1].set_title("Gender Split by Top 5 Counties", fontsize=13, fontweight='bold')
axes[1].set_xlabel("")
axes[1].set_ylabel("Meters")
axes[1].legend(['Female', 'Male'], loc='upper right')
axes[1].tick_params(axis='x', rotation=45)

plt.tight_layout()
plt.savefig(OUT / "5_gender_analysis.png", dpi=150)
plt.close()

# ── 4. Device & Technology ──────────────────────────────────────────────
fig, axes = plt.subplots(1, 2, figsize=(13, 5))

mfr = df['manufacturer'].value_counts()
axes[0].pie(mfr.values, labels=mfr.index, autopct='%1.1f%%', colors=COLORS[:len(mfr)],
    startangle=90, textprops={'fontsize': 11})
axes[0].set_title("Manufacturer Share", fontsize=13, fontweight='bold')

power = df['max_power_w'].dropna()
axes[1].hist(power, bins=20, color=COLORS[0], edgecolor='white')
axes[1].set_title("Device Power Rating Distribution", fontsize=13, fontweight='bold')
axes[1].set_xlabel("Max Power (W)")
axes[1].set_ylabel("Count")

plt.tight_layout()
plt.savefig(OUT / "6_device_technology.png", dpi=150)
plt.close()

# ── 5. Payment Model Analysis ──────────────────────────────────────────
fig, ax = plt.subplots(figsize=(8, 5))
payment = df['payment_plan'].str.strip().str.title().value_counts()
bars = ax.bar(payment.index, payment.values, color=COLORS[:len(payment)], edgecolor='white')
ax.bar_label(bars, fontsize=11, fontweight='bold')
ax.set_title("Payment Model Distribution", fontsize=15, fontweight='bold')
ax.set_ylabel("Number of Meters")
plt.tight_layout()
plt.savefig(OUT / "7_payment_model.png", dpi=150)
plt.close()

# ── 6. Monthly Growth Rate ──────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 5))
growth = monthly.pct_change().dropna() * 100
colors_growth = [COLORS[3] if v < 0 else COLORS[5] for v in growth.values]
bars = ax.bar(growth.index, growth.values, color=colors_growth, edgecolor='white')
ax.axhline(y=0, color='gray', linestyle='--', linewidth=0.8)
ax.set_title("Month-over-Month Deployment Growth Rate (%)", fontsize=15, fontweight='bold')
ax.set_ylabel("Growth Rate (%)")
ax.yaxis.set_major_formatter(mticker.PercentFormatter(decimals=0))
plt.xticks(rotation=45, ha='right')
plt.tight_layout()
plt.savefig(OUT / "8_growth_rate.png", dpi=150)
plt.close()

# ── 7. Geographic Heatmap: County × Month ───────────────────────────────
fig, ax = plt.subplots(figsize=(14, 7))
top_counties = county_counts.head(8).index
heatmap_data = df[df['county'].isin(top_counties)].groupby(['county', 'install_month']).size().unstack(fill_value=0)
heatmap_data.columns = heatmap_data.columns.astype(str)
sns.heatmap(heatmap_data, annot=True, fmt='d', cmap='YlOrRd', ax=ax, linewidths=0.5)
ax.set_title("Deployment Heatmap: County × Month", fontsize=15, fontweight='bold')
ax.set_xlabel("Month")
ax.set_ylabel("County")
plt.tight_layout()
plt.savefig(OUT / "9_heatmap_county_month.png", dpi=150)
plt.close()

# ── 8. Executive Summary Stats ──────────────────────────────────────────
summary = {
    "Total Meters Deployed": len(df),
    "Counties Reached": df['county'].nunique(),
    "Sub-Counties Reached": df['sub_county'].nunique(),
    "Deployment Period": f"{df['installation_date'].min().strftime('%b %Y')} - {df['installation_date'].max().strftime('%b %Y')}",
    "Female Beneficiaries (%)": f"{(df['customer_gender']=='F').sum() / df['customer_gender'].notna().sum() * 100:.1f}%",
    "Primary Manufacturer": df['manufacturer'].mode()[0],
    "Dominant Payment Model": df['payment_plan'].str.title().mode()[0],
    "Avg Monthly Deployments": f"{monthly.mean():.0f}",
    "Peak Month": f"{monthly.idxmax()} ({monthly.max()} meters)",
}

fig, ax = plt.subplots(figsize=(10, 6))
ax.axis('off')
ax.set_title("EXECUTIVE SUMMARY\nClean Cooking Meter Deployment Program", fontsize=16, fontweight='bold', pad=20)
table_data = [[k, v] for k, v in summary.items()]
table = ax.table(cellText=table_data, colLabels=["Metric", "Value"], loc='center', cellLoc='left')
table.auto_set_font_size(False)
table.set_fontsize(12)
table.scale(1.2, 1.8)
for (row, col), cell in table.get_celld().items():
    if row == 0:
        cell.set_facecolor(COLORS[0])
        cell.set_text_props(color='white', fontweight='bold')
    elif row % 2 == 0:
        cell.set_facecolor('#f0f0f0')
plt.tight_layout()
plt.savefig(OUT / "0_executive_summary.png", dpi=150)
plt.close()

print("All charts generated successfully!")
for k, v in summary.items():
    print(f"  {k}: {v}")
