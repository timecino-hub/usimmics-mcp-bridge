"""Invoke the actual stdio MCP server before a chat reloads its tool catalog."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('tool', help='Tool name, or list')
    parser.add_argument('--arguments-file', type=Path)
    parser.add_argument('--config', type=Path)
    args=parser.parse_args()
    env=dict(os.environ)
    if args.config: env['USIMMICS_BRIDGE_CONFIG']=str(args.config.resolve())
    params=StdioServerParameters(command=sys.executable,
        args=['-m','usimmics_bridge.server'], env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as session:
            await session.initialize()
            if args.tool=='list':
                print(json.dumps({'tools':[t.name for t in (await session.list_tools()).tools]}))
                return 0
            arguments=json.loads(args.arguments_file.read_text(encoding='utf-8-sig')) if args.arguments_file else {}
            result=await session.call_tool(args.tool,arguments)
            # Images already have a persistent image_path in the text metadata. Do not print base64.
            content=[]
            for item in result.content:
                if item.type=='text':
                    try: content.append(json.loads(item.text))
                    except ValueError: content.append(item.text)
                elif item.type=='image': content.append({'type':'image','mimeType':item.mimeType})
            print(json.dumps({'isError':result.isError,'content':content},ensure_ascii=False))
            return 1 if result.isError else 0


def entrypoint():
    raise SystemExit(asyncio.run(main()))

if __name__=='__main__':
    entrypoint()
