# STR AI — MVP

AI assistant for Lithuanian building regulations (STR).
RAG-based search with validity checking.

## Architecture
```
e-tar.lt → parser → ChromaDB → RAG Engine → Flask API → Web/Telegram
```

## Setup
```bash
pip install -r requirements.txt
python app.py
```
