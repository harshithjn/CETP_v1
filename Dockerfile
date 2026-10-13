# Reproducibility artifact (CONFERENCE_PLAN.md item 6).
#
# Regenerates every published number from the committed dataset, in a pinned environment,
# with no network access and no PostgreSQL required. `make results` reads the canonical CSV
# and refits every model in memory, so the analysis half of this project is reproducible
# from this image alone.
#
#   docker build -t cetp-artifact .
#   docker run --rm cetp-artifact                 # regenerate and print the headline
#   docker run --rm cetp-artifact make verify-determinism
#
# What this image does NOT do: re-collect measurements. That needs PostgreSQL with TPC-H,
# TPC-DS and IMDB loaded, and for the EC2 rows, hardware that no longer exists. The
# collection half is reproducible only in the sense that the harness and the queries are
# committed -- see scripts/collection/ and DATASHEET.md.
FROM python:3.13-slim

# scikit-learn is pinned EXACTLY: the published numbers depend on its RNG and tree-building
# details, and `make verify-determinism` asserts byte-identical output across two runs.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
 && apt-get update && apt-get install -y --no-install-recommends make \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /cetp

# Only what the analysis needs. Deliberately excludes data/external (third-party, large)
# and the version-coupled models/*.pkl -- canonical_evaluation.py refits from scratch and
# never loads a pickle, which is why its numbers are trustworthy while the shipped gate's
# artifacts are stale (models/MODEL_CARD.md).
COPY Makefile README.md ./
COPY requirements.txt pyproject.toml ./
COPY data/corrected ./data/corrected
COPY scripts/analysis ./scripts/analysis
COPY scripts/collection ./scripts/collection
COPY results/plan_drift_raw.csv results/elasticity_raw.csv results/plan_features_v2.csv ./results/
COPY results/plan_structure_features.csv ./results/

CMD ["sh", "-c", "make results && echo '---' && head -20 results/CANONICAL.md"]
