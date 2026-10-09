#!/usr/bin/env python3
"""Independent graph-audit-v1 semantic grader and helper-invocation observation."""
from collections import Counter
import copy
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET
import sys


def graph_value(g):
    assert isinstance(g,dict)
    assert isinstance(g.get('nodes'),list) and isinstance(g.get('edges'),list)
    nodes=g['nodes'];edges=g['edges']
    for n in nodes:
        assert all(isinstance(n.get(k),str) for k in ('id','text','speaker','type'))
        assert n['type'] in ('line','choice')
    assert len({n['id'] for n in nodes})==len(nodes)
    for e in edges:assert all(isinstance(e.get(k),str) for k in ('from','to','text'))
    return (Counter(tuple(n[k] for k in ('id','text','speaker','type')) for n in nodes),
            Counter(tuple(e[k] for k in ('from','to','text')) for e in edges))


def answer_value(d):
    assert isinstance(d,dict)
    assert type(d.get('valid')) is bool
    assert isinstance(d.get('errors'),list) and all(isinstance(s,str) for s in d['errors'])
    assert type(d.get('node_count')) is int and type(d.get('edge_count')) is int
    return (d['valid'],Counter(d['errors']),d['node_count'],d['edge_count'])


def grade(work,expected,with_skill):
    records=[];saved=None;function=None;dots={};calls=[];per_api=[]
    raw=(work/'graphs.json').read_bytes()
    valid_ids=[x['id'] for x in expected['records'] if x['valid']]
    def test(name,fn):
        try:fn();records.append({'name':name,'pass':True})
        except Exception as exc:records.append({'name':name,'pass':False,'error':type(exc).__name__+': '+str(exc)[:1200]})
    def files():
        for filename in ['solution.py','audit.json']+[id+ext for id in valid_ids for ext in ('.json','.svg')]:
            assert (work/filename).is_file(),'Missing '+filename
    test('required_files',files)
    def schema():
        nonlocal saved
        saved=json.loads((work/'audit.json').read_text())
        assert isinstance(saved,list) and len(saved)==len(expected['records'])
        for record in saved:
            assert isinstance(record.get('id'),str);answer_value(record)
        assert len({r['id'] for r in saved})==len(saved)
    test('audit_schema',schema)
    def report_exact():
        assert saved is not None
        assert {r['id']:answer_value(r) for r in saved}=={r['id']:answer_value(r) for r in expected['records']}
    test('all_endpoint_diagnostics',report_exact)
    def exported_json():
        for id in valid_ids:assert graph_value(json.loads((work/(id+'.json')).read_text()))==graph_value(expected['graphs'][id]),id+' export differs'
    test('valid_graph_json_exports',exported_json)
    def syntax():
        for id in valid_ids:
            element=ET.parse(work/(id+'.svg')).getroot()
            assert element.tag.rsplit('}',1)[-1]=='svg','Not an SVG document'
            dots[id]=element
    test('all_valid_svg_xml',syntax)
    def diagram():
        for id in valid_ids:
            g=expected['graphs'][id];root=dots[id]
            groups=[x for x in root.iter() if x.tag.rsplit('}',1)[-1]=='g']
            def title(group):
                child=next((x for x in group if x.tag.rsplit('}',1)[-1]=='title'),None)
                assert child is not None and child.text
                return child.text
            nodes={title(x):x for x in groups if x.get('class')=='node'}
            names={n['id'] for n in g['nodes']}
            if any(e['to']=='End' for e in g['edges']):names.add('End')
            assert set(nodes)==names,id+' SVG node IDs differ'
            def polygon_kind(group):
                polygon=next((x for x in group if x.tag.rsplit('}',1)[-1]=='polygon'),None)
                if polygon is None:
                    assert any(x.tag.rsplit('}',1)[-1]=='path' and x.get('d') for x in group),'Missing box outline'
                    return 'box'
                pts=set(tuple(float(v) for v in pair.split(',')) for pair in polygon.get('points','').split())
                assert len(pts)==4,'Expected four polygon vertices'
                xs=[x for x,y in pts];ys=[y for x,y in pts];lo_x,hi_x=min(xs),max(xs);lo_y,hi_y=min(ys),max(ys)
                assert hi_x>lo_x and hi_y>lo_y
                if all(x in (lo_x,hi_x) and y in (lo_y,hi_y) for x,y in pts):return 'box'
                mid_x=(lo_x+hi_x)/2;mid_y=(lo_y+hi_y)/2
                expected_points={(mid_x,lo_y),(mid_x,hi_y),(lo_x,mid_y),(hi_x,mid_y)}
                assert all(any(abs(x-a)<0.03 and abs(y-b)<0.03 for a,b in expected_points) for x,y in pts),'Expected axis-aligned diamond'
                return 'diamond'
            for n in g['nodes']:assert polygon_kind(nodes[n['id']])==('diamond' if n['type']=='choice' else 'box'),id+' wrong node shape'
            actual=Counter(title(x) for x in groups if x.get('class')=='edge')
            assert actual==Counter(e['from']+'->'+e['to'] for e in g['edges']),id+' directed connections differ'
    test('all_svg_connections_and_shapes',diagram)
    if with_skill:
        from dialogue_graph import Graph
        original_validate=Graph.validate
        def observed_validate(self):
            calls.append({'node_count':len(self.nodes),'edge_count':len(self.edges)})
            return original_validate(self)
        Graph.validate=observed_validate
    def invoke(graph):
        assert callable(function),'Missing audit_graph'
        start=len(calls);result=function(copy.deepcopy(graph))
        per_api.append(len(calls)>start)
        return answer_value(result)
    def api_main():
        nonlocal function
        spec=importlib.util.spec_from_file_location('graph_audit_submission',work/'solution.py');assert spec and spec.loader
        module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
        function=getattr(module,'audit_graph',None);assert callable(function)
        for record in expected['records']:
            assert invoke(expected['graphs'][record['id']])==answer_value(record),record['id']+' API result differs'
    test('audit_api_all_main_graphs',api_main)
    for index,case in enumerate(expected['held_out'],1):
        def held(case=case):assert invoke(case['graph'])==answer_value(case['answer']),'Additional-input diagnostics differ'
        test('additional_api_'+str(index),held)
    def input_ok():assert (work/'graphs.json').read_bytes()==raw,'Imported solution or API modified input'
    test('input_unchanged_during_api',input_ok)
    result={'pass':all(x['pass'] for x in records),'passed':sum(x['pass'] for x in records),'total':len(records),'checks':records,
            'helper_validation_calls':calls,'helper_used_on_all_api_inputs':len(per_api)==8 and all(per_api) if with_skill else None}
    if with_skill:Graph.validate=original_validate
    return result


if __name__=='__main__':
    work=Path(sys.argv[1]).resolve();target=Path(sys.argv[2]).resolve();expected=json.loads(Path(sys.argv[3]).read_text());skill=sys.argv[4]=='1'
    result=grade(work,expected,skill);target.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    raise SystemExit(0 if result['pass'] else 1)
