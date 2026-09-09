import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {Store} from '../src/store.mjs';

test('cursor and inbox persist together; redelivery is deduplicated',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-weixin-test-'));
  try {
    let store=new Store(path.join(root,'state.db'));
    const response={get_updates_buf:'cursor-1',msgs:[{message_id:12,message_type:1,text:'hello'}]};
    store.batch('account',response);store.batch('account',response);
    assert.equal(store.pending().length,1);store.db.close();
    store=new Store(path.join(root,'state.db'));
    assert.equal(store.get('cursor:account'),'cursor-1');assert.equal(store.pending().length,1);
    store.ack(store.pending()[0].id);assert.equal(store.pending().length,0);store.db.close();
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('multiple QR-bound users are persisted independently',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-weixin-test-'));
  try {
    const store=new Store(path.join(root,'state.db'));
    store.bindUser('first@im.wechat');
    store.bindUser('second@im.wechat');
    store.bindUser('first@im.wechat');
    assert.deepEqual(store.boundUsers().map(item=>item.uid),[
      'first@im.wechat','second@im.wechat',
    ]);
    store.db.close();
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('uncertain sends are not repeated after restart',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-weixin-test-'));
  try {
    let store=new Store(path.join(root,'state.db'));
    const payload={text:'approval'};
    store.outgoing('id',payload);store.status('id','sending');store.db.close();
    store=new Store(path.join(root,'state.db'));
    assert.equal(store.outgoing('id',payload),'sending');
    store.recover();
    assert.equal(store.outgoing('id',payload),'unknown');assert.equal(store.queued().length,0);
    assert.throws(()=>store.outgoing('id',{text:'changed'}));store.db.close();
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});

test('delivery preserves client ids and learns quoted message ids from echo',()=>{
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-weixin-test-'));
  try {
    const store=new Store(path.join(root,'state.db'));
    store.outgoing('delivery',{text:'hello'});
    store.sent('delivery',{recipient_ref:'route',fragment_index:0,client_id:'client-1'});
    assert.deepEqual(store.delivery('delivery').client_ids,['client-1']);
    assert.deepEqual(store.delivery('delivery').message_ids,[]);
    store.confirm('client-1',{message_id:12,item_msg_id:'quoted-12'});
    const delivery=store.delivery('delivery');
    assert.deepEqual(delivery.message_ids,['quoted-12','12']);
    assert.equal(delivery.messages[0].recipient_ref,'route');
    store.db.close();
  } finally {fs.rmSync(root,{recursive:true,force:true});}
});
