# TeaStore Bottleneck Experiment

This experiment compares a baseline deployment against a scaled deployment of
the TeaStore WebUI.

## Why WebUI

The WebUI is the client-facing entry point and a plausible bottleneck under
increasing request pressure. Scaling it is simple and measurable:

- baseline: 1 replica
- scaled: 2 replicas

## Baseline deployment

```bash
helm upgrade --install teastore-baseline examples/helm \
  -f examples/helm/values-bottleneck-baseline.yaml
```

## Scaled deployment

```bash
helm upgrade --install teastore-scaled examples/helm \
  -f examples/helm/values-bottleneck-scaled-webui.yaml
```

## Measurement flow

For each deployment:

1. Start resource sampling on the TeaStore host.
2. Run the same JMeter workload with `scripts/run_jmeter_repeated.sh`.
3. Summarize the JTL files.
4. Analyze the time series for overload transition.

Example:

```bash
python3 scripts/collect_docker_stats.py \
  --output-dir results/raw/baseline/resources \
  --prefix baseline \
  --interval-seconds 5 \
  --duration-seconds 300 \
  --include-manifest &

bash scripts/run_jmeter_repeated.sh \
  --jmeter-home ./jmeter \
  --plan examples/jmeter/teastore_browse_nogui.jmx \
  --host <tea-store-ip> \
  --port 8080 \
  --users 10 \
  --ramp-up 1 \
  --duration 300 \
  --runs 3 \
  --workload browse \
  --output-dir results/raw/baseline \
  --summary
```

Then repeat with the scaled deployment and compare:

```bash
python3 scripts/compare_experiments.py \
  --baseline-jtl results/raw/baseline/*.jtl \
  --scaled-jtl results/raw/scaled/*.jtl \
  --baseline-resources results/raw/baseline/resources/*.csv \
  --scaled-resources results/raw/scaled/resources/*.csv
```

The compare script reports throughput, latency, error rate, and resource usage
differences.

It also prints repeated-run statistics for each condition:

- mean
- standard deviation
- 95% confidence interval
- Welch t-test for baseline vs scaled

To turn the same measurements into report figures:

```bash
python3 scripts/plot_teastore_results.py \
  --manifests results/raw/baseline results/raw/scaled \
  --timeseries results/raw/baseline/timeseries.csv results/raw/scaled/timeseries.csv \
  --resources results/raw/baseline/resources results/raw/scaled/resources \
  --output-dir results/figures/bottleneck \
  --prefix bottleneck
```

This generates:

- response time vs time
- error rate vs concurrent users
- throughput vs concurrent users
- p95 latency by workload
- CPU and memory per service around the overload point
