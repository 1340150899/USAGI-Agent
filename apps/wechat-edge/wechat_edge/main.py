"""Run in the logged-in Windows desktop session, not Service Session 0.

Collection reads the WeChat 4.x local databases directly (wechatauto-replica
WeChatDB + Listener, no UI); the client UI is only driven when an image's
original must be fetched (EdgeMediaDownloader tier 3). wechatauto imports
stay inside the live entry points so spool/events stay importable without
the dependency installed.
"""
import argparse
import json
import logging
import mimetypes
import os
import threading
from datetime import datetime
from pathlib import Path

import httpx

from .events import build_payload, classify, event_ref
from .spool import Spool

log = logging.getLogger("wechat_edge")

REQUIRED_KEYS = ('server_url', 'token_env', 'account_ref', 'self_ref', 'conversations')
REQUIRED_CONVERSATION_KEYS = ('name', 'ref', 'sender_ref')
DEPRECATED_KEYS = ('wxauto_module', 'poll_seconds', 'self_name')


class _LevelFileHandler(logging.Handler):
    def __init__(self, directory, level):
        super().__init__(level)
        self.directory, self.exact_level = Path(directory), level

    def emit(self, record):
        if record.levelno != self.exact_level:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            day = datetime.fromtimestamp(record.created).astimezone().date().isoformat()
            with (self.directory / f"{day}.{record.levelname.lower()}.log").open(
                'a', encoding='utf-8'
            ) as output:
                output.write(self.format(record) + '\n')
        except Exception:
            self.handleError(record)


def _log_root():
    configured = os.environ.get('USAGI_LOG_DIR')
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (candidate / 'apps').is_dir() and (candidate / 'usagi-agent').is_dir():
            return candidate / 'log'
    return Path.cwd() / 'log'


def configure_logging(log_root=None):
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s"
    )
    log.handlers.clear()
    root = Path(log_root).expanduser() if log_root else _log_root()
    if not root.is_absolute():
        root = (_log_root().parent / root).resolve()
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        handler = _LevelFileHandler(root / 'wechat-edge', level)
        handler.setFormatter(formatter)
        log.addHandler(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    log.addHandler(console)
    log.setLevel(logging.DEBUG)
    log.propagate = False


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        raise SystemExit('config missing required fields: ' + ', '.join(missing))
    if not config['conversations']:
        raise SystemExit('explicit conversation allowlist required')
    for conversation in config['conversations']:
        missing = [key for key in REQUIRED_CONVERSATION_KEYS
                   if not conversation.get(key)]
        if missing:
            raise SystemExit('conversation %r missing fields: %s' % (
                conversation.get('name'), ', '.join(missing)))
    for key in DEPRECATED_KEYS:
        if key in config:
            log.warning('config key %r is deprecated and ignored', key)
    if os.name != 'nt':
        raise SystemExit('live collection requires Windows with WeChat 4.x logged in')
    return config


def _is_critical_db(rel):
    name = os.path.basename(rel)
    return name.startswith('message') or name == 'contact.db' or 'session' in rel


def init_db(config, stop):
    """Construct WeChatDB, retrying until WeChat is logged in.

    A construction that leaves message/contact/session databases unkeyed
    counts as failure — WeChatDB only warns on stderr in that case
    (wechatauto db.py sets ``db.unkeyed``)."""
    from wechatauto.db import WeChatDB
    log.info('initialization_stage_started stage=wechat_database')
    while not stop.is_set():
        try:
            db = WeChatDB(account=config.get('account') or None,
                          db_dir=config.get('db_dir'))
            critical = [rel for rel in getattr(db, 'unkeyed', ())
                        if _is_critical_db(rel)]
            if critical:
                raise RuntimeError('unkeyed databases: ' + ', '.join(critical))
            log.info('initialization_stage_completed stage=wechat_database critical_databases_keyed=true')
            return db
        except Exception as exc:
            config['_status'] = 'initialization_failed:' + type(exc).__name__
            log.warning('WeChatDB unavailable (%s); retrying in 10s',
                        type(exc).__name__)
            stop.wait(10)
    return None


def resolve_conversations(db, config):
    resolved = []
    for conversation in config['conversations']:
        record = dict(conversation)
        username = record.get('username') or db.username_by_nickname(conversation['name'])
        if not username:
            candidates = db.search_contact(conversation['name'])
            log.error('cannot resolve conversation %r; search candidates: %s',
                      conversation['name'],
                      [candidate['username'] for candidate in candidates[:5]])
            raise SystemExit(2)
        record['username'] = username
        record['self_ref'] = config['self_ref']
        log.info('conversation %r -> %s (pin it in config as "username" to skip '
                 'nickname lookup)', conversation['name'], username)
        resolved.append(record)
    return resolved


def make_callback(db, media, spool, config, conversation):
    def on_message(msg, listener):
        item = classify(msg, conversation, db.wxid)
        if item is None:
            return
        log.info('message_captured event_ref=%s kind=%s',
                 event_ref(msg['username'], msg['sort_seq']), item['kind'])
        image_path = None
        if item['kind'] == 'image':
            path, tier = media.download(msg['username'], msg['local_id'],
                                        conversation['name'])
            log.info('image %s tier=%s', event_ref(msg['username'], msg['sort_seq']), tier)
            if path is None:
                config['_failed_images'] = config.get('_failed_images', 0) + 1
            else:
                image_path = str(path)
        payload = build_payload(config=config, conversation=conversation,
                                msg=msg, item=item, image_path=image_path)
        spool.enqueue(payload['source_event_ref'],
                      json.dumps(payload, ensure_ascii=False))
        log.info("抓到了消息")
    return on_message


def upload_loop(spool, config, stop):
    # trust_env=False: never route the local httpserver through a system
    # proxy (Clash et al. answer loopback requests with 502).
    with httpx.Client(base_url=config['server_url'], trust_env=False, headers={
            'Authorization': 'Bearer ' + os.environ[config.get('token_env', 'USAGI_WXAUTO_TOKEN')]}, timeout=30) as client:
        while not stop.is_set():
            for event_id, raw in spool.pending():
                body = json.loads(raw)
                image = body.pop('_image_path', None)
                try:
                    if image:
                        file = Path(image)
                        result = client.post('/v1/ingress/media', content=file.read_bytes(),
                            headers={'Content-Type': mimetypes.guess_type(file.name)[0] or 'application/octet-stream'})
                        result.raise_for_status()
                        body['content_parts'].append({'type': 'image', 'media_id': result.json()['media_id']})
                    response = client.post('/v1/ingress/events', json=body); response.raise_for_status()
                    log.info('message_sent event_ref=%s status=%s', event_id, response.status_code)
                    spool.ack(event_id)
                    log.info("发送了消息")
                except (httpx.HTTPError, OSError) as exc:
                    log.exception('message_send_failed event_ref=%s error_type=%s',
                                  event_id, type(exc).__name__)
                    break
            try:
                client.post('/v1/adapters/heartbeat', json={
                    'pending': spool.size(),
                    'failed_images': config.get('_failed_images', 0),
                    'collector': config.get('_status', 'starting')}).raise_for_status()
            except httpx.HTTPError:
                pass
            stop.wait(2)


def watchdog(db, config, stop):
    """The Listener swallows poll errors, so probe the database directly;
    a WeChat logout otherwise looks like a healthy silent process."""
    failures = 0
    while not stop.wait(30):
        try:
            db.get_sessions(limit=1)
            if failures:
                log.info('collector recovered after %d probe failures', failures)
            failures = 0
            config['_status'] = 'observing'
        except Exception as exc:
            failures += 1
            config['_status'] = 'unavailable:' + type(exc).__name__
            log.warning('probe failed (%d/10): %s', failures, type(exc).__name__)
            if failures >= 10:
                log.error('10 consecutive probe failures; exiting so the '
                          'scheduled task restarts us')
                os._exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    configure_logging()
    log.info('initialization_stage_started stage=config')
    config = load_config(args.config)
    if config.get('log_dir'):
        configure_logging(config['log_dir'])
    log.info('initialization_stage_completed stage=config')
    root = Path(config.get('data_dir', '.usagi/edge')).resolve(); root.mkdir(parents=True, exist_ok=True)
    log.info('initialization_stage_completed stage=data_directory path=%s', root)
    spool = Spool()
    log.info('initialization_stage_completed stage=spool')
    stop = threading.Event()
    listener = None
    worker = threading.Thread(target=upload_loop, args=(spool, config, stop), daemon=True); worker.start()
    log.info('initialization_stage_completed stage=upload_worker')
    try:
        db = init_db(config, stop)
        if db is None:
            return
        conversations = resolve_conversations(db, config)
        log.info('initialization_stage_completed stage=conversations count=%d', len(conversations))
        from .images import EdgeMediaDownloader
        from wechatauto.db import Listener
        media = EdgeMediaDownloader(db, root / 'images')
        config['_status'] = 'detecting_image_key'
        log.info('initialization_stage_started stage=image_aes_key_detection')
        media.refresh_keys()
        log.info('initialization_stage_completed stage=image_aes_key_detection key_found=%s', media.has_keys)
        listener = Listener(db, interval=float(config.get('interval_seconds', 1.0)))
        log.info('initialization_stage_completed stage=listener_created')
        for conversation in conversations:
            listener.add_listener(conversation['username'],
                                  make_callback(db, media, spool, config, conversation))
        listener.start()
        log.info('initialization_stage_completed stage=listener_started')
        config['_status'] = 'observing'
        log.info('listening on %d conversation(s)', len(conversations))
        probe = threading.Thread(target=watchdog, args=(db, config, stop), daemon=True); probe.start()
        while not stop.is_set():
            stop.wait(5)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log.exception('wechat_edge_fatal error_type=%s', type(exc).__name__)
        raise
    finally:
        log.info('wechat_edge_shutdown_started')
        stop.set()
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        worker.join(timeout=35)
        log.info('wechat_edge_shutdown_complete')


if __name__ == '__main__':
    main()
