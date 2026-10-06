#!/usr/bin/env python3
"""Ask a deployed model-serving listing for the things it exists to do.

    charts/tests/smoke/model-server.py 'https://m.example.com/v1#key=…&model=…' --tools auto
    charts/tests/smoke/model-server.py --base-url http://127.0.0.1:8000/v1 --key … \
        --model llama-3.2-3b-instruct --tools auto --no-evidence

The first form takes the **connection link** the listing emits, which is the
point: the check consumes the exact artefact a person copies out of the
deployment's secrets panel and pastes into the router's *Add external model*
field, so the link format is exercised by the thing that proves the model works.
The parser here is the one `docs/model-connection-link.md` specifies, including
its refusals.

`--tools` says what the listing's card claims, and the check holds it to that:

    auto   `tool_choice: "auto"` must come back with a syntactically valid tool
           call — the acceptance criterion for a model listing, not a feature flag.
    named  only an explicitly named `tool_choice` works; `auto` is not offered.
    none   the model cannot do tool calling, the listing says so, and the
           endpoint must *refuse* a tools request rather than answer it with
           prose a client would mistake for an answer.

Exit code 0 means every claim held.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 180

# One tool, with a required argument that cannot be guessed from the prompt and a
# constrained one. A model that invents the city or drops `unit` has produced
# something that parses and does not work.
WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a given city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. Lisbon"},
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city", "unit"],
        },
    },
}
TOOL_PROMPT = "What is the weather in Lisbon right now? Answer in celsius."

failures: list[str] = []


def ok(message: str) -> None:
    print(f"  ok    {message}")


def fail(message: str) -> None:
    print(f"  FAIL  {message}")
    failures.append(message)


def step(message: str) -> None:
    print(f"\n\033[1m== {message}\033[0m")


def parse_connection_link(link: str) -> tuple[str, str, str]:
    """docs/model-connection-link.md, including every refusal it specifies."""
    url = urllib.parse.urlsplit(link)
    if url.scheme != "https":
        raise ValueError("a connection link is https only")
    if "@" in url.netloc:
        raise ValueError("userinfo in the base URL")
    if url.query:
        if "key" in urllib.parse.parse_qs(url.query, keep_blank_values=True):
            raise ValueError("the key is in the query, where it would be logged")
        raise ValueError("unexpected query")
    if not url.path.endswith("/v1"):
        raise ValueError("the base URL does not end in /v1")
    if not url.fragment:
        raise ValueError("no fragment, so no key and no model")
    params = urllib.parse.parse_qs(url.fragment, keep_blank_values=True)
    for name in ("key", "model"):
        values = params.get(name, [])
        if len(values) != 1:
            raise ValueError(f"{name} must appear exactly once")
        if not values[0]:
            raise ValueError(f"{name} is empty")
    base = urllib.parse.urlunsplit((url.scheme, url.netloc, url.path, "", ""))
    return base, params["model"][0], params["key"][0]


def request(
    url: str, *, key: str | None = None, body: dict | None = None, stream: bool = False
) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            raw = response.read()
            if stream:
                return response.status, raw.decode("utf-8", "replace")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            return err.code, json.loads(raw)
        except Exception:  # noqa: BLE001 - an error body need not be JSON
            return err.code, raw.decode("utf-8", "replace")


def chat(base: str, key: str, model: str, **extra) -> tuple[int, object]:
    body = {"model": model, "max_tokens": 256, "temperature": 0, **extra}
    return request(f"{base}/chat/completions", key=key, body=body)


def check_auth(base: str, key: str, model: str) -> None:
    step("the endpoint is authenticated")
    status, _ = request(f"{base}/models")
    if status == 401:
        ok("/v1/models without a key is 401")
    else:
        fail(f"/v1/models without a key answered {status}: this endpoint is public")

    status, _ = request(f"{base}/models", key=key + "x")
    if status == 401:
        ok("/v1/models with a wrong key is 401")
    else:
        fail(f"/v1/models with a wrong key answered {status}")

    status, payload = request(f"{base}/models", key=key)
    if status != 200:
        fail(f"/v1/models with the key answered {status}: {str(payload)[:200]}")
        return
    ids = [entry.get("id") for entry in (payload or {}).get("data", [])]  # type: ignore[union-attr]
    if model in ids:
        ok(f"/v1/models advertises {model}")
    else:
        fail(f"/v1/models has {ids}, not {model}")


def check_chat(base: str, key: str, model: str) -> None:
    step("a plain completion, with a system message")
    # The system message is not decoration. Gemma 2's own chat template calls
    # raise_exception on one, so an endpoint serving it without an override
    # answers 400 to the first request any ordinary client makes.
    status, payload = chat(
        base,
        key,
        model,
        messages=[
            {"role": "system", "content": "You are terse. Answer in one word."},
            {"role": "user", "content": "What is the capital of Portugal?"},
        ],
    )
    if status != 200:
        fail(f"a system message answered {status}: {str(payload)[:300]}")
        return
    content = (payload["choices"][0]["message"].get("content") or "").strip()  # type: ignore[index]
    if content:
        ok(f"answered: {content[:60]!r}")
    else:
        fail("answered 200 with empty content")


def check_stream(base: str, key: str, model: str) -> None:
    step("streaming")
    body = {
        "model": model,
        "max_tokens": 64,
        "temperature": 0,
        "stream": True,
        "messages": [{"role": "user", "content": "Count from one to five."}],
    }
    status, text = request(f"{base}/chat/completions", key=key, body=body, stream=True)
    if status != 200:
        fail(f"a streamed completion answered {status}")
        return
    chunks = [line for line in str(text).splitlines() if line.startswith("data: ")]
    if len(chunks) > 2 and chunks[-1].strip() == "data: [DONE]":
        ok(f"{len(chunks)} SSE chunks, terminated with [DONE]")
    else:
        fail(f"streamed {len(chunks)} chunks, last {chunks[-1][:40] if chunks else '<none>'!r}")


def validate_tool_call(payload: object, where: str) -> bool:
    choice = payload["choices"][0]  # type: ignore[index]
    calls = choice["message"].get("tool_calls") or []
    if not calls:
        fail(f"{where}: no tool_calls; the model answered "
             f"{str(choice['message'].get('content'))[:120]!r}")
        return False
    call = calls[0]
    name = call.get("function", {}).get("name")
    if name != WEATHER_TOOL["function"]["name"]:
        fail(f"{where}: called {name!r}, not {WEATHER_TOOL['function']['name']!r}")
        return False
    raw = call["function"].get("arguments")
    try:
        args = json.loads(raw)
    except Exception:  # noqa: BLE001
        fail(f"{where}: arguments are not JSON: {str(raw)[:160]}")
        return False
    if not isinstance(args, dict):
        fail(f"{where}: arguments are not an object: {str(raw)[:160]}")
        return False
    missing = [k for k in WEATHER_TOOL["function"]["parameters"]["required"] if k not in args]
    if missing:
        fail(f"{where}: arguments {args} are missing {missing}")
        return False
    if choice.get("finish_reason") not in ("tool_calls", "stop"):
        fail(f"{where}: finish_reason is {choice.get('finish_reason')!r}")
        return False
    ok(f"{where}: {name}({json.dumps(args)}), finish_reason={choice.get('finish_reason')}")
    return True


def check_tools(base: str, key: str, model: str, claim: str) -> None:
    step(f"tool calling — the card claims {claim!r}")
    messages = [{"role": "user", "content": TOOL_PROMPT}]

    status, payload = chat(
        base, key, model, messages=messages, tools=[WEATHER_TOOL], tool_choice="auto"
    )
    if claim == "auto":
        if status != 200:
            fail(f"tool_choice=auto answered {status}: {str(payload)[:300]}")
        else:
            validate_tool_call(payload, "tool_choice=auto")
    elif status == 200 and (payload["choices"][0]["message"].get("tool_calls")):  # type: ignore[index]
        fail("tool_choice=auto produced a tool call, but the card says it cannot. "
             "Either the card is wrong or the engine is guessing")
    else:
        ok(f"tool_choice=auto is refused ({status}), as the card says")

    named = {"type": "function", "function": {"name": WEATHER_TOOL["function"]["name"]}}
    status, payload = chat(
        base, key, model, messages=messages, tools=[WEATHER_TOOL], tool_choice=named
    )
    if claim in ("auto", "named"):
        if status != 200:
            fail(f"a named tool_choice answered {status}: {str(payload)[:300]}")
        else:
            validate_tool_call(payload, "tool_choice=<named>")
    else:
        # `none` still reports what happens, because "the listing says so" has to
        # be checkable: a named tool_choice is grammar-constrained decoding and
        # may well produce well-formed JSON from a model that was never told what
        # the tool is for. That is the thing the card must not sell as support.
        shape = "a tool call" if (
            status == 200 and payload["choices"][0]["message"].get("tool_calls")  # type: ignore[index]
        ) else f"status {status}"
        ok(f"a named tool_choice gives {shape} — see the listing's README for why "
           f"this is not tool-calling support")


def check_evidence(base: str) -> None:
    step("the deployment publishes evidence")
    host = urllib.parse.urlsplit(base)
    url = urllib.parse.urlunsplit((host.scheme, host.netloc, "/.well-known/swarm-evidence", "", ""))
    status, payload = request(url)
    if status != 200:
        fail(f"{url} answered {status}: a router cannot attest what it cannot fetch")
        return
    if isinstance(payload, dict) and payload.get("jws"):
        ok("/.well-known/swarm-evidence serves a JWS")
    else:
        fail(f"/.well-known/swarm-evidence has no jws: {str(payload)[:200]}")



# The test-vector table from docs/model-connection-link.md, executable. The spec
# is the contract between this repository (which produces links) and the
# Confidential Router's admin console (which parses them), so the vectors should
# not only be prose. `--self-test` needs no endpoint and runs in CI.
LINK_VECTORS: list[tuple[str, str | None, tuple[str, str, str] | None]] = [
    ("https://m.example.com/v1#key=sk-abc&model=llama-3.2-3b-instruct", None,
     ("https://m.example.com/v1", "llama-3.2-3b-instruct", "sk-abc")),
    ("https://m.example.com/v1#model=gemma-2-2b-it&key=sk-abc", None,
     ("https://m.example.com/v1", "gemma-2-2b-it", "sk-abc")),
    ("https://m.example.com:8443/v1#key=sk-abc&model=m", None,
     ("https://m.example.com:8443/v1", "m", "sk-abc")),
    ("https://m.example.com/v1#key=a%26b%3Dc&model=m", None,
     ("https://m.example.com/v1", "m", "a&b=c")),
    ("https://m.example.com/v1#key=a+b&model=m", None,
     ("https://m.example.com/v1", "m", "a b")),
    ("https://m.example.com/v1#key=sk-abc&model=m&price=1", None,
     ("https://m.example.com/v1", "m", "sk-abc")),
    ("https://m.example.com/v1?key=sk-abc#model=m", "key is in the query", None),
    ("https://m.example.com/v1#key=sk-abc", "model must appear exactly once", None),
    ("https://m.example.com/v1#model=m", "key must appear exactly once", None),
    ("https://m.example.com/v1#key=&model=m", "key is empty", None),
    ("https://m.example.com/v1#key=a&key=b&model=m", "key must appear exactly once", None),
    ("http://m.example.com/v1#key=sk-abc&model=m", "https only", None),
    ("https://m.example.com/v1", "no fragment", None),
    ("https://m.example.com/#key=sk-abc&model=m", "does not end in /v1", None),
    ("https://user:pw@m.example.com/v1#key=sk-abc&model=m", "userinfo", None),
]


def self_test() -> int:
    step("docs/model-connection-link.md test vectors")
    for link, expected_error, expected in LINK_VECTORS:
        shown = link if len(link) < 62 else link[:59] + "..."
        try:
            got = parse_connection_link(link)
        except ValueError as err:
            if expected_error is None:
                fail(f"{shown} was rejected: {err}")
            elif expected_error in str(err):
                ok(f"{shown} -> rejected ({expected_error})")
            else:
                fail(f"{shown} was rejected for the wrong reason: {err}")
            continue
        if expected_error is not None:
            fail(f"{shown} was accepted; it should fail on {expected_error}")
        elif got != expected:
            fail(f"{shown} parsed as {got}, expected {expected}")
        else:
            ok(f"{shown} -> {got[0]} model={got[1]}")
    print()
    if failures:
        print(f"\033[1m{len(failures)} failed\033[0m")
        return 1
    print("\033[1mall vectors agree with the spec\033[0m")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("link", nargs="?", help="the listing's connection link")
    parser.add_argument("--base-url", help="instead of a link: http(s)://host/v1")
    parser.add_argument("--key")
    parser.add_argument("--model")
    parser.add_argument("--tools", choices=["auto", "named", "none"],
                        help="what the listing's card claims about tool calling")
    parser.add_argument("--self-test", action="store_true",
                        help="run the connection-link test vectors and exit; needs no endpoint")
    parser.add_argument("--no-evidence", action="store_true",
                        help="skip the evidence check (an in-cluster or local endpoint has none)")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.tools:
        parser.error("--tools is required unless --self-test is given")

    if args.link:
        try:
            base, model, key = parse_connection_link(args.link)
        except ValueError as err:
            print(f"  FAIL  the connection link is not valid: {err}")
            return 1
        print(f"connection link parsed: {base} model={model} key=<{len(key)} chars>")
    elif args.base_url and args.key and args.model:
        base, model, key = args.base_url.rstrip("/"), args.model, args.key
        print(f"explicit endpoint: {base} model={model}")
    else:
        parser.error("give a connection link, or --base-url with --key and --model")

    check_auth(base, key, model)
    check_chat(base, key, model)
    check_stream(base, key, model)
    check_tools(base, key, model, args.tools)
    if not args.no_evidence:
        check_evidence(base)

    print()
    if failures:
        print(f"\033[1m{len(failures)} failed\033[0m")
        for message in failures:
            print(f"  - {message}")
        return 1
    print("\033[1mall checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
