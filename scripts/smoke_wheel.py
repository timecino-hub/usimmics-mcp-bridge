"""Install a built wheel into a fresh venv, then exercise it outside the checkout.

This driver uses only the standard library until it runs inside the new venv.
Pass --requirements with uv's exact runtime export for the locked dependency job.
Without it, pip resolves the latest versions allowed by the wheel's metadata.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv


def _clean_environment() -> dict:
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'USIMMICS_BRIDGE_CONFIG', 'USIMMICS_HOME', 'QUCS_HOME'):
        env.pop(key, None)
    env['PYTHONNOUSERSITE'] = '1'
    return env


def _installed_check() -> None:
    import usimmics_bridge
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from PIL import Image

    prefix = Path(sys.prefix).resolve()
    package = Path(usimmics_bridge.__file__).resolve()
    assert sys.prefix != sys.base_prefix, 'Smoke check must run in its fresh venv'
    assert package.is_relative_to(prefix), f'Imported checkout instead of installed wheel: {package}'
    binaries = prefix / ('Scripts' if os.name == 'nt' else 'bin')
    command = binaries / ('usimmics-bridge.exe' if os.name == 'nt' else 'usimmics-bridge')
    config = Path.cwd() / 'portable-config.json'
    projects = Path.cwd() / 'projects with spaces'
    projects.mkdir()
    config.write_text(json.dumps({'installation_root': str(Path.cwd() / 'no-simulator'),
                                  'project_roots': [str(projects)]}), encoding='utf-8')
    env = _clean_environment()
    env['USIMMICS_BRIDGE_CONFIG'] = str(config)

    def cli(*arguments):
        result = subprocess.run([str(command), *arguments], check=True,
                                capture_output=True, text=True, env=env, timeout=60)
        return json.loads(result.stdout)

    assert {item['name'] for item in cli('examples', 'list')['templates']} == {'voltage_divider', 'mixer'}
    template = cli('examples', 'show', 'voltage_divider')
    assert template['requires_model'] is None
    assert cli('examples', 'show', 'mixer')['requires_model']
    request_file = Path.cwd() / 'divider-build.json'

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
                                           args=['-m', 'usimmics_bridge.server'], env=env)
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert {'create_project', 'build_schematic', 'preview_schematic',
                        'read_schematic', 'save_schematic', 'validate_schematic',
                        'list_schematic_templates', 'get_schematic_template',
                        'start_job', 'job_status', 'cancel_job'} <= set(catalog)
                schema = catalog['build_schematic'].inputSchema
                assert {'path', 'components', 'connections'} <= set(schema.get('required', []))

                async def call(name, arguments, expect_error=False):
                    result = await session.call_tool(name, arguments)
                    assert bool(result.isError) == expect_error, (name, result)
                    return result

                def data(result):
                    return json.loads(next(item.text for item in result.content if item.type == 'text'))

                project = data(await call('create_project', {'name': 'first_example'}))
                path = str(Path(project['project_path']) / 'divider.sch')
                cli('examples', 'export', 'voltage_divider', '--output', str(request_file),
                    '--schematic-path', path)
                arguments = json.loads(request_file.read_text(encoding='utf-8'))
                await call('build_schematic', arguments)
                preview = await call('preview_schematic', {'path': path})
                image = next(item for item in preview.content if item.type == 'image')
                Image.open(io.BytesIO(base64.b64decode(image.data))).verify()
                source = data(await call('read_schematic', {'path': path}))
                await call('save_schematic', {'path': path, 'content': source['content']}, expect_error=True)
                saved = data(await call('save_schematic', {'path': path, 'content': source['content'],
                                                           'expected_sha256': source['sha256']}))
                assert Path(saved['backup']).is_file()
                reread = data(await call('read_schematic', {'path': path}))
                assert reread['content'] == source['content']
                assert data(await call('validate_schematic', {'path': path}))['valid']
                names = data(await call('list_schematic_templates', {}))
                assert {item['name'] for item in names['templates']} == {'voltage_divider', 'mixer'}
                fetched = data(await call('get_schematic_template', {'name': 'voltage_divider'}))
                assert fetched['specification'] == template['specification']

    asyncio.run(asyncio.wait_for(exercise(), timeout=90))
    print(json.dumps({'status': 'passed', 'version': importlib.metadata.version('usimmics-mcp-bridge'),
                      'package': str(package), 'working_directory': str(Path.cwd()),
                      'portable_workflow': ['create', 'build', 'preview', 'read', 'save'],
                      'native_solver_executed': False}))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, help='A .whl file, or the dist directory containing one wheel')
    parser.add_argument('--requirements', type=Path, help='Exact runtime dependency export for locked CI')
    parser.add_argument('--installed-check', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.installed_check:
        _installed_check()
        return 0
    if args.wheel is None:
        parser.error('--wheel is required')
    wheels = sorted(args.wheel.glob('*.whl')) if args.wheel.is_dir() else [args.wheel]
    if len(wheels) != 1 or not wheels[0].is_file() or wheels[0].suffix != '.whl':
        parser.error('--wheel must identify exactly one built .whl')
    wheel = wheels[0].resolve()
    requirements = args.requirements.resolve() if args.requirements else None
    with tempfile.TemporaryDirectory(prefix='usimmics-wheel-smoke-') as temporary:
        root = Path(temporary)
        environment = root / 'venv'
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
        python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        outside = root / 'outside-checkout'
        outside.mkdir()
        env = _clean_environment()
        install = [str(python), '-m', 'pip', 'install', '--disable-pip-version-check']
        if requirements:
            install += ['--requirement', str(requirements)]
        install.append(str(wheel))
        subprocess.run(install, check=True, cwd=outside, env=env, timeout=300)
        driver = outside / 'installed_smoke.py'
        shutil.copy2(Path(__file__), driver)
        subprocess.run([str(python), str(driver), '--installed-check'], check=True,
                       cwd=outside, env=env, timeout=120)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
