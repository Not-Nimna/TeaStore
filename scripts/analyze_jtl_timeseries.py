#!/usr/bin/env python3

from pathlib import Path
import runpy
import sys

script_dir = Path(__file__).resolve().parent
target = script_dir.parent / "tools" / "analyze_jtl_timeseries.py"
sys.argv[0] = str(target)
runpy.run_path(str(target), run_name="__main__")
