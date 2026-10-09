#!/usr/bin/env python3
"""Local Codex runtime and setup checks."""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'runs' / 'setup-checks'
SOURCE = ROOT / 'source'
REVISION = 'graph-audit-reproduction-1'


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def api(path, data=None):
    request = urllib.request.Request('http://127.0.0.1:11434' + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def python_path():
    return ROOT / '.venv' / 'bin' / 'python'


def run_process(command, cwd, folder, stem, timeout, stdin=None, env=None):
    folder.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    timed_out, interrupted = False, False
    with (folder / (stem + '.stdout')).open('wb') as stdout, (folder / (stem + '.stderr')).open('wb') as stderr:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
            stdout=stdout, stderr=stderr, env=env, start_new_session=True)
        try:
            pending = stdin
            deadline = start + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    process.communicate(pending, timeout=min(30, remaining))
                    break
                except subprocess.TimeoutExpired:
                    pending = None
                    if time.monotonic() >= deadline:
                        raise
                    print(f'  {stem}: {int(time.monotonic() - start)}s elapsed; logs are being saved.', flush=True)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            interrupted = isinstance(exc, KeyboardInterrupt)
            timed_out = not interrupted
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    return {'exit_code': process.returncode, 'timed_out': timed_out, 'interrupted': interrupted,
            'elapsed_s': round(time.monotonic() - start, 3)}


def clean_env(work=None, with_skill=False):
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTEST_ADDOPTS'):
        env.pop(key, None)
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    env['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
    env['PATH'] = str(python_path().parent) + os.pathsep + env.get('PATH', '')
    if work is not None:
        temp = work / '.tmp'
        temp.mkdir(parents=True, exist_ok=True)
        for key in ('TMPDIR', 'TMP', 'TEMP'):
            env[key] = str(temp)
        env['TMPPREFIX'] = str(temp / 'zsh')
    if with_skill:
        env['PYTHONPATH'] = str(work / '.agents/skills/dialogue-graph/scripts')
    return env


def config_flags(work, grading=False, extra_read=()):
    """Read only the workspace and runtime; keep controller/source/other runs private."""
    read_paths = {ROOT / '.venv', Path(sys.base_prefix).resolve(), python_path().resolve().parent.parent}
    for directory in ('/opt/homebrew', '/usr/local', '/usr/lib', '/usr/share', '/bin', '/sbin', '/System', '/Library/Frameworks'):
        if Path(directory).exists():
            read_paths.add(Path(directory))
    if grading:
        read_paths.update({SOURCE / 'verifier', ROOT / 'contract_check.py'})
    filesystem = {
        ':root': 'deny', ':minimal': 'read', ':tmpdir': 'write', ':slash_tmp': 'deny',
        str(work): 'write',
        str(work / '.tmp'): 'write',
    }
    if not grading:
        filesystem[str(work / 'script.txt')] = 'read'
        filesystem[str(work / '.agents')] = 'read'
    for path in read_paths | set(extra_read):
        filesystem[str(path)] = 'read'
    entries = {'default_permissions': 'study', 'permissions.study.extends': ':workspace',
               'permissions.study.network.enabled': False}
    flags = []
    for key, value in entries.items():
        flags += ['-c', key + '=' + json.dumps(value)]
    # CLI dotted keys split at dots without interpreting quoted path components.
    # Put the complete filesystem map in one TOML value so paths retain their actual keys.
    table = '{' + ','.join(json.dumps(k) + '=' + json.dumps(v)
                           for k, v in sorted(filesystem.items())) + '}'
    flags += ['-c', 'permissions.study.filesystem=' + table]
    return flags


def sandbox_command(codex, work, command, grading=False, extra_read=()):
    flags = config_flags(work, grading, extra_read)
    return [codex, 'sandbox', '--permission-profile', 'study', '--cd', str(work)] + flags + ['--'] + command


def external_skills(work):
    bases = {Path.home() / '.agents/skills', Path.home() / '.codex/skills'}
    for parent in work.parents:
        bases.add(parent / '.agents/skills')
    paths = set()
    for base in bases:
        if base.exists():
            paths.update(str(p.resolve()) for p in base.rglob('SKILL.md'))
    return sorted(paths)


def isolated_config(work, with_skill):
    pairs = {
        'cli_auth_credentials_store': 'ephemeral',
        'model_catalog_json': str(ROOT / 'local_models.json'),
        'model_reasoning_effort': 'none',
        'web_search': 'disabled', 'model_context_window': 65536,
        'features.apps': False,
        'features.plugins': False, 'features.remote_models': False,
        'features.memories': False, 'features.multi_agent': False,
        'skills.bundled.enabled': False, 'memories.use_memories': False,
        'memories.generate_memories': False, 'project_doc_max_bytes': 0,
        'shell_environment_policy.inherit': 'core',
        'shell_environment_policy.set.PATH': clean_env(work)['PATH'],
        'shell_environment_policy.set.PYTHONDONTWRITEBYTECODE': '1',
        'shell_environment_policy.set.TMPDIR': str(work / '.tmp'),
        'shell_environment_policy.set.TMP': str(work / '.tmp'),
        'shell_environment_policy.set.TEMP': str(work / '.tmp'),
        'shell_environment_policy.set.TMPPREFIX': str(work / '.tmp/zsh'),
        'developer_instructions': (
            'This task runs in a native macOS workspace. All /app paths in the task refer '
            'to the current working directory. Python dependencies are already installed; '
            f'use {python_path()} to run Python. Graphviz is available. '
            'Use only the workspace and installed runtime. Do not install dependencies or access the network.'
        ),
    }
    if with_skill:
        pairs['shell_environment_policy.set.PYTHONPATH'] = str(work / '.agents/skills/dialogue-graph/scripts')
    else:
        pairs['shell_environment_policy.set.PYTHONPATH'] = ''
    flags = config_flags(work)
    for key, value in pairs.items():
        flags += ['-c', key + '=' + json.dumps(value)]
    disabled = external_skills(work)
    # A TOML inline-table array, supplied as one argv element without shell interpolation.
    if disabled:
        value = '[' + ','.join('{path=' + json.dumps(path) + ',enabled=false}' for path in disabled) + ']'
        flags += ['-c', 'skills.config=' + value]
    return flags, disabled


def verify_sources():
    manifest = json.loads((ROOT / 'source_manifest.json').read_text())
    for relative, expected in manifest['sha256'].items():
        if sha(ROOT / relative) != expected:
            raise RuntimeError('Source changed: ' + relative)
    return manifest


def environment():
    codex = shutil.which('codex')
    if not codex:
        raise RuntimeError('codex is missing; install with brew install --cask codex')
    if not python_path().exists():
        raise RuntimeError('Run python3 study.py setup first.')
    if not shutil.which('dot'):
        raise RuntimeError('Graphviz is missing; run brew install graphviz')
    return {
        'codex_path': codex,
        'codex_version': subprocess.check_output([codex, '--version'], text=True, timeout=30).strip(),
        'ollama_version': api('/api/version'), 'host': platform.platform(),
        'python_version': subprocess.check_output([str(python_path()), '--version'], text=True).strip(),
        'dot_version': subprocess.run(['dot', '-V'], capture_output=True, text=True).stderr.strip(),
        'revision': REVISION, 'source': verify_sources(),
        'controller_sha256': sha(Path(__file__)), 'contract_sha256': sha(ROOT / 'contract_check.py'),
        'catalog_sha256': sha(ROOT / 'local_models.json'),
        'prompt_sha256': sha(ROOT / 'task_prompt.txt'),
    }


def selfcheck(codex, folder):
    work = folder / 'workspace'
    work.mkdir(parents=True)
    (work / 'script.txt').write_text('sandbox test\n')
    canary = folder / 'private-canary.txt'
    canary.write_text('controller-only\n')
    code = (
        'from pathlib import Path; import socket\n'
        'assert Path("script.txt").read_text() == "sandbox test\\n"\n'
        'Path("writable.txt").write_text("ok")\n'
        f'p=Path({str(canary)!r})\n'
        'try: p.read_text()\n'
        'except PermissionError: pass\n'
        'else: raise AssertionError("outside read was allowed")\n'
        'try: p.write_text("changed")\n'
        'except PermissionError: pass\n'
        'else: raise AssertionError("outside write was allowed")\n'
        'try:\n'
        ' s=socket.socket(); s.settimeout(2); s.connect(("127.0.0.1",11434))\n'
        'except OSError as e: assert e.errno in (1,13), "network failed without proof of a permission denial"\n'
        'else: raise AssertionError("command network was allowed")\n'
        'print("ISOLATION_PASS")\n'
    )
    command = sandbox_command(codex, work, [str(python_path()), '-c', code])
    result = run_process(command, work, folder, 'isolation', 60, env=clean_env(work))
    result['pass'] = (result['exit_code'] == 0 and not result['timed_out']
                      and not result.get('interrupted')
                      and 'ISOLATION_PASS' in (folder/'isolation.stdout').read_text())
    result['command'] = command
    if not result['pass']:
        dump(folder / 'isolation.json', result)
        raise RuntimeError('Sandbox self-check failed. No model run started. See ' + str(folder / 'isolation.stderr')
                           + '\n' + (folder / 'isolation.stderr').read_text(errors='replace')[-4000:])
    payload = ('#' + 'x' * 24000 + '\n'
               'import os, tempfile\nfrom pathlib import Path\n'
               f'assert Path(tempfile.gettempdir()).resolve() == Path({str(work / ".tmp")!r}).resolve()\n'
               'with tempfile.TemporaryFile() as f: f.write(b"temporary writes work")\n'
               'print("HEREDOC_PASS")\n')
    shell = 'cat > heredoc_probe.py <<\'PY_CODE\'\n' + payload + 'PY_CODE\n' + str(python_path()) + ' heredoc_probe.py\n'
    heredoc = run_process(sandbox_command(codex, work, ['/bin/zsh', '-lc', shell]),
        work, folder, 'heredoc', 60, env=clean_env(work))
    result['heredoc_check'] = heredoc
    result['pass'] = (result['pass'] and heredoc['exit_code'] == 0 and not heredoc['timed_out']
                      and 'HEREDOC_PASS' in (folder / 'heredoc.stdout').read_text())
    dump(folder / 'isolation.json', result)
    if not result['pass']:
        raise RuntimeError('Large-heredoc self-check failed. See ' + str(folder / 'heredoc.stderr')
                           + '\n' + (folder / 'heredoc.stderr').read_text(errors='replace')[-2000:])
    return result


def grade(codex, work, folder, with_skill):
    snapshot = folder / 'verification-workspace'
    shutil.copytree(work, snapshot)
    env = clean_env(snapshot, with_skill)
    # Keep collection inside the permitted root. An absolute test path outside
    # rootdir makes pytest scan ancestors such as /Users before importing it.
    # This is a byte-identical copy, created only after the model exits.
    verifier = snapshot / '.verification' / 'test_outputs.py'
    verifier.parent.mkdir()
    shutil.copyfile(SOURCE / 'verifier/test_outputs.py', verifier)
    if sha(verifier) != sha(SOURCE / 'verifier/test_outputs.py'):
        raise RuntimeError('Verifier copy differs from the pinned source.')
    report = snapshot / 'upstream.xml'
    command = sandbox_command(codex, snapshot,
        [str(python_path()), '-m', 'pytest', '-q', '--capture=sys', '-p', 'no:cacheprovider', '--noconftest',
         '-c', '/dev/null', '--rootdir=' + str(snapshot), '--confcutdir=' + str(snapshot),
         '.verification/test_outputs.py', '--junitxml=' + str(report)], grading=True)
    execution = run_process(command, snapshot, folder, 'upstream', 120, env=env)
    counts = {'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0, 'total': 0}
    if report.exists():
        for case in ET.parse(report).getroot().iter('testcase'):
            counts['total'] += 1
            key = 'failed' if case.find('failure') is not None else 'errors' if case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
            counts[key] += 1
        shutil.copyfile(report, folder / 'upstream.xml')
    upstream_pass = (execution['exit_code'] == 0 and not execution['timed_out']
                     and counts['total'] == 35 and counts['passed'] == counts['total'])
    contract_path = snapshot / 'contract.json'
    command = sandbox_command(codex, snapshot,
        [str(python_path()), str(ROOT / 'contract_check.py'), str(snapshot), str(contract_path)],
        grading=True, extra_read=[work / '.agents'] if with_skill else ())
    contract_execution = run_process(command, snapshot, folder, 'contract', 60, env=env)
    contract = json.loads(contract_path.read_text()) if contract_path.exists() else {'pass': False, 'error': 'No contract report'}
    contract['pass'] = bool(contract.get('pass')) and contract_execution['exit_code'] == 0 and not contract_execution['timed_out']
    syntax = run_process(sandbox_command(codex, snapshot, ['dot', '-Tdot', 'dialogue.dot'], grading=True),
        snapshot, folder, 'dot-syntax', 60, env=env)
    result = {'verifier_sha256': sha(verifier),
              'upstream': {**counts, **execution, 'pass': upstream_pass,
                          'score': counts['passed'] / counts['total'] if counts['total'] else 0},
              'contract': {**contract, 'execution': contract_execution},
              'dot_syntax': {**syntax, 'pass': syntax['exit_code'] == 0 and not syntax['timed_out']}}
    dump(folder / 'verification.json', result)
    return result


def read_events(path):
    events, bad = [], 0
    for line in path.read_text(errors='replace').splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError('event is not an object')
            events.append(value)
        except ValueError:
            bad += 1
    return events, bad


def agent_command(info, model, work, with_skill):
    flags, disabled = isolated_config(work, with_skill)
    command = [info['codex_path'], '--no-daemon', '-a', 'never', 'exec', '--ignore-user-config',
        '--oss', '--local-provider', 'ollama', '--model', model, '--skip-git-repo-check',
        '--ephemeral', '--json', '--cd', str(work)] + flags + ['-']
    return command, disabled


def nonfatal_event_warning(message):
    # Codex emits these informational notices as item.type=error. Keep all
    # messages in the record, but do not equate a notice with a failed turn.
    return ((message.startswith('Model metadata for `') and 'Defaulting to fallback metadata' in message)
            or message.startswith('Heads up: Long threads and multiple compactions'))


def setup():
    if platform.system() != 'Darwin':
        raise RuntimeError('This runner targets macOS. It is not the official container benchmark runner.')
    verify_sources()
    if not shutil.which('dot'):
        raise RuntimeError('First run: brew install graphviz')
    if not python_path().exists():
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / '.venv')], check=True)
    subprocess.run([str(python_path()), '-m', 'pip', 'install', 'graphviz==0.20.3', 'pytest==8.3.4'], check=True)
    env_info = environment()
    folder = DATA / 'setup' / dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    folder.mkdir(parents=True)
    dump(folder / 'environment.json', env_info)
    selfcheck(env_info['codex_path'], folder / 'isolation')
    # Run the untouched public oracle only to validate the grading setup, never as model data.
    work = folder / 'oracle-workspace'
    work.mkdir()
    shutil.copyfile(SOURCE / 'environment/script.txt', work / 'script.txt')
    shutil.copyfile(SOURCE / 'oracle/solution.py', work / 'solution.py')
    process = run_process(sandbox_command(env_info['codex_path'], work, [str(python_path()), 'solution.py']),
                          work, folder, 'oracle', 60, env=clean_env(work))
    verification = grade(env_info['codex_path'], work, folder / 'oracle-verification', False)
    setup_pass = (process['exit_code'] == 0 and not process['timed_out']
                  and not process['interrupted'] and verification['upstream']['pass']
                  and verification['dot_syntax']['pass'])
    dump(folder / 'setup-result.json', {'pass': setup_pass, 'oracle_execution': process,
                                      'oracle_verification': verification})
    if not setup_pass:
        report = folder / 'oracle-verification/upstream.xml'
        details = ''
        if report.exists():
            for node in ET.parse(report).getroot().iter():
                if node.tag in ('error', 'failure'):
                    details = '\n' + ''.join(node.itertext())[-2000:]
                    break
        raise RuntimeError('Oracle fixture did not pass. No model run started. See ' + str(folder) + details)
    print('SETUP_PASS. Original oracle passes output tests; its text API mismatch is recorded separately.')
    print('Logs: ' + str(folder))


def require_setup(current):
    checks = sorted((DATA / 'setup').glob('*/setup-result.json'))
    if not checks or not json.loads(checks[-1].read_text()).get('pass'):
        raise RuntimeError('Complete setup first: both sandbox and oracle output checks must pass.')
    saved = json.loads((checks[-1].parent / 'environment.json').read_text())
    for key in ('controller_sha256', 'contract_sha256', 'catalog_sha256', 'prompt_sha256', 'codex_version',
                'ollama_version', 'dot_version', 'python_version'):
        if current.get(key) != saved.get(key):
            raise RuntimeError('Setup environment changed: ' + key + '. Run setup again.')
