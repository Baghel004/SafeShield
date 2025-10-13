# backend-python/app.py
import os
import json
import threading
import traceback
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel

import logging
import re
import pdfplumber
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer
import nltk
nltk.download('punkt', quiet=True)

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ml-api")

MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE, CHUNK_OVERLAP, TOP_K = 1200, 300, 8
MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100MB limit

# persistence
PERSIST_DIR = os.path.join(os.getcwd(), 'persist')
INDEX_PATH = os.path.join(PERSIST_DIR, 'index.faiss')
CORPUS_PATH = os.path.join(PERSIST_DIR, 'corpus.json')

def clean_text(t): return re.sub(r"\s+", " ", t).strip()

def extract_pdf_chunks(pdf_path):
    chunks = []
    with pdfplumber.open(pdf_path) as pdf:
        for pageno, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            text = clean_text(text)
            if not text:
                continue
            start = 0
            while start < len(text):
                end = start + CHUNK_SIZE
                chunk = text[start:end]
                chunks.append({
                    "pdf_name": os.path.basename(pdf_path),
                    "page_no": pageno,
                    "chunk_text": chunk
                })
                start = end - CHUNK_OVERLAP
                if start >= len(text):
                    break
    return chunks

def build_index_from_pdfs(pdf_paths):
    corpus = []
    for path in pdf_paths:
        corpus.extend(extract_pdf_chunks(path))
    if not corpus:
        # handle empty PDF gracefully
        model = SentenceTransformer(MODEL_NAME)
        index = None
        return model, index, corpus

    model = SentenceTransformer(MODEL_NAME)
    texts = [c["chunk_text"] for c in corpus]
    embs = model.encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=True)
    dim = embs.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(embs)
    return model, index, corpus

def query_index(q, model, index, corpus, k=TOP_K):
    if model is None or index is None:
        return []
    qv = model.encode([q], convert_to_numpy=True, normalize_embeddings=True)
    D,I = index.search(qv, k)
    res=[]
    for d,i in zip(D[0],I[0]):
        if 0<=i<len(corpus):
            r = corpus[i].copy(); r["score"]=float(d); res.append(r)
    return res

def synthesize_answer(query, retrieved, max_excerpt_chars=1200, top_n=5):
    if not retrieved:
        return {"answer":"No relevant clauses found.","refs":[], "full_texts":[]}

    answer_text = " ".join([r["chunk_text"][:300].strip() + "..." for r in retrieved[:5]])

    refs = []
    full_texts = []
    for r in retrieved[:top_n]:
        chunk = r["chunk_text"]
        excerpt = chunk[:max_excerpt_chars].strip()
        clause_match = None
        m = re.search(r'(?i)(clause|section|article)\s*[:#]?\s*([0-9]+(?:\.[0-9]+)*)', chunk)
        clause_text = None
        if m:
            clause_match = m.group(0)
            idx = m.start()
            start = max(0, idx - 300)
            end = min(len(chunk), m.end() + 300)
            clause_text = chunk[start:end].strip()

        refs.append({
            "policy": r.get("pdf_name"),
            "page": r.get("page_no"),
            "score": round(r.get("score", 0), 3),
            "excerpt": re.sub(r'\s+', ' ', excerpt)
        })
        full_texts.append({
            "policy": r.get("pdf_name"),
            "page": r.get("page_no"),
            "full_text": re.sub(r'\s+', ' ', chunk),
            "clause_found": bool(clause_match),
            "clause_text": clause_text
        })

    return {"answer": answer_text, "refs": refs, "full_texts": full_texts}

def recommend_policy(user_need, model, index, corpus, top_n=3):
    res = query_index(user_need, model, index, corpus, k=30)
    scores={}
    for r in res:
        scores.setdefault(r["pdf_name"],[]).append(r["score"])
    if not scores:
        return []
    avg={p:np.mean(sorted(s,reverse=True)[:5]) for p,s in scores.items()}
    ranked=sorted(avg.items(),key=lambda x:x[1],reverse=True)
    recs=[]
    for i,(name,score) in enumerate(ranked[:top_n],1):
        related=[r for r in res if r["pdf_name"]==name][:2]
        snips=[clean_text(r["chunk_text"][:250]) for r in related]
        recs.append({"rank":i,"policy":name,"score":round(score,3),"snips":snips})
    return recs

def add_uploaded_policy(path, corpus, model, index):
    new_chunks = extract_pdf_chunks(path)
    if not new_chunks:
        return corpus, index
    new_embs = model.encode([c["chunk_text"] for c in new_chunks], convert_to_numpy=True, normalize_embeddings=True)
    if index is None:
        dim = new_embs.shape[1]
        index = faiss.IndexFlatIP(dim)
    index.add(new_embs)
    corpus.extend(new_chunks)
    return corpus, index

# FastAPI app and state
app = FastAPI(title="ML Index API")
state = { "model": None, "index": None, "corpus": [] }
state_lock = threading.Lock()

def save_state():
    try:
        os.makedirs(PERSIST_DIR, exist_ok=True)
        # Save corpus as JSON
        with open(CORPUS_PATH, 'w', encoding='utf-8') as f:
            json.dump(state["corpus"], f, ensure_ascii=False)
        # Save FAISS index
        if state["index"] is not None:
            faiss.write_index(state["index"], INDEX_PATH)
        logging.info("State persisted to %s", PERSIST_DIR)
    except Exception as e:
        logging.error("Failed to persist state: %s", e)

def load_state():
    try:
        if os.path.exists(CORPUS_PATH) and os.path.exists(INDEX_PATH):
            with open(CORPUS_PATH, 'r', encoding='utf-8') as f:
                state["corpus"] = json.load(f)
            state["index"] = faiss.read_index(INDEX_PATH)
            logging.info("Loaded persisted index and corpus from %s", PERSIST_DIR)
        else:
            logging.info("No persisted state found in %s", PERSIST_DIR)
    except Exception as e:
        tb = traceback.format_exc()
        logging.error("Failed to load persisted state: %s\n%s", str(e), tb)

class BuildRequest(BaseModel):
    pdf_paths: List[str]

class QueryRequest(BaseModel):
    q: str
    k: Optional[int] = TOP_K

class RecommendRequest(BaseModel):
    need: str
    top_n: Optional[int] = 3

@app.on_event("startup")
def startup_event():
    try:
        logger.info("Loading SentenceTransformer model '%s' (may take a while)...", MODEL_NAME)
        state["model"] = SentenceTransformer(MODEL_NAME)
        logger.info("Model loaded.")
        state["index"] = None
        state["corpus"] = []
        # attempt to load persisted index/corpus
        load_state()
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Model load failed at startup: %s\n%s", str(e), tb)
        state["model"] = None
        state["index"] = None
        state["corpus"] = []

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": state["model"] is not None}

@app.post("/build")
def build(req: BuildRequest):
    with state_lock:
        model, index, corpus = build_index_from_pdfs(req.pdf_paths)
        state["model"], state["index"], state["corpus"] = model, index, corpus
        save_state()
    return {"status":"ok","total_chunks": len(state["corpus"])}

@app.post("/query")
def query(req: QueryRequest):
    with state_lock:
        if state["model"] is None or state["index"] is None:
            return {"error":"index not built yet or model unavailable"}
        res = query_index(req.q, state["model"], state["index"], state["corpus"], k=req.k)
        answer = synthesize_answer(req.q, res)
        return answer

@app.post("/recommend")
def recommend(req: RecommendRequest):
    with state_lock:
        if state["model"] is None or state["index"] is None:
            return {"error":"index not built yet or model unavailable"}
        recs = recommend_policy(req.need, state["model"], state["index"], state["corpus"], top_n=req.top_n)
        return {"recs": recs}

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    tmpdir = "uploads"
    os.makedirs(tmpdir, exist_ok=True)
    dst = os.path.join(tmpdir, file.filename)

    try:
        # stream-to-disk (avoid loading entire file into memory)
        bytes_written = 0
        CHUNK = 1024 * 64
        with open(dst, "wb") as f:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File too large")
        logger.info("Saved uploaded file %s (%d bytes)", dst, bytes_written)

        # guarded indexing
        with state_lock:
            try:
                if state["model"] is None:
                    logger.info("Model not loaded at startup; loading now...")
                    state["model"] = SentenceTransformer(MODEL_NAME)
                    logger.info("Model loaded during upload.")
                if state["index"] is None:
                    model, index, corpus = build_index_from_pdfs([dst])
                    state["model"], state["index"], state["corpus"] = model, index, corpus
                else:
                    state["corpus"], state["index"] = add_uploaded_policy(dst, state["corpus"], state["model"], state["index"])
                # persist after successful update
                save_state()
            except Exception as idx_err:
                tb = traceback.format_exc()
                logger.error("Indexing error for %s: %s\n%s", dst, str(idx_err), tb)
                return {"status": "uploaded_but_index_failed", "filename": file.filename, "indexing_error": str(idx_err)}

        return {"status":"uploaded", "filename": file.filename}
    except HTTPException:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        logger.error("ERROR in /upload handler: %s\n%s", str(e), tb)
        raise HTTPException(status_code=500, detail=f"Upload handler error: {str(e)}")

# End of file
