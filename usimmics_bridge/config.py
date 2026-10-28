"""Portable configuration discovery; no installation of the third-party simulator."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .core import BridgeError


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
    roots=[Path(p).expanduser().resolve() for p in (projects or [Path.home()/'.qucs'])]
    if not roots: raise BridgeError('At least one project root is required')
    for root in roots: root.mkdir(parents=True,exist_ok=True)
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        from uuid import uuid4
        shutil.copy2(dest,dest.with_name(dest.name+'.bak.'+uuid4().hex))
    payload={'installation_root':str(install),'project_roots':[str(p) for p in roots]}
    # This is an explicit CLI setup action, not an MCP mutation of host policy.
    with dest.open('w',encoding='utf-8',newline='\n') as f:
        json.dump(payload,f,indent=2);f.write('\n')
    return {'config_path':str(dest),**payload}


def doctor(path=None):
    dest=config_path(path)
    report={'config_path':str(dest),'configured':dest.is_file(),'python':sys.version.split()[0],
            'platform':sys.platform,'native_solver_supported':os.name=='nt'}
    if dest.is_file():
        try:
            from .workspace import Workbench
            report.update(Workbench(dest).environment_status())
        except (OSError, ValueError) as exc: report['error']=str(exc)
    else:
        found=discover_installation()
        report.update(discovered_installation=str(found) if found else None,
                      next_step='Run usimmics-bridge configure --installation-root <uSimmics root> --project-root <project root>')
    report['ready']=bool(report.get('configured') and report.get('simulator_exists') and
                         '5.9' in report.get('simulator_banner','') and os.name=='nt')
    return report
