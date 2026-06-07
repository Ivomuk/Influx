import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

fig, ax = plt.subplots(figsize=(11, 14))
ax.set_xlim(0, 10)
ax.set_ylim(0, 14)
ax.axis('off')

def box(x, y, w, h, text, fc="#ffffff", ec="#333333", fontsize=10, weight='normal', textcolor='#222222'):
    b = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08,rounding_size=0.12",
                       linewidth=1.4, edgecolor=ec, facecolor=fc)
    ax.add_patch(b)
    ax.text(x + w/2, y + h/2, text, ha='center', va='center',
            fontsize=fontsize, weight=weight, color=textcolor, wrap=True)
    return b

def arrow(x1, y1, x2, y2, color='#555555', style='-|>'):
    a = FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style, mutation_scale=14,
                        linewidth=1.3, color=color)
    ax.add_patch(a)

# Title
ax.text(5, 13.5, "AI Agent Layer on a Risk-Decisioning System", ha='center',
        fontsize=15, weight='bold')
ax.text(5, 13.05, "(anonymized reference architecture)", ha='center',
        fontsize=9.5, style='italic', color='#555555')

# ---- Existing system outer box ----
box(0.5, 9.4, 9, 3.1, "", fc="#eef3f8", ec="#9aa9bb")
ax.text(1.0, 12.15, "EXISTING PRODUCTION SYSTEM  (preserved, unchanged)",
        fontsize=10.5, weight='bold', color='#33495e')

box(1.0, 10.0, 3.6, 1.7,
    "ML Prediction Layer\n\n• risk scores\n• probabilities\n• behavioral signals",
    fc="#ffffff", ec="#5b7fa6", fontsize=9.5)

box(5.1, 10.0, 3.9, 1.7,
    "Deterministic Policy Layer\n\nthresholds → actions:\napprove / reject / adjust /\nflag / cap\n\n+ audit trail",
    fc="#ffffff", ec="#5b7fa6", fontsize=9.5)

arrow(4.6, 10.85, 5.1, 10.85, color="#5b7fa6")

# ---- AI Agent layer outer box ----
box(0.5, 1.0, 9, 8.0, "", fc="#fbf3ec", ec="#c98a55")
ax.text(1.0, 8.65, "ADDITIVE AI AGENT LAYER  (new — sits alongside, not inside)",
        fontsize=10.5, weight='bold', color='#8a4f1f')

# connectors from existing system down into agent layer (read-only via tools)
arrow(2.8, 10.0, 2.8, 8.9, color="#7a7a7a")
arrow(7.05, 10.0, 7.05, 8.9, color="#7a7a7a")
ax.text(2.85, 9.45, "read-only\n(via tools)", fontsize=7.5, color='#555555', ha='left')
ax.text(7.1, 9.45, "read-only\n(via tools)", fontsize=7.5, color='#555555', ha='left')

agent_labels = [
    "Decision\nReasoning Agent",
    "Risk / Fraud\nMonitoring Agent",
    "Protection &\nFairness Agent",
    "Portfolio\nMonitoring Agent",
]
agent_xs = [0.85, 3.05, 5.25, 7.45]
for x, label in zip(agent_xs, agent_labels):
    box(x, 7.1, 1.85, 1.35, label, fc="#ffffff", ec="#c98a55", fontsize=9)

for x in agent_xs:
    arrow(x + 0.925, 7.1, 5.0, 6.2, color="#c98a55")

box(3.6, 5.15, 2.8, 1.0, "Orchestrator\n— resolves conflicts\n— logs all reasoning",
    fc="#ffffff", ec="#c98a55", fontsize=9.5, weight='bold')

arrow(5.0, 5.15, 5.0, 4.25, color="#c98a55")

box(2.6, 2.75, 4.8, 1.45,
    "Natural-Language Reporting Layer\n\nper-decision explanations,\nstakeholder & QA summaries",
    fc="#ffffff", ec="#c98a55", fontsize=9.5)

arrow(5.0, 2.75, 5.0, 1.95, color="#c98a55")
ax.text(5.0, 1.55, "→ compliance / risk officers / ops stakeholders\n   (plain-language output, fully traceable to source data)",
        ha='center', fontsize=8.5, color='#555555')

# Legend / principles box
ax.text(0.5, 0.55,
    "Design principles:  ①  Additive, not invasive — existing models & policy engine stay untouched\n"
    "②  Grounded reasoning — agents call tools over real system outputs, never free-generate facts\n"
    "③  AI adds judgment & language; the existing system remains the single source of truth\n"
    "④  Full auditability — every agent decision and generated narrative is logged & traceable",
    fontsize=8.3, color='#444444', va='top')

plt.tight_layout()
plt.savefig('/home/user/Influx/architecture_diagram.pdf', bbox_inches='tight')
plt.savefig('/home/user/Influx/architecture_diagram.png', dpi=160, bbox_inches='tight')
print("done")
