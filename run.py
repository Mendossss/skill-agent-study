#!/usr/bin/env python3
import argparse
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description='Local graph-audit skill study')
    parser.add_argument('command', choices=['setup', 'run'], nargs='?', default='run')
    args = parser.parse_args()
    if args.command == 'setup':
        load(ROOT / 'harness/study.py', 'runtime').setup()
        return 0
    return load(ROOT / 'experiment/run.py', 'experiment').main()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
