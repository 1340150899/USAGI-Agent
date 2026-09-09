"""Run in the logged-in Windows desktop session, not Service Session 0."""
import argparse
import hashlib
import importlib
import json
import mimetypes
import os
import threading
import time
from pathlib import Path

import httpx
from .spool import Spool


def upload_loop(spool,config,stop):
    with httpx.Client(base_url=config['server_url'],headers={
            'Authorization':'Bearer '+os.environ[config.get('token_env','USAGI_WXAUTO_TOKEN')]},timeout=30) as client:
        while not stop.is_set():
            for event_id,raw in spool.pending():
                body=json.loads(raw)
                image=body.pop('_image_path',None)
                try:
                    if image:
                        file=Path(image)
                        result=client.post('/v1/ingress/media',content=file.read_bytes(),
                            headers={'Content-Type':mimetypes.guess_type(file.name)[0] or 'application/octet-stream'})
                        result.raise_for_status()
                        body['content_parts'].append({'type':'image','media_id':result.json()['media_id']})
                    response=client.post('/v1/ingress/events',json=body);response.raise_for_status()
                    spool.ack(event_id)
                except (httpx.HTTPError,OSError):
                    break
            try:
                client.post('/v1/adapters/heartbeat',json={'pending':len(spool.pending()),'collector':config.get('_status','starting')}).raise_for_status()
            except httpx.HTTPError:
                pass
            stop.wait(2)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--rebaseline',action='store_true',help='Discard local observation anchor; separately clear any server gap after review')
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8'))
    if os.name!='nt':raise RuntimeError('live wxauto collection requires Windows')
    if not config.get('conversations'):raise ValueError('explicit conversation allowlist required')
    root=Path(config.get('data_dir','.usagi-edge')).resolve();root.mkdir(parents=True,exist_ok=True)
    (root/'images').mkdir(exist_ok=True)
    spool=Spool(root/'edge.db')
    module=config.get('wxauto_module','wxauto')
    if module not in ('wxauto','wxauto4','wxautox4'):raise ValueError('unsupported wxauto module')
    factory=importlib.import_module(module).WeChat
    wx=None
    stop=threading.Event()
    worker=threading.Thread(target=upload_loop,args=(spool,config,stop),daemon=True);worker.start()
    baseline=args.rebaseline
    try:
        while not stop.is_set():
            if wx is None:
                try:
                    wx=factory(ads=False) if module=='wxauto4' else factory()
                except Exception as exc:
                    config['_status']='initialization_failed:'+type(exc).__name__
                    stop.wait(10)
                    continue
            for conversation in config['conversations']:
                try:
                    wx.ChatWith(conversation['name'])
                    observed=[]
                    for msg in wx.GetAllMessage():
                        kind=getattr(msg,'type','text')
                        if kind not in ('text','image'):continue
                        sender=str(getattr(msg,'sender',''))
                        outgoing=getattr(msg,'attr','')=='self' or sender==config.get('self_name')
                        text=str(getattr(msg,'content',''))
                        record={'sender_ref':config['self_ref'] if outgoing else conversation['sender_ref'],
                                'direction':'outgoing' if outgoing else 'incoming','text':text}
                        record['signature']=hashlib.sha256(json.dumps([sender,kind,text,str(getattr(msg,'time',''))],ensure_ascii=False).encode()).hexdigest()
                        if kind=='image':
                            # Download while the UI message object is still valid.
                            download=getattr(msg,'download',None)
                            if download is None:raise RuntimeError('this wxauto version has no image download API')
                            file=download(dir_path=str(root/'images'))
                            if not isinstance(file,(str,Path)) or not Path(file).is_file():raise RuntimeError('image unavailable')
                            data=Path(file).read_bytes()
                            immutable=root/'images'/(hashlib.sha256(data).hexdigest()+Path(file).suffix)
                            if not immutable.exists():immutable.write_bytes(data)
                            record['image_path']=str(immutable)
                        observed.append(record)
                    spool.capture(config,conversation,observed,rebaseline=baseline)
                    config['_status']='observing'
                except Exception as exc:
                    config['_status']='unavailable:'+type(exc).__name__
            baseline=False
            stop.wait(config.get('poll_seconds',3))
    except KeyboardInterrupt:
        pass
    finally:
        stop.set();worker.join(timeout=35)


if __name__=='__main__':main()
