import hashlib
import io
import math
import re
import zlib
from collections.abc import Sequence

from pypdf import PdfReader, PdfWriter

from app.db.schema import EMBEDDING_DIM
from app.llm.client import ChatMessage, Completion, LLMError

_TOKEN = re.compile(r"[a-z0-9]+")


class FakeEmbedder:
    """Deterministic hashed bag-of-words. Shared words give real cosine similarity, so
    retrieval tests are meaningful without downloading a model."""

    model_name = "fake-hash-384"

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.query_calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * EMBEDDING_DIM
        for token in _TOKEN.findall(text.lower()):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest, "big") % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]

    def _check(self) -> None:
        if self.fail:
            raise RuntimeError("embedding backend unavailable")

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self._check()
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self._check()
        self.query_calls += 1
        return self._vector(text)


def _assemble_pdf(objects: dict[int, bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += f"{object_id} 0 obj\n".encode() + objects[object_id] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode() + b"0000000000 65535 f \n"
    for object_id in range(1, size):
        out += f"{offsets[object_id]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_pdf(pages: Sequence[str]) -> bytes:
    """A minimal valid PDF with one line of Helvetica text per page."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] "
            f"/Count {len(pages)} >>"
        ).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, text in zip(page_ids, pages, strict=True):
        content_id = page_id + 1
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({safe}) Tj ET".encode("latin-1")
        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    return _assemble_pdf(objects)


def make_heavy_pdf(stream_bytes: int) -> bytes:
    """A few-KB PDF whose single page decompresses to `stream_bytes` of text operators.
    pypdf's extraction time grows roughly quadratically with that size."""
    line = b"BT /F1 12 Tf 72 720 Td (word) Tj ET\n"
    data = zlib.compress(line * (stream_bytes // len(line)), 9)
    return _assemble_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
            3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
            5: b"<< /Length "
            + str(len(data)).encode()
            + b" /Filter /FlateDecode >>\nstream\n"
            + data
            + b"\nendstream",
        }
    )


def encrypt_pdf(data: bytes, password: str = "secret") -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(data)))
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


class RecordingLLM:
    """Records every prompt so tests can inspect exactly what the model would have seen."""

    model = "recording-llm"

    def __init__(self, reply: str = "Answer from the sources [S1].", *, fail: bool = False) -> None:
        self.reply = reply
        self.fail = fail
        self.calls: list[list[ChatMessage]] = []

    def complete(self, messages: Sequence[ChatMessage]) -> Completion:
        self.calls.append(list(messages))
        if self.fail:
            raise LLMError("provider unreachable")
        return Completion(text=self.reply, model=self.model, response_id="recorded-1")

    def prompt_text(self) -> str:
        return "\n".join(m.content for call in self.calls for m in call)
