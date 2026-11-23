// Calls the clinic's scheduling system the way the application's MCP client
// does — the official SDK over Streamable HTTP — so a check here predicts the
// deployment rather than resembling it.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';

const url = process.env.MCP_URL;
const token = process.env.MCP_TOKEN ?? '';
const transport = new StreamableHTTPClientTransport(new URL(url), {
  requestInit: { headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), 'X-Aivo-Tenant-Id': process.env.TENANT ?? 'probe' } },
});
const client = new Client({ name: 'probe', version: '0.0.1' });
try {
  await client.connect(transport);
  const tools = await client.listTools();
  console.log('tools:', JSON.stringify(tools.tools.map((t) => ({ name: t.name, annotations: t.annotations })), null, 1));
  if (process.env.CALL) {
    const result = await client.callTool({ name: 'schedule_recall', arguments: JSON.parse(process.env.CALL) });
    console.log('isError:', result.isError === true);
    for (const item of result.content ?? []) console.log(item.text);
  }
  await client.close();
} catch (error) {
  console.log('REFUSED', error?.message ?? String(error));
  process.exit(2);
}
