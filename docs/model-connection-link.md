# The model connection link, v1

A deployed model-serving listing has three things somebody else needs: the base URL, the model id,
and the API key. A connection link is those three in one string, so that handing a model over is a
copy and a paste rather than three fields transcribed by hand.

```
https://<host>/v1#key=<api-key>&model=<model-id>
```

This file is the normative definition. It is written here because this repository holds the
**producer** — every model-serving listing emits one as an output of `type: secret` — and the
consumer (the Confidential Router's admin *Add external model* field, SUP-226) has to parse exactly
what is produced. Where this file and an implementation disagree, this file is the bug report.

## Why the fragment, and not a query

A URL fragment is never sent to a server: it is not in the request line, so it is not in an access
log, not in a proxy log, not in a `Referer` header and not in a server-side error report. A query
string is in all five. The key in this link is a live credential, so the difference is the whole
reason the link has this shape.

That makes one rule non-negotiable: **a parser must reject a link that carries `key` in the query**,
rather than accepting it with a warning. By the time such a link exists the key has already been
written into whatever logged the URL, and accepting it teaches a producer that the query works.

## Grammar

```
link        = base-url "#" fragment
base-url    = "https://" host [ ":" port ] path        ; path ends with "/v1"
fragment    = pair *( "&" pair )
pair        = name "=" value                            ; application/x-www-form-urlencoded
```

- `base-url` is exactly what an OpenAI client is given as its `base_url` — scheme `https` only, no
  userinfo, no query, no trailing slash, path ending in `/v1`.
- `fragment` is `application/x-www-form-urlencoded`: pairs joined by `&`, percent-encoding for
  anything outside the unreserved set, `+` decoding to a space.
- Parameter names are lowercase and case-sensitive.

| Parameter | Required | Meaning |
|---|---|---|
| `key` | yes | The bearer token, verbatim after percent-decoding. Sent as `Authorization: Bearer <key>`. |
| `model` | yes | The model id exactly as `GET /v1/models` reports it. |

### Rules for a parser

1. **Reject** a link with no fragment, with an empty `key`, or with an empty `model`.
2. **Reject** a link whose query carries `key` (above). A query carrying anything else is still a
   malformed base URL and should be rejected too.
3. **Reject** a repeated `key` or `model`. Last-one-wins on a credential is a silent wrong answer.
4. **Reject** a non-`https` scheme. The link is a credential; `http` spends it on the first hop.
5. **Ignore** parameters it does not know. That is the forward-compatibility seam — v2 adds a
   parameter, v1 parsers keep working.
6. **Do not** require a particular order.

### Rules for a producer

1. Percent-encode the key for everything outside `A-Za-z0-9-._~` — in particular `&`, `=`, `#`,
   `+`, `%` and space.
2. Never emit a link with an empty `key` or `model`.
3. Keep the whole link under 2000 characters, so it survives being pasted into a single-line input.
4. Surface it only as an output of `type: secret`. A connection link is a credential with a URL
   wrapped around it; `type: url` would render it in the clear and put it in a browser's history.
5. A producer with no percent-encoder to hand — a marketplace listing's output template is string
   interpolation and nothing more — must instead **constrain the key's alphabet** so that encoding
   is a no-op. The listings in this repository do: the key parameter validates against
   `^[A-Za-z0-9]{32,}$`, and the platform's own generator for `generated` parameters emits
   `A-Za-z0-9` anyway (`deployment-bundle.service.ts`, `generateSecret`). An operator who brings
   their own key is held to the same alphabet rather than silently producing a link that splits on
   its own `&`.

### What is deliberately not in the link

- **The measurement, or any attestation claim.** The consumer attests `<host>` itself — that is the
  entire point of ADR-008 — and a measurement carried in the link is a measurement the producer
  asserted about itself. A parser that read one would be trusting the thing it is about to verify.
- **The evidence URL.** It is derivable: `https://<host>/.well-known/swarm-evidence`, served by the
  platform on every hostname it terminates TLS for.
- **Prices.** The registering admin sets them (ADR-008 decision 4). They are not a property of the
  endpoint.

### Handling, once parsed

The link is a secret in one piece. A consumer should split it immediately and never reassemble it
for display: `key` into the secret envelope (write-only, prefix for display), `base-url` and `model`
into ordinary columns. Nothing should log the link, echo it back, or put it in an error message.

## Test vectors

A parser that agrees with this table agrees with the spec. `✓` means accepted with the fields shown.

| Link | Verdict |
|---|---|
| `https://m.example.com/v1#key=sk-abc&model=llama-3.2-3b-instruct` | ✓ base `https://m.example.com/v1`, key `sk-abc`, model `llama-3.2-3b-instruct` |
| `https://m.example.com/v1#model=gemma-2-2b-it&key=sk-abc` | ✓ order does not matter |
| `https://m.example.com:8443/v1#key=sk-abc&model=m` | ✓ a port is fine |
| `https://m.example.com/v1#key=a%26b%3Dc&model=m` | ✓ key `a&b=c` |
| `https://m.example.com/v1#key=a+b&model=m` | ✓ key `a b` |
| `https://m.example.com/v1#key=sk-abc&model=m&price=1` | ✓ unknown `price` ignored |
| `https://m.example.com/v1?key=sk-abc#model=m` | ✗ key in the query |
| `https://m.example.com/v1#key=sk-abc` | ✗ no `model` |
| `https://m.example.com/v1#model=m` | ✗ no `key` |
| `https://m.example.com/v1#key=&model=m` | ✗ empty `key` |
| `https://m.example.com/v1#key=a&key=b&model=m` | ✗ repeated `key` |
| `http://m.example.com/v1#key=sk-abc&model=m` | ✗ not https |
| `https://m.example.com/v1` | ✗ no fragment |
| `https://m.example.com/#key=sk-abc&model=m` | ✗ base URL does not end in `/v1` |

## Reference parser

Twelve lines, and the only interesting thing in it is what it refuses.

```ts
export function parseModelConnectionLink(link: string): {
  baseUrl: string; model: string; key: string;
} {
  const url = new URL(link);                                  // throws on a non-URL
  if (url.protocol !== 'https:') throw new Error('not https');
  if (url.username || url.password) throw new Error('userinfo in the base URL');
  if (url.searchParams.has('key')) throw new Error('key in the query, not the fragment');
  if (url.search) throw new Error('unexpected query');
  if (!url.pathname.endsWith('/v1')) throw new Error('base URL does not end in /v1');
  if (!url.hash) throw new Error('no fragment');

  const params = new URLSearchParams(url.hash.slice(1));
  for (const name of ['key', 'model'] as const) {
    if (params.getAll(name).length !== 1) throw new Error(`${name} must appear exactly once`);
    if (!params.get(name)) throw new Error(`${name} is empty`);
  }
  return {
    baseUrl: `${url.origin}${url.pathname}`,
    model: params.get('model') as string,
    key: params.get('key') as string,
  };
}
```

`URLSearchParams` decodes `+` as a space and percent-escapes as themselves, which is why the
fragment is specified as `application/x-www-form-urlencoded` rather than as raw percent-encoding:
the parser is the platform's, not hand-rolled.
