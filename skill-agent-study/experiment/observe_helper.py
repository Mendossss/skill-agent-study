#!/usr/bin/env python3
"""Observe original helper methods in a copied workspace; never modify agent code."""
import hashlib
import json
from pathlib import Path
import runpy
import sys
import traceback


def digest(value):
    encoded=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def main():
    solution=Path(sys.argv[1]).resolve();target=Path(sys.argv[2]).resolve()
    source_hash=hashlib.sha256(solution.read_bytes()).hexdigest()
    inputs=solution.parent/'graphs.json';input_hash=hashlib.sha256(inputs.read_bytes()).hexdigest()
    supplied={}
    for record in json.loads(inputs.read_text()):
        supplied.setdefault(digest(record['graph']),[]).append(record['id'])
    from dialogue_graph import Graph
    original_to_dict=Graph.to_dict
    originals={name:getattr(Graph,name) for name in ('validate','to_json','visualize')}
    calls=[];exit_code=0;error=None
    def wrap(name,method):
        def observed(self,*args,**kwargs):
            graph=original_to_dict(self);value=method(self,*args,**kwargs)
            calls.append({'method':name,'graph_sha256':digest(graph),
                          'node_count':len(graph['nodes']),'edge_count':len(graph['edges']),
                          'supplied_graph_ids':supplied.get(digest(graph),[])})
            return value
        return observed
    for name,method in originals.items():setattr(Graph,name,wrap(name,method))
    try:
        runpy.run_path(str(solution),run_name='__main__')
    except SystemExit as exc:
        exit_code=exc.code if isinstance(exc.code,int) else (0 if exc.code is None else 1)
        if exit_code:error='SystemExit: '+str(exc.code)
    except BaseException as exc:
        exit_code=1;error=type(exc).__name__+': '+str(exc);traceback.print_exc()
    finally:
        for name,method in originals.items():setattr(Graph,name,method)
        unchanged=(hashlib.sha256(solution.read_bytes()).hexdigest()==source_hash and
                   hashlib.sha256(inputs.read_bytes()).hexdigest()==input_hash)
        result={'observation_kind':'post-run execution of byte-identical agent program in an isolated copy',
                'solution_sha256':source_hash,'input_sha256':input_hash,'exit_code':exit_code,'error':error,
                'solution_and_input_unchanged':unchanged,'calls':calls,
                'used_for_supplied_graphs':exit_code==0 and unchanged and any(c['supplied_graph_ids'] for c in calls)}
        target.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return exit_code


if __name__=='__main__':raise SystemExit(main())
