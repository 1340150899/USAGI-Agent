"""Unit tests for wechat_edge.events (classification + payload contract).

The module is stdlib-only; these tests must run without wechatauto-replica
installed, mirroring the sys.path injection in test_ingress_worker.py.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / 'apps/wechat-edge'))
from wechat_edge.events import build_payload, classify, event_ref

CONFIG = {'account_ref': 'desktop-main', 'self_ref': 'owner-desktop'}


def conversation(**overrides):
    base = {'username': 'wxid_peer', 'name': 'Peer', 'ref': 'conversation-01',
            'sender_ref': 'contact-01', 'self_ref': 'owner-desktop'}
    base.update(overrides)
    return base


def message(**overrides):
    base = {'local_id': 7, 'type': '文本', 'sender_id': 999,
            'sender_username': 'wxid_peer', 'create_time': 1700000000,
            'content': 'hi', 'sort_seq': 42, 'username': 'wxid_peer'}
    base.update(overrides)
    return base


def test_one_on_one_incoming():
    item = classify(message(), conversation(), 'wxid_self')
    assert item == {'kind': 'text', 'text': 'hi', 'sender_wxid': None,
                    'sender_ref': 'contact-01', 'direction': 'incoming'}


def test_self_message_direction_uses_sender_id_not_username():
    # self rows have real_sender_id==2 and an empty sender_username
    item = classify(message(sender_id=2, sender_username=''), conversation(), 'wxid_self')
    assert item['direction'] == 'outgoing'
    assert item['sender_ref'] == 'owner-desktop'
    # sender_id matching the account wxid also counts as self
    item = classify(message(sender_id=555, sender_username=''), conversation(), '555')
    assert item['direction'] == 'outgoing'


def test_group_member_prefix_is_stripped_and_mapped():
    conv = conversation(username='12345@chatroom',
                        sender_map={'wxid_member': 'contact-02'})
    item = classify(message(username='12345@chatroom',
                            content='wxid_member:\n你好'), conv, 'wxid_self')
    assert item['text'] == '你好'
    assert item['sender_wxid'] == 'wxid_member'
    assert item['sender_ref'] == 'contact-02'
    assert item['direction'] == 'incoming'


def test_group_self_message_uses_chatroom_prefix():
    conv = conversation(username='12345@chatroom')
    item = classify(message(username='12345@chatroom',
                            content='12345@chatroom:\n我发的'), conv, 'wxid_self')
    assert item['direction'] == 'outgoing'
    assert item['sender_ref'] == 'owner-desktop'
    assert item['text'] == '我发的'


def test_group_unmapped_member_falls_back_to_conversation_sender_ref():
    conv = conversation(username='12345@chatroom')
    item = classify(message(username='12345@chatroom',
                            content='wxid_stranger:\n呀'), conv, 'wxid_self')
    assert item['sender_ref'] == 'contact-01'


def test_unknown_and_system_types_are_skipped():
    conv = conversation()
    assert classify(message(type=10000, content='撤回了一条消息'), conv, 'wxid_self') is None
    assert classify(message(type=57), conv, 'wxid_self') is None
    assert classify(message(type='系统消息'), conv, 'wxid_self') is None


def test_group_image_prefix_is_stripped():
    conv = conversation(username='12345@chatroom')
    item = classify(message(type='图片', username='12345@chatroom',
                            content='wxid_member:\n[图片 md5=abc]'), conv, 'wxid_self')
    assert item['kind'] == 'image'
    assert item['text'] == '[图片 md5=abc]'


def test_payload_contract():
    item = classify(message(type='图片', content=''), conversation(), 'wxid_self')
    payload = build_payload(config=CONFIG, conversation=conversation(),
                            msg=message(type='图片', content=''), item=item,
                            image_path='images/abc.jpg')
    assert payload['schema_version'] == 1
    assert payload['source_stream_id'] == 'desktop-main:conversation-01'
    assert payload['source_event_ref'] == payload['source_cursor_ref'] == 'wxid_peer:42'
    assert payload['ordering_mode'] == 'observation_ordered'
    assert payload['sender_ref'] == 'contact-01'
    assert payload['direction'] == 'incoming'
    assert payload['occurred_at'] == datetime.fromtimestamp(
        1700000000, tz=timezone.utc).isoformat()
    assert payload['source_gap'] is False
    assert payload['content_parts'] == [{'type': 'text', 'text': ''}]
    assert payload['_image_path'] == 'images/abc.jpg'
    assert event_ref('wxid_peer', 42) == 'wxid_peer:42'


def test_failed_image_degrades_to_placeholder():
    item = classify(message(type='图片', content='[图片 md5=abc]'),
                    conversation(), 'wxid_self')
    payload = build_payload(config=CONFIG, conversation=conversation(),
                            msg=message(type='图片', content='[图片 md5=abc]'),
                            item=item, image_path=None)
    assert payload['content_parts'] == [{'type': 'text', 'text': '[图片采集失败]'}]
    assert payload['_image_path'] is None
