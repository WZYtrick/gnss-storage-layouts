# GNSS storage layouts v1.0.0

Code: [https://github.com/WZYtrick/gnss-storage-layouts](https://github.com/WZYtrick/gnss-storage-layouts)  
Versioned full evidence archive: [10.5281/zenodo.24241891](https://doi.org/10.5281/zenodo.24241891)  
GitHub release: [v1.0.0](https://github.com/WZYtrick/gnss-storage-layouts/releases/tag/v1.0.0)

Reproduction materials for **Storage Layouts and Query Costs for Global Navigation Satellite System Observations**. The original six-block layout study and the completed reviewer-requested two-block GeoRinex comparison are distinct datasets. Construction costs use six fresh builds for every dataset/layout combination.

## Offline quick replay

Use Python 3.12.14 and a fresh virtual environment. Install `requirements.lock.txt` once; all following analysis commands are local and require no source GNSS files. Use a new output directory for each run.

```text
python -m pip install -r requirements.lock.txt
python reproduce.py analyze --output original_analysis
python independent_review.py --analysis-root original_analysis
python reproduce.py analyze-revision --source-manifest evidence/revision/source_manifest.json --output revision_analysis
python reproduce.py verify-revision --source-manifest evidence/revision/source_manifest.json --analysis-root revision_analysis
python plot_results.py --analysis-root original_analysis --output original_figures
python test_revision.py
```

The original replay covers 3,036 primary scenarios (253 weights × two CPU capacities × three contracts × two metrics), and separately 1,224 historical alpha scenarios. Its independent implementation selects median calls directly from raw logs. The supplementary replay verifies 18,144 formal calls, 6,048 planned warm-ups, 5,453 separate recovery warm-ups and 204 fresh builds. It selects the median of three formal total-time calls and takes all stages from that same call. `verify-revision` independently recomputes the tables without importing the primary analyzer. The two query blocks and six construction repetitions are never pooled as additional query blocks.

The full archive contains the raw logs needed by both independent checkers. The smaller GitHub source distribution omits only `evidence/raw` from the original study; obtain that directory from the full archive before invoking `independent_review.py`. Both quick analyses and the supplementary independent verification run directly in the GitHub distribution. Row order is not a scientific result; values, scenario membership and selected layouts are.

## Rebuild and measure on another computer

```text
python reproduce.py download --output raw_sources --datasets D0m D1 D2
python reproduce.py rebuild --source-root raw_sources --output rebuilt --datasets D0m D1 D2 D2m
python reproduce.py benchmark --data-root rebuilt --output fresh_queries --dataset D2 --methods W C Cstar T F GeoRinex_R GeoRinex_H --contract ordered_dense --cpu 1 --blocks 2 --repeats 3
python reproduce.py construction --source-root raw_sources --output fresh_builds --datasets D0m D1 D2 D2m --blocks 6
```

Run the baseline command for D1 as well to reproduce both datasets. The original layout experiment uses six blocks and its own declared contract/capacity matrix. Input checksums are verified before parsing; compressed-only inputs are supported. `rebuild --sample` checks one source per dataset and all applicable layouts/contracts/tasks; it is a portability check, not a replacement for full measurements. `construction --sample --blocks 1` is likewise a functional check. Explicitly invoked fresh benchmarks are labeled external reproduction and do not join the authors' evidence. Historical supervisors are archived for inspection; the portable entry points do not inherit the original computer's drive letters or scheduling windows.

GeoRinex 1.16.2 parses observation values unchanged using its native time/constellation/code selection. A timed adapter retains source identity and LLI/SSI flags, half-open intervals and the ordered output contract. R reads expanded RINEX; H expands the CRX.gz source on each call. Neither caches parsed results. Duplicate epoch/satellite identities unsupported by this standard-parser path are rejected. Source-calendar time labels are retained, not converted to UTC.

## Evidence and analysis map

- `inputs/`: source URLs/checksums, exact queries and tasks, truth digests, original layout metrics.
- `evidence/raw/`: original layout/task/calibration calls and execution metadata (full archive).
- `evidence/freezes/` and `evidence/training/`: frozen predictions and independent initial parameter selection.
- `evidence/revision/source_manifest.json`: four completed query blocks, 24 construction dataset-blocks, session authorization and recovery/exclusion records.
- `evidence/revision/provenance.json`: original-to-published hashes for 7,016 archived evidence files. Only local paths and identity strings are normalized.
- `analysis/existing_evidence/`: thread results, threshold sensitivity, candidate qualifications and original scenario replay.
- `analysis/revision/`: completed parsing, construction, absolute storage and amortization tables.
- `validation/`: independent replay and relocation evidence, separately labeled software fault tests.
- `release_manifest.json`: versioned file checksums; it excludes itself and generated replay output directories.

Amortization uses the six-build arithmetic mean standalone preparation cost divided by the two-query-block arithmetic mean per-request saving, rounded upward. Nonpositive saving gives no finite break-even point. All 253 workload mixtures and all six-build × two-query-block combinations are supplied. Storage separates Parquet, required metadata and audit metadata; the original frozen tie-break uses the fully retained footprint. Normalizing published metadata changes its byte count, so the tables correctly report the original measured storage, not the size of this release.

## License and citation

Code is MIT licensed; original timing and derived data use CC BY 4.0. Upstream software retains its own licenses. Raw source GNSS files are not redistributed; source URLs and hashes are provided. See `CITATION.cff` in the signed release for attribution. The anonymous review archive omits identifying citation metadata and public author links.

AI tools assisted with writing portions of the code.
