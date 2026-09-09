from pathlib import Path
import sys
import time
import json

import pytest
pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).parents[3]/'apps/httpserver'))
sys.path.insert(0,str(Path(__file__).parents[3]/'apps/wechat-edge'))
from usagi_httpserver.store import AppStore
from usagi_httpserver.service import ApplicationService
from usagi_httpserver.api import create_app
from wechat_edge.spool import Spool, overlap
from tests.tools.test_durable_approval import build


def test_observation_overlap_and_gap(tmp_path):
    assert overlap(['a','b'],['b','c'])==1
    assert overlap(['a','b'],['x','c']) is None
    assert overlap(['a'],['a','a']) is None
    assert overlap([],['first message'])==0
    spool=Spool(tmp_path/'edge.db')
    config={'account_ref':'desktop'};conv={'ref':'chat','sender_ref':'contact'}
    def msg(signature):return {'signature':signature,'sender_ref':'contact','direction':'incoming','text':signature}
    spool.capture(config,conv,[msg('a'),msg('b')]);assert spool.pending()==[]
    spool.capture(config,conv,[msg('b'),msg('c')]);assert len(spool.pending())==1
    spool.capture(config,conv,[msg('x')]);assert json.loads(spool.pending()[-1][1])['source_gap']


def test_material_gap_blocks_snapshot(tmp_path):
    store=AppStore(tmp_path/'app.db')
    event={'account_ref':'a','source_stream_id':'s','source_event_ref':'e','conversation_ref':'c',
           'source_gap':True,'content_parts':[{'type':'text','text':'gap'}]}
    store.accept('owner','wxauto','material',event);store.collect(silence=0,maximum=0)
    assert store.claim() is None


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
    headers={'Authorization':'Bearer '+owner}
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
        run_id=row['run_id']
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
