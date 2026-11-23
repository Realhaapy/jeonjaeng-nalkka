const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { normalize } = require('./widget/civil-alerts.js');
const { install } = require('./widget/inject.cjs');
const config = require('./endpoint.json');

async function main() {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'civil-panel-'));
  const filename = path.join(dir, 'snapshot.json');
  let snapshot;
  try {
    // Server-render verified records even for readers with JavaScript disabled.
    execFileSync('python3', [path.join(__dirname, 'civil_feed.py'), '--output', filename], {
      timeout: 25000, stdio: 'inherit'
    });
    snapshot = normalize(JSON.parse(await fs.readFile(filename, 'utf8')), Date.now());
  } catch {
    try {
      const response = await fetch(config.snapshot, { signal: AbortSignal.timeout(8000), redirect: 'error' });
      if (!response.ok) throw new Error('No saved snapshot');
      snapshot = normalize(await response.json(), Date.now());
    } catch {
      console.warn('No verified build-time snapshot; the panel will fetch again on open.');
    }
  } finally {
    await fs.rm(filename, { force: true });
    await fs.rmdir(dir);
  }
  install({
    publicDir: 'public', feedUrl: config.live || config.snapshot,
    backupUrl: config.live ? config.snapshot : undefined, snapshot
  });
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
