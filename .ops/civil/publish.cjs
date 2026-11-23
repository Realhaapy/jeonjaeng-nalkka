const fs = require('node:fs/promises');
const path = require('node:path');

// Keep the public data branch independent of the hourly article publisher.
module.exports = async function publish({ github, context, core }) {
  const repo = context.repo;
  const branch = 'civil-notices';
  const file = 'alerts.json';
  const next = JSON.parse(await fs.readFile(path.join(process.env.RUNNER_TEMP, 'civil-notices.json'), 'utf8'));
  const checked = Date.parse(next.checked_at);
  if (next.schema !== 1 || !Array.isArray(next.items) || !Number.isFinite(checked) || !next.complete) {
    throw new Error('Incomplete source result. The previous public snapshot is preserved.');
  }

  let head;
  try {
    head = (await github.rest.git.getRef({ ...repo, ref: 'heads/' + branch })).data.object.sha;
  } catch (err) {
    if (err.status !== 404) throw err;
  }

  if (head) {
    const content = await github.rest.repos.getContent({ ...repo, path: file, ref: head });
    const prior = JSON.parse(Buffer.from(content.data.content, 'base64').toString('utf8'));
    const age = checked - Date.parse(prior.checked_at);
    if (age <= 0) return core.info('Kept a newer snapshot.');
    if (age < 10 * 60 * 1000 && JSON.stringify(prior.items) === JSON.stringify(next.items)) {
      return core.info('Official list checked, no changes since the last snapshot.');
    }
  }

  const tree = await github.rest.git.createTree({ ...repo, tree: [
    { path: file, mode: '100644', type: 'blob', content: JSON.stringify(next) + '\n' }
  ] });
  const commit = await github.rest.git.createCommit({
    ...repo, message: 'Update official civilian notices ' + next.checked_at,
    tree: tree.data.sha, parents: head ? [head] : []
  });
  if (head) {
    await github.rest.git.updateRef({ ...repo, ref: 'heads/' + branch, sha: commit.data.sha, force: false });
  } else {
    await github.rest.git.createRef({ ...repo, ref: 'refs/heads/' + branch, sha: commit.data.sha });
  }
  core.info('Published ' + next.items.length + ' civilian message records.');
};
