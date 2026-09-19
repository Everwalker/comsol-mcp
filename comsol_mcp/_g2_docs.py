"""Version separated, offline help index used by the G2 documentation tools."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import html
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterable, Mapping

from ._execution_contract import ExecutionContractError


_VERSION_RE = re.compile(r"(?:COMSOL|comsol|version|v)?[^0-9]*(6[.]3|6[.]4)(?:[^0-9]|$)")
_TEXT_SUFFIXES = {".txt", ".md", ".rst", ".html", ".htm", ".xml", ".json", ".java"}
_SECRET_PARTS = {".phase1-private", ".phase2-private", "control-private", ".ssh", ".aws", ".gnupg"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def infer_version(value: str, fallback: str | None = None) -> str:
    match = _VERSION_RE.search(value or "")
    if match:
        return match.group(1)
    return str(fallback or "unknown")


def _clean_text(path: Path) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        return ""
    text = raw.decode("utf-8", errors="replace")
    if path.suffix.lower() in {".html", ".htm", ".xml"}:
        text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _chunks(text: str, *, max_chars: int = 6000) -> Iterable[tuple[int, str]]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not paragraphs:
        paragraphs = [text] if text else []
    buffer: list[str] = []
    size = 0
    offset = 0
    for paragraph in paragraphs:
        if buffer and size + len(paragraph) + 2 > max_chars:
            body = "\n\n".join(buffer)
            yield offset, body
            offset += len(body) + 2
            buffer, size = [], 0
        buffer.append(paragraph)
        size += len(paragraph) + 2
    if buffer:
        yield offset, "\n\n".join(buffer)


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    document_ref: str
    source: str
    source_sha256: str
    content_sha256: str
    version: str
    product: str
    title: str
    offset: int
    content: str

    def as_dict(self, *, include_content: bool = False) -> dict[str, Any]:
        out = {
            "document_ref": self.document_ref, "source": self.source,
            "source_sha256": self.source_sha256, "content_sha256": self.content_sha256,
            "version": self.version, "product": self.product, "title": self.title,
            "offset": self.offset,
        }
        if include_content:
            out["content"] = self.content
        return out


class OfflineDocsIndex:
    """Small SQLite FTS-like index with exact version isolation.

    The database stores source and content hashes so a returned fragment is
    traceable to a specific local file.  Text is never interpreted as a
    command; it is only indexed and returned as data.
    """

    def __init__(self, database: str | Path, *, allowed_roots: Iterable[str | Path] | None = None):
        self.database = Path(database)
        roots = list(allowed_roots or [])
        if not roots:
            # Standalone callers/tests may place the index and fixture corpus
            # under a temporary project root.  ManagedBackend always passes
            # its explicit project and official-help roots.
            roots = [self.database.parent]
        self.allowed_roots = tuple(Path(path).resolve() for path in roots if Path(path).exists())
        if not self.allowed_roots:
            raise ExecutionContractError("UNAVAILABLE", "no approved offline documentation root is available")
        self.database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS documents(
                document_ref TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                version TEXT NOT NULL,
                product TEXT NOT NULL,
                title TEXT NOT NULL,
                offset INTEGER NOT NULL,
                content TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS documents_version ON documents(version, product);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _source_files(self, source: str) -> list[Path]:
        path = Path(source).expanduser()
        if not path.exists():
            raise ExecutionContractError("UNAVAILABLE", f"documentation source is unavailable: {source}")
        if path.is_symlink():
            raise ExecutionContractError("PERMISSION_DENIED", "documentation source may not be a symlink")
        resolved = path.resolve()
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            raise ExecutionContractError("PERMISSION_DENIED", "documentation source is outside approved project/help roots")
        if any(part in _SECRET_PARTS for part in resolved.parts):
            raise ExecutionContractError("PERMISSION_DENIED", "documentation source enters a private directory")
        if resolved.is_file():
            return [resolved] if resolved.suffix.lower() in _TEXT_SUFFIXES else []
        rows: list[Path] = []
        for item in sorted(resolved.rglob("*")):
            if item.is_symlink() or not item.is_file() or item.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            child = item.resolve()
            if not any(child == root or root in child.parents for root in self.allowed_roots):
                continue
            if any(part in _SECRET_PARTS for part in child.parts):
                continue
            rows.append(child)
        return rows

    def index(self, *, runtime_id: str, sources: list[str], version: str | None = None, product: str = "COMSOL") -> dict[str, Any]:
        if not isinstance(runtime_id, str) or not runtime_id.strip():
            raise ExecutionContractError("INVALID_REQUEST", "runtime_id is required")
        if not isinstance(sources, list) or not all(isinstance(item, str) and item for item in sources):
            raise ExecutionContractError("INVALID_REQUEST", "sources must be a non-empty array of paths")
        selected_version = infer_version(runtime_id, version)
        indexed, skipped = [], []
        for source in sources:
            files = self._source_files(source)
            if not files:
                skipped.append({"source": source, "reason": "no_supported_text_files"})
            for path in files:
                # A source can contain a version marker that is more specific
                # than the runtime alias.  Never merge 6.3 and 6.4 rows.
                file_version = infer_version(str(path), selected_version)
                text = _clean_text(path)
                if not text:
                    skipped.append({"source": str(path), "reason": "empty_or_binary"})
                    continue
                source_hash, content_hash = _sha256(path), hashlib.sha256(text.encode("utf-8")).hexdigest()
                title = next((line.strip("# ") for line in text.splitlines() if line.strip()), path.name)
                self.db.execute("DELETE FROM documents WHERE source=? AND version=?", (str(path), file_version))
                for offset, chunk in _chunks(text):
                    ref = f"docs:{file_version}:{content_hash[:16]}:{offset}"
                    self.db.execute(
                        "INSERT OR REPLACE INTO documents(document_ref,source,source_sha256,content_sha256,version,product,title,offset,content) VALUES(?,?,?,?,?,?,?,?,?)",
                        (ref, str(path), source_hash, content_hash, file_version, product, title, offset, chunk),
                    )
                    indexed.append(ref)
        self.db.commit()
        return {"status": "SUCCEEDED", "runtime_id": runtime_id, "version": selected_version,
                "indexed": indexed, "indexed_count": len(indexed), "skipped": skipped}

    def _row(self, row: sqlite3.Row, *, include_content: bool = False) -> IndexedDocument:
        return IndexedDocument(document_ref=row["document_ref"], source=row["source"], source_sha256=row["source_sha256"],
                               content_sha256=row["content_sha256"], version=row["version"], product=row["product"],
                               title=row["title"], offset=int(row["offset"]), content=row["content"])

    def search(self, *, query: str, version: str, product: str | None = None, limit: int = 10) -> dict[str, Any]:
        if not isinstance(query, str) or not query.strip() or not isinstance(version, str) or not version.strip():
            raise ExecutionContractError("INVALID_REQUEST", "query and version are required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ExecutionContractError("INVALID_REQUEST", "limit must be between 1 and 100")
        where = ["version=?"]
        params: list[Any] = [version]
        if product:
            where.append("product=?"); params.append(product)
        available = self.db.execute(f"SELECT COUNT(*) FROM documents WHERE {' AND '.join(where)}", params).fetchone()[0]
        if not available:
            return {"status": "UNAVAILABLE", "version": version, "results": [], "query": query}
        # Tokenized LIKE keeps this dependency-free and handles punctuation in
        # Java API names without constructing an unsafe SQL FTS expression.
        terms = [term for term in re.findall(r"[\w.:-]+", query.lower()) if term]
        score_sql = " + ".join(["(CASE WHEN lower(title) LIKE ? THEN 4 ELSE 0 END)", "(CASE WHEN lower(content) LIKE ? THEN 1 ELSE 0 END)"] * max(1, len(terms)))
        score_params: list[Any] = []
        for term in terms or [query.lower()]:
            score_params.extend([f"%{term}%", f"%{term}%"])
        # Filter zero-score rows before publication.  Without this predicate
        # every query returned an arbitrary document whenever that version had
        # any indexed content, which falsely turned a missing concept into a
        # successful help lookup.
        sql = f"WITH ranked AS (SELECT *, ({score_sql}) AS relevance FROM documents WHERE {' AND '.join(where)}) " \
              "SELECT * FROM ranked WHERE relevance > 0 ORDER BY relevance DESC, document_ref LIMIT ?"
        # SQLite binds placeholders in the CTE's score expression, then its
        # version/product predicates, then the outer LIMIT.
        rows = self.db.execute(sql, score_params + params + [limit]).fetchall()
        results = []
        for row in rows:
            doc = self._row(row)
            results.append({**doc.as_dict(), "snippet": doc.content[:1000], "relevance": row["relevance"]})
        return {"status": "SUCCEEDED" if results else "NOT_FOUND", "version": version, "results": results, "query": query}

    def get(self, *, document_ref: str, section: str | None = None, offset: int = 0, length: int = 6000) -> dict[str, Any]:
        if not isinstance(document_ref, str) or not document_ref:
            raise ExecutionContractError("INVALID_REQUEST", "document_ref is required")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= 100_000:
            raise ExecutionContractError("INVALID_REQUEST", "offset/length are invalid")
        row = self.db.execute("SELECT * FROM documents WHERE document_ref=?", (document_ref,)).fetchone()
        if not row:
            return {"status": "NOT_FOUND", "document_ref": document_ref, "content": ""}
        doc = self._row(row)
        content = doc.content
        if section:
            match = re.search(re.escape(section), content, flags=re.I)
            if not match:
                return {"status": "NOT_FOUND", "document_ref": document_ref, "content": ""}
            offset += match.start()
        return {"status": "SUCCEEDED", **doc.as_dict(), "content": content[offset:offset + length]}

    def close_and_reopen(self) -> None:
        self.db.close()
        self.db = sqlite3.connect(self.database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
