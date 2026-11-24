#!/usr/bin/env node
// Fetch a cluster space's namespace kubeconfig from the cloud's own GraphQL.
//
//   CLOUD_USER=… CLOUD_PASSWORD=… node space-kubeconfig.js            # list the spaces
//   CLOUD_USER=… CLOUD_PASSWORD=… SPACE_ID=… node space-kubeconfig.js > ./stand.kubeconfig
//
// Why a script and not the query pasted into a README. The by-hand part of a
// window — restarting the egress sidecar and deleting the previous grounding Job
// on a cloud from before SUP-383 — needs this file, and it needs it DURING the
// window, with the stand off the air and somebody watching a clock. Writing the
// query out by hand at that moment is how a window gets long.
//
// The file is a credential for a live namespace. `stands/latica/.gitignore`
// ignores `*.kubeconfig`, this writes to stdout rather than choosing a path, and
// the README tells people to delete it when the window closes.
const BASE = (process.env.CLOUD_BASE || 'https://azure-test.swarm.superprotocol.dev').replace(/\/$/, '');
const SPACE = process.env.SPACE_ID;

async function token() {
  const response = await fetch(`${BASE}/api/auth/sign-in/email`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: BASE },
    body: JSON.stringify({ email: required('CLOUD_USER'), password: required('CLOUD_PASSWORD') }),
  });
  const body = await response.json().catch(() => null);
  if (!body || !body.token) throw new Error(`sign-in ${response.status}`);
  return body.token;
}

function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`set ${name}`);
  return value;
}

async function graphql(bearer, query, variables) {
  const response = await fetch(`${BASE}/graphql`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${bearer}`, Origin: BASE },
    body: JSON.stringify({ query, variables }),
  });
  const body = await response.json().catch(() => null);
  if (!body || body.errors) throw new Error(`graphql ${response.status} ${JSON.stringify(body && body.errors)}`);
  return body.data;
}

const LIST = `query { clusterSpaces { nodes { id name status locked namespace } } }`;
const ONE = `query ($id: ID!) {
  clusterSpaces(filter: { id: { eq: $id } }) { nodes { id name locked namespace kubeConfig } }
}`;

(async () => {
  const bearer = await token();
  if (!SPACE) {
    const data = await graphql(bearer, LIST);
    for (const space of data.clusterSpaces.nodes) {
      // To stderr: stdout is the kubeconfig's channel and nothing else, so
      // `> ./stand.kubeconfig` cannot pick up a listing by accident.
      console.error(
        `${space.id}  locked=${space.locked}  ${space.status || ''}  ${space.namespace || ''}  ${space.name || ''}`,
      );
    }
    console.error('\nre-run with SPACE_ID=<id> and redirect stdout to a file');
    return;
  }
  const data = await graphql(bearer, ONE, { id: SPACE });
  const space = data.clusterSpaces.nodes[0];
  if (!space) throw new Error(`no cluster space ${SPACE}`);
  if (!space.kubeConfig) throw new Error(`${SPACE} returned no kubeConfig — are you its owner?`);
  console.error(`${space.name} (${space.namespace}) locked=${space.locked}`);
  process.stdout.write(space.kubeConfig.endsWith('\n') ? space.kubeConfig : `${space.kubeConfig}\n`);
})().catch((error) => {
  console.error('FAILED', error.message);
  process.exit(1);
});
