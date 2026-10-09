#!/usr/bin/env python3
"""Run the three-condition graph audit experiment."""
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

CASE=Path(__file__).resolve().parent
ROOT=CASE.parent/'harness'
DATA=CASE.parent/'runs/graph-audit-9b'
RUN_LIMIT=360


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def dump(path,value):path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def load_harness():
    spec=importlib.util.spec_from_file_location('validated_dialogue_harness',ROOT/'study.py')
    if spec is None or spec.loader is None:raise RuntimeError('Existing research harness is missing.')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    info=module.environment();module.require_setup(info)
    original_catalog=json.loads((ROOT/'local_models.json').read_text())
    effective_catalog=json.loads((CASE/'local_models.json').read_text())
    old_models={m['slug']:m for m in original_catalog['models']}
    new_models={m['slug']:m for m in effective_catalog['models']}
    assert all(new_models.get(k)==v for k,v in old_models.items()),'Existing catalog entries changed'
    expected=dict(old_models['qwen3.5:4b']);expected['slug']=expected['display_name']='qwen3.5:9b'
    assert new_models.get('qwen3.5:9b')==expected,'9B generic harness metadata differs'
    assert set(new_models)==set(old_models)|{'qwen3.5:9b'},'Unexpected custom catalog entry'
    original_agent_command=module.agent_command
    def effective_agent_command(*args):
        command,disabled=original_agent_command(*args)
        old='model_catalog_json='+json.dumps(str(ROOT/'local_models.json'))
        new='model_catalog_json='+json.dumps(str(CASE/'local_models.json'))
        assert command.count(old)==1,'Missing unique catalog configuration'
        return [new if a==old else a for a in command],disabled
    module.agent_command=effective_agent_command
    info['effective_catalog_path']=str(CASE/'local_models.json')
    info['effective_catalog_sha256']=sha(CASE/'local_models.json')
    info['catalog_override']='In-memory command argument only; validated base files unchanged'
    return module,info


def prepare_model(harness,batch):
    folder=DATA/batch/'preparation';folder.mkdir(parents=True,exist_ok=True)
    model='qwen3.5:9b'
    before=harness.api('/api/tags');dump(folder/'models-before.json',before)
    installed=next((m for m in before['models'] if m.get('name')==model),None)
    record={'model':model,'installed_before':installed is not None,'download_limit_s':600,
            'source':'https://ollama.com/library/qwen3.5:9b','preparation_is_model_trial':False}
    dump(folder/'preparation.json',record)
    if installed is None:
        ollama=shutil.which('ollama')
        if not ollama:raise RuntimeError('ollama executable missing; cannot download the 9B candidate')
        print('Downloading qwen3.5:9b (up to 10 minutes).',flush=True)
        execution=harness.run_process([ollama,'pull',model],CASE,folder,'model-pull',600,env=dict(os.environ))
        record['download_execution']=execution;dump(folder/'preparation.json',record)
        if execution['exit_code']!=0 or execution['timed_out'] or execution['interrupted']:
            raise RuntimeError('9B download did not complete; see preparation/model-pull.stderr. No model trial started.')
    after=harness.api('/api/tags');dump(folder/'models-after.json',after)
    installed=next((m for m in after['models'] if m.get('name')==model),None)
    if installed is None:raise RuntimeError('9B tag still missing after preparation')
    record['installed_model']=installed;record['ready']=True;dump(folder/'preparation.json',record)
    dump(folder/'model-show.json',harness.api('/api/show',{'model':model}))
    print('9B candidate ready. Exact digest/backend/settings are recorded; performance is not assumed.',flush=True)


def trial(harness,info,model,skill,batch):
    run_id=model.replace(':','_')+('-with-' if skill else '-without-')+uuid.uuid4().hex[:6]
    folder=DATA/batch/'runs'/run_id;work=folder/'workspace';work.mkdir(parents=True)
    shutil.copyfile(CASE/'graphs.json',work/'graphs.json')
    skill_source=harness.SOURCE/'environment/skills/dialogue-graph'
    if skill:shutil.copytree(skill_source,work/'.agents/skills/dialogue-graph')
    installed=next((m for m in harness.api('/api/tags')['models'] if m.get('name')==model),None)
    if installed is None:raise RuntimeError('Missing model: '+model)
    command,disabled=harness.agent_command(info,model,work,skill)
    protected_old=str(work/'script.txt');protected_new=str(work/'graphs.json')
    assert any(protected_old in argument for argument in command),'Missing input read-only rule'
    command=[argument.replace(protected_old,protected_new) for argument in command]
    prompt=(CASE/'task.txt').read_bytes()
    manifest={'task_id':'graph-audit-9b','phase':'prospective-weaker-candidate-selection','model':model,'with_skill':skill,
              'started_at':dt.datetime.now().astimezone().isoformat(),
              'run_id':run_id,'model_digest':installed['digest'],'model_details':installed.get('details'),
              'environment':info,'mini_runner_sha256':sha(Path(__file__)),'mini_checker_sha256':sha(CASE/'check.py'),
              'expected_sha256':sha(CASE/'expected.json'),'input_sha256':sha(work/'graphs.json'),
              'prompt_sha256':sha(CASE/'task.txt'),'prompt':prompt.decode(),'timeout_s':RUN_LIMIT,
              'command':command,'disabled_external_skills':disabled,
              'source':'Prospective 9B candidate after 4B V3 failure. V3 task, input, checker, observer and public skill byte-identical. Effective custom catalog adds only generic 9B metadata; three fresh conditions. Earlier trials preserved. Not a SkillsBench score.'}
    dump(folder/'manifest.json',manifest);dump(folder/'model-show.json',harness.api('/api/show',{'model':model}))
    print('\nRUN '+run_id+' (limit '+str(RUN_LIMIT)+' seconds)',flush=True)
    execution=harness.run_process(command,work,folder,'agent',RUN_LIMIT,stdin=prompt,env=harness.clean_env(work,skill))
    try:dump(folder/'loaded-model.json',harness.api('/api/ps'))
    except (OSError,ValueError) as exc:dump(folder/'loaded-model.json',{'snapshot_error':str(exc)})
    (folder/'agent.stdout').rename(folder/'events.jsonl')
    events,bad=harness.read_events(folder/'events.jsonl')
    items=[e.get('item',{}) for e in events if e.get('type')=='item.completed']
    commands=[i for i in items if i.get('type')=='command_execution']
    errors=[i.get('message','') for i in items if i.get('type')=='error']
    stderr=(folder/'agent.stderr').read_text(errors='replace')
    auth_error=any(s in stderr for s in ('HTTP 401','Failed to refresh token','unauthorized_unknown'))
    input_ok=(work/'graphs.json').is_file() and sha(work/'graphs.json')==manifest['input_sha256']
    skill_ok=not skill or all((work/'.agents/skills/dialogue-graph'/p.relative_to(skill_source)).is_file() and
        sha(work/'.agents/skills/dialogue-graph'/p.relative_to(skill_source))==sha(p) for p in skill_source.rglob('*') if p.is_file())
    snapshot=folder/'verification-workspace';shutil.copytree(work,snapshot)
    command=harness.sandbox_command(info['codex_path'],snapshot,
        [str(harness.python_path()),str(CASE/'check.py'),str(snapshot),str(snapshot/'checks.json'),str(CASE/'expected.json'),'1' if skill else '0'],
        grading=True,extra_read=[CASE/'check.py',CASE/'expected.json']+([work/'.agents'] if skill else []))
    verification=harness.run_process(command,snapshot,folder,'verification',60,env=harness.clean_env(snapshot,skill))
    report=json.loads((snapshot/'checks.json').read_text()) if (snapshot/'checks.json').exists() else {'pass':False,'passed':0,'total':10,'checks':[],'error':'No grader report'}
    dump(folder/'verification.json',{'execution':verification,'report':report})
    completed=any(e.get('type')=='turn.completed' for e in events)
    integration=(completed and execution['exit_code']==0 and not execution['timed_out'] and not execution['interrupted']
                 and not auth_error and bad==0 and not any(not harness.nonfatal_event_warning(e) for e in errors))
    result={'run_id':run_id,'model':model,'with_skill':skill,**execution,'turn_completed':completed,
            'passed_checks':report['passed'],'total_checks':report['total'],
            'task_pass':bool(report['pass']) and verification['exit_code']==0 and not verification['timed_out'] and input_ok,
            'input_unchanged':input_ok,'skill_unchanged':skill_ok,'integration_ok':integration,'auth_error':auth_error,
            'unparsed_event_lines':bad,'item_errors':errors,'command_calls':len(commands),
            'failed_commands':sum(c.get('exit_code')!=0 for c in commands),
            'skill_mentions_in_commands':sum('dialogue-graph' in c.get('command','') or 'dialogue_graph' in c.get('command','') for c in commands),
            'usage':[e.get('usage') for e in events if e.get('type')=='turn.completed'],
            'finished_at':dt.datetime.now().astimezone().isoformat()}
    result['observed_skill_read']=any(c.get('exit_code')==0 and 'SKILL.md' in c.get('command','') and 'dialogue-graph' in c.get('command','') for c in commands)
    code=(work/'solution.py').read_text() if (work/'solution.py').is_file() else ''
    try:
        tree=__import__('ast').parse(code)
        result['helper_import_in_solution']=any((isinstance(n,__import__('ast').ImportFrom) and n.module=='dialogue_graph') or (isinstance(n,__import__('ast').Import) and any(a.name=='dialogue_graph' for a in n.names)) for n in __import__('ast').walk(tree))
    except SyntaxError:result['helper_import_in_solution']=False
    result['helper_used_on_all_api_inputs']=report.get('helper_used_on_all_api_inputs')
    result['helper_validation_calls']=len(report.get('helper_validation_calls',[]))
    # Observe a second, isolated copy; never repair agent code or original outputs.
    observation={}
    if skill and (work/'solution.py').is_file():
        observed_work=folder/'helper-observation-workspace';shutil.copytree(work,observed_work)
        observed_command=harness.sandbox_command(info['codex_path'],observed_work,
            [str(harness.python_path()),str(CASE/'observe_helper.py'),str(observed_work/'solution.py'),str(observed_work/'helper-observation.json')],
            grading=True,extra_read=[CASE/'observe_helper.py'])
        observed_execution=harness.run_process(observed_command,observed_work,folder,'helper-observation',60,
                                             env=harness.clean_env(observed_work,True))
        observation_path=observed_work/'helper-observation.json'
        if observation_path.is_file():observation=json.loads(observation_path.read_text())
        observed_skill_ok=all((observed_work/'.agents/skills/dialogue-graph'/p.relative_to(skill_source)).is_file() and
            sha(observed_work/'.agents/skills/dialogue-graph'/p.relative_to(skill_source))==sha(p)
            for p in skill_source.rglob('*') if p.is_file())
        observation['execution']=observed_execution;observation['skill_unchanged']=observed_skill_ok
        observation['reexecution_ok']=(observed_execution['exit_code']==0 and not observed_execution['timed_out'] and
            not observed_execution['interrupted'] and observation.get('solution_and_input_unchanged') is True and observed_skill_ok)
        dump(folder/'helper-observation.json',observation)
    result['batch_helper_method_calls']=len(observation.get('calls',[]))
    result['batch_helper_used_for_supplied_graphs']=(observation.get('reexecution_ok') is True and
                                                  observation.get('used_for_supplied_graphs') is True) if skill else None
    result['helper_use_observed']=(result['helper_validation_calls']>0 or
                                 result['batch_helper_used_for_supplied_graphs'] is True) if skill else None
    result['functional_success']=result['task_pass'] and integration and skill_ok
    result['success']=result['functional_success'] and (not skill or
                         (result['observed_skill_read'] and result['helper_use_observed'] is True))
    dump(folder/'result.json',result)
    print(json.dumps(result,indent=2),flush=True)
    if execution['interrupted']:raise RuntimeError('Interrupted; current evidence saved.')
    return result


def export(batch,results,status):
    folder=DATA/batch;folder.mkdir(parents=True,exist_ok=True)
    dump(folder/'status.json',{'status':status,'completed_trials':len(results),'formal_repetitions':0,
                             'task_id':'graph-audit-9b','prior_results_reused':False,'planned_conditions':3})
    keys=['model','with_skill','functional_success','success','passed_checks','total_checks','elapsed_s',
          'command_calls','failed_commands','timed_out','integration_ok','observed_skill_read',
          'helper_use_observed','helper_used_on_all_api_inputs','batch_helper_method_calls']
    with (folder/'results.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');writer.writeheader();writer.writerows(results)
    required=[r for r in results if r['model']=='qwen3.5:27b' or r['with_skill']]
    pattern=len(results)==3 and len(required)==3 and all(r['success'] for r in required)
    lines=['# Graph audit results','',status,'',
           'Three-condition case observed: '+str(pattern)+'. One run per condition.','',
           '| Model | Skill | Success | Checks | Seconds | Commands |',
           '|---|---|---|---:|---:|---:|']
    for r in results:
        lines.append(f'| {r["model"]} | {r["with_skill"]} | {r["success"]} | {r["passed_checks"]}/{r["total_checks"]} | {r["elapsed_s"]:.3f} | {r["command_calls"]} |')
    lines+=['','Functional checks, instruction reading and method use are separate observations.',
            'Inspect helper-observation.json and solution.py for the exact methods used.','']
    (folder/'report.md').write_text('\n'.join(lines))
    print('\nRequired case pattern observed: '+str(pattern),flush=True)
    target=DATA.parent/('graph-audit-9b-results-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(folder.rglob('*')):
            if p.is_file():z.write(p,Path('graph-audit-9b')/batch/p.relative_to(folder))
        for p in sorted(CASE.iterdir()):
            if p.is_file() and p.suffix!='.pyc':z.write(p,Path('reproduction/graph_audit_9b')/p.name)
        for name in ('study.py','contract_check.py','local_models.json','source_manifest.json','task_prompt.txt'):
            z.write(ROOT/name,Path('reproduction/validated_harness')/name)
        for p in (ROOT/'source').rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:z.write(p,Path('reproduction/validated_harness/source')/p.relative_to(ROOT/'source'))
    print('\nRESULT_ARCHIVE: '+str(target),flush=True)
    if sys.platform=='darwin':subprocess.run(['open','-R',str(target)],check=False)


def main():
    batch=dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:4]
    results=[];status='in progress'
    try:
        harness,info=load_harness()
        prepare_model(harness,batch)
        print('Three required fresh conditions, 360s maximum each; no task-failure gate or automatic retries. Model budget up to 18 minutes plus verification.',flush=True)
        for model,skill in [('qwen3.5:9b',True),('qwen3.5:27b',False),('qwen3.5:27b',True)]:
            results.append(trial(harness,info,model,skill,batch))
        status='Three required graph-audit-9b conditions attempted once each. All failures and timeouts retained.'
    except (Exception,KeyboardInterrupt) as exc:
        status='Stopped: '+type(exc).__name__+': '+str(exc)
        print(status,file=sys.stderr,flush=True)
        return 1
    finally:
        try:export(batch,results,status)
        except Exception as exc:print('Export failed: '+str(exc)+'; logs remain at '+str(DATA/batch),file=sys.stderr,flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
