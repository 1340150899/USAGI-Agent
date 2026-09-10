"""Authenticated channel ingress and durable task/media endpoints."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field


class Part(BaseModel):
    model_config=ConfigDict(extra='forbid')
    type: Literal['text','image']
    text: str | None = None
    media_id: str | None = None


class Event(BaseModel):
    model_config=ConfigDict(extra='forbid')
    schema_version: Literal[1]=1
    source_stream_id: str = Field(min_length=1,max_length=256)
    source_event_ref: str = Field(min_length=1,max_length=256)
    source_cursor_ref: str=''
    ordering_mode: Literal['observation_ordered']='observation_ordered'
    account_ref: str
    conversation_ref: str
    sender_ref: str
    uid: str | None=None
    message_id: str | None=None
    ref_msg_id: str | None=None
    reply_route_ref: str | None=None
    direction: Literal['incoming','outgoing']='incoming'
    occurred_at: str
    source_gap: bool=False
    content_parts: list[Part] = Field(max_length=100)


def install_ingress(app, service, authenticate):
    from fastapi import Depends
    store=service.store

    @app.post('/v1/ingress/events',status_code=202)
    async def events(event: Event, authorization: str=Header(default=''),auth=Depends(authenticate)):
        binding=service.settings['credentials'].get(authorization.removeprefix('Bearer '),{})
        source=binding.get('source')
        static_route_required=source=='wxauto'
        if (source not in ('weixin','wxauto')
                or event.account_ref!=binding.get('account_ref')
                or (static_route_required and (
                    event.sender_ref not in binding.get('sender_refs',[])
                    or event.conversation_ref not in binding.get('conversation_refs',[])))):
            raise HTTPException(403,'source/account/sender/conversation not bound')
        principal=auth.principal.principal_opaque_id
        for part in event.content_parts:
            if part.type=='image' and not store.media(part.media_id,principal):
                raise HTTPException(409,'media is not ready')
            if part.type=='text' and part.text is None:
                raise HTTPException(422,'text required')
        if binding['source']=='wxauto':
            event.reply_route_ref=None
        else:
            event.reply_route_ref=event.conversation_ref
        try:
            seq=store.accept(principal,binding['source'],
                             'material' if binding['source']=='wxauto' else 'interactive',event.model_dump())
        except ValueError as exc:
            raise HTTPException(409,str(exc)) from exc
        return {'accepted':True,'accepted_seq':seq,'source_event_ref':event.source_event_ref}

    @app.post('/v1/ingress/media',status_code=201)
    async def media(request: Request,auth=Depends(authenticate)):
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>20*1024*1024:
                raise HTTPException(413,'media exceeds 20 MiB')
        mime=request.headers.get('content-type','').split(';')[0]
        valid=(mime=='image/jpeg' and content[:3]==b'\xff\xd8\xff' or
               mime=='image/png' and content[:8]==b'\x89PNG\r\n\x1a\n' or
               mime=='image/webp' and content[:4]==b'RIFF' and content[8:12]==b'WEBP' or
               mime=='image/gif' and content[:6] in (b'GIF87a',b'GIF89a'))
        if not valid:
            raise HTTPException(415,'supported media: JPEG, PNG, WebP, GIF')
        principal=auth.principal.principal_opaque_id
        digest=hashlib.sha256(content).hexdigest()
        media_id=hashlib.sha256((principal+':'+digest).encode()).hexdigest()
        root=Path(service.settings['data_dir']).resolve()/'media';root.mkdir(parents=True,exist_ok=True)
        path=root/(media_id+{'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'}[mime])
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise HTTPException(409,'media content conflict')
        else:
            temporary=None
            try:
                with tempfile.NamedTemporaryFile(dir=root,delete=False) as output:
                    temporary=output.name
                    output.write(content);output.flush();os.fsync(output.fileno())
                os.replace(temporary,path)
            finally:
                if temporary and os.path.exists(temporary):os.unlink(temporary)
        with store.db() as db:
            db.execute('INSERT OR IGNORE INTO media VALUES (?,?,?,?,?)',(media_id,principal,str(path),mime,digest))
        return {'media_id':media_id,'sha256':digest,'media_type':mime}

    @app.get('/v1/media/{media_id}')
    async def download(media_id: str,auth=Depends(authenticate)):
        record=store.media(media_id,auth.principal.principal_opaque_id)
        if not record:
            raise HTTPException(404,'media not found')
        return FileResponse(record['path'],media_type=record['mime'])

    @app.get('/v1/tasks/{task_id}')
    async def task(task_id: str,auth=Depends(authenticate)):
        record=store.job(task_id,auth.principal.principal_opaque_id)
        if not record:
            raise HTTPException(404,'task not found')
        return {k:record[k] for k in ('id','status','run_id','outcome')}

    @app.get('/v1/notifications')
    async def notifications(auth=Depends(authenticate)):
        with store.db() as db:
            return [{**dict(row),'payload':json.loads(row['payload'])} for row in db.execute(
                'SELECT * FROM deliveries WHERE principal=? ORDER BY rowid DESC LIMIT 100',
                (auth.principal.principal_opaque_id,))]

    @app.get('/v1/adapters/status')
    async def adapter_status(auth=Depends(authenticate)):
        prefix=auth.principal.principal_opaque_id+':'
        with store.db() as db:
            return [dict(row) for row in db.execute('SELECT * FROM heartbeat WHERE substr(source,1,?)=?',(len(prefix),prefix))]

    @app.post('/v1/adapters/heartbeat')
    async def heartbeat(request: Request,auth=Depends(authenticate)):
        body=await request.json()
        if len(json.dumps(body))>8192:
            raise HTTPException(413,'heartbeat too large')
        import time
        binding=service.settings['credentials'][request.headers['authorization'].removeprefix('Bearer ')]
        source=auth.principal.principal_opaque_id+':'+binding.get('source','api')+':'+binding.get('account_ref','')
        with store.db() as db:
            db.execute('INSERT OR REPLACE INTO heartbeat VALUES (?,?,?)',
                       (source,json.dumps(body),time.time()))
        return {'accepted':True}

    @app.post('/v1/conversations/{conversation_ref}/clear-gap')
    async def clear_gap(conversation_ref: str,auth=Depends(authenticate)):
        # Explicit operator reconciliation discards the incomplete pending range.
        with store.db() as db:
            db.execute("UPDATE events SET consumed=1,status='consumed',selection_id=NULL WHERE principal=? AND conversation=? AND mode='material' AND status='unconsumed'",
                       (auth.principal.principal_opaque_id,conversation_ref))
        return {'cleared':True,'discarded_pending_range':True}
