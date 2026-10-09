#!/usr/bin/env python3
"""Separate check for the parse_script(text) requirement omitted by upstream tests."""
import collections
import importlib.util
import json
from pathlib import Path
import sys


def normalize(graph):
    if hasattr(graph, 'to_dict'):
        graph = graph.to_dict()
    if not isinstance(graph, dict):
        raise AssertionError('parse_script must return a dictionary or object with to_dict().')
    nodes, edges = graph.get('nodes'), graph.get('edges')
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise AssertionError('Returned graph must contain nodes and edges lists.')
    for node in nodes:
        if not isinstance(node, dict) or not all(isinstance(node.get(k), str)
                for k in ('id', 'text', 'speaker', 'type')):
            raise AssertionError('Returned node does not satisfy the declared schema.')
    for edge in edges:
        if not isinstance(edge, dict) or not all(isinstance(edge.get(k), str)
                for k in ('from', 'to', 'text')):
            raise AssertionError('Returned edge does not satisfy the declared schema.')
    node_rows = collections.Counter(tuple(n[k] for k in ('id', 'text', 'speaker', 'type')) for n in nodes)
    edge_rows = collections.Counter(tuple(e[k] for k in ('from', 'to', 'text')) for e in edges)
    return node_rows, edge_rows


def main():
    work = Path(sys.argv[1]).resolve()
    output = Path(sys.argv[2]).resolve()
    result = {'pass': False, 'requirement': 'parse_script receives real script content and returns the saved graph'}
    try:
        raw = (work / 'script.txt').read_bytes()
        text = raw.decode('utf-8')
        saved = json.loads((work / 'dialogue.json').read_text())
        spec = importlib.util.spec_from_file_location('submitted_solution', work / 'solution.py')
        if spec is None or spec.loader is None:
            raise AssertionError('solution.py cannot be imported.')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        function = getattr(module, 'parse_script', None)
        if not callable(function):
            raise AssertionError('solution.py has no callable parse_script.')
        parsed = function(text)
        if normalize(parsed) != normalize(saved):
            raise AssertionError('Returned graph differs from dialogue.json (ignoring node/edge ordering).')
        if (work / 'script.txt').read_bytes() != raw:
            raise AssertionError('Calling parse_script modified its source data.')
        result['pass'] = True
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'[:2500]
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
