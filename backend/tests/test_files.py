import io

from pypdf import PdfWriter

from .conftest import read_sse

PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000100e221bc330000000049454e44ae426082"
)


def upload(client, name, data, mime="application/octet-stream"):
    return client.post("/api/files", files={"file": (name, io.BytesIO(data), mime)})


def make_pdf(text: str) -> bytes:
    # Minimal hand-written PDF with one text object (pypdf can extract it).
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def test_text_file_extracted(client):
    resp = upload(client, "notes.md", b"# Title\n\nSome notes.")
    assert resp.status_code == 201
    meta = resp.json()
    assert meta["kind"] == "text" and meta["char_count"] > 0 and "text" not in meta
    full = client.get(f"/api/files/{meta['id']}").json()
    assert full["text"].startswith("# Title")


def test_pdf_text_extracted(client):
    resp = upload(client, "doc.pdf", make_pdf("Hello from a PDF"))
    assert resp.status_code == 201, resp.text
    text = client.get(f"/api/files/{resp.json()['id']}").json()["text"]
    assert "Hello from a PDF" in text


def test_blank_pdf_is_a_clear_error(client):
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    resp = upload(client, "scan.pdf", buf.getvalue())
    assert resp.status_code == 422 and resp.json()["detail"]["code"] == "pdf_no_text"


def test_rejects_unsupported_and_spoofed_files(client):
    assert upload(client, "run.exe", b"MZ\x90\x00").status_code == 422
    assert upload(client, "fake.pdf", b"not a pdf").status_code == 422
    assert upload(client, "fake.png", b"<script>alert(1)</script>").status_code == 422
    assert upload(client, "bin.txt", b"\x00\x01\x02binary").status_code == 422
    assert upload(client, "empty.txt", b"").status_code == 422


def test_size_limit_enforced(client):
    client.patch("/api/settings", json={"max_attachment_mb": 1})
    resp = upload(client, "big.txt", b"a" * (1024 * 1024 + 10))
    assert resp.status_code == 413


def test_long_text_truncated_to_limit(client):
    client.patch("/api/settings", json={"max_attachment_chars": 1000})
    meta = upload(client, "long.txt", b"x" * 5000).json()
    assert meta["truncated"] is True and meta["char_count"] == 1000


def test_path_traversal_filename_sanitized(client):
    meta = upload(client, "../../etc/passwd.txt", b"hello").json()
    assert meta["filename"] == "passwd.txt"


def test_attachment_content_reaches_model(client, conversation, mock):
    fid = upload(client, "data.csv", b"name,age\nada,36\n").json()["id"]
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "summarize", "attachments": [fid]}) as r:
        events = read_sse(r)
    start = next(d for e, d in events if e == "start")
    assert start["user_message"]["attachments"][0]["filename"] == "data.csv"
    sent = mock.app.state.requests[0]["messages"][-1]["content"]
    assert '<file name="data.csv">' in sent and "ada,36" in sent and sent.endswith("summarize")


def test_images_require_vision_capable_model(client, conversation, mock):
    fid = upload(client, "pic.png", PNG_1PX).json()["id"]
    resp = client.post(f"/api/conversations/{conversation['id']}/messages", json={"content": "what is this", "attachments": [fid]})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "vision_unsupported"

    mock.app.state.vision = True
    test = client.post(f"/api/providers/{conversation['provider_id']}/test").json()
    assert test["capabilities"]["vision"] is True
    with client.stream("POST", f"/api/conversations/{conversation['id']}/messages", json={"content": "what is this", "attachments": [fid]}) as r:
        events = read_sse(r)
    done = next(d for e, d in events if e == "done")
    assert "I can see 1 image(s)" in done["message"]["content"]
    parts = mock.app.state.requests[-2]["messages"][-1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_image_served_inline_with_safe_headers(client):
    fid = upload(client, "pic.png", PNG_1PX).json()["id"]
    resp = client.get(f"/api/files/{fid}/content")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/png"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert client.delete(f"/api/files/{fid}").status_code == 204
    assert client.get(f"/api/files/{fid}/content").status_code == 404
