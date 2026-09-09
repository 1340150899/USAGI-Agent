import { build } from 'esbuild';
await build({entryPoints:['src/main.ts'],bundle:true,platform:'node',format:'esm',target:'node24',
  outfile:'dist/main.js',packages:'external'});
