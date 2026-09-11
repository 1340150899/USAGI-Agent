import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).parents[3]/'apps/httpserver'))
sys.path.insert(0,str(Path(__file__).parents[3]/'apps/wechat-edge'))
from usagi_httpserver.api import create_app
from usagi_httpserver.draft_images import selected_image_indices
from usagi_httpserver.service import ApplicationService
from usagi_httpserver.store import AppStore
from usagi_httpserver.tool_specs import XHS_TOOL_SPECS
from usagi_httpserver.wechat_worker import WeChatMaterialWorker
from wechat_edge.spool import Spool

from tests.tools.test_durable_approval import build
from usagi_agent.prompts import WECHAT_MATERIAL_PROMPT
from usagi_agent.types.refs import ArtifactRef


def test_candidate_pool_migrates_existing_event_rows(tmp_path):
    path=tmp_path/'old.db'
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT,event_key TEXT UNIQUE,digest TEXT,
            principal TEXT,conversation TEXT,mode TEXT,payload TEXT,received REAL,
            consumed INTEGER DEFAULT 0)''')
        db.execute("INSERT INTO events VALUES (1,'a','d','owner','c','material','{}',1,0)")
        db.execute("INSERT INTO events VALUES (2,'b','d','owner','c','material','{}',2,1)")
    store=AppStore(path)
    with store.db() as db:
        rows=db.execute('SELECT status,available_since FROM events ORDER BY seq').fetchall()
    assert [(row['status'],row['available_since']) for row in rows]==[
        ('unconsumed',1),('consumed',2)]


def test_spool_upload_queue_semantics(tmp_path):
    spool=Spool()
    spool.enqueue('chat:101',json.dumps({'source_event_ref':'chat:101'}))
    spool.enqueue('chat:102',json.dumps({'source_event_ref':'chat:102'}))
    # FIFO order is preserved (no loss, no reordering)
    assert [event_id for event_id,_ in spool.pending()]==['chat:101','chat:102']
    # Duplicate event ids are dropped (idempotent queue-level dedup)
    spool.enqueue('chat:101',json.dumps({'source_event_ref':'chat:101'}))
    assert len(spool.pending())==2
    # ack removes exactly the acked event; unknown ids are harmless
    spool.ack('chat:101');spool.ack('nonexistent')
    assert [event_id for event_id,_ in spool.pending()]==['chat:102']
    assert spool.size()==1
    assert not (tmp_path/'edge.db').exists()


def test_material_window_requires_silence_and_image_and_tracks_states(tmp_path):
    store=AppStore(tmp_path/'app.db')
    base={'account_ref':'a','source_stream_id':'s','conversation_ref':'c','source_gap':False}
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'1',
        'content_parts':[{'type':'text','text':'caption'}]})
    assert store.select_material_window(silence=90,now=time.time()+100) is None
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'2',
        'content_parts':[{'type':'image','media_id':'image'}]})
    assert store.select_material_window(silence=90,now=time.time()+10) is None

    window=store.select_material_window(silence=90,now=time.time()+100)
    assert window is not None
    with store.db() as db:
        assert [row['status'] for row in db.execute('SELECT status FROM events ORDER BY seq')]==['selected','selected']
    assert store.select_material_window(silence=0,now=time.time()+100) is None

    store.finish_material_window(window['id'],consumed=False)
    with store.db() as db:
        assert [row['status'] for row in db.execute('SELECT status FROM events ORDER BY seq')]==['unconsumed','unconsumed']
    assert store.select_material_window(silence=90,now=time.time()+10) is None
    assert store.select_material_window(silence=90,now=time.time()+100) is None
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'3',
        'content_parts':[{'type':'text','text':'new material'}]})
    assert store.select_material_window(silence=90,now=time.time()+10) is None
    retry=store.select_material_window(silence=90,now=time.time()+100)
    store.finish_material_window(retry['id'],consumed=True)
    with store.db() as db:
        assert [row['status'] for row in db.execute('SELECT status FROM events ORDER BY seq')]==['consumed','consumed','consumed']


@pytest.mark.asyncio
@pytest.mark.parametrize(('published','expected'),[(False,'selected'),(True,'consumed')])
async def test_material_window_calls_agent_directly_and_settles_pool(tmp_path,published,expected):
    store=AppStore(tmp_path/'app.db')
    image=tmp_path/'image.png';image.write_bytes(b'image')
    digest=hashlib.sha256(b'image').hexdigest()
    with store.db() as db:
        db.execute('INSERT INTO media VALUES (?,?,?,?,?)',('media','owner',str(image),'image/png',digest))
    event={'account_ref':'a','source_stream_id':'s','conversation_ref':'c','source_gap':False,
           'source_event_ref':'1','content_parts':[{'type':'image','media_id':'media'},{'type':'text','text':'素材'}]}
    store.accept('owner','wxauto','material',event)
    window=store.select_material_window(silence=0,now=time.time()+1)

    class Artifacts:
        async def put(self,**kwargs):
            return ArtifactRef(artifact_id='artifact',content_type='image/png')
    class ToolExecutions:
        async def list_by_run(self,run_id):
            return ((SimpleNamespace(tool_name='xhs_publish_content',execution_status='settled_success'),)
                    if published else ())
    class Server:
        def __init__(self):
            self.requests=[]
            self.runtime=SimpleNamespace(
                persistence=SimpleNamespace(
                    artifact_manager=Artifacts(),tool_execution_store=ToolExecutions()),
                session_manager=SimpleNamespace())
        async def create_session(self,request,auth=None):
            self.requests.append(request)
            return SimpleNamespace(session_id='session',run_id='run',
                outcome=SimpleNamespace(kind='completed'),message='done')
    server=Server()
    worker=WeChatMaterialWorker(
        server,store,{'super_uid':'6'},stopping=SimpleNamespace())
    await worker.execute(window)

    assert server.requests[0].scenario_key=='example.research_writer'
    assert '先判断' in server.requests[0].input.query
    assert 'xhs_publish_content' in server.requests[0].input.query
    assert '这一轮禁止调用' in server.requests[0].input.query
    with store.db() as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
        assert db.execute('SELECT status FROM events').fetchone()[0]==expected
        payload=json.loads(db.execute('SELECT payload FROM deliveries').fetchone()[0])
    assert payload['broadcast'] is True
    assert payload['media_ids']==['media']  # draft without a marker still shows every image


@pytest.mark.asyncio
async def test_insufficient_initial_material_is_released(tmp_path):
    store=AppStore(tmp_path/'app.db')
    event={'account_ref':'a','source_stream_id':'s','conversation_ref':'c',
           'source_gap':False,'source_event_ref':'1',
           'content_parts':[{'type':'image','media_id':'media'}]}
    store.accept('owner','wxauto','material',event)
    window=store.select_material_window(silence=0,now=time.time()+1)

    class ToolExecutions:
        async def list_by_run(self,run_id): return ()
    worker=WeChatMaterialWorker(
        SimpleNamespace(runtime=SimpleNamespace(persistence=SimpleNamespace(
            tool_execution_store=ToolExecutions()))),store,{},stopping=SimpleNamespace())

    await worker._settle_initial_turn(
        window['id'],'run','completed','素材不足：缺少可用文字。'
    )

    with store.db() as db:
        assert db.execute('SELECT status FROM events').fetchone()[0]=='unconsumed'


@pytest.mark.asyncio
async def test_wechat_worker_reconciles_approved_publication(tmp_path):
    store=AppStore(tmp_path/'app.db')
    event={'account_ref':'a','source_stream_id':'s','conversation_ref':'c','source_gap':False,
           'source_event_ref':'1','content_parts':[{'type':'image','media_id':'media'}]}
    store.accept('owner','wxauto','material',event)
    window=store.select_material_window(silence=0,now=time.time()+1)
    store.bind_material_window(window['id'],'session','run')

    class ToolExecutions:
        async def list_by_run(self,run_id):
            return (SimpleNamespace(tool_name='xhs_publish_content',
                execution_status='settled_success'),)
    class Server:
        runtime=SimpleNamespace(persistence=SimpleNamespace(
            tool_execution_store=ToolExecutions()))
        async def get_run(self,run_id,auth=None):
            return SimpleNamespace(kind='completed')
    worker=WeChatMaterialWorker(
        Server(),store,{'super_uid':'6'},stopping=SimpleNamespace())

    await worker.reconcile_selected_windows()

    with store.db() as db:
        assert db.execute('SELECT status FROM events').fetchone()[0]=='consumed'


@pytest.mark.asyncio
@pytest.mark.parametrize(('published','expected'),[(True,'consumed'),(False,'unconsumed')])
async def test_wechat_worker_settles_after_user_followup(tmp_path,published,expected):
    store=AppStore(tmp_path/'app.db')
    event={'account_ref':'a','source_stream_id':'s','conversation_ref':'c',
           'source_gap':False,'source_event_ref':'1',
           'content_parts':[{'type':'image','media_id':'media'}]}
    store.accept('owner','wxauto','material',event)
    window=store.select_material_window(silence=0,now=time.time()+1)
    store.bind_material_window(window['id'],'session','draft-run')
    store.record_session_run('session','reply-run')

    class ToolExecutions:
        async def list_by_run(self,run_id):
            if run_id=='reply-run' and published:
                return (SimpleNamespace(tool_name='xhs_publish_content',
                    execution_status='settled_success'),)
            return ()
    class Server:
        runtime=SimpleNamespace(persistence=SimpleNamespace(
            tool_execution_store=ToolExecutions()))
        async def get_run(self,run_id,auth=None):
            return SimpleNamespace(kind='completed')
    worker=WeChatMaterialWorker(Server(),store,{'super_uid':'6'},
                                stopping=SimpleNamespace())

    await worker.reconcile_selected_windows()

    with store.db() as db:
        assert db.execute('SELECT status FROM events').fetchone()[0]==expected


def test_material_gap_blocks_snapshot(tmp_path):
    store=AppStore(tmp_path/'app.db')
    event={'account_ref':'a','source_stream_id':'s','source_event_ref':'e','conversation_ref':'c',
           'source_gap':True,'content_parts':[{'type':'text','text':'gap'}]}
    store.accept('owner','wxauto','material',event);store.collect(silence=0,maximum=0)
    assert store.claim() is None


def test_material_gap_starts_a_new_window_instead_of_blocking_future_events(tmp_path):
    store=AppStore(tmp_path/'app.db')
    base={'account_ref':'a','source_stream_id':'s','conversation_ref':'c'}
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'before',
        'source_gap':False,'content_parts':[{'type':'image','media_id':'old-image'}]})
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'gap',
        'source_gap':True,'content_parts':[{'type':'text','text':'gap'}]})
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'after-image',
        'source_gap':False,'content_parts':[{'type':'image','media_id':'new-image'}]})
    store.accept('owner','wxauto','material',{**base,'source_event_ref':'after-text',
        'source_gap':False,'content_parts':[{'type':'text','text':'new material'}]})

    window=store.select_material_window(silence=0,now=time.time()+1)

    assert window['payload']['event_range']==[3,4]
    assert [part.get('media_id') for part in window['payload']['content_parts']
            if part['type']=='image']==['new-image']
    with store.db() as db:
        assert [row['status'] for row in db.execute(
            'SELECT status FROM events ORDER BY seq')]==[
                'unconsumed','unconsumed','selected','selected']


def test_interactive_turn_consumes_preceding_buffered_images(tmp_path):
    store=AppStore(tmp_path/'app.db')
    event={'account_ref':'a','source_stream_id':'s','conversation_ref':'c'}
    store.accept('owner','weixin','interactive',{**event,'source_event_ref':'1','content_parts':[{'type':'image','media_id':'image'}]})
    store.accept('owner','weixin','interactive',{**event,'source_event_ref':'2','content_parts':[{'type':'text','text':'批准 abc123'}]})
    store.accept('owner','weixin','interactive',{**event,'source_event_ref':'3','content_parts':[{'type':'text','text':'开始生成'}]})
    store.collect()
    first=json.loads(store.claim()['payload'])
    assert first['query']=='批准 abc123'
    assert first['content_parts'][0]['media_id']=='image'
    store.collect()
    draft=json.loads(store.claim()['payload'])
    assert len(draft['content_parts'])==1
    assert draft['query']=='开始生成'


def test_delivery_sending_state_is_polled(tmp_path):
    store=AppStore(tmp_path/'app.db')
    store.notify('delivery','owner',{'text':'test','reply_route_ref':'route'})
    store.delivery_status('delivery','sending')
    with store.db() as db:db.execute('UPDATE deliveries SET next_attempt=0')
    assert store.pending_deliveries()[0]['status']=='sending'


def test_delivery_message_id_maps_back_to_session(tmp_path):
    store=AppStore(tmp_path/'app.db')
    complete=store.record_delivery_messages('delivery','session-1',[{
        'recipient_ref':'route','fragment_index':0,'client_id':'client-1',
        'message_id':'outer','item_msg_id':'quoted-id'}],'sent')
    assert complete
    assert store.session_for_message('quoted-id')=='session-1'
    assert XHS_TOOL_SPECS['publish_content']['requires_approval'] is True


def test_ingress_dedup_worker_approval_and_restart(tmp_path):
    owner='owner-credential-at-least-24-characters'
    edge='edge-credential-at-least-24-characters'
    settings={'data_dir':str(tmp_path),'credentials':{
        owner:{'principal':'owner','source':'api'},
        edge:{'principal':'owner','source':'weixin','account_ref':'a','sender_refs':['person'],'conversation_refs':['chat']}},
        'reply_routes':{'owner':'route'}}
    store=AppStore(tmp_path/'app.db')
    def app():
        server=build(tmp_path)
        service=ApplicationService(server,store,settings)
        return create_app(server,credentials={owner:'owner',edge:'owner'},scenario_key='example.research_writer',service=service)
    # Weixin routes are learned dynamically after QR binding; the authenticated
    # Adapter account, rather than a static sender list, is the trust boundary.
    event={'account_ref':'a','sender_ref':'person-2','conversation_ref':'chat-2','source_stream_id':'s',
           'source_event_ref':'e','occurred_at':'2026-09-07T00:00:00Z','content_parts':[{'type':'text','text':'research'}]}
    with TestClient(app()) as client:
        assert client.post('/v1/sessions',headers={'Authorization':'Bearer '+edge},json={'query':'x','request_idempotency_key':'x'}).status_code==403
        response=client.post('/v1/ingress/events',headers={'Authorization':'Bearer '+edge},json=event)
        assert response.status_code==202,response.text
        again=client.post('/v1/ingress/events',headers={'Authorization':'Bearer '+edge},json=event)
        assert response.json()==again.json()
        for _ in range(100):
            with store.db() as db:row=db.execute("SELECT * FROM jobs WHERE status='suspended'").fetchone()
            if row:break
            time.sleep(.05)
        assert row is not None
        delivery=None
        for _ in range(100):
            with store.db() as db:
                delivery=db.execute('SELECT payload FROM deliveries ORDER BY rowid DESC LIMIT 1').fetchone()
            if delivery:break
            time.sleep(.05)
        assert delivery is not None
        session_id=json.loads(delivery['payload'])['session_id']
        store.record_delivery_messages('test',session_id,[{
            'client_id':'client','item_msg_id':'approval-message'}],'sent')
    with TestClient(app()) as client:
        approval_event={**event,'source_event_ref':'e2','uid':'any-broadcast-recipient',
                        'ref_msg_id':'approval-message','content_parts':[{'type':'text','text':'YES'}]}
        response=client.post('/v1/ingress/events',headers={'Authorization':'Bearer '+edge},json=approval_event)
        assert response.status_code==202,response.text
        for _ in range(100):
            with store.db() as db:
                result=db.execute("SELECT * FROM jobs WHERE id!='events_1_1' ORDER BY created DESC LIMIT 1").fetchone()
            if result and result['status'] not in ('queued','running'):break
            time.sleep(.05)
        assert result['status']=='completed',dict(result)
        for _ in range(100):
            with store.db() as db:count=db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0]
            if count>=2:break
            time.sleep(.05)
        with store.db() as db:
            assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0]==2
            assert db.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0]>=2


def test_selected_image_indices_three_states():
    assert selected_image_indices('您的小红书运营助手：标题\n正文',total=2) is None
    assert selected_image_indices('草稿\n配图：无',total=2)==[]
    assert selected_image_indices('草稿\n配图：第1张、第3张',total=5)==[1,3]
    assert selected_image_indices('旧稿\n配图：第1张\n新稿\n配图：第 2 张',total=2)==[2]
    assert selected_image_indices('配图:2,3',total=3)==[2,3]
    assert selected_image_indices('配图：第1张、第9张',total=2)==[1]
    assert selected_image_indices('配图：第9张',total=2)==[]


def test_wechat_material_prompt_render_smoke():
    rendered=WECHAT_MATERIAL_PROMPT.render({'material_text':'素材文本'})
    for fragment in ('先判断','xhs_publish_content','这一轮禁止调用',
                     '配图：第1张、第3张','配图：无','您的小红书运营助手：',
                     '素材文字：\n素材文本'):
        assert fragment in rendered


def test_session_media_positions_survive_duplicates_and_replays(tmp_path):
    store=AppStore(tmp_path/'app.db')
    store.record_session_media('session',['a','a','b'])
    assert store.session_media_ids('session')==['a','a','b']
    store.record_session_media('session',['a','a','b'])  # replayed execution keeps numbering
    assert store.session_media_ids('session')==['a','a','b']
    store.record_session_media('session',['c'])
    assert store.session_media_ids('session')==['a','a','b','c']


class _Outcome:
    def __init__(self,kind):
        self.kind=kind
    def model_dump(self,mode=None):
        return {'kind':self.kind}


class _DraftServer:
    def __init__(self,message,kind='completed'):
        self.message=message
        self.kind=kind
        self.requests=[]
        self.runtime=SimpleNamespace(persistence=SimpleNamespace(
            artifact_manager=SimpleNamespace(),
            tool_execution_store=SimpleNamespace(list_by_run=self._tools)))
    async def _tools(self,run_id):
        return ()
    async def create_session(self,request,auth=None):
        self.requests.append(request)
        return SimpleNamespace(session_id='session',run_id='run',
            outcome=_Outcome(self.kind),message=self.message)


def _media_window_store(tmp_path):
    store=AppStore(tmp_path/'app.db')
    for name in ('one','two'):
        image=tmp_path/f'{name}.png';image.write_bytes(name.encode())
        digest=hashlib.sha256(name.encode()).hexdigest()
        with store.db() as db:
            db.execute('INSERT INTO media VALUES (?,?,?,?,?)',
                (name,'owner',str(image),'image/png',digest))
    event={'account_ref':'a','source_stream_id':'s','conversation_ref':'c','source_gap':False,
           'source_event_ref':'1','content_parts':[
               {'type':'image','media_id':'one'},{'type':'text','text':'素材'},
               {'type':'image','media_id':'two'}]}
    store.accept('owner','wxauto','material',event)
    return store


async def _draft_delivery_payload(tmp_path,message,kind='completed'):
    store=_media_window_store(tmp_path)
    window=store.select_material_window(silence=0,now=time.time()+1)
    class Artifacts:
        async def put(self,**kwargs):
            return ArtifactRef(artifact_id='artifact',content_type='image/png')
    server=_DraftServer(message,kind)
    server.runtime.persistence.artifact_manager=Artifacts()
    worker=WeChatMaterialWorker(server,store,{'super_uid':'6'},stopping=SimpleNamespace())
    await worker.execute(window)
    with store.db() as db:
        rows=db.execute('SELECT payload FROM deliveries').fetchall()
    return store,[json.loads(row[0]) for row in rows]


@pytest.mark.asyncio
async def test_material_draft_broadcasts_only_selected_images(tmp_path):
    store,payloads=await _draft_delivery_payload(
        tmp_path,'您的小红书运营助手：草稿\n配图：第2张')
    assert [payload['media_ids'] for payload in payloads]==[['two']]


@pytest.mark.asyncio
async def test_insufficient_draft_returns_to_pool_silently(tmp_path):
    store,payloads=await _draft_delivery_payload(
        tmp_path,'您的小红书运营助手：素材不足：缺少可用文字。')
    assert payloads==[]
    with store.db() as db:
        assert db.execute('SELECT status FROM events').fetchone()[0]=='unconsumed'


@pytest.mark.asyncio
async def test_suspended_draft_broadcasts_text_without_images(tmp_path):
    store,payloads=await _draft_delivery_payload(
        tmp_path,'₍ᐢ..ᐢ₎ USAGI想使用xhs_publish_content工具…',kind='suspended')
    assert len(payloads)==1
    assert 'media_ids' not in payloads[0]


@pytest.mark.asyncio
async def test_interactive_revision_reply_carries_selected_images(tmp_path):
    store=AppStore(tmp_path/'app.db')
    store.record_delivery_messages('delivery','session',[{
        'client_id':'client','item_msg_id':'draft-message'}],'sent')
    store.record_session_media('session',['one','two'])
    store.enqueue('owner','reply',{
        'kind':'message','query':'换成第2张','content_parts':[],'mode':'interactive',
        'conversation_ref':'c','reply_route_ref':'route','uid':'owner',
        'message_id':'m2','ref_msg_id':'draft-message','event_range':[1,1]})
    job=store.claim()
    class Sessions:
        async def get(self,session_id):
            return SimpleNamespace(uid='owner')
    class Server:
        runtime=SimpleNamespace(session_manager=Sessions())
        async def continue_session(self,session_id,request,auth=None):
            return SimpleNamespace(session_id='session',run_id='run2',
                outcome=_Outcome('completed'),
                message='您的小红书运营助手：已更新\n配图：第2张')
    service=ApplicationService(Server(),store,{'super_uid':'6'})
    await service.execute(job)
    with store.db() as db:
        payload=json.loads(db.execute('SELECT payload FROM deliveries').fetchone()[0])
    assert payload['session_id']=='session'
    assert payload['media_ids']==['two']


@pytest.mark.asyncio
async def test_interactive_plain_reply_has_no_media_ids(tmp_path):
    store=AppStore(tmp_path/'app.db')
    store.record_delivery_messages('delivery','session',[{
        'client_id':'client','item_msg_id':'draft-message'}],'sent')
    store.record_session_media('session',['one','two'])
    store.enqueue('owner','reply',{
        'kind':'message','query':'今天天气如何','content_parts':[],'mode':'interactive',
        'conversation_ref':'c','reply_route_ref':'route','uid':'owner',
        'message_id':'m2','ref_msg_id':'draft-message','event_range':[1,1]})
    job=store.claim()
    class Sessions:
        async def get(self,session_id):
            return SimpleNamespace(uid='owner')
    class Server:
        runtime=SimpleNamespace(session_manager=Sessions())
        async def continue_session(self,session_id,request,auth=None):
            return SimpleNamespace(session_id='session',run_id='run2',
                outcome=_Outcome('completed'),
                message='您的小红书运营助手：只是一段普通回答')
    service=ApplicationService(Server(),store,{'super_uid':'6'})
    await service.execute(job)
    with store.db() as db:
        payload=json.loads(db.execute('SELECT payload FROM deliveries').fetchone()[0])
    assert 'media_ids' not in payload
