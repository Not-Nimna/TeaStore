# Scripts

This directory provides report-facing entry points for the measurement and
analysis pipeline.

The actual implementations live in `tools/`:

- `tools/run_jmeter_repeated.sh`
- `tools/collect_docker_stats.py`
- `tools/summarize_jtl.py`
- `tools/analyze_jtl_timeseries.py`
- `tools/plot_teastore_results.py`
- `tools/compare_experiments.py`

The thin wrappers here let the report point to stable top-level paths.
