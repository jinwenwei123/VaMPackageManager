"""Deterministic App Server protocol fixture, launched only by tests."""
import json
import sys

def send(value):
    print(json.dumps(value),flush=True)

turn=0
for line in sys.stdin:
    message=json.loads(line)
    method=message.get('method')
    if method=='initialize': send({'id':message['id'],'result':{'userAgent':'fixture'}})
    elif method in ('thread/start','thread/resume'):
        if method=='thread/start': assert any(t['name']=='get_status' for t in message['params']['dynamicTools'])
        send({'id':message['id'],'result':{'thread':{'id':'fixture-thread'}}})
    elif method=='turn/start':
        turn+=1
        send({'id':message['id'],'result':{'turn':{'id':str(turn),'status':'inProgress'}}})
        if 'wait' in message['params']['input'][0]['text']: continue
        request={'id':1000+turn,'method':'item/tool/call','params':{'threadId':'fixture-thread','turnId':str(turn),
            'callId':'call-'+str(turn),'tool':'get_status','arguments':{}}}
        send(request)
    elif message.get('id')==1000+turn and 'result' in message:
        result=message['result'];assert result['success']
        value=json.loads(result['contentItems'][0]['text'])
        assert '_images' not in value
        assert result['contentItems'][1]=={'type':'inputImage','imageUrl':'data:image/png;base64,fixture'}
        text='当前资源数：'+str(value['stats']['packages'])
        send({'method':'item/agentMessage/delta','params':{'itemId':'answer-'+str(turn),'delta':text[:4]}})
        send({'method':'item/agentMessage/delta','params':{'itemId':'answer-'+str(turn),'delta':text[4:]}})
        send({'method':'item/completed','params':{'item':{'id':'answer-'+str(turn),'type':'agentMessage','text':text}}})
        send({'method':'turn/completed','params':{'turn':{'id':str(turn),'status':'completed'}}})
    elif method=='turn/interrupt':
        send({'id':message['id'],'result':{}})
