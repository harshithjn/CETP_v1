"""
N4: conformal risk control for the SLA gate (TASKS.md Tier 3, item 42).

Tier 1 built a conformal gate at a FIXED miscoverage alpha and observed that its PASS state
honoured the implied bound. That is coverage control, not risk control: it guarantees a
property of the prediction interval, not of the DECISION the gate makes.

What a deployment owner actually states is a risk budget: "no more than 5% of the queries I
approve may breach the SLA." Conformal Risk Control (Angelopoulos, Bates, Candes, Jordan &
Lei; see also Bates et al. on RCPS) turns that sentence into a threshold with a finite-sample
guarantee, which is what this does.

Setup. The gate applies a safety multiplier lambda to the predicted scaling factor and
approves when the inflated prediction still clears the SLA:

    PASS(lambda)  <=>  lambda * predicted_scaling <= tau

Risk is the rate of WRONGLY APPROVED deployments over all candidates:

    R(lambda) = E[ 1{ actual > tau  AND  PASS(lambda) } ]

This is monotonically non-increasing in lambda (a larger multiplier approves strictly fewer
queries), which is the condition CRC requires. The calibrated threshold is

    lambda_hat = inf { lambda : (n * R_hat(lambda) + 1) / (n + 1) <= alpha }

with the (n+1) correction giving the finite-sample bound rather than an asymptotic one.
Calibration uses the training machines; the guarantee is then tested on the held-out machine.

Note R(lambda) is defined over ALL candidates, not conditionally on PASS. Conditional risk
P(breach | PASS) is not monotone in lambda and cannot be controlled this way -- a real
limitation, reported rather than hidden, since "5% of what I approve" is the more natural
reading of the sentence.

Writes results/tier3_risk_control.csv and results/tier3_risk_control.md.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from canonical_evaluation import (  # noqa: E402
    COMPUTE_MULTI, RESULTS_DIR, attach_probabilities, build_pairs, fit_classifier,
    load_cells,
)

ALPHAS = [0.01, 0.05, 0.10]
TAUS = [1.2, 1.5, 2.0]
LAMBDA_GRID = np.round(np.arange(0.5, 4.001, 0.01), 3)


def risk(actual, pred, tau, lam):
    """Rate of wrongly approved deployments over all candidates."""
    passes = lam * pred <= tau
    return float(np.mean((actual > tau) & passes))


def crc_lambda(actual, pred, tau, alpha):
    """Smallest lambda whose finite-sample risk bound meets the budget."""
    n = len(actual)
    for lam in LAMBDA_GRID:
        r = risk(actual, pred, tau, lam)
        if (n * r + 1.0) / (n + 1.0) <= alpha:
            return float(lam)
    return float(LAMBDA_GRID[-1])


def main():
    cells = load_cells()
    pairs, machines = build_pairs(cells, COMPUTE_MULTI)

    rows = []
    for held_out in machines:
        train_machines = [m for m in machines if m != held_out]
        clf = fit_classifier(cells[cells["machine_id"].isin(train_machines)])
        tr = attach_probabilities(
            pairs[pairs["dev_machine"].isin(train_machines)
                  & pairs["prod_machine"].isin(train_machines)], clf)
        te = attach_probabilities(
            pairs[(pairs["dev_machine"] == held_out)
                  | (pairs["prod_machine"] == held_out)], clf)

        for pred_name in ("cmp_only", "analytical"):
            tr_pred = (1.0 / tr["compute_ratio"]).to_numpy() if pred_name == "cmp_only" \
                else tr["analytical_scaling"].to_numpy()
            te_pred = (1.0 / te["compute_ratio"]).to_numpy() if pred_name == "cmp_only" \
                else te["analytical_scaling"].to_numpy()
            tr_act = tr["scaling_factor_p50"].to_numpy()
            te_act = te["scaling_factor_p50"].to_numpy()

            for tau in TAUS:
                for alpha in ALPHAS:
                    lam = crc_lambda(tr_act, tr_pred, tau, alpha)
                    passes = lam * te_pred <= tau
                    breaches = te_act > tau
                    n_pass = int(passes.sum())
                    rows.append({
                        "held_out_machine": held_out, "predictor": pred_name,
                        "tau": tau, "alpha": alpha, "lambda": lam,
                        "n_test": len(te_act),
                        "pass_rate": float(passes.mean()),
                        "realised_risk": float(np.mean(breaches & passes)),
                        "breach_rate_given_pass": (float(breaches[passes].mean())
                                                   if n_pass else np.nan),
                        "base_breach_rate": float(breaches.mean()),
                    })

    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "tier3_risk_control.csv", index=False)

    L = ["# N4: conformal risk control for the SLA gate (Tier 3, item 42)", "",
         "A deployment owner states a risk budget — *\"no more than 5% of the queries I approve "
         "may breach the SLA\"* — and this derives the gate threshold that honours it, with a "
         "finite-sample guarantee, instead of hand-tuning a constant.", "",
         "The gate approves when `lambda * predicted_scaling <= tau`. `lambda` is calibrated on "
         "the four training machines by conformal risk control and then tested on the held-out "
         "machine, which the calibration never saw.", ""]

    for pred in ("cmp_only", "analytical"):
        sub = df[df["predictor"] == pred]
        L += [f"## Predictor: `{pred}`", "",
              "| tau | risk budget | lambda | approved | realised risk | breach rate among "
              "approved | budget met |",
              "|---:|---:|---:|---:|---:|---:|---|"]
        for tau in TAUS:
            for alpha in ALPHAS:
                g = sub[(sub["tau"] == tau) & (sub["alpha"] == alpha)]
                lam = g["lambda"].mean()
                rr = g["realised_risk"].mean()
                pr = g["pass_rate"].mean()
                bg = g["breach_rate_given_pass"].mean()
                worst = g["realised_risk"].max()
                met = "yes" if worst <= alpha + 1e-9 else f"**no** (worst fold {worst:.1%})"
                L.append(f"| {tau} | {alpha:.0%} | {lam:.2f} | {pr:.1%} | {rr:.2%} | "
                         + (f"{bg:.1%}" if not np.isnan(bg) else "—") + f" | {met} |")
        L.append("")

    main_tau = 1.5
    sub = df[(df["predictor"] == "cmp_only") & (df["tau"] == main_tau)]
    # How often does the realised risk on the held-out machine exceed its budget?
    worst = df.groupby(["predictor", "tau", "alpha"])["realised_risk"].max().reset_index()
    worst["over_budget"] = worst["realised_risk"] > worst["alpha"] + 1e-9
    n_viol, n_tot = int(worst["over_budget"].sum()), len(worst)

    L += ["## The guarantee does not survive an unseen machine", "",
          f"**{n_viol} of {n_tot} configurations exceed their stated risk budget on the "
          f"held-out machine.** The thresholds are calibrated correctly — on the calibration "
          f"machines the bound holds by construction — but the realised risk on a machine the "
          f"calibration never saw routinely overshoots, in the worst cases by several times "
          f"the budget.", "",
          "This is not an implementation defect. Conformal risk control guarantees its bound "
          "**under exchangeability** between calibration and test data. A held-out machine is "
          "not exchangeable with the training machines — that is precisely the finding the c5a "
          "outlier established in Phase 7 and that Tier 1 reproduced. The guarantee is void in "
          "exactly the setting the product cares about, and the honest contribution here is "
          "**measuring by how much it is void** rather than reporting the calibration as a "
          "success.", "",
          "Tier 1 found the same thing from the coverage side: marginal coverage held on "
          "average (97.0% at nominal 95%) while conditional coverage on the worst machine "
          "failed (90.5%). Two independent formulations, same conclusion. Weighting by "
          "hardware distance was tried there and bought nothing, because four calibration "
          "machines give only four distinct weights.", "",
          f"At tau = {main_tau} the base breach rate is "
          f"{sub['base_breach_rate'].mean():.1%}, and the derived multipliers are:", ""]
    for alpha in ALPHAS:
        g = sub[sub["alpha"] == alpha]
        ok = g["realised_risk"].max() <= alpha + 1e-9
        L.append(f"- a **{alpha:.0%}** budget needs `lambda = {g['lambda'].mean():.2f}`, "
                 f"approves **{g['pass_rate'].mean():.1%}** of candidates, and realises "
                 f"{g['realised_risk'].mean():.2%} mean risk "
                 f"({'within budget' if ok else f'**over budget**, worst fold ' + format(g['realised_risk'].max(), '.1%')})")
    L += ["",
          "## What is nonetheless worth keeping", "",
          "The machinery is right and the framing is right: a deployment owner states a risk "
          "budget, and a threshold is *derived* rather than chosen. Compare that with the "
          "shipped gate's hand-set 0.6 confidence constant, which Tier 1 showed could not even "
          "be tuned at n=5 machines because the score takes one value per machine. Here the "
          "threshold is calibrated on *pairs*, of which there are hundreds, so it is at least "
          "estimable.", "",
          "The gap is exchangeability, and it is a **data problem, not a method problem**. With "
          "30+ machines spanning the hardware space, a target machine is far more plausibly "
          "exchangeable with its calibration set, and weighted CRC under covariate shift gets "
          "enough distinct weights to matter. Until then, a stated risk budget on unseen "
          "hardware should be reported as *aspirational*, and the gate should lean on "
          "abstention rather than on a guarantee it cannot honour.", "",
          "## Honest limitations", "",
          "- **The controlled quantity is the rate of wrongly approved deployments over ALL "
          "candidates**, not the breach rate *among* approved ones. The latter is the more "
          "natural reading of the sentence, but it is not monotone in lambda and so cannot be "
          "controlled by this method. Both columns are above; the gap between them is large at "
          "tight budgets.",
          "- 5 machines means 4 calibration machines per fold. Every caveat about small-sample "
          "conformal applies.",
          f"- At loose budgets the derived `lambda` falls below 1.0 (e.g. "
          f"{sub[sub['alpha'] == 0.10]['lambda'].mean():.2f} at a 10% budget), i.e. CRC asks "
          f"the gate to be *less* conservative than the raw prediction and approves nearly "
          f"everything. That is correct behaviour for a loose budget, but it means the gate "
          f"stops being a gate.", ""]
    (RESULTS_DIR / "tier3_risk_control.md").write_text("\n".join(L) + "\n")
    print(df.groupby(["predictor", "tau", "alpha"])[
        ["lambda", "pass_rate", "realised_risk"]].mean().round(4).to_string())
    print(f"\nWrote {RESULTS_DIR / 'tier3_risk_control.md'}")


if __name__ == "__main__":
    main()
