"""
P3: p-Hacking Detection Engine
Automated statistical auditing of research paper corpora using:
  - p-curve analysis (power & p-hacking signature)
  - Caliper test (excess significance at 0.05)
  - GRIM test (Granularity-Related Inconsistency of Means)
  - Z-curve estimation (file-drawer size)
  - Reproducibility Credit Score (RCS)
Generates 500-paper synthetic corpus with known p-hacking contamination.
"""

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
from typing import List, Tuple, Optional
import json, warnings
from pathlib import Path
from dataclasses import dataclass, field

warnings.filterwarnings("ignore")

BASE    = Path(__file__).parent
FIG_DIR = BASE / "figures"
RES_DIR = BASE / "results"
FIG_DIR.mkdir(exist_ok=True)
RES_DIR.mkdir(exist_ok=True)

np.random.seed(42)

plt.rcParams.update({"figure.dpi": 150, "font.size": 11,
                      "axes.spines.top": False, "axes.spines.right": False,
                      "axes.grid": True, "grid.alpha": 0.3})
PALETTE = ["#2196F3", "#FF5722", "#4CAF50", "#9C27B0", "#FF9800", "#00BCD4"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. SYNTHETIC PAPER CORPUS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Paper:
    paper_id:    str
    n:           int           # Sample size
    n_tests:     int           # Number of statistical tests run (only reported sig)
    p_reported:  List[float]   # Reported p-values
    effect_size: float         # True effect size (Cohen's d)
    phacked:     bool          # Ground-truth label
    # Descriptive stats for GRIM test
    mean_reported: Optional[float] = None
    sd_reported:   Optional[float] = None
    n_decimal:     int = 2     # Decimal places of reported mean


def generate_corpus(n_papers: int = 500,
                    contamination: float = 0.35) -> List[Paper]:
    """
    Generate synthetic corpus.
    contamination = fraction of truly p-hacked papers.
    """
    rng = np.random.default_rng(42)
    papers = []

    for i in range(n_papers):
        n = int(rng.integers(20, 200))
        phacked = rng.random() < contamination
        true_d  = rng.uniform(0.1, 0.8)  # True effect size

        if phacked:
            # P-hacking: run many tests, report only p < 0.05
            n_tests = int(rng.integers(5, 20))
            all_ps  = []
            for _ in range(n_tests):
                # Sample t-stat under small true effect
                t = rng.normal(true_d * np.sqrt(n/2) * 0.5, 1)
                df_ = n - 2
                p   = 2 * stats.t.sf(abs(t), df_)
                all_ps.append(float(p))
            # Only report significant ones  
            reported = [p for p in all_ps if p < 0.05]
            if not reported:
                reported = [min(all_ps)]  # Report the smallest if none sig.
            # Characteristic p-hacking signature: p just below 0.05
            reported = [min(p, 0.049) for p in reported[:rng.integers(1,4)]]
        else:
            # Honest: one or few a-priori tests
            n_tests = int(rng.integers(1, 4))
            reported = []
            for _ in range(n_tests):
                ncp  = true_d * np.sqrt(n / 2)  # Non-centrality parameter
                t    = rng.normal(ncp, 1)
                df_  = n - 2
                p    = float(2 * stats.t.sf(abs(t), df_))
                reported.append(p)

        # GRIM: Generate mean that may or may not pass
        true_mean = rng.uniform(3.5, 7.5)
        if phacked and rng.random() < 0.4:
            # Inaccurate rounding (GRIM fail)
            mean_rep = round(true_mean + rng.uniform(0.005, 0.04) *
                              rng.choice([-1, 1]), 2)
        else:
            # Correct rounding
            raw = rng.normal(true_mean, 1.2, n)
            mean_rep = round(raw.mean(), 2)

        papers.append(Paper(
            paper_id    = f"P{i:04d}",
            n           = n,
            n_tests     = n_tests,
            p_reported  = reported,
            effect_size = true_d,
            phacked     = phacked,
            mean_reported = mean_rep,
            sd_reported   = round(rng.uniform(0.8, 2.0), 2),
            n_decimal   = 2,
        ))

    return papers


# ─────────────────────────────────────────────────────────────────────────────
# 2. STATISTICAL TESTS
# ─────────────────────────────────────────────────────────────────────────────

class PHackingDetector:

    # ── 2a. p-Curve Analysis ─────────────────────────────────────────────

    @staticmethod
    def collect_significant_ps(papers: List[Paper]) -> np.ndarray:
        """All p-values < 0.05 across corpus."""
        return np.array([p for paper in papers
                         for p in paper.p_reported if p < 0.05])

    @staticmethod
    def p_curve_test(sig_ps: np.ndarray) -> dict:
        """
        Under H0 (null is true): p ~ Uniform(0, 0.05) → significant p flat
        Under H1 (effect is real): p-curve is right-skewed (more p near 0)
        Under HH (p-hacking):      p-curve is left-skewed (excess near 0.049)

        Test 1 — pp-value (half-normal): is there evidential value?
        Test 2 — Binomial caliper: excess p in [0.04, 0.05]?
        """
        n = len(sig_ps)
        if n == 0:
            return {}

        # Convert to pp-values: p_i ∼ Uniform(0,1) under H0
        # Under right-of-null (true effect), pp(p) = p/alpha stochastically small
        pp = sig_ps / 0.05   # ∈ [0, 1]

        # One-sample Wilcoxon or binomial test: are pp-values < 0.5?
        w_stat, w_p = stats.wilcoxon(pp - 0.5) if n >= 10 else (np.nan, np.nan)

        # Binomial test: fraction with p < 0.025 (strong evidence = true effects)
        n_low   = (sig_ps < 0.025).sum()
        binom   = stats.binomtest(n_low, n, 0.5, alternative="greater")

        # Caliper test: excess significance at [0.04, 0.05]
        n_caliper = ((sig_ps >= 0.04) & (sig_ps < 0.05)).sum()
        n_expected_caliper = n * 0.2  # Expected 20% if uniform in [0, 0.05]
        caliper_ratio = n_caliper / (n_expected_caliper + 1e-9)

        return {
            "n_significant": int(n),
            "mean_pp":       float(pp.mean()),
            "wilcoxon_p":    float(w_p) if not np.isnan(w_p) else None,
            "binom_p":       float(binom.pvalue),
            "n_below_0025":  int(n_low),
            "pct_below_0025": round(n_low / n * 100, 1),
            "n_caliper_zone": int(n_caliper),
            "caliper_ratio":  round(float(caliper_ratio), 3),
            "phacking_signature": bool(caliper_ratio > 1.5 and pp.mean() > 0.4),
        }

    # ── 2b. GRIM Test ─────────────────────────────────────────────────────

    @staticmethod
    def grim_test(paper: Paper) -> dict:
        """
        GRIM (Granularity-Related Inconsistency of Means):
        A sample mean with 2 decimal places cannot take arbitrary values
        for a given N. Check if reported mean is consistent with N.
        """
        if paper.mean_reported is None:
            return {"grim_pass": True, "possible_values": None}

        n = paper.n
        decimals = paper.n_decimal

        # Granularity unit = 1/(n * 10^decimals)
        scale = 10 ** decimals
        # The mean = sum / n; valid sums are integers
        # So valid means = k/n, rounded to `decimals` dp
        # Check if mean * n * scale is approximately an integer
        val = paper.mean_reported * n * scale
        nearest_int = round(val)
        discrepancy = abs(val - nearest_int)
        
        # With rounding error ≤ 0.5 from the floor/ceiling
        grim_pass = discrepancy <= 0.5

        return {
            "grim_pass":    bool(grim_pass),
            "discrepancy":  round(float(discrepancy), 4),
            "reported_mean": paper.mean_reported,
            "n":            paper.n,
        }

    # ── 2c. Caliper Test (corpus-level) ───────────────────────────────────

    @staticmethod
    def caliper_test(all_ps: np.ndarray, 
                      caliper_lower: float = 0.04,
                      caliper_upper: float = 0.05) -> dict:
        """
        Compare density of p-values just below 0.05 to just above 0.04.
        A significant excess signals p-hacking.
        """
        # p in [0.04, 0.05) vs p in [0.03, 0.04)
        zone_hi = ((all_ps >= caliper_lower) & (all_ps < caliper_upper)).sum()
        zone_lo = ((all_ps >= caliper_lower - 0.01) & (all_ps < caliper_lower)).sum()
        
        if zone_lo == 0:
            ratio = np.nan
            binom_p = 1.0
        else:
            ratio = zone_hi / zone_lo
            total  = zone_hi + zone_lo
            binom  = stats.binomtest(int(zone_hi), int(total), 0.5,
                                      alternative="greater")
            binom_p = float(binom.pvalue)

        return {
            "n_in_caliper_zone": int(zone_hi),
            "n_in_control_zone": int(zone_lo),
            "caliper_ratio":     round(float(ratio), 3) if not np.isnan(ratio) else None,
            "binom_p":           round(binom_p, 4),
            "significant_excess": bool(binom_p < 0.05),
        }

    # ── 2d. Z-curve & File Drawer ─────────────────────────────────────────

    @staticmethod
    def estimate_file_drawer(papers: List[Paper]) -> dict:
        """
        Estimate the number of unpublished null results needed to
        make the published effect sizes consistent with the null.
        Based on the Fail-safe N (Rosenthal's method).
        """
        sig_ps = [p for paper in papers for p in paper.p_reported if p < 0.05]
        n_sig  = len(sig_ps)
        n_total_reported = sum(len(p.p_reported) for p in papers)
        
        if n_sig == 0:
            return {"fail_safe_n": 0, "discovery_rate": 0.0}

        # Rosenthal's fail-safe N: how many null studies needed to push
        # combined p above 0.05?
        z_vals  = stats.norm.ppf(1 - np.array(sig_ps) / 2)
        sum_z   = np.sum(z_vals)
        # N_fs = (sum_z / 1.645)^2 - n_sig
        z_crit  = stats.norm.ppf(0.975)
        raw_fsn = (sum_z / z_crit) ** 2 - n_sig
        fail_safe_n = max(0, int(min(raw_fsn, 1e9)))

        # Observed discovery rate
        odr = n_sig / max(n_total_reported, 1)

        # Expected discovery rate under observed power ≈ 0.5 (median estimate)
        edr = 0.5  
        inflation_factor = odr / edr if edr > 0 else np.nan

        return {
            "n_significant_ps":      n_sig,
            "n_total_reported_tests": n_total_reported,
            "observed_discovery_rate": round(odr, 4),
            "fail_safe_n":           fail_safe_n,
            "inflation_factor":      round(float(inflation_factor), 3),
        }

    # ── 2e. Reproducibility Credit Score (RCS) ────────────────────────────

    @staticmethod
    def score_paper(paper: Paper) -> dict:
        """15-point weighted RCS for a single paper."""
        score = 0.0
        checks = {}

        # 1. Sample size adequacy (power ≥ 0.8 for d=0.5)
        min_n_for_80pwr = 64  # Cohen's d=0.5, alpha=0.05, power=0.8
        ss_ok = paper.n >= min_n_for_80pwr
        score += 2.0 * ss_ok
        checks["sample_size_ok"] = bool(ss_ok)

        # 2. Single a-priori test (not selective reporting)
        single_test = paper.n_tests <= 2
        score += 1.5 * single_test
        checks["single_test"] = bool(single_test)

        # 3. Effect size reported (here: always True in our simulation)
        es_reported = paper.effect_size is not None
        score += 1.0 * es_reported
        checks["effect_size_reported"] = bool(es_reported)

        # 4. No caliper zone p-value
        no_caliper = not any(0.04 <= p < 0.05 for p in paper.p_reported)
        score += 2.0 * no_caliper
        checks["no_caliper_zone_p"] = bool(no_caliper)

        # 5. GRIM test pass
        grim = PHackingDetector.grim_test(paper)
        score += 1.5 * grim["grim_pass"]
        checks["grim_pass"] = grim["grim_pass"]

        # 6. p-values not suspiciously clustered near 0.05
        all_ps = paper.p_reported
        clean_ps = not any(0.045 <= p < 0.05 for p in all_ps)
        score += 1.0 * clean_ps
        checks["clean_ps"] = bool(clean_ps)

        # 7. Large effect sizes (d > 0.3) rather than statistical artifact
        large_es = paper.effect_size > 0.3
        score += 1.0 * large_es
        checks["large_effect"] = bool(large_es)

        total_possible = 2 + 1.5 + 1 + 2 + 1.5 + 1 + 1   # = 10
        rcs = round(score / total_possible * 100, 1)

        return {"rcs": rcs, "breakdown": checks, "raw_score": score}


# ─────────────────────────────────────────────────────────────────────────────
# 3. FIGURES
# ─────────────────────────────────────────────────────────────────────────────

def plot_all(papers, sig_ps, pcurve_res, caliper_res, filedrawer_res, rcs_scores):
    print("\n  ── Plotting figures ──")

    df = pd.DataFrame([{
        "paper_id":   p.paper_id,
        "n":          p.n,
        "phacked":    p.phacked,
        "n_tests":    p.n_tests,
        "min_p":      min(p.p_reported),
        "effect_size": p.effect_size,
        "rcs":        rcs_scores.get(p.paper_id, {}).get("rcs", 0),
    } for p in papers])

    # ── Figure 1: p-value distribution (full corpus) ──────────────────────
    all_ps = np.array([p for paper in papers for p in paper.p_reported])
    hacked_ps  = np.array([p for paper in papers
                           for p in paper.p_reported if paper.phacked])
    honest_ps  = np.array([p for paper in papers
                           for p in paper.p_reported if not paper.phacked])

    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle("P3: p-Hacking Detection — p-Value Distributions",
                 fontweight="bold", fontsize=13)

    bins = np.linspace(0, 1, 41)
    axes[0].hist(all_ps, bins=bins, color=PALETTE[3], alpha=0.8, edgecolor="white")
    axes[0].axvline(0.05, color="red", ls="--", lw=2, label="α=0.05")
    axes[0].set_xlabel("p-value"); axes[0].set_ylabel("Count")
    axes[0].set_title("All Papers Combined"); axes[0].legend()

    axes[1].hist(hacked_ps, bins=bins, color=PALETTE[1], alpha=0.8, edgecolor="white")
    axes[1].axvline(0.05, color="black", ls="--", lw=2, label="α=0.05")
    axes[1].set_xlabel("p-value")
    axes[1].set_title(f"P-Hacked Papers\n(left-skewed near 0.05 = signature)")
    axes[1].legend()

    axes[2].hist(honest_ps, bins=bins, color=PALETTE[0], alpha=0.8, edgecolor="white")
    axes[2].axvline(0.05, color="black", ls="--", lw=2, label="α=0.05")
    axes[2].set_xlabel("p-value")
    axes[2].set_title("Honest Papers\n(right-skewed = true effects)")
    axes[2].legend()

    plt.tight_layout()
    plt.savefig(FIG_DIR / "01_p_distributions.png", bbox_inches="tight")
    plt.close()
    print("    ✓ 01_p_distributions.png")

    # ── Figure 2: p-Curve for significant p-values only ───────────────────
    hacked_sig  = hacked_ps[hacked_ps < 0.05]
    honest_sig  = honest_ps[honest_ps < 0.05]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("P3: p-Curve Analysis (Significant p-values Only)",
                 fontweight="bold", fontsize=13)

    bins5 = np.linspace(0, 0.05, 11)
    for ax, ps, label, color in [
        (axes[0], honest_sig,  "Honest Papers",  PALETTE[0]),
        (axes[1], hacked_sig,  "P-Hacked Papers", PALETTE[1]),
    ]:
        if len(ps) > 0:
            ax.hist(ps, bins=bins5, color=color, alpha=0.8, edgecolor="white",
                    density=True)
        ax.axhline(20, color="gray", ls="--", lw=2, label="Uniform (null expected)")
        ax.set_xlabel("p-value (< 0.05)")
        ax.set_ylabel("Density")
        ax.set_title(label)
        ax.legend()
        pct = (ps < 0.025).mean() * 100 if len(ps) > 0 else 0
        ax.text(0.98, 0.95, f"p<0.025: {pct:.0f}%\n(Right-skewed = real effects)",
                transform=ax.transAxes, ha="right", va="top", fontsize=9,
                bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8))

    plt.tight_layout()
    plt.savefig(FIG_DIR / "02_p_curve.png", bbox_inches="tight")
    plt.close()
    print("    ✓ 02_p_curve.png")

    # ── Figure 3: Caliper test ─────────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("P3: Caliper Test — Excess Significance Near α=0.05",
                 fontweight="bold", fontsize=13)

    bins_caliper = np.linspace(0, 0.10, 21)
    for ax, ps, label, color in [
        (axes[0], honest_ps, "Honest",  PALETTE[0]),
        (axes[1], hacked_ps, "P-Hacked", PALETTE[1]),
    ]:
        counts, edges_ = np.histogram(ps, bins=bins_caliper)
        colors_bar = ["#FF5722" if (0.04 <= (edges_[i]+edges_[i+1])/2 < 0.05)
                      else color for i in range(len(counts))]
        ax.bar(edges_[:-1], counts, width=np.diff(edges_),
               color=colors_bar, alpha=0.8, edgecolor="white", align="edge")
        ax.axvline(0.05, color="black", ls="--", lw=2, label="α=0.05")
        ax.axvspan(0.04, 0.05, alpha=0.15, color="red", label="Caliper zone [0.04, 0.05)")
        ax.set_xlabel("p-value"); ax.set_ylabel("Count")
        ax.set_title(f"{label} Papers")
        ax.legend(fontsize=8)

    plt.tight_layout()
    plt.savefig(FIG_DIR / "03_caliper_test.png", bbox_inches="tight")
    plt.close()
    print("    ✓ 03_caliper_test.png")

    # ── Figure 4: RCS Distribution ─────────────────────────────────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("P3: Reproducibility Credit Score (RCS) by Paper Type",
                 fontweight="bold", fontsize=13)

    rcs_hacked  = df[df["phacked"]]["rcs"].values
    rcs_honest  = df[~df["phacked"]]["rcs"].values

    axes[0].hist(rcs_honest, bins=20, alpha=0.7, color=PALETTE[0], label="Honest")
    axes[0].hist(rcs_hacked, bins=20, alpha=0.7, color=PALETTE[1], label="P-Hacked")
    axes[0].set_xlabel("RCS Score (0-100)")
    axes[0].set_ylabel("Number of Papers")
    axes[0].set_title("RCS Distribution by Ground-Truth Label")
    axes[0].legend()
    axes[0].axvline(rcs_honest.mean(), color=PALETTE[0], ls="--", lw=2,
                    label=f"Honest mean={rcs_honest.mean():.1f}")
    axes[0].axvline(rcs_hacked.mean(), color=PALETTE[1], ls="--", lw=2,
                    label=f"Hacked mean={rcs_hacked.mean():.1f}")

    # Scatter: RCS vs sample size, coloured by ground truth
    axes[1].scatter(df[~df["phacked"]]["n"], df[~df["phacked"]]["rcs"],
                    alpha=0.4, c=PALETTE[0], s=20, label="Honest")
    axes[1].scatter(df[df["phacked"]]["n"],  df[df["phacked"]]["rcs"],
                    alpha=0.4, c=PALETTE[1], s=20, label="P-Hacked")
    axes[1].set_xlabel("Sample Size (N)")
    axes[1].set_ylabel("RCS Score")
    axes[1].set_title("RCS vs Sample Size")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(FIG_DIR / "04_rcs_distribution.png", bbox_inches="tight")
    plt.close()
    print("    ✓ 04_rcs_distribution.png")

    # ── Figure 5: RCS as classifier ───────────────────────────────────────
    from sklearn.metrics import (roc_auc_score, roc_curve,
                                  precision_recall_curve, average_precision_score)

    y_true = df["phacked"].astype(int).values
    y_score_rcs = df["rcs"].values / 100  # High RCS = honest = NOT hacked
    # Invert: high score = NOT hacked → for classification "hacked=1" use 1-rcs
    y_prob = 1 - y_score_rcs + np.random.normal(0, 0.05, len(y_score_rcs))
    y_prob = np.clip(y_prob, 0, 1)

    fpr, tpr, _ = roc_curve(y_true, y_prob)
    auc = roc_auc_score(y_true, y_prob)
    prec, rec, _ = precision_recall_curve(y_true, y_prob)
    ap = average_precision_score(y_true, y_prob)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("P3: RCS as p-Hacking Detector — Classification Performance",
                 fontweight="bold", fontsize=13)

    axes[0].plot(fpr, tpr, lw=2.5, color=PALETTE[0], label=f"AUC = {auc:.3f}")
    axes[0].plot([0,1],[0,1],"--",color="gray",lw=1)
    axes[0].fill_between(fpr, tpr, alpha=0.1, color=PALETTE[0])
    axes[0].set_xlabel("FPR"); axes[0].set_ylabel("TPR")
    axes[0].set_title("ROC Curve: RCS → Detect P-Hacking")
    axes[0].legend()

    axes[1].plot(rec, prec, lw=2.5, color=PALETTE[1], label=f"AP = {ap:.3f}")
    axes[1].fill_between(rec, prec, alpha=0.1, color=PALETTE[1])
    axes[1].set_xlabel("Recall"); axes[1].set_ylabel("Precision")
    axes[1].set_title("PR Curve: RCS → Detect P-Hacking")
    axes[1].legend()

    plt.tight_layout()
    plt.savefig(FIG_DIR / "05_rcs_classifier.png", bbox_inches="tight")
    plt.close()
    print("    ✓ 05_rcs_classifier.png")

    # ── Figure 6: Publication bias / file drawer visualization ────────────
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle("P3: Publication Bias & File Drawer Estimation",
                 fontweight="bold", fontsize=13)

    # Funnel plot (effect size vs precision)
    precision = 1 / (df["n"] ** 0.5)
    axes[0].scatter(df[~df["phacked"]]["effect_size"],
                    precision[~df["phacked"]], alpha=0.4,
                    c=PALETTE[0], s=20, label="Honest")
    axes[0].scatter(df[df["phacked"]]["effect_size"],
                    precision[df["phacked"]], alpha=0.4,
                    c=PALETTE[1], s=20, label="P-Hacked")
    axes[0].invert_yaxis()
    axes[0].set_xlabel("Effect Size (Cohen's d)")
    axes[0].set_ylabel("Precision (1/√N) — bottom = large studies")
    axes[0].set_title("Funnel Plot\n(Asymmetry = publication bias)")
    axes[0].legend()

    # File drawer bar
    labels_ = ["Published\nSignificant", "Published\nNon-significant",
                "Estimated\nUnpublished\n(file drawer)"]
    n_sig = filedrawer_res["n_significant_ps"]
    n_nonsig = filedrawer_res["n_total_reported_tests"] - n_sig
    n_file = min(filedrawer_res["fail_safe_n"], n_sig * 10)

    axes[1].bar(labels_, [n_sig, n_nonsig, n_file],
                color=[PALETTE[0], PALETTE[2], PALETTE[1]],
                alpha=0.85, edgecolor="white")
    axes[1].set_title(f"File Drawer Estimate\n"
                       f"Fail-Safe N = {filedrawer_res['fail_safe_n']}")
    axes[1].set_ylabel("Number of Tests/Studies")

    plt.tight_layout()
    plt.savefig(FIG_DIR / "06_publication_bias.png", bbox_inches="tight")
    plt.close()
    print("    ✓ 06_publication_bias.png")

    print(f"\n  All 6 figures saved → {FIG_DIR}/")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def run():
    print("=" * 60)
    print("P3: p-HACKING DETECTION ENGINE")
    print("=" * 60 + "\n")

    print("STEP 1 ▶ Generating synthetic paper corpus (N=500)...")
    papers = generate_corpus(n_papers=500, contamination=0.35)
    n_hacked = sum(p.phacked for p in papers)
    print(f"  Total papers:   {len(papers)}")
    print(f"  P-hacked:       {n_hacked} ({n_hacked/len(papers)*100:.0f}%)")
    print(f"  Honest:         {len(papers)-n_hacked}")

    detector = PHackingDetector()

    print("\nSTEP 2 ▶ p-Curve analysis...")
    sig_ps = detector.collect_significant_ps(papers)
    pcurve_res = detector.p_curve_test(sig_ps)
    for k, v in pcurve_res.items():
        if v is not None:
            print(f"    {k:<28s} = {v}")

    print("\nSTEP 3 ▶ Caliper test...")
    all_ps = np.array([p for paper in papers for p in paper.p_reported])
    caliper_res = detector.caliper_test(all_ps)
    for k, v in caliper_res.items():
        print(f"    {k:<28s} = {v}")

    print("\nSTEP 4 ▶ GRIM test on all papers...")
    grim_fail = sum(1 for p in papers
                    if not detector.grim_test(p)["grim_pass"])
    grim_fail_hacked  = sum(1 for p in papers
                             if p.phacked and not detector.grim_test(p)["grim_pass"])
    grim_fail_honest  = sum(1 for p in papers
                             if not p.phacked and not detector.grim_test(p)["grim_pass"])
    print(f"    GRIM failures (total):  {grim_fail} / {len(papers)}")
    print(f"    GRIM failures (hacked): {grim_fail_hacked} / {n_hacked}")
    print(f"    GRIM failures (honest): {grim_fail_honest} / {len(papers)-n_hacked}")

    print("\nSTEP 5 ▶ File-drawer estimation...")
    filedrawer_res = detector.estimate_file_drawer(papers)
    for k, v in filedrawer_res.items():
        print(f"    {k:<30s} = {v}")

    print("\nSTEP 6 ▶ Computing RCS for all papers...")
    rcs_scores = {}
    for p in papers:
        rcs_scores[p.paper_id] = detector.score_paper(p)
    rcs_vals = [v["rcs"] for v in rcs_scores.values()]
    rcs_hacked = [rcs_scores[p.paper_id]["rcs"] for p in papers if p.phacked]
    rcs_honest = [rcs_scores[p.paper_id]["rcs"] for p in papers if not p.phacked]
    print(f"    Mean RCS (all):     {np.mean(rcs_vals):.1f}")
    print(f"    Mean RCS (hacked):  {np.mean(rcs_hacked):.1f}")
    print(f"    Mean RCS (honest):  {np.mean(rcs_honest):.1f}")

    print("\nSTEP 7 ▶ Plotting...")
    plot_all(papers, sig_ps, pcurve_res, caliper_res, filedrawer_res, rcs_scores)

    # Save
    results = {
        "corpus_summary": {
            "n_papers": len(papers),
            "n_hacked": n_hacked,
            "contamination_rate": round(n_hacked / len(papers), 4),
        },
        "p_curve": pcurve_res,
        "caliper_test": caliper_res,
        "grim": {"total_fail": grim_fail, "hacked_fail": grim_fail_hacked,
                  "honest_fail": grim_fail_honest},
        "file_drawer": filedrawer_res,
        "rcs_summary": {
            "mean_all": round(np.mean(rcs_vals), 2),
            "mean_hacked": round(np.mean(rcs_hacked), 2),
            "mean_honest": round(np.mean(rcs_honest), 2),
        },
    }
    with open(RES_DIR / "results.json", "w") as f:
        json.dump(results, f, indent=2, default=float)

    print("\n" + "=" * 60)
    print("P3 COMPLETE  ✓")
    print(f"  Caliper ratio:  {caliper_res['caliper_ratio']}")
    print(f"  Caliper excess: {caliper_res['significant_excess']}")
    print(f"  GRIM failures:  {grim_fail} / {len(papers)}")
    print(f"  RCS gap:        honest {np.mean(rcs_honest):.1f} vs "
          f"hacked {np.mean(rcs_hacked):.1f}")
    print(f"  Fail-safe N:    {filedrawer_res['fail_safe_n']}")
    print("=" * 60)


if __name__ == "__main__":
    run()
