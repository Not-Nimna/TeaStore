#!/bin/bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  tools/run_jmeter_repeated.sh --plan PLAN.jmx --host HOST --port PORT \
    --users N --ramp-up N --duration N --runs N [options]

Required:
  --plan PATH        JMeter .jmx test plan
  --host HOST        TeaStore host or IP
  --port PORT        TeaStore port
  --users N          Concurrent users
  --ramp-up N        Ramp-up seconds
  --duration N       Test duration seconds
  --runs N           Number of repeated runs

Options:
  --jmeter-home PATH JMeter install directory (default: $JMETER_HOME or ~/apache-jmeter-5.6.3)
  --workload NAME    Label for output files (default: workload)
  --output-dir DIR   Directory for results (default: ./results)
  --pause-seconds N  Pause between runs in seconds (default: 10)
  --summary          Print a compact summary table after all runs
  --summarize-script PATH
                     Path to tools/summarize_jtl.py (default: ./tools/summarize_jtl.py)
  -h, --help         Show this help text

Output:
  - Creates one .jtl per run
  - Creates one metadata file per run
  - Optionally prints a summary table across all runs
EOF
}

jmeter_home="${JMETER_HOME:-$HOME/apache-jmeter-5.6.3}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workload="workload"
output_dir="./results"
pause_seconds="10"
summarize="false"
summarize_script="$script_dir/summarize_jtl.py"
plan=""
host=""
port=""
users=""
ramp_up=""
duration=""
runs=""

while (($# > 0)); do
  case "$1" in
    --plan)
      plan="${2:-}"
      shift 2
      ;;
    --host)
      host="${2:-}"
      shift 2
      ;;
    --port)
      port="${2:-}"
      shift 2
      ;;
    --users)
      users="${2:-}"
      shift 2
      ;;
    --ramp-up)
      ramp_up="${2:-}"
      shift 2
      ;;
    --duration)
      duration="${2:-}"
      shift 2
      ;;
    --runs)
      runs="${2:-}"
      shift 2
      ;;
    --jmeter-home)
      jmeter_home="${2:-}"
      shift 2
      ;;
    --workload)
      workload="${2:-}"
      shift 2
      ;;
    --output-dir)
      output_dir="${2:-}"
      shift 2
      ;;
    --pause-seconds)
      pause_seconds="${2:-}"
      shift 2
      ;;
    --summary)
      summarize="true"
      shift
      ;;
    --summarize-script)
      summarize_script="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$plan" || -z "$host" || -z "$port" || -z "$users" || -z "$ramp_up" || -z "$duration" || -z "$runs" ]]; then
  usage >&2
  exit 2
fi

if [[ ! -f "$plan" ]]; then
  echo "Plan not found: $plan" >&2
  exit 1
fi

plan_path="$(realpath "$plan")"

if [[ ! -f "$jmeter_home/bin/ApacheJMeter.jar" ]]; then
  echo "JMeter jar not found: $jmeter_home/bin/ApacheJMeter.jar" >&2
  exit 1
fi

mkdir -p "$output_dir"
output_dir="$(realpath "$output_dir")"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
meta_file="$output_dir/${workload}_${timestamp}_runs.csv"

cat >"$meta_file" <<EOF
run,workload,host,port,users,ramp_up,duration,started_utc,jtl_file,meta_file
EOF

for run in $(seq 1 "$runs"); do
  run_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  run_base="${workload}_${timestamp}_run$(printf '%02d' "$run")"
  jtl_file="$output_dir/${run_base}.jtl"
  run_meta="$output_dir/${run_base}.txt"

  {
    echo "workload=$workload"
    echo "run=$run"
    echo "host=$host"
    echo "port=$port"
    echo "users=$users"
    echo "ramp_up=$ramp_up"
    echo "duration=$duration"
    echo "started_utc=$run_stamp"
    echo "plan=$plan"
    echo "jmeter_home=$jmeter_home"
    echo "jtl_file=$jtl_file"
  } >"$run_meta"

  printf '%s\n' "Starting run ${run}/${runs}: ${run_base}"
  (
    cd "$jmeter_home"
    java -jar bin/ApacheJMeter.jar \
      -t "$plan_path" \
      -Jhostname "$host" \
      -Jport "$port" \
      -JnumUser "$users" \
      -JrampUp "$ramp_up" \
      -Jduration "$duration" \
      -l "$jtl_file" \
      -n
  )

  printf '%s,%s,%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$run" "$workload" "$host" "$port" "$users" "$ramp_up" "$duration" "$run_stamp" "$jtl_file" "$run_meta" >>"$meta_file"

  if [[ "$run" -lt "$runs" && "$pause_seconds" != "0" ]]; then
    sleep "$pause_seconds"
  fi
done

if [[ "$summarize" == "true" ]]; then
  python3 "$summarize_script" "$output_dir/${workload}_${timestamp}_run"*.jtl
fi

echo "Results written to: $output_dir"
echo "Run metadata: $meta_file"
