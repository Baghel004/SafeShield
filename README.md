# SafeShield 🛡️

A full-stack insurance company website featuring intelligent policy recommendations and an AI-powered chatbot to answer policy-related queries.

---

## 📋 Overview

**SafeShield** is a modern, intelligent insurance platform that helps users find the right insurance policies and get instant answers to their questions. It combines a powerful backend architecture with:

- 🤖 **AI Policy Recommender** - Intelligent suggestions based on user needs
- 💬 **Intelligent Chatbot** - Real-time answers to policy questions
- 📄 **Semantic Search** - Search through policy documents using natural language
- 🔍 **RAG-based Q&A** - Retrieval-Augmented Generation for accurate policy information

---

## 🏗️ Architecture

SafeShield uses a **Full-Stack** architecture with three main components:

### Frontend
- Modern responsive web interface
- User-friendly policy recommendation flow
- Integrated chatbot interface
- Real-time interactions

### Backend (Node.js)
- Express.js server for API routing
- Proxy layer to Python ML service
- File upload handling
- CORS support for development

### Backend (Python - ML Service)
- FastAPI for high-performance API
- Semantic search using FAISS
- Policy recommendation engine
- PDF document processing and indexing
- Sentence transformers for embeddings

---

## 🚀 Features

### 1. **Policy Recommender System**
Intelligent algorithm that:
- Analyzes user needs and preferences
- Searches through policy documents
- Ranks policies by relevance
- Provides top 3 policy recommendations with relevance scores
- Shows policy snippets for quick review

### 2. **Chatbot - Policy Q&A**
AI-powered chatbot that:
- Understands natural language queries
- Searches policy documents for relevant clauses
- Provides citations and page references
- Returns structured answers with full text excerpts
- Handles complex policy-related questions

### 3. **Semantic Search**
Advanced document search using:
- Sentence transformers (`all-MiniLM-L6-v2`)
- FAISS vector database for fast similarity search
- Intelligent chunking with overlap for better context
- PDF text extraction and processing

### 4. **Document Management**
- Upload multiple insurance policy PDFs
- Automatic indexing and embedding
- Persistent state storage
- Support for policy documents up to 100MB

---

## 📦 Tech Stack

| Component | Technology |
|-----------|-----------|
| **Frontend** | HTML/CSS/JavaScript |
| **Backend (API)** | Node.js, Express.js |
| **Backend (ML)** | Python, FastAPI |
| **ML Libraries** | SentenceTransformers, FAISS, PDFPlumber |
| **Utilities** | Multer, Axios, Fetch API |
| **Development** | Nodemon, Morgan (logging) |

---

## ⚙️ Setup & Installation

### Prerequisites
- Node.js (v18+) and npm
- Python 3.8+
- pip

### 1. Clone the Repository
```bash
git clone https://github.com/Baghel004/SafeShield.git
cd SafeShield
```

### 2. Backend Setup (Node.js)

#### Step 1: Navigate to Backend Directory
```bash
cd backend
```

#### Step 2: Install Dependencies
```bash
npm install
```

This will install all required packages:
- **express** (v5.1.0) - Web framework for routing and middleware
- **cors** (v2.8.5) - Cross-Origin Resource Sharing support
- **multer** (v2.0.2) - File upload middleware
- **axios** (v1.12.2) - HTTP client for making requests
- **morgan** (v1.10.1) - HTTP request logger
- **form-data** (v4.0.4) - Multipart form data handling
- **node-fetch** (v3.3.2) - Fetch API for Node.js (fallback)
- **undici** (v7.16.0) - HTTP client (fallback for older Node versions)

#### Step 3: Start the Server
```bash
npm start
```

The server will start and output:
```
Node server running: http://localhost:3000 (proxy -> http://127.0.0.1:8000)
```

#### Step 4: Verify Server is Running
```bash
curl http://localhost:3000/api/health
```

Expected response:
```json
{
  "status": "ok",
  "upstream": "http://127.0.0.1:8000"
}
```

#### Backend Server Details

**Port:** 3000 (configurable via `PORT` environment variable)

**Key Features:**
- Serves frontend static files from `../frontend` directory
- Proxies API requests to Python backend at `http://127.0.0.1:8000`
- Handles file uploads with multipart/form-data
- Includes CORS middleware for development
- Request logging with Morgan
- Custom error handling and timeout management

**Server Configuration:**
- JSON payload limit: 5MB
- Header timeout: 300 seconds
- Keep-alive timeout: 65 seconds
- Request timeout: Unlimited (for long-running operations)

**Upload Directory:** `backend/uploads/`
- Created automatically on first upload
- Temporary files cleaned up after processing
- Max file size: 100MB (limited by upstream Python service)

#### Environment Variables
```bash
export PORT=3000                    # Server port (default: 3000)
export PY_URL=http://127.0.0.1:8000 # Python service URL (default: http://127.0.0.1:8000)
```

#### Development Mode (with Auto-restart)
```bash
npm install -g nodemon              # Install nodemon globally (optional)
npx nodemon server.js               # Or use nodemon directly
```

The server will automatically restart on file changes.

#### Troubleshooting Backend

**Port already in use:**
```bash
# Find process using port 3000
lsof -i :3000

# Kill the process
kill -9 <PID>

# Or use a different port
PORT=3001 npm start
```

**Cannot connect to Python service:**
- Verify Python service is running on port 8000
- Check firewall settings
- Update `PY_URL` environment variable if needed

**File upload fails:**
```bash
# Ensure uploads directory exists and has write permissions
mkdir -p backend/uploads
chmod 755 backend/uploads
```

**Module not found errors:**
```bash
# Clear node_modules and reinstall
rm -rf node_modules package-lock.json
npm install
```

#### API Routes Available

After starting the backend, the following routes are available:

1. **Health Check**
   ```bash
   GET /api/health
   ```

2. **Build Index**
   ```bash
   POST /api/build
   Content-Type: application/json
   
   {
     "pdf_paths": [
       "/path/to/policy1.pdf",
       "/path/to/policy2.pdf"
     ]
   }
   ```

3. **Query Policies**
   ```bash
   POST /api/query
   Content-Type: application/json
   
   {
     "q": "What is the coverage limit?",
     "k": 8
   }
   ```

4. **Get Recommendations**
   ```bash
   POST /api/recommend
   Content-Type: application/json
   
   {
     "need": "I need comprehensive health coverage",
     "top_n": 3
   }
   ```

5. **Upload Policy Document**
   ```bash
   POST /api/upload
   Content-Type: multipart/form-data
   
   file: <your-policy.pdf>
   ```

6. **Serve Frontend**
   ```bash
   GET /
   GET /index.html
   ```

---

### 3. Backend Setup (Python ML Service)

#### Step 1: Navigate to Python Backend
```bash
cd ../backend-python
```

#### Step 2: Create Virtual Environment
```bash
python -m venv venv
```

#### Step 3: Activate Virtual Environment

**On Linux/macOS:**
```bash
source venv/bin/activate
```

**On Windows:**
```bash
venv\Scripts\activate
```

#### Step 4: Install Dependencies
```bash
pip install fastapi uvicorn pydantic
pip install sentence-transformers faiss-cpu pdfplumber nltk
```

Or use the comprehensive requirements:
```bash
pip install fastapi==0.104.1 uvicorn==0.24.0
pip install sentence-transformers==2.2.2 faiss-cpu==1.7.4
pip install pdfplumber==0.10.3 nltk==3.8.1
```

#### Step 5: Start the Service
```bash
uvicorn app:app --reload --port 8000
```

The service will start and output:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete
```

#### Step 6: Verify Service is Running
```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "ok",
  "model_loaded": false
}
```

---

### 4. Frontend
The frontend is served by the Node.js backend from the `frontend` directory.
Access the application at `http://localhost:3000`

---

## 📡 API Endpoints

### Node.js Backend

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/health` | GET | Health check |
| `/api/build` | POST | Build/rebuild the search index |
| `/api/query` | POST | Query policies with natural language |
| `/api/recommend` | POST | Get policy recommendations |
| `/api/upload` | POST | Upload new policy documents |

### Python ML Service

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Service health status |
| `/build` | POST | Build index from PDF paths |
| `/query` | POST | Query indexed policies |
| `/recommend` | POST | Get policy recommendations |
| `/upload` | POST | Upload and index new policies |

---

## 🔧 Configuration

### Environment Variables

**Node.js Backend** (`backend/`):
```env
PORT=3000                           # Server port
PY_URL=http://127.0.0.1:8000       # Python service URL
```

**Python Backend** (`backend-python/`):
```env
# Default configuration in app.py
MODEL_NAME="all-MiniLM-L6-v2"       # Sentence transformer model
CHUNK_SIZE=1200                     # PDF chunk size
CHUNK_OVERLAP=300                   # Overlap between chunks
TOP_K=8                             # Top results for queries
```

---

## 🗂️ Project Structure

```
SafeShield/
├── frontend/                    # Frontend web application
│   └── ...
├── backend/                     # Node.js API server
│   ├── server.js               # Main server file
│   ├── package.json            # Dependencies
│   └── uploads/                # Temporary upload directory
├── backend-python/             # Python ML service
│   ├── app.py                  # FastAPI application
│   ├── venv/                   # Virtual environment
│   └── persist/                # Persisted index & corpus
├── datasets/                   # Sample policy PDFs
├── train_datasets.js           # Script to build initial index
└── .gitignore                  # Git ignore rules
```

---

## 📊 How It Works

### Policy Recommendation Flow
1. User submits their insurance need
2. ML service encodes the user's query into a vector
3. FAISS searches for most similar policy chunks
4. Algorithm ranks policies by relevance score
5. Top 3 policies returned with snippets

### Chatbot Q&A Flow
1. User asks a policy question
2. Query is encoded into embedding
3. FAISS retrieves top 8 relevant chunks
4. System synthesizes answer with citations
5. Response includes excerpts and page references

### Document Indexing
1. PDF files are uploaded or specified
2. PDFPlumber extracts text from each page
3. Text is split into overlapping chunks (1200 chars, 300 char overlap)
4. Chunks are encoded using SentenceTransformers
5. Embeddings added to FAISS index
6. State (corpus + index) persisted to disk

---

## 🚀 Building Initial Index

Use the provided script to build the index with sample datasets:

```bash
npm install (if needed)
node train_datasets.js
```

This script:
- Sends PDF paths to the `/api/build` endpoint
- Processes multiple policy documents
- Creates searchable index
- Handles long-running requests with generous timeouts

---

## 📝 Example Usage

### Query Endpoint
```bash
curl -X POST http://localhost:3000/api/query \
  -H "Content-Type: application/json" \
  -d '{"q": "What are the coverage limits?"}'
```

**Response:**
```json
{
  "answer": "Coverage limits are specified in Section 2...",
  "refs": [
    {
      "policy": "policy.pdf",
      "page": 3,
      "score": 0.892,
      "excerpt": "Coverage limits..."
    }
  ],
  "full_texts": [
    {
      "policy": "policy.pdf",
      "page": 3,
      "full_text": "...",
      "clause_found": true,
      "clause_text": "Clause 2.1: Coverage limits are..."
    }
  ]
}
```

### Recommend Endpoint
```bash
curl -X POST http://localhost:3000/api/recommend \
  -H "Content-Type: application/json" \
  -d '{"need": "I need comprehensive health coverage for my family"}'
```

**Response:**
```json
{
  "recs": [
    {
      "rank": 1,
      "policy": "health_policy.pdf",
      "score": 0.876,
      "snips": ["Family coverage with comprehensive benefits...", "Premium rates from..."]
    }
  ]
}
```

---

## 🔐 Security Features

- File upload size limit: 100MB
- CORS support for development
- Request timeout handling
- Error sanitization in API responses
- Safe JSON parsing with fallbacks

---

## 📈 Performance

- **Embedding Model**: all-MiniLM-L6-v2 (22M parameters, fast)
- **Vector Search**: FAISS IndexFlatIP (inner product similarity)
- **Chunk Processing**: Overlapping chunks for context preservation
- **Persistence**: Automatic state saving/loading
- **Concurrency**: Thread-safe operations with locks

---

## 🛠️ Development

### Running in Development Mode

**Terminal 1 - Python Backend:**
```bash
cd backend-python
source venv/bin/activate
uvicorn app:app --reload --port 8000
```

**Terminal 2 - Node.js Backend:**
```bash
cd backend
npm install
npm start
```

**Terminal 3 - Build Index (Optional):**
```bash
node train_datasets.js
```

Then access the application at `http://localhost:3000`

---

## 🐛 Troubleshooting

### Common Issues

**Python service not reachable:**
- Ensure Python service is running on port 8000
- Check `PY_URL` environment variable

**File upload fails:**
- Verify upload directory has write permissions
- Check file size is under 100MB

**Index not building:**
- Verify PDF paths exist in `train_datasets.js`
- Check Python service logs for PDF parsing errors

**Model not loading:**
- First time loading takes time (downloads embeddings model)
- Requires internet connection for model download

---

## 📚 Dependencies

### Backend (Node.js)
- express: Web framework
- multer: File upload handling
- axios: HTTP client
- cors: Cross-origin support
- morgan: Request logging
- form-data: Multipart form handling

### Backend (Python)
- fastapi: Web framework
- pydantic: Data validation
- sentence-transformers: Embedding generation
- faiss-cpu: Vector search
- pdfplumber: PDF text extraction
- nltk: Natural language toolkit

---

## 🤝 Contributing

Contributions are welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a pull request

---

## 📄 License

This project is open source and available under the ISC License.

---

## 👨‍💻 Author

**Baghel004** - [GitHub Profile](https://github.com/Baghel004)

---

## 📞 Support

For issues, questions, or suggestions, please open an [GitHub Issue](https://github.com/Baghel004/SafeShield/issues).

---

## 🎯 Roadmap

- [ ] User authentication and profiles
- [ ] Payment integration
- [ ] Admin dashboard for policy management
- [ ] Advanced analytics
- [ ] Mobile app
- [ ] Multi-language support
- [ ] Real-time policy comparison
- [ ] Customer reviews and ratings

---

**Made with ❤️ for insurance innovation**
