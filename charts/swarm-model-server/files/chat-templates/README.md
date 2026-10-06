# Chat templates

A model's chat template is Jinja, and Jinja is `{{ … }}`. That is why these are
files in the chart rather than a value in a listing.

A listing's values are scanned by the marketplace's own expression interpolator,
which parses everything between `{{` and `}}` as *its* language — seven
namespaces, a dotted path, one optional `| json` filter, no function calls and no
arithmetic. A chat template inlined in `deployment.values.base` therefore fails
to publish with one error per tag: `"bos_token" is not a known namespace`,
`"raise_exception(…)" is not a valid reference`, `"trim + '<end_of_turn>' " is
not a known filter`. It renders, it lints, it passes the JSON Schema, and the
stand refuses it. That is SUP-230, and `apps/tests/expressions.py` now catches it
in CI.

Helm is the second interpolator with a claim on `{{ }}`, and `.Files.Get` is what
answers both: it reads a file's bytes and does not render them as a template. So
the template reaches the ConfigMap verbatim, and nothing anywhere has to escape a
brace. Do not try to escape them — two layers of escaping is how this gets
rediscovered a third time.

A listing selects one by name:

```yaml
model:
  chatTemplateFile: gemma-2.jinja
```

The chart refuses to render if the named file is not here, and refuses a listing
that tries to pass a template inline.

| File | Why it exists |
|---|---|
| `gemma-2.jinja` | Gemma 2's own template calls `raise_exception('System role not supported')`, so an endpoint serving it answers the first request from any OpenAI client with a 400. This is that template with one change — a leading system message is folded into the first user turn, which is what Google's guidance says to do for a model with no system role. Nothing else differs. |

Most models need nothing here: leave `model.chatTemplateFile` empty and the
engine uses the template that ships with the weights, which is the one the
manifest pinned by sha256.
