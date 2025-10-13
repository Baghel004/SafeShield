// train_datasets.js
// Usage: node train_datasets.js

let fetchFn = null;
let dispatcher = null;
try {
  if (typeof globalThis.fetch === 'function') {
    fetchFn = globalThis.fetch.bind(globalThis);
  } else {
    // Fallback to undici if on older Node
    const undici = require('undici');
    fetchFn = undici.fetch;
    // Configure generous timeouts for long build
    dispatcher = new undici.Agent({
      keepAliveTimeout: 65_000,
      headersTimeout: 1200_000, // 20 minutes to receive headers
      bodyTimeout: 0           // disable body timeout
    });
  }
} catch (e) {
  console.error('No fetch available. Use Node 18+, or `npm i undici`.', e);
  process.exit(1);
}

const body = {
  pdf_paths: [
    "C:\\Users\\rajar\\OneDrive\\Desktop\\SE Project\\datasets\\BAJHLIP23020V012223.pdf",
    "C:\\Users\\rajar\\OneDrive\\Desktop\\SE Project\\datasets\\CHOTGDP23004V012223.pdf",
    "C:\\Users\\rajar\\OneDrive\\Desktop\\SE Project\\datasets\\EDLHLGA23009V012223.pdf",
    "C:\\Users\\rajar\\OneDrive\\Desktop\\SE Project\\datasets\\HDFC-Life-Group-Poorna-Suraksha-101N137V02-Policy-Document.pdf",
    "C:\\Users\\rajar\\OneDrive\\Desktop\\SE Project\\datasets\\HDFHLIP23024V072223.pdf",
    "C:\\Users\\rajar\\OneDrive\\Desktop\\SE Project\\datasets\\ICIHLIP22012V012223.pdf",
  ]
};

(async () => {
  try {
    console.log('Sending build request to /api/build ...');
    const r = await fetchFn('http://localhost:3000/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      // Pass dispatcher when using undici
      ...(dispatcher ? { dispatcher } : {})
    });
    const text = await r.text();
    console.log('Status:', r.status);
    try { console.log('JSON:', JSON.parse(text)); } catch { console.log('Raw:', text); }
    if (!r.ok) process.exit(1);
  } catch (err) {
    console.error('Build request failed:', err);
    process.exit(1);
  }
})();