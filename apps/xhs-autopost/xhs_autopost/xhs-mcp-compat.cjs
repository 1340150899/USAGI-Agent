// Compatibility for xhs-mcp 0.8.13 and the creator center's note-card layout.
// Load the installed package in memory; do not alter the npm installation.
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');

function patchSource(source) {
  const replacements = [
    ["NOTE_ELEMENTS: 'div.note',", "NOTE_ELEMENTS: 'div.note, .note-card',"],
    ["NOTE_ITEM: [\\n        'div.note',", "NOTE_ITEM: [\\n        '.note-card',\\n        'div.note',"],
    ["DELETE_BUTTON: [\\n        '.control.data-del',", "DELETE_BUTTON: [\\n        '.note-card__action-btn--del',\\n        '.control.data-del',"],
  ];
  for (const [before, after] of replacements) {
    if (source.split(before).length !== 2) {
      throw new Error(`Unsupported xhs-mcp bundle: expected exactly one ${before}`);
    }
    source = source.replace(before, after);
  }
  return source;
}

function main() {
  const checkOnly = process.argv[2] === '--check';
  const entry = fs.realpathSync(process.argv[checkOnly ? 3 : 2]);
  const metadata = JSON.parse(fs.readFileSync(path.join(path.dirname(entry), '..', 'package.json'), 'utf8'));
  if (metadata.name !== 'xhs-mcp' || metadata.version !== '0.8.13') {
    throw new Error('Creator-center compatibility requires xhs-mcp 0.8.13');
  }
  const source = patchSource(fs.readFileSync(entry, 'utf8'));
  if (checkOnly) {
    console.log('xhs-mcp 0.8.13: all three creator-center compatibility patches matched');
    return;
  }
  process.argv = [process.argv[0], entry, ...process.argv.slice(3)];
  const upstream = new Module(entry, module);
  upstream.filename = entry;
  upstream.paths = Module._nodeModulePaths(path.dirname(entry));
  upstream._compile(source, entry);
}

module.exports = { patchSource };
if (require.main === module) main();
