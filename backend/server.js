const express = require('express');
const path = require('path');
const multer = require('multer');
const fs = require('fs');
const morgan = require('morgan');
const axios = require('axios');


let fetch;
try {
  if (typeof globalThis.fetch === 'function') {
    fetch = globalThis.fetch.bind(globalThis);
  } else {
    try {
      
      fetch = require('undici').fetch;
    } catch (e) {
    
      const nf = require('node-fetch');

      fetch = nf;
    }
  }
} catch (err) {
  console.error('No fetch available. Install undici or node-fetch@2 or use Node 18+.', err);
  throw err;
}

const FormData = require('form-data'); 
const app = express();

const FRONTEND_DIR = path.join(__dirname, '../frontend');
const UPSTREAM_PY = process.env.PY_URL || 'http://127.0.0.1:8000';
const PORT = process.env.PORT || 3000;

app.use(morgan('dev'));
app.use(express.json({ limit: '5mb' }));
app.use(express.urlencoded({ extended: true, limit: '5mb' }));

// CORS for dev (adjust in prod)
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Origin', req.headers.origin || '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,HEAD,POST,PUT,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Accept, Authorization, X-Requested-With');
  res.setHeader('Access-Control-Allow-Credentials', 'true');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

app.use(express.static(FRONTEND_DIR));

async function safeJsonResponse(response) {
  const status = response.status;
  const text = await response.text().catch(() => '');
  if (!text) return { status, body: null, raw: '' };
  try {
    const obj = JSON.parse(text);
    return { status, body: obj, raw: text };
  } catch (err) {
    return { status, body: null, raw: text };
  }
}

async function proxyJsonPost(upstreamPath, req, res) {
  try {
    const upstreamUrl = `${UPSTREAM_PY}${upstreamPath}`;
    const r = await fetch(upstreamUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    const parsed = await safeJsonResponse(r);
    if (parsed.body !== null) {
      return res.status(parsed.status).json(parsed.body);
    } else {
      return res.status(parsed.status).json({
        error: `Upstream returned non-JSON (status ${parsed.status})`,
        raw: parsed.raw,
      });
    }
  } catch (err) {
    console.error(`Proxy error to ${upstreamPath}:`, err);
    return res.status(502).json({ error: 'Failed to contact upstream service', detail: err.message });
  }
}

app.get('/api/health', (req, res) => res.json({ status: 'ok', upstream: UPSTREAM_PY }));

app.post('/api/build', (req, res) => proxyJsonPost('/build', req, res));
app.post('/api/query', (req, res) => proxyJsonPost('/query', req, res));
app.post('/api/recommend', (req, res) => proxyJsonPost('/recommend', req, res));

// Upload forwarding
const UPLOAD_DIR = path.join(__dirname, 'uploads');
try {
  fs.mkdirSync(UPLOAD_DIR, { recursive: true });
} catch (e) {
  console.warn('Could not ensure upload dir exists:', UPLOAD_DIR, e);
}
const upload = multer({ dest: UPLOAD_DIR });

app.post('/api/upload', upload.single('file'), async (req, res) => {
  if (!req.file) {
    return res.status(400).json({ error: 'No file received', hint: 'Ensure multipart/form-data and field name "file"' });
  }

  const tmpPath = req.file.path;
  const originalName = req.file.originalname;

  // we will try both common upstream endpoints (adjust as needed)
  const tryPaths = ['/upload', '/api/upload'];
  let lastErr = null;

  try {
    for (const p of tryPaths) {
      const upstreamUrl = `${UPSTREAM_PY}${p}`;
      console.log(`Forwarding file to upstream: ${upstreamUrl}`);

      // create a fresh form each attempt and append a fresh stream
      const form = new FormData();
      form.append('file', fs.createReadStream(tmpPath), { filename: originalName });

      try {
        const axiosResp = await axios.post(upstreamUrl, form, {
          headers: {
            ...form.getHeaders(), // ensures proper Content-Type with boundary
          },
          maxBodyLength: Infinity,
          maxContentLength: Infinity,
          validateStatus: () => true // we'll handle non-2xx manually
        });

        // axiosResp.status and axiosResp.data available
        if (axiosResp.status >= 200 && axiosResp.status < 300) {
          console.log(`Upstream ${upstreamUrl} OK status ${axiosResp.status}`);
          return res.status(axiosResp.status).json(axiosResp.data);
        } else {
          console.warn(`Upstream ${upstreamUrl} returned status ${axiosResp.status} data:`, axiosResp.data);
          lastErr = new Error(`Upstream ${upstreamUrl} returned status ${axiosResp.status}`);
          // try next endpoint
        }
      } catch (axErr) {
        console.error(`Axios POST to ${upstreamUrl} failed:`, axErr && axErr.stack ? axErr.stack : axErr);
        lastErr = axErr;
       
      }
    }

    const msg = lastErr ? lastErr.message || String(lastErr) : 'All upstream attempts failed';
    return res.status(502).json({ error: 'Failed to forward file to Python service', detail: msg });
  } finally {
    
    fs.unlink(tmpPath, (err) => {
      if (err) console.warn('Failed to remove temp upload:', tmpPath, err);
    });
  }
});



app.use((req, res, next) => {
  if (req.path.startsWith('/api/')) return next();
  const indexPath = path.join(FRONTEND_DIR, 'index.html');
  if (fs.existsSync(indexPath)) return res.sendFile(indexPath);
  return next();
});

const server = app.listen(PORT, () => console.log(`Node server running: http://localhost:${PORT} (proxy -> ${UPSTREAM_PY})`));

try {
  server.headersTimeout = 300000;
  server.requestTimeout = 0;      
  server.keepAliveTimeout = 65000; 
} catch (e) {
  console.warn('Could not adjust server timeouts:', e);
}
