import test from 'node:test';
import assert from 'node:assert/strict';
import {parseWeixinJson} from '../src/weixin/api/json.mjs';

test('Weixin message ids larger than MAX_SAFE_INTEGER remain exact strings',()=>{
  const parsed=parseWeixinJson('{"message_id":7503014928614951688,"nested":{"msg_id":7503014928614951689},"ret":0}');
  assert.equal(parsed.message_id,'7503014928614951688');
  assert.equal(parsed.nested.msg_id,'7503014928614951689');
});
