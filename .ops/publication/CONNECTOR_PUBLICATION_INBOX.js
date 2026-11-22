// Ari functions.exec publication sender. Configuration: load('connectorInboxConfig').
// Only the approved inbox file and a verified, fast-forward main ref may change.
function ensure(ok, message) { if (!ok) throw new Error(message); }

// SHA-256 for ASCII transport, independent of Node/WebCrypto/TextEncoder.
function sha256Ascii(s) {
  ensure(!/[^\x00-\x7f]/.test(s), 'Transport must be ASCII');
  const K = [0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2];
  const H = [0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19];
  const bytes = new Uint8Array(Math.ceil((s.length + 9) / 64) * 64);
  for (let i=0;i<s.length;i++) bytes[i]=s.charCodeAt(i);
  bytes[s.length]=128;
  for (let i=0;i<8;i++) bytes[bytes.length-1-i]=Math.floor(s.length*8/2**(8*i))&255;
  const rotr=(x,n)=>(x>>>n)|(x<<(32-n));
  for (let offset=0;offset<bytes.length;offset+=64) {
    const w=new Int32Array(64);
    for(let i=0;i<16;i++) { const p=offset+4*i; w[i]=(bytes[p]<<24)|(bytes[p+1]<<16)|(bytes[p+2]<<8)|bytes[p+3]; }
    for(let i=16;i<64;i++) { const x=w[i-15],y=w[i-2]; w[i]=((rotr(x,7)^rotr(x,18)^(x>>>3))+w[i-16]+(rotr(y,17)^rotr(y,19)^(y>>>10))+w[i-7])|0; }
    let [a,b,c,d,e,f,g,h]=H;
    for(let i=0;i<64;i++) { const t1=(h+(rotr(e,6)^rotr(e,11)^rotr(e,25))+((e&f)^(~e&g))+K[i]+w[i])|0;
      const t2=((rotr(a,2)^rotr(a,13)^rotr(a,22))+((a&b)^(a&c)^(b&c)))|0;
      h=g;g=f;f=e;e=(d+t1)|0;d=c;c=b;b=a;a=(t1+t2)|0; }
    [a,b,c,d,e,f,g,h].forEach((value,i)=>H[i]=(H[i]+value)|0);
  }
  return H.map(n=>(n>>>0).toString(16).padStart(8,'0')).join('');
}

// The inbox branch is provisioned with .ops/publication/ and its receiver.
const ROOT = '/workspace/shared/jeonjaeng-nalkka-live';
const PYTHON = ROOT + '/.venv/bin/python';
const REPO = 'Realhaapy/jeonjaeng-nalkka';
const BRANCH = 'ari-publication-inbox-v1';
const DIR = '.ops/publication';
const PATH = DIR + '/incoming.json';
const SHA = /^[0-9a-f]{40}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const MAX_BYTES = 1_000_000;
const PRODUCER = {
  path: '.ops/publication/connector-inbox-export.py',
  sha256: 'c5a97adf6bc3e329352864f8cbbf3c045ab1d3cae8a48dfa0dc544890667cf8a',
  blob: '29ef42f968a781088f721a4c0f5ffd0ecd8cd183',
  bytes: 16143,
};
const quote = value => "'" + String(value).replace(/'/g, "'\\''") + "'";

const EXEC_DISCONNECTED = 'exec_command failed: CreateProcess { message: "Rejected(\\"Failed to create unified exec process: exec-server transport disconnected\\")" }';
function safeIssue(value) {
  const msg = String(value?.error?.message ?? value?.message ?? value?.error_message ?? value?.content?.[0]?.text ?? value?.error ?? value);
  const denial = /denied|forbidden|disallow|reject|거절|거부/i;
  const fields = [value?.status,value?.code,value?.reason,value?.error_class,value?.error_code,value?.error?.code,value?.error?.reason];
  if (fields.some(field => field === 403 || denial.test(String(field ?? '')))) return 'permission denied or rejected';
  if (msg === EXEC_DISCONNECTED) return 'exec-server transport disconnected';
  if (denial.test(msg)) return 'permission denied or rejected';
  if (/disconnect|transport|timeout|deadline|시간 초과/i.test(msg)) return 'transport disconnected or timed out';
  if (/conflict|mismatch|already exists/i.test(msg)) return 'state conflict';
  return 'unconfirmed tool result';
}

function unpack(value, label) {
  for (let depth = 0; depth < 8; depth++) {
    if (value?.isError === true || value?.error || value === EXEC_DISCONNECTED ||
        ['failed','denied','rejected'].includes(value?.status)) {
      throw new Error(label + ': ' + safeIssue(value));
    }
    if (value && (value.sha || value.commit || value.commit_sha || value.object || value.exit_code != null || value.session_id != null)) break;
    let next = value?.structuredContent;
    if (next == null) {
      if (typeof value?.content === 'string') next = value.content;
      else if (Array.isArray(value?.content) && value.content.length === 1) next = value.content[0].text;
    }
    if (next == null) break;
    if (typeof next === 'string') {
      try { next = JSON.parse(next); } catch (_) {
        if (next === EXEC_DISCONNECTED) throw new Error(label + ': ' + safeIssue(next));
        return next;
      }
    }
    value = next;
  }
  return value;
}

function githubTool(suffix, fields) {
  const matches = ALL_TOOLS.filter(tool => tool.name.includes('github') && tool.name.endsWith(suffix));
  ensure(matches.length === 1 && fields.every(field => matches[0].description.includes(field)),
         'Missing GitHub connector schema for ' + suffix);
  return tools[matches[0].name];
}

async function command(args, budget = 50_000, timeout = 180_000, expectedAsciiBytes = 0) {
  const deadline = Date.now() + timeout;
  async function shell(tool, parameters) {
    try { return await tool(parameters); }
    catch (error) { throw new Error('Ari shell: ' + safeIssue(error)); }
  }
  let raw = await shell(tools.exec_command,{cmd: args.map(quote).join(' '), workdir: ROOT,
                                            max_output_tokens: budget, yield_time_ms: 1000});
  let output = '', session;
  while (true) {
    const part = unpack(raw, 'Ari shell');
    ensure(typeof part?.output === 'string', 'Ari shell returned no output field');
    output += part.output;
    ensure(output.length <= 256 * 1024, 'Ari shell exceeded its response bound');
    if (part.exit_code != null) {
      ensure(part.exit_code === 0, 'Ari shell exited with code ' + part.exit_code + '; inspect the run receipt');
      if (expectedAsciiBytes) {
        // Python print appends one newline. All envelope bytes are printable ASCII.
        const bytes = output.replace(/\r?\n$/, '');
        if (bytes.length === expectedAsciiBytes && /^[\x20-\x7e]+$/.test(bytes)) return bytes;
        const clipped = bytes.length < expectedAsciiBytes || /(?:tokens?|output)\s+truncated|truncated\s+output/i.test(bytes);
        const error = new Error(clipped ? 'Full public packet output was truncated' : 'Unexpected full public packet framing');
        error.truncatedPublicPacket = clipped;
        throw error;
      }
      try { return JSON.parse(output); } catch (_) { throw new Error('Ari shell returned incomplete JSON'); }
    }
    ensure(Number.isInteger(part.session_id) && (session == null || session === part.session_id), 'Lost Ari shell session');
    session = part.session_id;
    ensure(Date.now() < deadline, 'Ari shell still running; inspect the existing session');
    raw = await shell(tools.write_stdin,{session_id:session,chars:'',max_output_tokens:budget,yield_time_ms:10000});
  }
}

async function publishInbox() {
  const config = load('connectorInboxConfig') || {};
  ensure(!config.root || config.root === ROOT, 'Incorrect canonical root');
  const write = config.allowWrites === true;
  let plan = config.plan;
  if (plan == null) {
    const lookup = 'import json;from pathlib import Path;s=json.loads(Path("data/last-run.json").read_bytes());print(json.dumps(s["publication"]["plan"]))';
    plan = await command([PYTHON, '-B', '-c', lookup]);
  }
  ensure(typeof plan === 'string' && /^\/workspace\/shared\/jeonjaeng-nalkka-live\/data\/cloud-publication-plans\/[0-9a-f]{32}\.json$/.test(plan),
         'An existing immutable canonical plan is required');
  let entry = [ROOT + '/scripts/connector-inbox-export.py'];
  if (config.opsCommit !== undefined) {
    ensure(typeof config.opsCommit === 'string' && SHA.test(config.opsCommit), 'A full pinned operations commit is required');
    const fetchSource = githubTool('_fetch_file',['ref','repository_full_name','path','encoding']);
    const file = unpack(await fetchSource({repository_full_name:REPO,ref:config.opsCommit,
                                           path:PRODUCER.path,encoding:'utf-8'}), 'GitHub source read');
    ensure(file?.sha === PRODUCER.blob && file.encoding === 'utf-8' &&
           typeof file.content === 'string' && file.content.length === PRODUCER.bytes &&
           sha256Ascii(file.content) === PRODUCER.sha256, 'Pinned public exporter source failed integrity verification');
    entry = ['-c',file.content];
  }
  const exporter = [PYTHON,'-B',...entry,'--root',ROOT,'--plan',plan];
  const read = (mode, ...args) => command([...exporter,'--mode',mode,...args]);
  const bound = await read('bound');
  const run = bound.run_id, base = bound.base_commit_sha, tree = bound.expected_tree_sha;
  ensure(/^[0-9a-f]{32}$/.test(run) && plan.endsWith('/' + run + '.json') &&
         SHA.test(base) && SHA.test(tree) && DIGEST.test(bound.plan_sha256) &&
         /^r-[0-9a-f]{16}$/.test(bound.release_id) && typeof bound.generated_at === 'string', 'Incomplete canonical plan binding');
  const receipt = {run_id: run, release_id: bound.release_id, base_commit_sha: base, expected_tree_sha: tree};
  const fetch = githubTool('_fetch',['url']);
  const api = 'https://api.github.com/repos/' + REPO;
  async function get(route) {
    return unpack(await fetch({url:api + route}), 'GitHub read');
  }
  async function head(branch) {
    const result = await get('/git/ref/heads/' + branch);
    ensure(result?.ref === 'refs/heads/' + branch && result.object?.type === 'commit' && SHA.test(result.object.sha),
           'Unexpected GitHub branch ref');
    return result.object.sha;
  }
  async function ready() {
    const branch = 'ari-ready-' + run;
    const matches = await get('/git/matching-refs/heads/' + branch);
    ensure(Array.isArray(matches), 'Invalid ready-ref lookup');
    const exact = matches.filter(ref => ref.ref === 'refs/heads/' + branch);
    ensure(exact.length <= 1, 'Duplicate ready ref');
    if (!exact.length) return null;
    const tip = exact[0].object;
    ensure(tip?.type === 'commit' && SHA.test(tip.sha), 'Invalid prepared ref');
    const commit = await get('/git/commits/' + tip.sha);
    ensure(commit.sha === tip.sha && commit.tree?.sha === tree &&
           commit.parents?.length === 1 && commit.parents[0].sha === base, 'Candidate does not match the plan parent and full tree');
    return tip.sha;
  }
  async function inboxBlob(commit) {
    const listing = await get('/contents/' + DIR + '?ref=' + commit);
    ensure(Array.isArray(listing) && listing.length < 1000, 'Inbox directory not provisioned or listing incomplete');
    const found = listing.filter(entry => entry.path === PATH);
    ensure(found.length <= 1 && (!found.length || (found[0].type === 'file' && SHA.test(found[0].sha))), 'Invalid inbox file listing');
    return found[0]?.sha || null;
  }
  const stateKey = 'warwatchInboxAttempt:' + run + ':';
  async function mutate(stage, action) {
    ensure(!load(stateKey + stage), 'Previous ' + stage + ' tool call needs outcome review; do not repeat it');
    store(stateKey + stage, {state:'requested', at:new Date().toISOString()});
    try {
      const value = unpack(await action(), 'GitHub ' + stage);
      store(stateKey + stage,{state:'returned', at:new Date().toISOString()});
      return value;
    } catch (err) {
      store(stateKey + stage,{state:'requires_review', reason:safeIssue(err)});
      throw new Error('GitHub ' + stage + ' stopped (' + safeIssue(err) + '); inspect the actual action outcome before any further write');
    }
  }
  function sameBinding(value) {
    ensure(['run_id','plan_sha256','base_commit_sha','expected_tree_sha','release_id','generated_at']
      .every(key => value[key] === bound[key]), 'Current canonical plan changed');
  }
  async function finalize(commit) {
    ensure(await head('main') === commit, 'Main changed before finalization');
    if (bound.verified_commit === commit) return {...receipt, stage:'verified', commit};
    if (!write || config.externalFinalize === true) return {...receipt,stage:'main_verified_awaiting_finalize',commit,plan};
    const summarize = [
      'import json,subprocess,sys',
      'p=subprocess.run([sys.executable,"-B","scripts/cloud-run.py","--finalize-connector-publish",sys.argv[1],"--commit-sha",sys.argv[2]],capture_output=True)',
      'v=json.loads(p.stdout); pub=v.get("publication",{}); verify=v.get("verification",{})',
      'print(json.dumps({"run_id":v.get("run_id"),"status":v.get("status"),"verified":pub.get("verified"),"commit":pub.get("commit"),"https":verify.get("status"),"release_id":verify.get("release_id"),"generated_at":verify.get("generated_at"),"exit_code":p.returncode}))'
    ].join('\n');
    const outcome = await command([PYTHON,'-B','-c',summarize,plan,commit], 2000, 720_000);
    ensure(outcome.run_id === run && outcome.status === 'succeeded' && outcome.verified === true &&
           outcome.commit === commit && outcome.https === 'verified' && outcome.release_id === bound.release_id &&
           outcome.generated_at === bound.generated_at && outcome.exit_code === 0,
           'Main updated; canonical finalizer has not verified the HTTPS release');
    return {...receipt, stage:'verified', commit};
  }
  let candidate = await ready();
  let current = await head('main');
  if (candidate && current === candidate) return finalize(candidate);
  ensure(current === base, 'Main diverged from the prepared base; retain the inbox and plan');
  const meta = await read('meta');
  sameBinding(meta);
  ensure(Number.isSafeInteger(meta.bytes) && meta.bytes > 0 && meta.bytes <= MAX_BYTES &&
         meta.chunk_chars === 12000 && DIGEST.test(meta.sha256) && SHA.test(meta.blob_sha), 'Envelope metadata outside the protocol');
  async function preflight() {
    const proof = await read('preflight','--sha256',meta.sha256);
    sameBinding(proof);
    ensure(proof.bytes === meta.bytes && proof.sha256 === meta.sha256 && proof.blob_sha === meta.blob_sha &&
           await head('main') === base, 'Canonical preflight changed');
  }
  async function packetBytes() {
    if (meta.bytes <= 96_000) {
      try {
        // Capture exact public bytes at the tool boundary, without re-serializing or printing them.
        return await command([...exporter,'--mode','full'],50_000,180_000,meta.bytes);
      } catch (error) {
        if (error.truncatedPublicPacket !== true) throw error;
      }
    }
    const slices = [];
    for (let first = 0; first < meta.bytes; first += meta.chunk_chars * 4) {
      const offsets = Array.from({length:Math.min(4, Math.ceil((meta.bytes-first)/meta.chunk_chars))}, (_,i) => first+i*meta.chunk_chars);
      const parts = await Promise.all(offsets.map(offset => read('chunk','--offset',String(offset),'--sha256',meta.sha256)));
      parts.forEach((part,i) => {
        const length = Math.min(meta.chunk_chars,meta.bytes-offsets[i]);
        ensure(part.offset===offsets[i] && part.bytes===length && typeof part.chunk==='string' &&
               part.chunk.length===length && sha256Ascii(part.chunk)===part.sha256, 'Truncated or changed inbox slice');
        slices.push(part.chunk);
      });
    }
    return slices.join('');
  }
  if (!candidate) {
    const inboxHead = await head(BRANCH);
    const before = await inboxBlob(inboxHead);
    if (before !== meta.blob_sha) {
      if (!write) return {...receipt, stage:'ready_to_submit', bytes:meta.bytes};
      const content = await packetBytes();
      ensure(content.length === meta.bytes && sha256Ascii(content) === meta.sha256, 'Envelope byte digest mismatch');
      const envelope = JSON.parse(content);
      ensure(Object.keys(envelope).sort().join() === 'base_commit_sha,bundle_base64,bundle_sha256,expected_tree_sha,generated_at,protocol,release_id,run_id' &&
             envelope.protocol===1 && ['run_id','base_commit_sha','expected_tree_sha','generated_at','release_id'].every(k=>envelope[k]===bound[k]) &&
             DIGEST.test(envelope.bundle_sha256) && typeof envelope.bundle_base64==='string' &&
             envelope.bundle_base64.length>0 && /^[a-zA-Z0-9+/]+=*$/.test(envelope.bundle_base64), 'Unexpected public envelope');
      await preflight();
      ensure(await head(BRANCH)===inboxHead, 'Inbox branch advanced while preparing; reconcile its current packet');
      const args={repository_full_name:REPO,branch:BRANCH,path:PATH,message:'Receive Warwatch publication '+run,content};
      const required=['branch','content','repository_full_name','path','message'];
      const tool=before ? githubTool('_update_file',[...required,'sha']) : githubTool('_create_file',required);
      if (before) args.sha=before;
      const written=await mutate('submit',()=>tool(args));
      const wrote=typeof written==='string' ? written : written?.commit_sha ?? written?.commit?.sha ?? written?.sha;
      ensure(SHA.test(wrote) && (await inboxBlob(wrote))===meta.blob_sha, 'Inbox write returned without a matching remote blob; reconcile before retry');
      notify({...receipt,stage:'inbox_verified',commit:wrote});
    } else if (!write) return {...receipt,stage:'awaiting_receiver'};
    for (let tries=0; tries<30; tries++) {
      candidate = await ready();
      if (candidate) break;
      if (tries<29) await new Promise(resolve=>setTimeout(resolve,10000));
    }
    if (!candidate) return {...receipt,stage:'awaiting_receiver'};
  }
  current = await head('main');
  if (current === candidate) return finalize(candidate);
  ensure(current === base, 'Main changed after the receiver produced the candidate');
  await preflight();
  if (!write) return {...receipt,stage:'candidate_verified',commit:candidate};
  const move = githubTool('_update_ref',['branch_name','sha','expected_sha','force','repository_full_name']);
  await mutate('promote',()=>move({repository_full_name:REPO,branch_name:'main',sha:candidate,expected_sha:base,force:false}));
  return finalize(candidate);
}

text(await publishInbox());
