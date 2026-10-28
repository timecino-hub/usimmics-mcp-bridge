import asyncio
import base64
import io
import json
import os
from pathlib import Path
import sys
from PIL import Image
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


def test_real_stdio_workspace_roundtrip(tmp_path):
    cfg=tmp_path/'config.json'
    cfg.write_text(json.dumps({'installation_root':str(tmp_path/'no-simulator'),'project_roots':[str(tmp_path)]}),encoding='utf-8')
    spec=json.loads((Path(__file__).parents[1]/'examples/voltage_divider.json').read_text(encoding='utf-8'))
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=['-m','usimmics_bridge.server'],
                                    env={**os.environ,'USIMMICS_BRIDGE_CONFIG':str(cfg)})
        async with stdio_client(params) as (r,w):
            async with ClientSession(r,w) as s:
                await s.initialize()
                names={t.name for t in (await s.list_tools()).tools}
                assert {'build_schematic','parameter_sweep','compare_results','save_schematic','preview_schematic'}<=names
                async def call(name,args,error=False):
                    out=await s.call_tool(name,args)
                    assert bool(out.isError)==error,(name,out)
                    return out
                def data(out):return json.loads(next(c.text for c in out.content if c.type=='text'))
                proj=data(await call('create_project',{'name':'test'}))
                path=str(Path(proj['project_path'])/'divider.sch')
                await call('build_schematic',{'path':path,**spec})
                source=data(await call('read_schematic',{'path':path}))
                assert data(await call('validate_schematic',{'path':path}))['valid']
                out=await call('preview_schematic',{'path':path})
                image=next(c for c in out.content if c.type=='image')
                Image.open(io.BytesIO(base64.b64decode(image.data))).verify()
                await call('save_schematic',{'path':path,'content':source['content']},error=True)
                await call('save_schematic',{'path':path,'content':source['content'],'expected_sha256':source['sha256']})
                await call('clone_schematic',{'source':path,'destination':str(Path(path).with_name('variant.sch'))})
    asyncio.run(exercise())
