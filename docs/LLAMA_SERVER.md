# Using Chimera with llama.cpp's llama-server

Chimera and `llama-server` are two separate programs:

| | llama-server | Chimera |
| --- | --- | --- |
| Loads model weights (GGUF), runs inference on CPU/GPU | ✔ | ✘ |
| HTTP API (`/v1/chat/completions`, …) | ✔ | consumes it |
| Conversations, documents, settings, UI | ✘ | ✔ |

You can run them on the same machine or on different ones (e.g. Chimera on a
laptop, llama-server on a GPU box on your LAN).

## 1. Start llama-server

Download or build llama.cpp (<https://github.com/ggml-org/llama.cpp>), then:

```bash
llama-server \
  -m ~/models/qwen2.5-7b-instruct-q4_k_m.gguf \
  --host 127.0.0.1 --port 8080 \
  -c 8192 \          # context window (tokens)
  -ngl 99 \          # offload layers to the GPU, if you have one
  --jinja            # use the model's own chat template
```

Check it's up:

```bash
curl http://127.0.0.1:8080/health        # {"status":"ok"}
curl http://127.0.0.1:8080/v1/models
```

Useful options:

| Option | Why |
| --- | --- |
| `--api-key SECRET` | Require a key. Enter the same key in Chimera's connection settings. |
| `--alias my-model` | The model ID reported by `/v1/models` (otherwise the file path). |
| `-np 2` | Parallel slots, so a title request doesn't queue behind a long answer. |
| `--mmproj mmproj.gguf` | Multimodal projector for vision models (enables image attachments). |
| `--embeddings` | Enables `/v1/embeddings` for the knowledge base. |
| `--host 0.0.0.0` | Listen on the LAN. Only do this with `--api-key` on a trusted network. |

## 2. Add the connection in Chimera

**Models → Add connection**

- **Type:** llama.cpp server
- **Server URL:** `http://127.0.0.1:8080`
  - Chimera in Docker, llama-server on the host: `http://host.docker.internal:8080`
  - llama-server via `docker compose --profile llama`: `http://llama-server:8080`
- **API key:** only if you used `--api-key`
- **Advanced:** API path (default `/v1`), custom auth header, timeout, image
  input override, manual model IDs.

Click **Test connection**. Chimera reports each check separately:

```
✔ health  ready              3 ms
✔ models  1 model(s)         2 ms
✔ props   server properties available
```

A connection is shown as **Connected** only after a check or a request has
actually succeeded. Never-tested connections show **Not tested**; failures show
**Unreachable** with the error.

## 3. Multiple servers

Run one `llama-server` per model on different ports and add each as a
connection:

```bash
llama-server -m general.gguf   --port 8080 &
llama-server -m coder.gguf     --port 8081 &
llama-server -m reasoning.gguf --port 8082 &
llama-server -m vision.gguf --mmproj mmproj.gguf --port 8083 &
```

The model picker groups models by connection, shows each connection's status,
and lets you switch model mid-conversation (the next answer uses the new model).

llama-server's *router mode* (several models behind one server) is also
understood: models report whether they're loaded.

## Exactly which API Chimera uses

| Endpoint | Used for | Required |
| --- | --- | --- |
| `GET /health` | Readiness. `503` while the model is loading is reported as "not ready". | optional |
| `GET /props` | `n_ctx`, `total_slots`, `modalities.vision`, `build_info`, model path. | optional |
| `GET {api_path}/models` | Model discovery (`data[].id`, `meta.n_ctx_train`, router `status`). | yes¹ |
| `POST {api_path}/chat/completions` | Chat with `stream: true` and `stream_options.include_usage`. | yes |
| `POST {api_path}/embeddings` | Knowledge-base embeddings. | optional |

¹ If discovery is unavailable, add model IDs manually in the connection's advanced settings.

Streaming details handled:

- SSE parsed incrementally (arbitrary chunk boundaries, CRLF, comments, multi-line data, split UTF-8).
- `delta.content` and `delta.reasoning_content` (reasoning models), `finish_reason`, `[DONE]`.
- llama-server `timings` (tokens/s, prompt time) and OpenAI `usage`.
- Error events mid-stream and error bodies (`{"error": {"message": …}}`) are shown verbatim.
- Malformed events are skipped (and logged); a stream that ends without
  `finish_reason`/`[DONE]` keeps the partial text and is marked as failed.
- If the server ignores `stream: true` and returns JSON, the full answer is used.

Sampling parameters sent to llama-server (only those you set):
`temperature, top_p, top_k, min_p, max_tokens, stop, repeat_penalty,
presence_penalty, frequency_penalty, seed`. Unset parameters use the server's
defaults (e.g. from `--temp`, `--top-k`).

**Cancellation:** pressing *Stop* closes the HTTP connection to llama-server,
which stops decoding and frees the slot immediately.

**Timeout:** the connection timeout is the maximum wait for the *next* chunk
(including the first token). Raise it for very long prompts on slow hardware.

## Vision

Start a multimodal model with its projector (`--mmproj`). Chimera reads
`modalities.vision` from `/props` and only then enables image attachments, which
are sent as `image_url` data URIs. If your build doesn't report modalities, set
*Image input → Supported* in the connection's advanced settings.

## Embeddings for the knowledge base

Keyword search (SQLite FTS5) works without any embedding model. For semantic
search, run an embedding model, for example:

```bash
llama-server -m nomic-embed-text-v1.5.Q8_0.gguf --embeddings --port 8090 -c 2048
```

Add it as a connection, then in **Settings → Documents** choose that connection
and model. New documents are embedded on upload; use the re-index button for
existing ones.

## Other servers

- **Ollama:** type *Ollama*, URL `http://127.0.0.1:11434` (native `/api/tags`,
  `/api/chat`, `/api/embed`).
- **LM Studio / vLLM / LocalAI / llamafile:** type *OpenAI-compatible*, URL of the
  server, API path `/v1`.
- **Cloud APIs:** also *OpenAI-compatible* with an API key. Nothing remote is
  configured by default; adding one sends your prompts to that service.
