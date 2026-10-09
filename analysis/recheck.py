"""Recheck original outputs and summarize visible traces; no model or code repair."""
from pathlib import Path
from zipfile import ZipFile
import hashlib,importlib.util,json,sys

sys.dont_write_bytecode=True
B=Path(__file__).resolve().parent.parent
if len(sys.argv)==3:
    archive_path=Path(sys.argv[1]).expanduser().resolve()
    base=Path(sys.argv[2]).expanduser().resolve();base.mkdir(parents=True,exist_ok=False)
    with ZipFile(archive_path) as z:
        assert z.testzip() is None
        for name in z.namelist():
            destination=base/name
            assert destination.resolve().is_relative_to(base),'Unsafe archive path'
        z.extractall(base)
else:raise SystemExit('Usage: python3 analyze.py RAW_ARCHIVE.zip NEW_ANALYSIS_DIRECTORY')
roots=list((base/'graph-audit-9b').glob('*/runs'));assert len(roots)==1,'Expected one 9B batch'
root=roots[0]
sys.path.insert(0,str(base/'reproduction/validated_harness/source/environment/skills/dialogue-graph/scripts'))
checker_path=base/'reproduction/graph_audit_9b/check.py'
spec=importlib.util.spec_from_file_location('candidate_recheck',checker_path);c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
expected=json.loads((checker_path.parent/'expected.json').read_text())
with ZipFile(archive_path) as z:
    skill_prefix='reproduction/validated_harness/source/environment/skills/dialogue-graph/'
    original_skill={n.removeprefix(skill_prefix):z.read(n) for n in z.namelist() if n.startswith(skill_prefix) and not n.endswith('/')}
runs=[]
for folder in sorted(root.iterdir()):
    r=json.loads((folder/'result.json').read_text());manifest=json.loads((folder/'manifest.json').read_text())
    original_verification=json.loads((folder/'verification.json').read_text())
    report=c.grade(folder/'workspace',expected,r['with_skill'])
    assert report['passed']==r['passed_checks'] and report['pass']==r['task_pass']
    assert [(x['name'],x['pass']) for x in report['checks']]==[(x['name'],x['pass']) for x in original_verification['report']['checks']]
    events=[json.loads(x) for x in (folder/'events.jsonl').read_text().splitlines() if x.strip()]
    commands=[e['item'] for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='command_execution']
    writes=[i for i,x in enumerate(commands,1) if 'cat >' in x.get('command','') and 'solution.py' in x['command']]
    stderr=(folder/'agent.stderr').read_text()
    skill_reads=[{'index':i,'command':x['command'],'output_sha256':hashlib.sha256(x.get('aggregated_output','').encode()).hexdigest()} for i,x in enumerate(commands,1) if x.get('exit_code')==0 and 'SKILL.md' in x.get('command','') and 'dialogue-graph' in x['command']]
    svg_prefixes={p.name:p.read_text()[:100] for p in (folder/'workspace').glob('*.svg')}
    entry={'result':r,'manifest':manifest,'original_verification':original_verification,'local_verification':report,
           'visible_full_file_write_count':len(writes),'successful_skill_read_commands':skill_reads,'stdout_svg_prefixes':svg_prefixes,
           'stderr_rule_rejection_count':stderr.count('exec_command failed: CreateProcess'),
           'stderr_unsupported_image_tool':('view_image is not allowed' in stderr),
           'visible_full_file_write_indices':writes,
           'svg_sizes':{p.name:p.stat().st_size for p in (folder/'workspace').glob('*.svg')},
           'source_sha256':hashlib.sha256((folder/'workspace/solution.py').read_bytes()).hexdigest()}
    entry['model_show']={k:v for k,v in json.loads((folder/'model-show.json').read_text()).items() if k not in ('license','modelfile','template')}
    entry['loaded_model']=json.loads((folder/'loaded-model.json').read_text())
    entry['nonzero_commands']=[{'index':i,'command':x.get('command'),'output':x.get('aggregated_output')} for i,x in enumerate(commands,1) if x.get('exit_code')!=0]
    entry['stderr_tool_router_errors']=[line for line in stderr.splitlines() if 'tools::router' in line and 'ERROR' in line]
    entry['command_heads']=[{'index':i,'exit_code':x.get('exit_code'),'first_line':x.get('command','').splitlines()[0]} for i,x in enumerate(commands,1)]
    entry['first_command_output']=commands[0].get('aggregated_output')
    if r['with_skill']:
        entry['helper_observation']=json.loads((folder/'helper-observation.json').read_text())
        entry['batch_method_counts']=dict(__import__('collections').Counter(x['method'] for x in entry['helper_observation']['calls']))
        entry['helper_serialization_used']=entry['batch_method_counts'].get('to_json',0)>0

    runs.append(entry)
    assert hashlib.sha256((folder/'workspace/graphs.json').read_bytes()).hexdigest()==manifest['input_sha256']
    if r['with_skill']:
        package=folder/'workspace/.agents/skills/dialogue-graph'
        for name,data in original_skill.items():assert (package/name).read_bytes()==data,name
assert len({x['manifest']['prompt_sha256'] for x in runs})==1
assert len({x['manifest']['input_sha256'] for x in runs})==1
assert len({x['manifest']['mini_checker_sha256'] for x in runs})==1
lookup={(x['result']['model'],x['result']['with_skill']):x for x in runs}
a=lookup[('qwen3.5:27b',False)]['result'];b=lookup[('qwen3.5:27b',True)]['result']
deltas={'27b_with_minus_without_elapsed_s':round(b['elapsed_s']-a['elapsed_s'],3),
        '27b_with_minus_without_commands':b['command_calls']-a['command_calls']}
# Verify the effective catalog, task identity, and unedited validated source.
case=base/'reproduction/graph_audit_9b'
original_catalog=json.loads((base/'reproduction/validated_harness/local_models.json').read_text())
effective_catalog=json.loads((case/'local_models.json').read_text())
assert effective_catalog['models'][:len(original_catalog['models'])]==original_catalog['models']
new=effective_catalog['models'][-1];expected_model=dict(original_catalog['models'][0]);expected_model['slug']=expected_model['display_name']='qwen3.5:9b'
assert new==expected_model
catalog_sha=hashlib.sha256((case/'local_models.json').read_bytes()).hexdigest()
assert all(x['manifest']['environment']['effective_catalog_sha256']==catalog_sha for x in runs)
assert len({x['manifest']['mini_runner_sha256'] for x in runs})==1
for x in runs:
    assert x['manifest']['mini_runner_sha256']==hashlib.sha256((case/'run.py').read_bytes()).hexdigest()
pattern=(set(lookup)=={('qwen3.5:9b',True),('qwen3.5:27b',False),('qwen3.5:27b',True)} and all(x['result']['success'] for x in runs))
summary={'task_id':'graph-audit-9b','batch':root.parent.name,'raw_archive':archive_path.name,
         'raw_archive_sha256':hashlib.sha256(archive_path.read_bytes()).hexdigest(),'required_case_observed':pattern,
         'analysis_is_new_model_run':False,'runs':runs,'paired_descriptive_differences':deltas,
         'preparation':json.loads((root.parent/'preparation/preparation.json').read_text()),
         'effective_catalog_sha256':catalog_sha,
         'findings':['All three required fresh conditions pass all ten original functional checks; no older model output is reused.',
          '9B with skill executes original from_dict/validate/visualize; all eight API inputs invoke validate. Batch observation records six validations and three renders. JSON export uses json.dump, so the requested to_json reuse is incomplete.',
          '27B with skill executes from_dict/validate/to_json/visualize. Batch observation records twelve validations, three serializations and three renders; all eight API inputs use the original validator.',
          'Each program has one visible complete file write and passes native verification. All inputs and supplied helper files are byte-identical to the archived original.',
          '9B nonzero command reads nonexistent __init__.py. 27B baseline nonzero commands probe absent skill paths. 27B with-skill grep for doublecircle exits one because the SVG contains geometry rather than that literal; it is not a failed render.',
          '27B baseline stderr has one unavailable image-tool call; 27B with-skill stderr has unsupported read_file, unknown write_stdin process, and persistent-thread goal-tool errors. These are outside completed shell-command counts. Original integration_ok fields remain unchanged.',
          '9B and 27B both report MLX/NVFP4 in model-show; tag sizes are 9.7B and 27.4B. Runtime snapshots show 65,536 context. Only 9B show parameters list draft_num_predict=3, so cross-model latency is not an isolated parameter-count effect.',
          '27B with-minus-without is 52.586 seconds and five commands. No 9B without-skill observation or formal repetitions exist; the case does not prove a causal skill improvement.'],
         'case_interpretation':'Requested three-success case observed once under the shared prospective protocol; selected exploratory case, not a general capability or causal claim.'}
(base/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
print(json.dumps({'all_original_scores_reproduced':True,'integrity_verified':True,'required_case_observed':pattern,
                  'writes':{x['result']['run_id']:x['visible_full_file_write_count'] for x in runs},'deltas':deltas,
                  'method_counts':{x['result']['run_id']:x.get('batch_method_counts') for x in runs},
                  'stderr_router_errors':{x['result']['run_id']:len(x['stderr_tool_router_errors']) for x in runs}},indent=2))
