import fs from 'node:fs';
import path from 'node:path';

type Level='debug'|'info'|'warn'|'error';
let serviceDirectory: string|undefined;

function defaultRoot() {
  if(process.env.USAGI_LOG_DIR)return path.resolve(process.env.USAGI_LOG_DIR);
  let current=process.cwd();
  for(;;) {
    if(fs.existsSync(path.join(current,'apps')) && fs.existsSync(path.join(current,'usagi-agent')))
      return path.join(current,'log');
    const parent=path.dirname(current);
    if(parent===current)return path.join(process.cwd(),'log');
    current=parent;
  }
}

export function configureLogger(logRoot?:string) {
  const root=logRoot?(path.isAbsolute(logRoot)?logRoot:path.resolve(path.dirname(defaultRoot()),logRoot)):defaultRoot();
  serviceDirectory=path.join(root,'adapter-server');
  fs.mkdirSync(serviceDirectory,{recursive:true,mode:0o700});
}

function write(level:Level,args:unknown[]) {
  if(!serviceDirectory)configureLogger();
  const now=new Date();
  const timestamp=now.toISOString();
  const localDay=new Date(now.getTime()-now.getTimezoneOffset()*60_000).toISOString().slice(0,10);
  const message=args.map(value=>value instanceof Error?
    `${value.name}: ${value.message}`:typeof value==='string'?value:JSON.stringify(value)).join(' ');
  const line=`${timestamp} ${level.toUpperCase()} weixin_adapter ${message}\n`;
  const fileLevel=level==='warn'?'warning':level;
  fs.appendFileSync(path.join(serviceDirectory!,`${localDay}.${fileLevel}.log`),line,{encoding:'utf8',mode:0o600});
  (level==='error'?console.error:level==='warn'?console.warn:console.log)(line.trimEnd());
}

export const logger={
  debug:(...args:unknown[])=>write('debug',args),
  info:(...args:unknown[])=>write('info',args),
  warn:(...args:unknown[])=>write('warn',args),
  error:(...args:unknown[])=>write('error',args),
};
