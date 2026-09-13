import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import {timingSafeEqual} from 'node:crypto';
import {Store,digest} from './store.mjs';
import {getUpdates} from './weixin/api/api.js';
import {configureAccounts} from './weixin/auth/accounts.js';
import {startWeixinLoginWithQr,waitForWeixinLogin,displayQRCode} from './weixin/auth/login-qr.js';
import {downloadMediaFromItem} from './weixin/media/media-download.js';
import {generateClientId,sendMessageWeixin} from './weixin/messaging/send.js';
import {sendWeixinMediaFile} from './weixin/messaging/send-media.js';
import {configureLogger,logger} from './weixin/util/logger.js';

const config=JSON.parse(fs.readFileSync(process.env.USAGI_WEIXIN_CONFIG ?? 'config.json','utf8'));
configureLogger(config.log_dir);
logger.info('adapter_initialization_started');
process.on('uncaughtException',error=>{
  logger.error(`adapter_fatal type=uncaughtException error=${JSON.stringify(safeDeliveryError(error))}`);
  process.exit(1);
});
process.on('unhandledRejection',error=>{
  logger.error(`adapter_fatal type=unhandledRejection error=${JSON.stringify(safeDeliveryError(error))}`);
  process.exit(1);
});
config.apiToken=process.env[config.api_token_env ?? 'USAGI_ADAPTER_TOKEN'];
config.serverToken=process.env[config.server_token_env ?? 'USAGI_WEIXIN_TOKEN'];
if(!config.apiToken || config.apiToken.length<24 || !config.serverToken)throw new Error('configure adapter/server credentials');
const root=path.resolve(config.data_dir ?? '.usagi-weixin');
fs.mkdirSync(root,{recursive:true,mode:0o700});
// Hold one owner for login and polling alike. A second process must not reset
// another process's in-flight outgoing rows during startup recovery.
const lockPath=path.join(root,'adapter.lock');
try {
  const pid=Number(fs.readFileSync(lockPath,'utf8'));
  if(!Number.isSafeInteger(pid) || pid<=0)throw new Error('invalid lock; inspect data directory');
  try {process.kill(pid,0);throw new Error('adapter is already running; stop it before login');}
  catch(error:any) {if(error.code!=='ESRCH')throw error;fs.unlinkSync(lockPath);}
} catch(error:any) {if(error.code!=='ENOENT')throw error;}
const lock=fs.openSync(lockPath,'wx',0o600);
fs.writeFileSync(lock,String(process.pid));
process.on('exit',()=>{fs.closeSync(lock);try{fs.unlinkSync(lockPath);}catch{}});
const stores={dev:new Store(path.join(root,'adapter-dev.db')),debug:new Store(path.join(root,'adapter-debug.db'))};
const databaseEnvironment=config.database_environment??'dev';
if(databaseEnvironment!=='dev' && databaseEnvironment!=='debug')throw new Error('database_environment must be dev or debug');
const store=stores[databaseEnvironment as 'dev'|'debug'];
store.recover();
logger.info(`adapter_store_initialized environment=${databaseEnvironment}`);
const accountRef=config.account_ref ?? 'weixin-main';
const baseUrl=config.base_url ?? 'https://ilinkai.weixin.qq.com';
const cdnBaseUrl=config.cdn_base_url ?? 'https://novac2c.cdn.weixin.qq.com/c2c';
let accounts=store.get('accounts',{});
const legacyAccount=store.get('account');
if(legacyAccount && !Object.keys(accounts).length) {
  accounts={[legacyAccount.botId??accountRef]:legacyAccount};
  store.set('accounts',accounts);
}
configureAccounts(accounts);
logger.info(`adapter_accounts_initialized success=true connections=${Object.keys(accounts).length}`);
const sleep=(ms:number)=>new Promise(resolve=>setTimeout(resolve,ms));

function safeDeliveryError(error:unknown) {
  const value=error as {name?:unknown;code?:unknown;cause?:{code?:unknown}};
  const message=error instanceof Error?error.message:String(error??'');
  const httpStatus=message.match(/\b(?:sendMessage\s+)?([45]\d\d)\b/)?.[1];
  const upstreamRet=message.match(/\bret=(-?\d+)\b/)?.[1];
  return {
    name:typeof value?.name==='string'?value.name:'UnknownError',
    ...(typeof value?.code==='string'?{code:value.code}:{}),
    ...(typeof value?.cause?.code==='string'?{cause_code:value.cause.code}:{}),
    ...(httpStatus?{http_status:Number(httpStatus)}:{}),
    ...(upstreamRet?{upstream_ret:Number(upstreamRet)}:{}),
    timeout:/timeout|aborted/i.test(message),
  };
}

const MAX_MEDIA_PER_DELIVERY = 9;

function normalizeMediaIds(payload:any):string[] {
  const ids=(Array.isArray(payload.media_ids)?payload.media_ids:[]).filter(
    (id:unknown):id is string=>typeof id==='string' && id.length>0);
  if(typeof payload.media_id==='string' && !ids.includes(payload.media_id))ids.push(payload.media_id);
  return ids.slice(0,MAX_MEDIA_PER_DELIVERY);
}

function validMediaIds(payload:any):boolean {
  return payload.media_ids===undefined ||
    (Array.isArray(payload.media_ids) && payload.media_ids.every((id:unknown)=>typeof id==='string'));
}

if(process.argv.includes('login')) {
  const start=await startWeixinLoginWithQr({apiBaseUrl:baseUrl});
  if(!start.qrcodeUrl)throw new Error(start.message);
  await displayQRCode(start.qrcodeUrl);
  const result=await waitForWeixinLogin({apiBaseUrl:baseUrl,sessionKey:start.sessionKey,timeoutMs:480000});
  if(result.connected && result.botToken) {
    const connection={token:result.botToken,baseUrl:result.baseUrl??baseUrl,userId:result.userId,botId:result.accountId};
    accounts={...accounts,[result.accountId!]:connection};
    store.set('accounts',accounts);
    store.bindUser(result.userId);
    // Opaque references for explicit application binding; never print bot credentials.
    console.log(JSON.stringify({account_ref:accountRef,sender_ref:digest([accountRef,result.userId]),
      conversation_ref:digest([accountRef,result.userId]),reply_route_ref:digest([accountRef,result.userId])}));
  } else if(!result.alreadyConnected)throw new Error(result.message);
  process.exit(0);
}

let alive=true;
const abort=new AbortController();
const headers={Authorization:'Bearer '+config.serverToken};
async function api(endpoint:string,options:any={}) {
  const response=await fetch(config.server_url.replace(/\/$/,'')+endpoint,
    {...options,headers:{...headers,...options.headers},signal:AbortSignal.timeout(30000)});
  if(!response.ok)throw new Error('application HTTP '+response.status);
  return response;
}
async function poll() {
  const pollAccount=async(accountId:string,account:any)=>{
   while(alive) {
    try {
      const response=await getUpdates({baseUrl:account.baseUrl,token:account.token,
        get_updates_buf:store.get('cursor:'+accountId,''),abortSignal:abort.signal});
      if(response.ret || response.errcode)throw new Error('login or upstream unavailable');
      for(const msg of response.msgs??[]) {
        if(msg.message_type===2 && msg.client_id) {
          const itemId=msg.item_list?.map(item=>item.msg_id).find(Boolean);
          store.confirm(msg.client_id,{message_id:msg.message_id,item_msg_id:itemId});
        }
      }
      store.batch(accountId,response);
      store.set('last_poll',Date.now());
    } catch(error) {
      store.set('last_poll_error',Date.now());
      logger.error(`message_poll_failed account_ref=${accountId} error=${JSON.stringify(safeDeliveryError(error))}`);
      await sleep(3000);
    }
   }
  };
  const entries=Object.entries(accounts);
  logger.info(`adapter_poll_initialized connections=${entries.length}`);
  if(!entries.length) {
    while(alive)await sleep(1000);
    return;
  }
  await Promise.all(entries.map(([accountId,account])=>pollAccount(accountId,account)));
}
function imageMime(buffer:Buffer) {
  if(buffer.subarray(0,3).equals(Buffer.from([255,216,255])))return 'image/jpeg';
  if(buffer.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10])))return 'image/png';
  if(buffer.subarray(0,4).toString()==='RIFF' && buffer.subarray(8,12).toString()==='WEBP')return 'image/webp';
  if(buffer.subarray(0,3).toString()==='GIF')return 'image/gif';
  throw new Error('unsupported image');
}
async function forward() {
  while(alive) {
    for(const row of store.pending()) {
      try {
        const {account:connectionId,msg}=JSON.parse(row.payload);
        logger.info(`message_received event_id=${row.id} item_count=${msg.item_list?.length??0}`);
        const allowed=config.allowed_peers;
        if(msg.message_type!==1 || (Array.isArray(allowed) && !allowed.includes(msg.from_user_id))){store.ack(row.id);continue;}
        store.bindUser(msg.from_user_id);
        const route=digest([accountRef,msg.from_user_id]);
        store.set('route:'+route,{to:msg.from_user_id,accountId:connectionId,
          contextToken:msg.context_token ?? store.get('route:'+route)?.contextToken});
        const parts:any[]=[];
        for(const item of msg.item_list??[]) {
          if(item.type===1 && item.text_item?.text)parts.push({type:'text',text:item.text_item.text});
          else if(item.type===3 && item.voice_item?.text)parts.push({type:'text',text:item.voice_item.text});
          else if(item.type===2) {
            let data:Buffer|undefined;
            await downloadMediaFromItem(item,{cdnBaseUrl,label:'inbound',log:()=>{},errLog:()=>{},
              saveMedia:async(buffer:Buffer)=>{if(buffer.length>20*1024*1024)throw new Error('image too large');data=buffer;return {path:'in-memory'};}});
            if(!data)throw new Error('image not ready');
            const response=await api('/v1/ingress/media',{method:'POST',headers:{'Content-Type':imageMime(data)},body:data});
            parts.push({type:'image',media_id:(await response.json()).media_id});
          } else parts.push({type:'text',text:'[当前应用仅支持文字与图片；此附件未作为创作素材导入。]'});
        }
        const refMsgId=(msg.item_list??[]).map((item:any)=>item.ref_msg?.message_item?.msg_id).find(Boolean);
        const event={schema_version:1,source_stream_id:accountRef,source_event_ref:row.id,
          source_cursor_ref:row.id,ordering_mode:'observation_ordered',account_ref:accountRef,
          conversation_ref:route,sender_ref:route,reply_route_ref:route,direction:'incoming',
          uid:msg.from_user_id,message_id:msg.message_id===undefined?undefined:String(msg.message_id),
          ref_msg_id:refMsgId===undefined?undefined:String(refMsgId),
          occurred_at:new Date(msg.create_time_ms??0).toISOString(),content_parts:parts};
        await api('/v1/ingress/events',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(event)});
        store.ack(row.id);
        logger.info(`message_forwarded event_id=${row.id} success=true`);
      } catch(error) {
        logger.error(`message_forward_failed event_id=${row.id} error=${JSON.stringify(safeDeliveryError(error))}`);
        break;
      }
    }
    await sleep(500);
  }
}
async function sendDelivery(deliveryId:string,payload:any) {
  logger.info(`delivery_received delivery_id=${deliveryId} broadcast=${Boolean(payload.broadcast)}`);
  if(store.claim(deliveryId)) {
      const targets=payload.broadcast?store.routes().map(item=>({routeRef:item.routeRef,route:item})):
        [{routeRef:payload.reply_route_ref,route:store.get('route:'+payload.reply_route_ref)}];
      if(!Object.keys(accounts).length || !targets.length)store.status(deliveryId,'failed');
      else {
      let successfulOperations=0;
      let successfulTargets=0;
      let failedTargets=0;
      const mediaIds=normalizeMediaIds(payload);
      const mediaBuffers=new Map<string,Buffer>();
      const downloadMedia=async(mediaId:string)=>{
        const cached=mediaBuffers.get(mediaId);
        if(cached)return cached;
        const response=await api('/v1/media/'+encodeURIComponent(mediaId));
        const bytes=Buffer.from(await response.arrayBuffer());
        mediaBuffers.set(mediaId,bytes);
        return bytes;
      };
      for(const [targetIndex,target] of targets.entries()) {
        let phase='resolve_account';
        try {
          if(!target.route?.contextToken)throw new Error('route context unavailable');
          const routeAccount=accounts[target.route.accountId] ?? Object.values(accounts)[0];
          if(!routeAccount)throw new Error('route account unavailable');
          const opts={baseUrl:routeAccount.baseUrl,token:routeAccount.token,contextToken:target.route.contextToken};
          const chars=Array.from(payload.text??'') as string[];
          let fragmentIndex=0;
          for(let i=0;i<chars.length;i+=2000) {
            phase='send_text';
            const clientId=generateClientId();
            store.sent(deliveryId,{recipient_ref:target.routeRef,fragment_index:fragmentIndex,client_id:clientId});
            const result=await sendMessageWeixin({to:target.route.to,text:chars.slice(i,i+2000).join(''),opts,clientId});
            store.sent(deliveryId,{recipient_ref:target.routeRef,fragment_index:fragmentIndex,
              client_id:result.clientId,message_id:result.messageId});
            successfulOperations++;
            fragmentIndex++;
          }
          for(const mediaId of mediaIds) {
            phase='download_media';
            const bytes=await downloadMedia(mediaId);
            const mime=imageMime(bytes);
            const filePath=path.join(root,digest(deliveryId+target.routeRef+mediaId)+({'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp','image/gif':'.gif'}[mime]));
            fs.writeFileSync(filePath,bytes,{mode:0o600});
            try {
              phase='send_media';
              const mediaResult=await sendWeixinMediaFile({filePath,to:target.route.to,text:'',opts,cdnBaseUrl});
              for(const message of mediaResult.messages) {
                store.sent(deliveryId,{recipient_ref:target.routeRef,fragment_index:fragmentIndex++,
                  client_id:message.clientId,message_id:message.messageId});
                successfulOperations++;
              }
            } finally {try{fs.unlinkSync(filePath);}catch{}}
          }
          successfulTargets++;
          logger.info(`message_sent delivery_id=${deliveryId} target_index=${targetIndex} success=true`);
        } catch(error) {
          failedTargets++;
          logger.error(JSON.stringify({event:'delivery_target_unknown',delivery_id:deliveryId,target_index:targetIndex,
            phase,error:safeDeliveryError(error)}));
        }
      }
      const status=failedTargets===0?'sent':
        successfulTargets>0 || successfulOperations>0?'partial':'unknown';
      if(failedTargets>0) {
        logger.error(JSON.stringify({event:'delivery_incomplete',delivery_id:deliveryId,status,
          target_count:targets.length,successful_targets:successfulTargets,failed_targets:failedTargets}));
      }
      store.status(deliveryId,status);
      }
  }
  while(alive) {
    const result=store.delivery(deliveryId);
    if(!result || result.status!=='sending')return result;
    await sleep(50);
  }
  return store.delivery(deliveryId);
}
async function heartbeat() {
  while(alive) {
    try {await api('/v1/adapters/heartbeat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({logged_in:Object.keys(accounts).length>0,connections:Object.keys(accounts).length,
        last_poll:store.get('last_poll'),last_poll_error:store.get('last_poll_error'),pending:store.pending().length})});}
    catch(error) {logger.warn(`heartbeat_failed error=${JSON.stringify(safeDeliveryError(error))}`);}
    for(let i=0;i<20 && alive;i++)await sleep(500);
  }
}
const server=http.createServer(async(req,res)=>{
  const respond=(code:number,data:any)=>{res.writeHead(code,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  if(req.url==='/health/live'){respond(200,{alive,logged_in:Object.keys(accounts).length>0,
    connections:Object.keys(accounts).length,last_poll:store.get('last_poll')});return;}
  const supplied=Buffer.from(req.headers.authorization??'');const expected=Buffer.from('Bearer '+config.apiToken);
  if(supplied.length!==expected.length || !timingSafeEqual(supplied,expected)){respond(401,{error:'unauthorized'});return;}
  try {
    if(req.method==='GET' && req.url?.startsWith('/internal/messages/')) {
      const result=store.delivery(decodeURIComponent(req.url.slice('/internal/messages/'.length)));
      respond(result?200:404,result??{error:'not found'});return;
    }
    if(req.method==='GET' && req.url==='/internal/accounts'){respond(200,{account_ref:accountRef,
      logged_in:Object.keys(accounts).length>0,connections:Object.keys(accounts).length});return;}
    if(req.method==='POST' && req.url==='/internal/messages') {
      const chunks=[];let size=0;
      for await(const chunk of req){size+=chunk.length;if(size>1024*1024)throw new Error('too large');chunks.push(chunk);}
      const payload=JSON.parse(Buffer.concat(chunks).toString());
      if(typeof payload.delivery_id!=='string' || typeof payload.reply_route_ref!=='string' || typeof payload.text!=='string' || !validMediaIds(payload))throw new Error('invalid payload');
      store.outgoing(payload.delivery_id,payload);
      respond(200,await sendDelivery(payload.delivery_id,payload));return;
    }
    if(req.method==='POST' && req.url==='/internal/broadcasts') {
      const chunks=[];let size=0;
      for await(const chunk of req){size+=chunk.length;if(size>1024*1024)throw new Error('too large');chunks.push(chunk);}
      const payload=JSON.parse(Buffer.concat(chunks).toString());
      if(typeof payload.delivery_id!=='string' || typeof payload.text!=='string' || !validMediaIds(payload))throw new Error('invalid payload');
      payload.broadcast=true;
      store.outgoing(payload.delivery_id,payload);
      respond(200,await sendDelivery(payload.delivery_id,payload));return;
    }
    respond(404,{error:'not found'});
  }catch(error) {
    logger.error(`adapter_http_request_failed method=${req.method} path=${req.url} error=${JSON.stringify(safeDeliveryError(error))}`);
    respond(409,{error:'invalid or conflicting request'});
  }
});
server.listen(config.port??8090,config.host??'127.0.0.1');
logger.info(`adapter_initialized success=true host=${config.host??'127.0.0.1'} port=${config.port??8090}`);
for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>{
  logger.info(`adapter_shutdown_started signal=${signal}`);alive=false;abort.abort();server.close();
});
await Promise.all([poll(),forward(),heartbeat()]);
store.db.close();
for(const candidate of Object.values(stores))if(candidate!==store)candidate.db.close();
logger.info('adapter_shutdown_complete');
