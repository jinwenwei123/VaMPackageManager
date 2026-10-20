import argparse
import json
import os
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from vampm.server import Application, serve
from vampm.util import atomic_json, read_json


def lock_instance(data):
    data.mkdir(parents=True,exist_ok=True)
    handle=(data/'server.lock').open('a+b')
    handle.seek(0,2)
    if handle.tell()==0: handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        return None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--game')
    parser.add_argument('--home')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    home=Path(args.home).resolve() if args.home else Path(__file__).resolve().parent.parent
    game=Path(args.game).resolve() if args.game else home.parent
    data=home/'data'
    lock=lock_instance(data)
    if lock is None:
        for _ in range(30):
            state=read_json(data/'server.json',{})
            try:
                req=urllib.request.Request(state['url']+'/api/status',headers={'X-Session-Token':state['token']})
                with urllib.request.urlopen(req,timeout=2) as r:
                    if r.status==200:
                        if not args.no_browser:
                            request=urllib.request.Request(state['url']+'/api/ui-session/launch',data=b'{}',
                                headers={'X-Session-Token':state['token'],'Content-Type':'application/json'})
                            with urllib.request.urlopen(request,timeout=2): pass
                            webbrowser.open(state['url']+'/#'+state['token'])
                        return
            except Exception:
                # The previous instance may be finishing its last-page shutdown.
                lock=lock_instance(data)
                if lock is not None: break
                time.sleep(.2)
        else: raise RuntimeError('已有实例尚未就绪，请稍后重试。')
    app=Application(game,home)
    if args.game and app.directories.path_key(game)!=app.directories.path_key(app.active.c.game):
        target=next((item for item in app.directories.value['items']
                     if not item.get('archived') and app.directories.path_key(item['path'])==app.directories.path_key(game)),None)
        if target is None:
            app.post('/api/game-directories/add',{'path':str(game)})
            target=next(item for item in app.directories.value['items']
                        if not item.get('archived') and app.directories.path_key(item['path'])==app.directories.path_key(game))
        app.post('/api/game-directories/switch',{'id':target['id']})
    server=serve(app,args.port)
    url='http://127.0.0.1:'+str(server.server_port)
    atomic_json(data/'server.json',{'url':url,'token':app.token,'pid':os.getpid()})
    try: os.chmod(data/'server.json',0o600)
    except OSError: pass
    app.pages.start()
    if not args.no_browser: webbrowser.open(url+'/#'+app.token)
    if sys.stdout:
        print(('VaM Package Manager started: ' if app.language.get()['language']=='en' else 'VaM 包管家已启动：')+url,flush=True)
    try: server.serve_forever(poll_interval=.3)
    finally:
        app.pages.stop()
        app.active.summaries.suspend()
        app.active.jobs.stop()
        app.active.agent.close()
        app.active.ai.close()
        app.active.jobs.wait()
        server.server_close()
        (data/'server.json').unlink(missing_ok=True)
        lock.close()


if __name__=='__main__':
    try: main()
    except Exception:
        import traceback
        home=Path(__file__).resolve().parent.parent
        (home/'data').mkdir(exist_ok=True)
        with (home/'data'/'startup-error.log').open('a',encoding='utf-8') as f:
            traceback.print_exc(file=f)
        if os.name=='nt':
            import ctypes
            from vampm.language import Language
            english = Language(home/'data').get()['language']=='en'
            ctypes.windll.user32.MessageBoxW(0,
                'Startup failed. See data\\startup-error.log.' if english else '启动失败，请查看 data\\startup-error.log。',
                'VaM Package Manager' if english else 'VaM 包管家',16)
        raise
