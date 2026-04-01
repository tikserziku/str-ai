"""
RAG Engine for Lithuanian Construction Regulations (STR).

Loads parsed STR data into ChromaDB, provides semantic search
and LLM-powered Q&A with exact citations.
"""

import json
import os
from pathlib import Path

import chromadb
from groq import Groq

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_PATH = BASE_DIR / "data" / "str_parsed.json"
CHROMA_DIR = str(BASE_DIR / "chroma_db")
COLLECTION_NAME = "str_regulations"


# ---------------------------------------------------------------------------
# ChromaDB setup
# ---------------------------------------------------------------------------

def _get_collection() -> chromadb.Collection:
    """Return (or create) the persistent ChromaDB collection."""
    client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )
    return collection


def load_data(json_path: str | Path | None = None, force: bool = False) -> int:
    """
    Parse *str_parsed.json* and upsert every punkt into ChromaDB.

    Parameters
    ----------
    json_path : path to the JSON file (default: data/str_parsed.json)
    force : if True, delete old collection and re-index

    Returns
    -------
    int – number of documents indexed
    """
    json_path = Path(json_path) if json_path else DATA_PATH
    if not json_path.exists():
        raise FileNotFoundError(f"Data file not found: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    client = chromadb.PersistentClient(path=CHROMA_DIR)
    if force:
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    # Prepare batches (with unique suffix for duplicate punkt numbers)
    ids, documents, metadatas = [], [], []
    seen_ids = {}
    for rec in records:
        base_id = f"{rec['str_number']}__p{rec['punkt']}"
        if base_id in seen_ids:
            seen_ids[base_id] += 1
            doc_id = f"{base_id}_{seen_ids[base_id]}"
        else:
            seen_ids[base_id] = 0
            doc_id = base_id
        ids.append(doc_id)
        documents.append(rec["text"])
        metadatas.append({
            "str_number": rec["str_number"],
            "str_title": rec.get("str_title", ""),
            "punkt": rec["punkt"],
            "status": rec.get("status", "galioja"),
            "expired_date": rec.get("expired_date") or "",
            "source_url": rec.get("source_url", ""),
        })

    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
    print(f"[RAG] Indexed {len(ids)} documents into '{COLLECTION_NAME}'")
    return len(ids)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(query: str, top_k: int = 5) -> list[dict]:
    """
    Semantic search over STR regulations.

    Returns a list of dicts with keys:
        id, str_number, punkt, status, text, score, source_url
    """
    collection = _get_collection()
    if collection.count() == 0:
        load_data()

    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        hits.append({
            "id": results["ids"][0][i],
            "str_number": meta["str_number"],
            "str_title": meta.get("str_title", ""),
            "punkt": meta["punkt"],
            "status": meta["status"],
            "expired_date": meta.get("expired_date", ""),
            "text": results["documents"][0][i],
            "distance": results["distances"][0][i],
            "source_url": meta.get("source_url", ""),
        })
    return hits


# ---------------------------------------------------------------------------
# LLM Answer (Groq – Llama 4 Scout)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Tu esi Lietuvos statybos reglamentų (STR) ekspertas-asistentas.

TAISYKLĖS:
1. Atsakyk TIKTAI pagal pateiktus STR punktus. Jei informacijos nepakanka — pasakyk.
2. Kiekviename atsakyme PRIVALOMA nurodyti:
   - Tikslią citatą iš STR (kabutėse)
   - STR numerį ir punktą (pvz. STR 1.01.08:2002, 12.1 p.)
   - Statusą: ✅ galioja ARBA ⚠️ NETEKO GALIOS (jei neteko — įspėk vartotoją!)
   - Šaltinio nuorodą
3. Atsakyk lietuvių kalba.
4. Būk tikslus ir trumpas.
"""


def _build_context(hits: list[dict]) -> str:
    """Format search hits into a context block for the LLM."""
    parts = []
    sources = []
    for i, h in enumerate(hits, 1):
        status_tag = "✅ GALIOJA" if h["status"] == "galioja" else "⚠️ NETEKO GALIOS"
        parts.append(
            f"[{i}] {h['str_number']}, {h['punkt']} p. [{status_tag}]\n"
            f"{h['text']}"
        )
        sources.append(f"[{i}] {h['str_number']}, {h['punkt']} p. — {h['source_url']}")
    result = "\n\n".join(parts)
    result += "\n\n---\nŠaltiniai:\n" + "\n".join(sources)
    return result


def answer(query: str, top_k: int = 5) -> dict:
    """
    Search relevant STR clauses and generate an LLM answer.
    Supports NVIDIA Nemotron (primary) and Groq (fallback).
    """
    hits = search(query, top_k=top_k)
    context = _build_context(hits)

    user_message = (
        f"Kontekstas (STR punktai):\n\n{context}\n\n"
        f"---\nVartotojo klausimas: {query}"
    )

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    # Try NVIDIA Nemotron first
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    if nvidia_key:
        try:
            from openai import OpenAI
            client = OpenAI(
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nvidia_key,
            )
            chat = client.chat.completions.create(
                model="nvidia/llama-3.1-nemotron-70b-instruct",
                messages=messages,
                temperature=0.2,
                max_tokens=1024,
            )
            return {
                "answer": chat.choices[0].message.content,
                "sources": hits,
                "model": "nvidia/nemotron-70b",
            }
        except Exception:
            pass  # fallback to Groq

    # Try Groq
    groq_key = os.environ.get("GROQ_API_KEY", "")
    if groq_key:
        try:
            client = Groq(api_key=groq_key)
            chat = client.chat.completions.create(
                model="llama-4-scout-17b-16e-instruct",
                messages=messages,
                temperature=0.2,
                max_tokens=1024,
            )
            return {
                "answer": chat.choices[0].message.content,
                "sources": hits,
                "model": "groq/llama-4-scout",
            }
        except Exception:
            pass

    # No LLM available — return raw results
    return {
        "answer": _build_context(hits),
        "sources": hits,
        "model": None,
    }


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------

def main():
    import sys

    # Force UTF-8 output on Windows
    if sys.stdout.encoding != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")

    if len(sys.argv) < 2:
        print("Usage: python rag_engine.py <command> [args]")
        print("Commands:")
        print("  index              - Index data/str_parsed.json into ChromaDB")
        print("  search <query>     - Semantic search")
        print("  answer <query>     - Search + LLM answer (needs GROQ_API_KEY)")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "index":
        n = load_data(force=True)
        print(f"Done. {n} documents indexed.")

    elif cmd == "search":
        q = " ".join(sys.argv[2:])
        if not q:
            print("Provide a search query.")
            sys.exit(1)
        hits = search(q)
        for h in hits:
            status_tag = "GALIOJA" if h["status"] == "galioja" else "!!! NETEKO GALIOS"
            print(f"\n--- {h['str_number']}, {h['punkt']} p. [{status_tag}] (dist={h['distance']:.4f})")
            print(f"    {h['text'][:200]}")
            print(f"    {h['source_url']}")

    elif cmd == "answer":
        q = " ".join(sys.argv[2:])
        if not q:
            print("Provide a query.")
            sys.exit(1)
        result = answer(q)
        print("\n" + "=" * 60)
        print(result["answer"])
        print("=" * 60)
        if result["model"]:
            print(f"\nModel: {result['model']}")
        print(f"Sources: {len(result['sources'])} hits")

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
