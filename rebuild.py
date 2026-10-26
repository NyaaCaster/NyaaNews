"""Build only: python rebuild.py [--no-cache]. Never starts or publishes containers."""
import argparse
import subprocess
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument("--no-cache",action="store_true")
args=parser.parse_args()
command=["docker","build","--tag","nyaanews:local"]
if args.no_cache:
    command.append("--no-cache")
command.append(".")
raise SystemExit(subprocess.run(command,cwd=Path(__file__).resolve().parent).returncode)
