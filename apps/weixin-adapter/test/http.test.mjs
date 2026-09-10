import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {Store} from '../src/store.mjs';

test('standalone HTTP authenticates and records failed delivery without a login',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-adapter-test-'));
  const listener=net.createServer();
  listener.listen(0,'127.0.0.1');await once(listener,'listening');
  const port=listener.address().port;
  await new Promise(resolve=>listener.close(resolve));
  const config=path.join(dir,'config.json');
  fs.writeFileSync(config,JSON.stringify({data_dir:dir,port,host:'127.0.0.1',server_url:'http://127.0.0.1:1'}));
  const token='standalone-test-token-at-least-24-characters';
  const child=spawn(process.execPath,[fileURLToPath(new URL('../dist/main.js',import.meta.url))],{
    env:{...process.env,USAGI_WEIXIN_CONFIG:config,USAGI_ADAPTER_TOKEN:token,USAGI_WEIXIN_TOKEN:token},stdio:'ignore'});
  const exited=once(child,'exit');
  const base=`http://127.0.0.1:${port}`;
  try {
    let health;
    for(let i=0;i<100;i++) {
      try {health=await fetch(base+'/health/live');break;}catch {await new Promise(r=>setTimeout(r,50));}
    }
    assert.ok(health,'adapter did not start');
    assert.equal((await health.json()).logged_in,false);
    assert.equal((await fetch(base+'/internal/accounts')).status,401);
    const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
    const body={delivery_id:'test-delivery',reply_route_ref:'missing-route',text:'local test only'};
    const response=await fetch(base+'/internal/messages',{method:'POST',headers,body:JSON.stringify(body)});
    assert.equal(response.status,200);
    const status=await response.json();
    assert.equal(status.status,'failed');
    assert.equal((await fetch(base+'/internal/messages',{method:'POST',headers,body:JSON.stringify({...body,text:'changed'})})).status,409);
  } finally {
    child.kill();await exited;
    assert.ok(dir.startsWith(path.join(os.tmpdir(),'usagi-adapter-test-')));
    fs.rmSync(dir,{recursive:true,force:true});
  }
});

test('broadcast continues after one target fails',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-adapter-broadcast-'));
  const upstream=http.createServer();
  const attempted=[];
  upstream.on('request',async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=chunks.length?JSON.parse(Buffer.concat(chunks).toString()):{};
    if(req.url?.endsWith('/sendmessage')) {
      const target=body.msg?.to_user_id;attempted.push(target);
      const result=target==='bad'?{ret:-2,errmsg:'rejected'}:{ret:0,message_id:'confirmed'};
      res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(result));return;
    }
    res.writeHead(200,{'Content-Type':'application/json'});
    res.end(JSON.stringify({ret:0,msgs:[],get_updates_buf:''}));
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  const upstreamBase=`http://127.0.0.1:${upstream.address().port}`;
  const listener=net.createServer();listener.listen(0,'127.0.0.1');await once(listener,'listening');
  const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
  const dbPath=path.join(dir,'adapter-debug.db');
  const seed=new Store(dbPath);
  seed.set('accounts',{account:{token:'token',baseUrl:upstreamBase,botId:'account'}});
  seed.set('route:bad-route',{accountId:'account',to:'bad',contextToken:'context'});
  seed.set('route:good-route',{accountId:'account',to:'good',contextToken:'context'});
  seed.db.close();
  const config=path.join(dir,'config.json');
  fs.writeFileSync(config,JSON.stringify({data_dir:dir,database_environment:'debug',port,
    host:'127.0.0.1',server_url:upstreamBase}));
  const token='standalone-test-token-at-least-24-characters';
  const child=spawn(process.execPath,[fileURLToPath(new URL('../dist/main.js',import.meta.url))],{
    env:{...process.env,USAGI_WEIXIN_CONFIG:config,USAGI_ADAPTER_TOKEN:token,
      USAGI_WEIXIN_TOKEN:token},stdio:'ignore'});
  const exited=once(child,'exit');
  try {
    const base=`http://127.0.0.1:${port}`;let health;
    for(let i=0;i<100;i++) {
      try {health=await fetch(base+'/health/live');break;}catch {await new Promise(r=>setTimeout(r,50));}
    }
    assert.ok(health,'adapter did not start');
    const response=await fetch(base+'/internal/broadcasts',{method:'POST',headers:{
      Authorization:'Bearer '+token,'Content-Type':'application/json'},
      body:JSON.stringify({delivery_id:'broadcast',text:'test'})});
    const result=await response.json();
    assert.equal(result.status,'partial');
    assert.deepEqual(new Set(attempted),new Set(['bad','good']));
    assert.equal(result.messages.filter(item=>item.message_id).length,1);
  } finally {
    child.kill();await exited;
    await new Promise(resolve=>upstream.close(resolve));
    fs.rmSync(dir,{recursive:true,force:true});
  }
});

test('broadcast delivers media attachments after the text',async()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'usagi-adapter-media-'));
  const png=Buffer.concat([Buffer.from([0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a]),Buffer.from('media-body')]);
  const upstreamRequests=[];
  const upstream=http.createServer(async(req,res)=>{
    const chunks=[];for await(const chunk of req)chunks.push(chunk);
    const body=chunks.length?Buffer.concat(chunks):Buffer.alloc(0);
    if(req.method==='GET' && req.url?.startsWith('/v1/media/')) {
      upstreamRequests.push('media:'+decodeURIComponent(req.url.slice('/v1/media/'.length)));
      res.writeHead(200,{'Content-Type':'image/png'});res.end(png);return;
    }
    if(req.url?.endsWith('/getuploadurl')) {
      upstreamRequests.push('getuploadurl');
      res.writeHead(200,{'Content-Type':'application/json'});
      res.end(JSON.stringify({ret:0,upload_full_url:`http://127.0.0.1:${upstream.address().port}/cdn-upload`}));return;
    }
    if(req.url?.endsWith('/cdn-upload')) {
      upstreamRequests.push('cdn-upload');
      res.writeHead(200,{'x-encrypted-param':'download-param'});res.end();return;
    }
    if(req.url?.endsWith('/sendmessage')) {
      const parsed=JSON.parse(body.toString());
      upstreamRequests.push('sendmessage:'+parsed.msg.item_list.map(item=>item.type).join(','));
      res.writeHead(200,{'Content-Type':'application/json'});
      res.end(JSON.stringify({ret:0,message_id:'confirmed'}));return;
    }
    res.writeHead(200,{'Content-Type':'application/json'});
    res.end(JSON.stringify({ret:0,msgs:[],get_updates_buf:''}));
  });
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  const upstreamBase=`http://127.0.0.1:${upstream.address().port}`;
  const listener=net.createServer();listener.listen(0,'127.0.0.1');await once(listener,'listening');
  const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));
  const dbPath=path.join(dir,'adapter-debug.db');
  const seed=new Store(dbPath);
  seed.set('accounts',{account:{token:'token',baseUrl:upstreamBase,botId:'account'}});
  seed.set('route:good-route',{accountId:'account',to:'good',contextToken:'context'});
  seed.db.close();
  const config=path.join(dir,'config.json');
  fs.writeFileSync(config,JSON.stringify({data_dir:dir,database_environment:'debug',port,
    host:'127.0.0.1',server_url:upstreamBase}));
  const token='standalone-test-token-at-least-24-characters';
  const child=spawn(process.execPath,[fileURLToPath(new URL('../dist/main.js',import.meta.url))],{
    env:{...process.env,USAGI_WEIXIN_CONFIG:config,USAGI_ADAPTER_TOKEN:token,
      USAGI_WEIXIN_TOKEN:token},stdio:'ignore'});
  const exited=once(child,'exit');
  try {
    const base=`http://127.0.0.1:${port}`;let health;
    for(let i=0;i<100;i++) {
      try {health=await fetch(base+'/health/live');break;}catch {await new Promise(r=>setTimeout(r,50));}
    }
    assert.ok(health,'adapter did not start');
    const response=await fetch(base+'/internal/broadcasts',{method:'POST',headers:{
      Authorization:'Bearer '+token,'Content-Type':'application/json'},
      body:JSON.stringify({delivery_id:'media-broadcast',text:'draft text',
        media_ids:['media-1','media-2']})});
    const result=await response.json();
    assert.equal(result.status,'sent');
    assert.equal(result.messages.filter(item=>item.message_id).length,3); // text + two images
    assert.deepEqual(upstreamRequests.filter(item=>item.startsWith('media:')),
      ['media:media-1','media:media-2']); // each image downloaded once despite reuse
    assert.equal(upstreamRequests.filter(item=>item==='getuploadurl').length,2);
    assert.equal(upstreamRequests.filter(item=>item==='cdn-upload').length,2);
    assert.equal(upstreamRequests.filter(item=>item.startsWith('sendmessage:')).length,3);
  } finally {
    child.kill();await exited;
    await new Promise(resolve=>upstream.close(resolve));
    fs.rmSync(dir,{recursive:true,force:true});
  }
});
