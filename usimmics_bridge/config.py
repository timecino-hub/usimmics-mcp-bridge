"""Portable configuration discovery; no installation of the third-party simulator."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .core import BridgeError
from .schemas import BridgeConfig


def config_path(explicit=None):
    if explicit:
        return Path(explicit).expanduser().resolve()
    override = os.environ.get('USIMMICS_BRIDGE_CONFIG')
    if override:
        return Path(override).expanduser().resolve()
    root = Path(os.environ.get('LOCALAPPDATA', Path.home()/'.config')) if os.name=='nt' else Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))
    return root/'usimmics-bridge'/'config.json'


def discover_installation():
    candidates=[]
    for key in ('USIMMICS_HOME', 'QUCS_HOME'):
        if os.environ.get(key): candidates.append(Path(os.environ[key]))
    exe=shutil.which('uSimmics.exe')
    if exe: candidates.append(Path(exe).parent.parent)
    candidates += [Path.home()/'AppData/Local/Programs/uSimmics', Path('C:/Apps/uSimmics'),
                   Path('C:/Program Files/uSimmics'), Path.home()/'uSimmics']
    return next((p.resolve() for p in candidates if (p/'bin/simulator.exe').is_file()), None)


def configure(installation=None, projects=None, path=None, force=False):
    dest=config_path(path)
    if dest.exists() and not force:
        raise BridgeError('Configuration exists; use --force to replace it with backup')
    install=Path(installation).expanduser().resolve() if installation else discover_installation()
    if not install or not (install/'bin/simulator.exe').is_file():
        raise BridgeError('Cannot find simulator.exe; specify --installation-root')
    requested = [Path.home()/'.qucs'] if projects is None else projects
    if not isinstance(requested, list) or not requested:
        raise BridgeError('project_roots must be a nonempty list of paths')
    if any(not isinstance(p, (str, os.PathLike)) or not str(p).strip() for p in requested):
        raise BridgeError('Project paths must be nonempty strings or paths')
    roots=[Path(p).expanduser().resolve() for p in requested]
    payload = BridgeConfig(installation_root=str(install), project_roots=[str(p) for p in roots]).model_dump()
    for root in roots: root.mkdir(parents=True,exist_ok=True)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        from uuid import uuid4
        shutil.copy2(dest,dest.with_name(dest.name+'.bak.'+uuid4().hex))
    # This is an explicit CLI setup action, not an MCP mutation of host policy.
    with dest.open('w',encoding='utf-8',newline='\n') as f:
        json.dump(payload,f,indent=2);f.write('\n')
    return {'config_path':str(dest),**payload}


def parse_simulator_version(banner):
    """Read a full version token, not a substring such as 5.9 inside 15.90.

    Prefer a version associated with the simulator's product name. A single
    unambiguous version token also accommodates terse native --version output.
    """
    product = re.search(r'\b(?:uSimmics|QucsStudio|simulator)\b[^\r\n\d]{0,48}(\d+)\.(\d+)(?:\.(\d+))?(?![\w.])', banner, re.I)
    if product:
        return '.'.join(part for part in product.groups() if part is not None)
    versions = set(re.findall(r'(?<![\w.])(\d+\.\d+(?:\.\d+)?)(?![\w.])', banner))
    return versions.pop() if len(versions) == 1 else None


def _workspace_check(roots):
    checks = []
    for value in roots:
        path = Path(value)
        check = {'path': str(path), 'exists': False, 'is_directory': False, 'writable': False}
        try:
            check['exists'] = path.exists()
            check['is_directory'] = path.is_dir()
            check['writable'] = check['is_directory'] and os.access(path, os.W_OK | os.X_OK)
        except OSError as exc:
            check['error'] = str(exc)
        check['ready'] = check['is_directory'] and check['writable']
        if not check['ready']:
            check['next_step'] = 'Create this directory or choose a writable project root with configure'
        checks.append(check)
    return {'ready': bool(checks) and all(item['ready'] for item in checks), 'roots': checks,
            'writability_check': 'OS permission check; actual writes can still fail if permissions or free space change'}


def doctor(path=None):
    dest=config_path(path)
    report={'config_path':str(dest),'configured':dest.is_file(),'python':sys.version.split()[0],
            'platform':sys.platform,'native_solver_supported':os.name=='nt'}
    workspace = _workspace_check([])
    if dest.is_file():
        try:
            from .workspace import Workbench
            workbench = Workbench(dest)
            report.update(workbench.environment_status())
            workspace = _workspace_check(workbench.roots)
        except (OSError, ValueError) as exc: report['error']=str(exc)
    else:
        found=discover_installation()
        report.update(discovered_installation=str(found) if found else None,
                      next_step='Run usimmics-bridge configure --installation-root <uSimmics root> --project-root <project root>')
    version = parse_simulator_version(report.get('simulator_banner', ''))
    version_ok = version is not None and version.split('.')[:2] == ['5', '9']
    solver = {'ready': bool(report.get('simulator_exists') and
                           report.get('simulator_returncode') == 0 and version_ok and os.name == 'nt'),
              'exists': bool(report.get('simulator_exists')), 'version': version,
              'supported_version': '5.9', 'version_supported': version_ok,
              'version_exit_code': report.get('simulator_returncode'),
              'platform_supported': os.name == 'nt'}
    if not solver['ready']:
        solver['next_step'] = 'Use Windows and configure a working uSimmics 5.9 installation; --version must exit successfully'
    bat54 = {'ready': bool(report.get('bat54_available')), 'required_for_base_workflow': False}
    if not bat54['ready']:
        bat54['next_step'] = 'For BAT54 circuits, restore library/B_Schottky.lib from your own uSimmics installation'
    report.update(workspace_ready=workspace['ready'], solver_ready=solver['ready'], bat54_ready=bat54['ready'],
                  checks={'workspace': workspace, 'solver': solver, 'bat54': bat54},
                  ready=workspace['ready'] and solver['ready'])
    return report
