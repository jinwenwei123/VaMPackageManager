"""Codex App Server stdio adapter; no shell scripts and no direct game-file access."""
import collections
import json
import os
import queue
import re
import subprocess
import threading
import time
import tomllib
from pathlib import Path

from . import __version__
from .providers import CODEX_DISABLED_FEATURES, find_codex
from .util import Cancelled, hidden_process_args, network_process_env, stop_process


def diagnostic(message):
    if any(x in message.lower() for x in ('access token could not be refreshed','unauthorized_unknown','refresh_token_reused')):
        return 'Codex 登录凭据已失效，请到「AI 与设置」点击「登录 Codex」重新登录。'
    message=re.sub(r'(?:sk-[\w-]+|eyJ[\w.-]+)', '[已隐藏]',str(message))
    message=re.sub(r'(?i)(bearer|authorization|api[_-]?key)\s*[:= ]+\S+',r'\1 [已隐藏]',message)
    return message[-1000:]


class CodexChat:
    def __init__(self, cfg, work):
        self.cfg, self.work = cfg, Path(work)
        self.process=None
        self.messages=queue.Queue()
        self.errors=collections.deque(maxlen=20)
        self.next_id=0
        self.write_lock=threading.Lock()
        self.process_lock=threading.RLock()
        self.thread_id=None
        self.turn_id=None

    def start(self, cancel):
        if cancel.is_set(): raise Cancelled('包管家正在退出。')
        if self.process and self.process.poll() is None: return
        self.work.mkdir(parents=True,exist_ok=True)
        # App Server has no exec --ignore-user-config flag. Override runtime features,
        # and explicitly disable configured MCP servers without reading their secrets.
        command=[find_codex(self.cfg['codex_path']),'app-server','--listen','stdio://']
        overrides={**{'features.'+name:False for name in CODEX_DISABLED_FEATURES},
            'web_search':'disabled','project_doc_max_bytes':0,
            'approval_policy':'never'}
        config=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'config.toml'
        try:
            with config.open('rb') as f:
                servers=tomllib.load(f).get('mcp_servers',{})
            for name in servers:
                overrides['mcp_servers.'+json.dumps(name)+'.enabled']=False
        except (OSError,ValueError): pass
        for key,value in overrides.items(): command+=['-c',key+'='+json.dumps(value)]
        self.messages=queue.Queue();self.errors.clear()
        with self.process_lock:
            if cancel.is_set(): raise Cancelled('包管家正在退出。')
            self.process=subprocess.Popen(command,cwd=self.work,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',bufsize=1,
                env=network_process_env(),**hidden_process_args())
            process=self.process;messages=self.messages
        def reader():
            try:
                for line in process.stdout:
                    try: messages.put(json.loads(line))
                    except ValueError: pass
            except (OSError,ValueError): pass
            finally: messages.put(None)
        def stderr_reader():
            try:
                for line in process.stderr:
                    self.errors.append(diagnostic(line.strip()))
            except (OSError,ValueError): pass
        threading.Thread(target=reader,daemon=True).start()
        threading.Thread(target=stderr_reader,daemon=True).start()
        self.rpc('initialize',{'clientInfo':{'name':'vam_package_manager','title':'VaM 包管家','version':__version__},
                              'capabilities':{'experimentalApi':True}},cancel)
        self.send({'method':'initialized','params':{}})

    def send(self, message):
        with self.write_lock:
            if not self.process or self.process.poll() is not None:
                raise ValueError('Codex 进程已退出。'+diagnostic('\n'.join(self.errors)))
            self.process.stdin.write(json.dumps(message,ensure_ascii=False)+'\n')
            self.process.stdin.flush()

    def receive(self, cancel, deadline):
        while True:
            if cancel.is_set(): raise Cancelled('已停止对话；已完成的文件操作可以在操作记录中恢复。')
            auth=next((x for x in list(self.errors) if '登录凭据已失效' in x),None)
            if auth: raise ValueError(auth)
            if time.monotonic()>deadline: raise TimeoutError('等待 Codex 响应超时，请检查网络、登录和模型权限。')
            try: message=self.messages.get(timeout=.15)
            except queue.Empty: continue
            if message is None: raise ValueError('Codex 进程已退出。'+diagnostic('\n'.join(self.errors)))
            return message

    def rpc(self, method, params, cancel, handler=None):
        self.next_id+=1;request_id=self.next_id
        self.send({'id':request_id,'method':method,'params':params})
        deadline=time.monotonic()+45
        while True:
            message=self.receive(cancel,deadline)
            if message.get('id')==request_id and 'method' not in message:
                if message.get('error'): raise ValueError(diagnostic(message['error'].get('message',str(message['error']))))
                return message.get('result',{})
            if handler: handler(message)

    def connect_thread(self, thread_id, instructions, tools, cancel):
        self.start(cancel)
        params={'model':self.cfg['codex_model'],'cwd':str(self.work),'approvalPolicy':'never',
                'sandbox':'read-only','baseInstructions':instructions}
        if thread_id:
            out=self.rpc('thread/resume',dict(params,threadId=thread_id,excludeTurns=True),cancel)
        else:
            out=self.rpc('thread/start',dict(params,ephemeral=False,environments=[],dynamicTools=tools),cancel)
        self.thread_id=out['thread']['id']
        return self.thread_id

    def turn(self, text, cancel, emit, tool):
        seen_calls={}
        def handler(message):
            method=message.get('method','');p=message.get('params',{})
            if method=='item/tool/call' and 'id' in message:
                call_id=p.get('callId',str(message['id']))
                if call_id not in seen_calls:
                    try:
                        output=tool(p['tool'],p.get('arguments',{}))
                        images=output.get('_images',[]) if isinstance(output,dict) else []
                        public={k:v for k,v in output.items() if k!='_images'} if isinstance(output,dict) else output
                        result={'success':True,'contentItems':[{'type':'inputText','text':json.dumps(public,ensure_ascii=False)}]+
                                [{'type':'inputImage','imageUrl':im['url']} for im in images]}
                    except Cancelled: raise
                    except Exception as e:
                        result={'success':False,'contentItems':[{'type':'inputText','text':str(e)}]}
                    seen_calls[call_id]=result
                self.send({'id':message['id'],'result':seen_calls[call_id]})
            elif 'id' in message and method:
                # Custom package tools require no Codex shell/file approvals.
                # Unsupported prompts are declined; the agent can ask in ordinary chat.
                if 'requestApproval' in method: result={'decision':'decline'}
                elif 'requestUserInput' in method: result={'answers':{}}
                else:
                    self.send({'id':message['id'],'error':{'code':-32601,'message':'仅支持包管家专用工具，请在对话中说明需要用户提供的信息。'}})
                    return
                self.send({'id':message['id'],'result':result})
            elif method=='item/agentMessage/delta':
                emit('delta',{'id':p.get('itemId','reply'),'text':p.get('delta','')})
            elif method=='item/completed' and p.get('item',{}).get('type')=='agentMessage':
                item=p['item'];emit('message',{'id':item['id'],'text':item.get('text','')})
            elif method=='thread/tokenUsage/updated': emit('usage',p.get('tokenUsage',{}))
            elif method=='error':
                error=diagnostic(p.get('error',{}).get('message',str(p)))
                if not p.get('willRetry') or any(x in error.lower() for x in ('not supported','unauthorized','invalid_api_key','does not exist')):
                    raise ValueError(error)
        params={'threadId':self.thread_id,'input':[{'type':'text','text':text,'text_elements':[]}]}
        if self.cfg.get('_chat_effort') is not None: params['effort']=self.cfg['_chat_effort']
        out=self.rpc('turn/start',params,cancel,handler)
        self.turn_id=out['turn']['id']
        deadline=time.monotonic()+300
        while True:
            message=self.receive(cancel,deadline)
            if message.get('method')=='turn/completed':
                turn=message['params']['turn']
                if turn.get('id')!=self.turn_id: continue
                if turn.get('status')!='completed':
                    raise ValueError(diagnostic((turn.get('error') or {}).get('message','对话已中断')))
                self.turn_id=None
                return
            start=time.monotonic()
            handler(message)
            if message.get('method')=='item/tool/call': deadline+=time.monotonic()-start

    def close(self):
        with self.process_lock: self._close()

    def _close(self):
        p=self.process
        if not p: return
        if p.poll() is None:
            if self.thread_id and self.turn_id:
                try: self.send({'id':-1,'method':'turn/interrupt','params':{'threadId':self.thread_id,'turnId':self.turn_id}})
                except (OSError,ValueError): pass
            try:
                p.stdin.close()
                p.wait(timeout=2)
            except (OSError,subprocess.TimeoutExpired):
                stop_process(p)
        for stream in (p.stdin,p.stdout,p.stderr):
            try: stream.close()
            except OSError: pass
        self.process=None;self.turn_id=None
