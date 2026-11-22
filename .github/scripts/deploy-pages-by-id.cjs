// Submit the receipt from upload-pages-artifact directly, then check this one deployment.
module.exports = async function deployPages({ github, context, core }) {
  const repo = context.repo;
  const sha = process.env.PAGES_COMMIT;
  const artifact = Number(process.env.PAGES_ARTIFACT_ID);
  if (!/^[a-f0-9]{40}$/.test(sha) || !Number.isSafeInteger(artifact) || artifact < 1)
    throw new Error('Invalid Pages commit or upload receipt');

  const prior = process.env.PAGES_RECOVERY_RUN;
  if (prior) {
    const run = (await github.rest.actions.getWorkflowRun({ ...repo, run_id: Number(prior) })).data;
    if (run.status !== 'completed' || run.conclusion !== 'failure' || run.head_sha !== sha ||
        run.run_attempt !== Number(process.env.PAGES_RECOVERY_ATTEMPT)) {
      core.notice('The original workflow has moved on');
      return;
    }
  }

  const statusRoute = 'GET /repos/{owner}/{repo}/pages/deployments/{deployment_id}';
  let id = sha;
  let state;
  try {
    state = (await github.request(statusRoute, { ...repo, deployment_id: sha })).data.status;
    if (typeof state !== 'string') throw new Error('Pages returned no deployment status');
  } catch (error) {
    if (error.status !== 404) throw error;
  }
  const failed = new Set(['deployment_failed', 'deployment_perms_error', 'deployment_content_failed',
    'deployment_cancelled', 'deployment_lost']);
  if (!state || failed.has(state)) {
    const oidc = await core.getIDToken();
    const head = await github.rest.git.getRef({ ...repo, ref: 'heads/main' });
    if (head.data.object.sha !== sha) {
      core.notice('A newer main is already queued for publication');
      return;
    }
    // A lost create response is ambiguous. Do not repeat this POST in this invocation.
    const created = await github.request('POST /repos/{owner}/{repo}/pages/deployments', {
      ...repo, artifact_id: artifact, pages_build_version: sha, oidc_token: oidc,
      request: { retries: 0 }
    });
    id = created.data.id || created.data.status_url?.split('/').pop() || sha;
    state = undefined;
  }

  const end = Date.now() + 600_000;
  while (Date.now() < end) {
    if (state === 'succeed') return core.info('Pages verified deployment for ' + sha);
    if (failed.has(state)) throw new Error('Pages reported ' + state + ' for ' + sha);
    await new Promise(resolve => setTimeout(resolve, 5000));
    try {
      state = (await github.request(statusRoute, { ...repo, deployment_id: id })).data.status;
    } catch (error) {
      if (!(error.status === 404 || error.status === 429 || error.status >= 500)) throw error;
    }
  }
  throw new Error('Pages did not confirm deployment for ' + sha + ' within ten minutes');
};
