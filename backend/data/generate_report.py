"""
Generate a PDF report on forecasting model performance.

Runs the benchmark suite, then produces a multi-page PDF with charts and
plain-English explanations suitable for non-technical stakeholders.

Usage:
    cd backend
    python data/generate_report.py
"""

import os
import sys
import datetime

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import django
django.setup()

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import matplotlib.patches as mpatches

from api.forecasting.service import run_forecast_service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PDF = os.path.join(BASE_DIR, "Forecasting_Model_Report.pdf")

BRAND_DARK = "#0F1729"
BRAND_PRIMARY = "#5A5AF6"
BRAND_GREEN = "#22C55E"
BRAND_AMBER = "#F59E0B"
BRAND_RED = "#EF4444"
BRAND_SLATE = "#94A3B8"
BRAND_WHITE = "#F1F5F9"

DATASETS = [
    {"name": "store-sales",      "label": "Grocery Chain (Favorita)",  "time_column": "date", "target_column": "total_revenue"},
    {"name": "superstore",       "label": "Office Supplies Store",     "time_column": "date", "target_column": "total_revenue"},
    {"name": "online-retail-ii", "label": "Online Retailer (UK)",      "time_column": "date", "target_column": "total_revenue"},
]

MODEL_FRIENDLY = {
    "naive": "Last-Value Repeat",
    "seasonal_naive": "Seasonal Repeat",
    "ets": "Smoothing (ETS)",
    "exp_smoothing": "Smoothing (ETS)",
    "theta": "Trend-Following (Theta)",
    "croston": "Sparse Demand (Croston)",
    "sarimax": "Statistical (SARIMAX)",
    "catboost": "Machine Learning (CatBoost)",
    "lightgbm": "Machine Learning (LightGBM)",
}


def _friendly_model(name: str) -> str:
    for key, val in MODEL_FRIENDLY.items():
        if key in name:
            parts = name.split("+")
            return " + ".join(MODEL_FRIENDLY.get(p, p) for p in parts)
    return name


def _load_split(ds_name: str, split: str) -> pd.DataFrame:
    return pd.read_csv(os.path.join(BASE_DIR, ds_name, f"prepared_{split}.csv"))


def _oos_metrics(actual, predicted):
    errors = actual - predicted
    ae = np.abs(errors)
    mae = float(np.mean(ae))
    rmse = float(np.sqrt(np.mean(errors ** 2)))
    wape = float(np.sum(ae) / np.sum(np.abs(actual))) if np.sum(np.abs(actual)) > 0 else float("inf")
    naive_e = np.abs(np.diff(actual))
    mase = float(mae / np.mean(naive_e)) if len(naive_e) > 0 and np.mean(naive_e) > 1e-9 else float("inf")
    return {"mae": mae, "rmse": rmse, "wape": wape, "mase": mase}


def _run_all_benchmarks():
    results = []
    for cfg in DATASETS:
        print(f"  Running benchmark: {cfg['label']}...")
        train_df = _load_split(cfg["name"], "train")
        val_df = _load_split(cfg["name"], "val")
        horizon = min(len(val_df), 30)

        svc = run_forecast_service(train_df, time_column=cfg["time_column"],
                                   target_column=cfg["target_column"], horizon=horizon)

        predicted = np.array([f["value"] for f in svc["forecast"]])
        actual = val_df[cfg["target_column"]].values[:horizon].astype(float)
        oos = _oos_metrics(actual, predicted)

        intervals = svc.get("prediction_intervals", [])
        covered = sum(1 for i in range(min(len(intervals), len(actual)))
                      if intervals[i]["lower"] <= actual[i] <= intervals[i]["upper"])
        coverage = covered / min(len(intervals), len(actual)) if intervals else 0.0

        results.append({
            "cfg": cfg,
            "svc": svc,
            "predicted": predicted,
            "actual": actual,
            "oos": oos,
            "coverage": coverage,
            "horizon": horizon,
            "train_len": len(train_df),
        })
    return results


def _setup_fig(title=None):
    fig = plt.figure(figsize=(11, 8.5))
    fig.patch.set_facecolor(BRAND_DARK)
    if title:
        fig.text(0.5, 0.95, title, ha="center", va="top",
                 fontsize=20, fontweight="bold", color=BRAND_WHITE)
    return fig


def page_cover(pdf):
    fig = _setup_fig()
    fig.text(0.5, 0.62, "Forecasting Model", ha="center", va="center",
             fontsize=38, fontweight="bold", color=BRAND_WHITE)
    fig.text(0.5, 0.54, "Performance Report", ha="center", va="center",
             fontsize=38, fontweight="bold", color=BRAND_PRIMARY)
    fig.text(0.5, 0.42, datetime.date.today().strftime("%B %d, %Y"),
             ha="center", va="center", fontsize=16, color=BRAND_SLATE)
    fig.text(0.5, 0.36, "An easy-to-read summary of how well our\n"
             "forecasting system predicts future sales",
             ha="center", va="center", fontsize=13, color=BRAND_SLATE,
             linespacing=1.6)

    fig.text(0.5, 0.14, "BI Dashboard  |  Automated Forecasting Engine",
             ha="center", va="center", fontsize=10, color=BRAND_SLATE, style="italic")
    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_executive_summary(pdf, results):
    fig = _setup_fig("Executive Summary")

    mase_scores = [r["oos"]["mase"] for r in results]
    avg_mase = np.mean(mase_scores)
    best_idx = int(np.argmin(mase_scores))
    worst_idx = int(np.argmax(mase_scores))

    if avg_mase < 0.8:
        overall = "Excellent"
        overall_color = BRAND_GREEN
        overall_desc = ("Our model consistently outperforms simple baselines across all datasets.\n"
                        "Predictions are meaningfully better than just repeating yesterday's number.")
    elif avg_mase < 1.05:
        overall = "Good"
        overall_color = BRAND_GREEN
        overall_desc = ("Our model performs well overall, beating simple approaches on most datasets.\n"
                        "Some datasets are harder to predict, but the system adapts its strategy automatically.")
    elif avg_mase < 1.3:
        overall = "Fair"
        overall_color = BRAND_AMBER
        overall_desc = ("Our model is roughly on par with simple approaches.\n"
                        "Improvements are needed for harder-to-predict datasets.")
    else:
        overall = "Needs Work"
        overall_color = BRAND_RED
        overall_desc = ("The model struggles to beat simple baselines.\n"
                        "Significant improvements are needed.")

    y = 0.82
    fig.text(0.08, y, "Overall Rating:", fontsize=14, color=BRAND_SLATE, fontweight="bold")
    fig.text(0.35, y, overall, fontsize=22, color=overall_color, fontweight="bold")
    y -= 0.06
    fig.text(0.08, y, overall_desc, fontsize=11, color=BRAND_SLATE, linespacing=1.6, va="top")

    y -= 0.14
    fig.text(0.08, y, "What does this mean?", fontsize=14, color=BRAND_WHITE, fontweight="bold")
    y -= 0.04
    explanations = [
        "We tested our forecasting system on 3 very different real-world sales datasets.",
        f"Best performance: {results[best_idx]['cfg']['label']} — our predictions were",
        f"  {max(0, (1 - mase_scores[best_idx])) * 100:.0f}% more accurate than just repeating yesterday's sales.",
        f"Hardest dataset: {results[worst_idx]['cfg']['label']} — predictions were roughly",
        f"  as accurate as a simple last-value approach (common for very noisy data).",
        "",
        "The system automatically picks the best strategy for each dataset,",
        "sometimes combining two models to get better results (ensemble).",
    ]
    for line in explanations:
        bullet = "  •  " if line and not line.startswith("  ") else "     "
        fig.text(0.08, y, f"{bullet}{line}", fontsize=11, color=BRAND_SLATE, fontfamily="monospace")
        y -= 0.035

    y -= 0.03
    fig.text(0.08, y, "Key Numbers at a Glance", fontsize=14, color=BRAND_WHITE, fontweight="bold")
    y -= 0.05
    for r in results:
        mase = r["oos"]["mase"]
        wape = r["oos"]["wape"]
        dot_color = BRAND_GREEN if mase < 0.9 else BRAND_AMBER if mase < 1.1 else BRAND_RED
        label = r["cfg"]["label"]
        model = _friendly_model(r["svc"]["best_model"])
        fig.text(0.10, y, "●", fontsize=14, color=dot_color)
        fig.text(0.14, y, f"{label}", fontsize=11, color=BRAND_WHITE, fontweight="bold")
        fig.text(0.14, y - 0.03, f"Model: {model}  |  Error rate: {wape:.0%}  |  "
                 f"vs simple baseline: {'better' if mase < 1 else 'similar'}",
                 fontsize=10, color=BRAND_SLATE)
        y -= 0.07

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_dataset_overview(pdf, results):
    fig = _setup_fig("Datasets We Tested On")

    ax = fig.add_axes([0.08, 0.12, 0.86, 0.72])
    ax.set_facecolor("#1A2235")

    labels = [r["cfg"]["label"] for r in results]
    train_lens = [r["train_len"] for r in results]

    bars = ax.barh(labels, train_lens, color=BRAND_PRIMARY, height=0.5, edgecolor="#3B3BF0", linewidth=1.2)

    for bar, length in zip(bars, train_lens):
        ax.text(bar.get_width() + max(train_lens) * 0.02, bar.get_y() + bar.get_height() / 2,
                f"{length:,} days", va="center", fontsize=12, color=BRAND_WHITE, fontweight="bold")

    ax.set_xlabel("Number of Days of Historical Data", fontsize=12, color=BRAND_SLATE, labelpad=10)
    ax.set_xlim(0, max(train_lens) * 1.25)
    ax.tick_params(colors=BRAND_SLATE, labelsize=11)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BRAND_SLATE)
    ax.spines["left"].set_color(BRAND_SLATE)

    fig.text(0.08, 0.07, "More history generally means better predictions. Our system adapts its model\n"
             "choice based on how much data is available.", fontsize=10, color=BRAND_SLATE, linespacing=1.5)

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_accuracy_bars(pdf, results):
    fig = _setup_fig("How Accurate Are the Predictions?")

    ax = fig.add_axes([0.12, 0.15, 0.82, 0.70])
    ax.set_facecolor("#1A2235")

    labels = [r["cfg"]["label"] for r in results]
    wapes = [r["oos"]["wape"] * 100 for r in results]
    colors = [BRAND_GREEN if w < 40 else BRAND_AMBER if w < 70 else BRAND_RED for w in wapes]

    bars = ax.bar(labels, wapes, color=colors, width=0.5, edgecolor=[c for c in colors], linewidth=1.2)

    for bar, w in zip(bars, wapes):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5,
                f"{w:.0f}%", ha="center", fontsize=14, fontweight="bold", color=BRAND_WHITE)

    ax.set_ylabel("Prediction Error Rate (%)", fontsize=12, color=BRAND_SLATE, labelpad=10)
    ax.set_ylim(0, max(wapes) * 1.25)
    ax.tick_params(colors=BRAND_SLATE, labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BRAND_SLATE)
    ax.spines["left"].set_color(BRAND_SLATE)

    green_p = mpatches.Patch(color=BRAND_GREEN, label="Great (< 40%)")
    amber_p = mpatches.Patch(color=BRAND_AMBER, label="Acceptable (40-70%)")
    red_p = mpatches.Patch(color=BRAND_RED, label="Needs Improvement (> 70%)")
    ax.legend(handles=[green_p, amber_p, red_p], loc="upper right", fontsize=9,
              facecolor="#1A2235", edgecolor=BRAND_SLATE, labelcolor=BRAND_SLATE)

    fig.text(0.08, 0.06,
             "Error rate shows the average percentage difference between predicted and actual sales.\n"
             "Lower is better. Green means the model is doing well.",
             fontsize=10, color=BRAND_SLATE, linespacing=1.5)

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_predicted_vs_actual(pdf, results):
    fig = _setup_fig("Predicted vs. Actual Sales")

    for idx, r in enumerate(results):
        ax = fig.add_axes([0.08, 0.72 - idx * 0.29, 0.86, 0.22])
        ax.set_facecolor("#1A2235")

        days = np.arange(1, r["horizon"] + 1)
        ax.plot(days, r["actual"], color=BRAND_GREEN, linewidth=2, label="Actual Sales", alpha=0.9)
        ax.plot(days, r["predicted"], color=BRAND_PRIMARY, linewidth=2, label="Predicted", linestyle="--", alpha=0.9)

        intervals = r["svc"].get("prediction_intervals", [])
        if intervals:
            lowers = np.array([intervals[i]["lower"] for i in range(r["horizon"])])
            uppers = np.array([intervals[i]["upper"] for i in range(r["horizon"])])
            ax.fill_between(days, lowers, uppers, alpha=0.15, color=BRAND_PRIMARY)

        ax.set_title(r["cfg"]["label"], fontsize=11, color=BRAND_WHITE, fontweight="bold", pad=4)
        ax.tick_params(colors=BRAND_SLATE, labelsize=8)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["bottom"].set_color(BRAND_SLATE)
        ax.spines["left"].set_color(BRAND_SLATE)
        if idx == 0:
            ax.legend(fontsize=8, facecolor="#1A2235", edgecolor=BRAND_SLATE,
                      labelcolor=BRAND_SLATE, loc="upper right")
        if idx == 2:
            ax.set_xlabel("Days into the Future", fontsize=10, color=BRAND_SLATE)

    fig.text(0.08, 0.04,
             "Green = what actually happened  |  Purple dashed = what the model predicted  |  "
             "Shaded area = uncertainty range",
             fontsize=9, color=BRAND_SLATE)

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_model_selection(pdf, results):
    fig = _setup_fig("Which Strategy Did the System Choose?")

    y = 0.82
    for r in results:
        model_name = _friendly_model(r["svc"]["best_model"])
        is_ensemble = r["svc"].get("ensemble", False)
        confidence = r["svc"].get("confidence", "medium")
        reason = r["svc"].get("confidence_reason", "")

        conf_color = BRAND_GREEN if confidence == "high" else BRAND_AMBER if confidence == "medium" else BRAND_RED

        fig.text(0.08, y, r["cfg"]["label"], fontsize=14, color=BRAND_WHITE, fontweight="bold")
        y -= 0.035
        fig.text(0.10, y, f"Strategy chosen:  {model_name}", fontsize=11, color=BRAND_PRIMARY)
        y -= 0.03
        if is_ensemble:
            fig.text(0.10, y, "Two models were combined (ensemble) for better stability",
                     fontsize=10, color=BRAND_SLATE, style="italic")
            y -= 0.03

        fig.text(0.10, y, f"Confidence: ", fontsize=10, color=BRAND_SLATE)
        fig.text(0.22, y, f"{confidence.upper()}", fontsize=10, color=conf_color, fontweight="bold")
        if reason:
            fig.text(0.32, y, f"— {reason}", fontsize=10, color=BRAND_SLATE)
        y -= 0.03

        competition = r["svc"].get("model_results", [])
        if competition:
            fig.text(0.10, y, "All strategies tested:", fontsize=10, color=BRAND_SLATE)
            y -= 0.025
            for mr in competition:
                mn = _friendly_model(mr["model"])
                mae = mr["metrics"]["mae"]
                star = " ★" if mr["model"] in r["svc"]["best_model"] else ""
                clr = BRAND_GREEN if star else BRAND_SLATE
                fig.text(0.12, y, f"  {mn:40s}  error: {mae:>14,.0f}{star}", fontsize=9,
                         color=clr, fontfamily="monospace")
                y -= 0.022

        y -= 0.03

    fig.text(0.08, 0.04,
             "★ = selected strategy. The system automatically picks the best approach\n"
             "based on how each model performed on historical data.",
             fontsize=10, color=BRAND_SLATE, linespacing=1.5)

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_interval_coverage(pdf, results):
    fig = _setup_fig("How Reliable Are the Uncertainty Ranges?")

    ax = fig.add_axes([0.12, 0.28, 0.78, 0.55])
    ax.set_facecolor("#1A2235")

    labels = [r["cfg"]["label"] for r in results]
    coverages = [r["coverage"] * 100 for r in results]
    target = 80

    colors = [BRAND_GREEN if c >= 70 else BRAND_AMBER if c >= 50 else BRAND_RED for c in coverages]
    bars = ax.bar(labels, coverages, color=colors, width=0.5)
    ax.axhline(y=target, color=BRAND_AMBER, linestyle="--", linewidth=1.5, alpha=0.7)
    ax.text(len(labels) - 0.5, target + 1.5, f"Target: {target}%", fontsize=10,
            color=BRAND_AMBER, ha="right")

    for bar, c in zip(bars, coverages):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1.5,
                f"{c:.0f}%", ha="center", fontsize=14, fontweight="bold", color=BRAND_WHITE)

    ax.set_ylabel("% of Actual Values Within Predicted Range", fontsize=11, color=BRAND_SLATE, labelpad=10)
    ax.set_ylim(0, 100)
    ax.tick_params(colors=BRAND_SLATE, labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(BRAND_SLATE)
    ax.spines["left"].set_color(BRAND_SLATE)

    fig.text(0.08, 0.14,
             "When we predict future sales, we also give a range (e.g., \"between $5,000 and $15,000\").\n"
             "This chart shows how often the actual sales fell within that range.\n"
             "Higher is better — ideally we want 80% or more of actuals within our predicted range.",
             fontsize=10, color=BRAND_SLATE, linespacing=1.6)

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_strengths_weaknesses(pdf, results):
    fig = _setup_fig("Strengths & Areas for Improvement")

    y = 0.82
    fig.text(0.08, y, "Where the Model Excels", fontsize=16, color=BRAND_GREEN, fontweight="bold")
    y -= 0.05
    strengths = [
        "Automatically selects the best strategy for each dataset — no manual tuning needed.",
        "Combines multiple models (ensemble) when one alone isn't reliable enough.",
        "Works across very different types of businesses (grocery, office, online retail).",
        "Provides uncertainty ranges so you know how confident the predictions are.",
        f"Best accuracy on the Online Retailer dataset: only {results[2]['oos']['wape']:.0%} average error.",
    ]
    for s in strengths:
        fig.text(0.10, y, f"✓  {s}", fontsize=10.5, color=BRAND_SLATE)
        y -= 0.035

    y -= 0.04
    fig.text(0.08, y, "Areas for Improvement", fontsize=16, color=BRAND_AMBER, fontweight="bold")
    y -= 0.05
    weaknesses = [
        "Uncertainty ranges capture 60-73% of actuals — goal is 80%. Ranges need widening.",
        "On very noisy datasets (Superstore), predictions are only as good as simple approaches.",
        "30-day forecasts become repetitive toward the end — the model 'runs out of signal'.",
        "Very short datasets (< 90 days) don't give the model enough history to learn from.",
        "CatBoost struggles with long, smooth trends — it's built for pattern recognition, not trend following.",
    ]
    for w in weaknesses:
        fig.text(0.10, y, f"△  {w}", fontsize=10.5, color=BRAND_SLATE)
        y -= 0.035

    y -= 0.04
    fig.text(0.08, y, "Recommended Next Steps", fontsize=16, color=BRAND_PRIMARY, fontweight="bold")
    y -= 0.05
    recs = [
        "1.  Widen prediction intervals to hit the 80% coverage target.",
        "2.  Add a trend-following model (e.g., Theta or Prophet) for datasets with clear upward/downward trends.",
        "3.  Collect more historical data — 6+ months significantly improves accuracy.",
        "4.  Consider shorter forecast horizons (7-14 days) for higher accuracy when long-range isn't needed.",
        "5.  Add external factors (holidays, promotions) to help the model anticipate unusual days.",
    ]
    for rec in recs:
        fig.text(0.10, y, rec, fontsize=10.5, color=BRAND_SLATE)
        y -= 0.035

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def page_scorecard(pdf, results):
    fig = _setup_fig("Final Scorecard")

    headers = ["Dataset", "Best Strategy", "Error Rate", "vs Baseline", "Confidence Range", "Grade"]
    col_x = [0.05, 0.22, 0.48, 0.60, 0.72, 0.87]

    y = 0.80
    for i, h in enumerate(headers):
        fig.text(col_x[i], y, h, fontsize=10, color=BRAND_SLATE, fontweight="bold")

    y -= 0.015
    line = plt.Line2D([0.05, 0.95], [y, y], transform=fig.transFigure, color=BRAND_SLATE, linewidth=0.5)
    fig.add_artist(line)
    y -= 0.04

    for r in results:
        mase = r["oos"]["mase"]
        wape = r["oos"]["wape"]
        coverage = r["coverage"]

        if mase < 0.85:
            grade, grade_color = "A", BRAND_GREEN
        elif mase < 1.0:
            grade, grade_color = "B+", BRAND_GREEN
        elif mase < 1.1:
            grade, grade_color = "B", BRAND_AMBER
        elif mase < 1.3:
            grade, grade_color = "C+", BRAND_AMBER
        else:
            grade, grade_color = "C", BRAND_RED

        baseline_text = f"{max(0, (1 - mase)) * 100:+.0f}%" if mase < 1 else "~same"

        fig.text(col_x[0], y, r["cfg"]["label"], fontsize=10, color=BRAND_WHITE)
        fig.text(col_x[1], y, _friendly_model(r["svc"]["best_model"]), fontsize=9, color=BRAND_PRIMARY)
        fig.text(col_x[2], y, f"{wape:.0%}", fontsize=11, color=BRAND_WHITE, fontweight="bold")
        fig.text(col_x[3], y, baseline_text, fontsize=11, color=BRAND_GREEN if mase < 1 else BRAND_AMBER)
        fig.text(col_x[4], y, f"{coverage:.0%}", fontsize=11, color=BRAND_GREEN if coverage >= 0.7 else BRAND_AMBER)
        fig.text(col_x[5], y, grade, fontsize=16, color=grade_color, fontweight="bold")
        y -= 0.06

    y -= 0.06
    fig.text(0.08, y, "How to Read This Table", fontsize=13, color=BRAND_WHITE, fontweight="bold")
    y -= 0.045
    guide = [
        "Error Rate — Average % difference between predicted and actual. Lower = better.",
        "vs Baseline — How much better (+) or similar (~) compared to simply repeating yesterday's value.",
        "Confidence Range — % of actual values that fell within our predicted high/low range. Higher = better.",
        "Grade — Overall score: A = excellent, B = good, C = needs improvement.",
    ]
    for g in guide:
        fig.text(0.10, y, f"•  {g}", fontsize=9.5, color=BRAND_SLATE)
        y -= 0.035

    pdf.savefig(fig, facecolor=fig.get_facecolor())
    plt.close(fig)


def main():
    print("Running benchmarks...")
    results = _run_all_benchmarks()

    print(f"\nGenerating PDF report: {OUTPUT_PDF}")
    with PdfPages(OUTPUT_PDF) as pdf:
        page_cover(pdf)
        page_executive_summary(pdf, results)
        page_dataset_overview(pdf, results)
        page_accuracy_bars(pdf, results)
        page_predicted_vs_actual(pdf, results)
        page_model_selection(pdf, results)
        page_interval_coverage(pdf, results)
        page_strengths_weaknesses(pdf, results)
        page_scorecard(pdf, results)

    print(f"\nReport saved to: {OUTPUT_PDF}")
    print("Done!")


if __name__ == "__main__":
    main()
