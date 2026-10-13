import io

from chimera.services.knowledge import chunk_text, fts_query

from .conftest import read_sse

DOC = (
    "The Zephyr reactor uses molten salt for cooling. Its core temperature is 650 degrees.\n\n"
    "Maintenance happens every 18 months and requires a full shutdown.\n\n"
    "The control room is staffed by four operators at all times."
)


def test_chunking_respects_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(2000))
    chunks = chunk_text(text, size=500, overlap=100)
    assert all(len(c) <= 600 for c in chunks)
    assert len(chunks) > 10
    # overlap: the start of chunk n+1 appears at the end of chunk n
    assert chunks[1].split()[0] in chunks[0]
    assert chunk_text("") == []


def test_fts_query_is_safe():
    assert fts_query('"; DROP TABLE x; --') == '"drop" OR "table"'
    assert fts_query("?!") is None


def make_collection(client):
    col = client.post("/api/collections", json={"name": "Reactor docs"}).json()
    resp = client.post(f"/api/collections/{col['id']}/documents", files={"file": ("reactor.md", io.BytesIO(DOC.encode()), "text/markdown")})
    assert resp.status_code == 201, resp.text
    return col, resp.json()


def test_keyword_search_without_embeddings(client):
    col, doc = make_collection(client)
    assert doc["chunk_count"] >= 1 and doc["embedding_model"] is None
    hits = client.post(f"/api/collections/{col['id']}/search", json={"query": "how often is maintenance?"}).json()
    assert hits and "Maintenance" in hits[0]["text"] and hits[0]["method"] == "keyword"


def test_semantic_search_with_local_embeddings(client, provider):
    client.patch("/api/settings", json={"knowledge": {"embedding_provider_id": provider["id"], "embedding_model": "qwen2.5-7b-instruct-q4_k_m.gguf", "chunk_size": 200, "chunk_overlap": 0}})
    col, doc = make_collection(client)
    assert doc["embedding_model"] == "qwen2.5-7b-instruct-q4_k_m.gguf" and doc["warning"] is None
    hits = client.post(f"/api/collections/{col['id']}/search", json={"query": "control room operators"}).json()
    assert "operators" in hits[0]["text"]
    assert hits[0]["method"] in ("hybrid", "semantic")


def test_embedding_failure_degrades_gracefully(client, provider):
    client.patch(f"/api/providers/{provider['id']}", json={"base_url": "http://127.0.0.1:9"})
    client.patch("/api/settings", json={"knowledge": {"embedding_provider_id": provider["id"], "embedding_model": "m"}})
    col, doc = make_collection(client)
    assert doc["embedding_model"] is None and "Keyword search only" in doc["warning"]
    assert client.post(f"/api/collections/{col['id']}/search", json={"query": "reactor"}).json()


def test_rag_injects_context_and_returns_sources(client, conversation, mock):
    col, _ = make_collection(client)
    client.patch(f"/api/conversations/{conversation['id']}", json={"collection_ids": [col["id"]]})
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "What is the core temperature of the reactor?"}) as r:
        events = read_sse(r)
    sources = next(d for e, d in events if e == "sources")["sources"]
    assert sources[0]["filename"] == "reactor.md" and sources[0]["n"] == 1
    system = mock.app.state.requests[0]["messages"][0]
    assert system["role"] == "system" and "650 degrees" in system["content"] and "[1]" in system["content"]
    done = next(d for e, d in events if e == "done")
    assert done["message"]["sources"][0]["filename"] == "reactor.md"


def test_delete_document_and_collection(client):
    col, doc = make_collection(client)
    assert client.delete(f"/api/documents/{doc['id']}").status_code == 204
    assert client.post(f"/api/collections/{col['id']}/search", json={"query": "reactor"}).json() == []
    assert client.delete(f"/api/collections/{col['id']}").status_code == 204
    assert client.get(f"/api/collections/{col['id']}").status_code == 404
