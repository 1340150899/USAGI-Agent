// Independent account ownership; no OpenClaw state-directory/config dependency.
let accounts: Record<string, any> = {};
export function configureAccounts(value: Record<string, any>) { accounts=value; }
export function listIndexedWeixinAccountIds() { return Object.keys(accounts); }
export function loadWeixinAccount(id: string) { return accounts[id]; }
export function loadConfigBotAgent() { return 'USAGI/0.1.0'; }
export function loadConfigRouteTag() { return process.env.WEIXIN_ROUTE_TAG; }
