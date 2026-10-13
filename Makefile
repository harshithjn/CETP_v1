# CETP. `make results` is the only sanctioned way to produce a number about this project.
PY := python3
CANON := scripts/analysis/canonical_evaluation.py

.PHONY: help install results verify-determinism test gate-demo clean-pyc \
        grid-setup grid-collect grid-reprobe operator-results probe-here \
        docs version-compat

help:
	@echo "make install            install pinned dependencies"
	@echo "make results            regenerate results/CANONICAL.md + canonical_metrics.json"
	@echo "make verify-determinism run results twice, assert byte-identical output"
	@echo "make test               run the gate test suite"
	@echo "make gate-demo          run the four worked SLA-gate scenarios"
	@echo ""
	@echo "per-operator model (see OPERATOR_MODEL.md):"
	@echo "  make probe-here       operator-probe THIS machine (run it idle)"
	@echo "  make grid-setup       load TPC-H SF1 into a Docker volume (once, ~10 min)"
	@echo "  make grid-collect     sweep the 4x2 capability grid (~2-3 h)"
	@echo "  make grid-reprobe     re-probe every grid cell without recollecting"
	@echo "  make operator-results evaluate the per-operator model on the grid"
	@echo "  make version-compat   parse every stored plan on every PG version"
	@echo "  make docs             rebuild docs/LLD.pdf and docs/HANDBOOK.pdf"

install:
	$(PY) -m pip install -r requirements.txt

# THE source of truth. Every number in the paper, deck, LLD and README comes from here.
results:
	$(PY) $(CANON)

# Item 13: determinism. Any non-reproducible result is a bug.
verify-determinism:
	@$(PY) $(CANON) >/dev/null
	@cp results/CANONICAL.md /tmp/cetp_canon_a.md
	@cp results/canonical_metrics.json /tmp/cetp_canon_a.json
	@$(PY) $(CANON) >/dev/null
	@cmp results/CANONICAL.md /tmp/cetp_canon_a.md \
	  && cmp results/canonical_metrics.json /tmp/cetp_canon_a.json \
	  && echo "DETERMINISTIC: both runs byte-identical" \
	  || (echo "NON-DETERMINISTIC: outputs differ between runs" && exit 1)

test:
	$(PY) -m pytest tests/test_e2e.py -q

gate-demo:
	$(PY) gate/cetp_gate.py --demo

clean-pyc:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

# --- Reproducibility artifact (CONFERENCE_PLAN.md item 6) ---

.PHONY: artifact artifact-verify

# Build the pinned image that regenerates every published number.
# DOCKER_CONFIG points at a credential-helper-free config: docker-credential-desktop on
# macOS can hang on the keychain and stall the build with "error getting credentials".
# The build needs no registry auth.
DOCKER_CFG := $(CURDIR)/.docker-noauth

artifact:
	@mkdir -p $(DOCKER_CFG) && printf '{"auths":{}}' > $(DOCKER_CFG)/config.json
	DOCKER_CONFIG=$(DOCKER_CFG) docker build -t cetp-artifact .

# Build, regenerate, and assert the headline matches what the repo claims.
artifact-verify: artifact
	DOCKER_CONFIG=$(DOCKER_CFG) docker run --rm cetp-artifact sh -c '\
	  make results >/dev/null && \
	  python3 -c "\
import json; d=json.load(open(\"results/canonical_metrics.json\"))[\"headline\"]; \
a=d[\"analytical_mape\"]; n=d[\"naive_linear_mape\"]; \
assert abs(a-21.61)<0.05, f\"analytical MAPE drifted: {a}\"; \
assert abs(n-23.05)<0.05, f\"naive-linear MAPE drifted: {n}\"; \
print(f\"artifact reproduces the headline: analytical {a:.2f}% vs naive-linear {n:.2f}%\")"'

# --- Model promotion (blocker 8) ---
# Analysis scripts write to models_candidate/. Promotion is deliberate and gated on tests,
# because during the Tier 0 audit an analysis run silently replaced the shipped artifacts
# and changed 5 of the 28 locked-value regression tests.
.PHONY: promote-models

promote-models:
	@test -d models_candidate || { echo "no models_candidate/ -- run the analysis first"; exit 1; }
	@echo "candidate artifacts:"; ls -la models_candidate/
	@echo "running the test suite against the CURRENT shipped models first..."
	@$(PY) -m pytest tests/test_e2e.py -q >/dev/null || { echo "tests already failing; fix that before promoting"; exit 1; }
	@mkdir -p models_backup && cp -f models/*.pkl models_backup/ 2>/dev/null || true
	@cp -f models_candidate/*.pkl models_candidate/*.json models/ 2>/dev/null || true
	@echo "promoted. re-running tests against the NEW models..."
	@$(PY) -m pytest tests/test_e2e.py -q || { \
	  echo ""; \
	  echo "TESTS FAIL with the new models. Locked prediction values changed."; \
	  echo "Review each change deliberately, then update the expected values."; \
	  echo "Previous artifacts are in models_backup/ -- restore with: cp models_backup/*.pkl models/"; \
	  exit 1; }
	@echo "promotion complete and tests pass."

# ---------------------------------------------------------------- per-operator model

# Probe THIS machine. Run it on an idle machine: a probe taken under load measures
# contention, not capability -- observed moving the parallel speedup by 48%.
probe-here:
	$(PY) scripts/collection/operator_probe.py --machine-id $${MACHINE_ID:-$$(hostname -s)} \
	    --db $${PGDATABASE:-postgres}

# The orthogonal capability grid. CPU quota x memory quota are crossed by construction,
# which is the one thing renting more EC2 instance types cannot buy: instance types are
# correlated bundles (the five-machine EC2 signature correlates at r=+0.958).
grid-setup:
	$(PY) scripts/collection/container_grid.py --setup

grid-collect:
	$(PY) scripts/collection/container_grid.py --collect

grid-reprobe:
	$(PY) scripts/collection/container_grid.py --probe-only --force-probe

operator-results:
	$(PY) scripts/analysis/operator_model_evaluation.py

# ------------------------------------------------------------------------ docs

# The PDFs are generated from the Markdown, so the Markdown is the single source and the
# PDFs never drift from it. Needs Chrome or Chromium (used headless to render).
docs:
	$(PY) scripts/docs/build_pdf.py docs/LLD.md
	$(PY) scripts/docs/build_pdf.py docs/HANDBOOK.md

# Parse every stored plan from every PostgreSQL version collected, and report what differs.
version-compat:
	$(PY) scripts/analysis/version_compat_check.py
