FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Index STR data into ChromaDB at build time
RUN python src/rag_engine.py index

EXPOSE 7860

CMD ["python", "app.py"]
