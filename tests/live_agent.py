"""Optional real Codex protocol/inference probe. Only a disposable sample is exposed."""
import argparse
import json
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from vampm.codex_chat import CodexChat
from vampm.agent_tools import TOOLS
from vampm.agent import INSTRUCTIONS

parser=argparse.ArgumentParser()
parser.add_argument('--infer',action='store_true')
parser.add_argument('--trace',action='store_true')
parser.add_argument('--model',default='gpt-6.1-sol')
args=parser.parse_args()
with tempfile.TemporaryDirectory() as temp:
    cfg={'codex_path':'','codex_model':args.model}
    chat=CodexChat(cfg,Path(temp)/'workspace')
    if args.trace:
        original=chat.receive
        def receive(*values):
            m=original(*values)
            p=m.get('params',{})
            print(json.dumps({'event':m.get('method','reply'),'id':m.get('id'),
                'tool':p.get('tool'),'status':p.get('turn',{}).get('status'),
                'item_type':p.get('item',{}).get('type'),
                'error':m.get('error') or (p if m.get('method') in ('error','warning') else None)},ensure_ascii=True),flush=True)
            return m
        chat.receive=receive
    try:
        thread=chat.connect_thread(None,INSTRUCTIONS,TOOLS,threading.Event())
        print('Codex initialize + thread/start with dynamic package tools: OK',flush=True)
        if args.infer:
            calls=[]
            def tool(name,arguments):
                calls.append(name)
                if name!='get_status': raise ValueError('This probe only exposes get_status')
                return {'stats':{'packages':1,'files':1,'active':1}}
            chat.turn('请调用 get_status 查看资源包总数，然后用一句中文告诉我。',threading.Event(),
                lambda kind,value:print(json.dumps({'kind':kind,'value':value},ensure_ascii=True),flush=True) if kind=='message' else None,tool)
            assert 'get_status' in calls
            print('Live inference and tool call: OK',flush=True)
        chat.close()
        resumed=CodexChat(cfg,Path(temp)/'workspace')
        try:
            assert resumed.connect_thread(thread,INSTRUCTIONS,TOOLS,threading.Event())==thread
            print('Codex thread/resume: OK',flush=True)
        finally:resumed.close()
    finally:chat.close()
