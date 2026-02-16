import http from 'http';
import fs from 'fs';
import path from 'path';
import crypto from 'crypto';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = 3847;

// ── Auth ──
const DASHBOARD_PASSWORD = fs.readFileSync(path.join(__dirname, '.dashboard-secret'), 'utf-8').trim();
// HMAC token = hash of password (sent by client after login, validated server-side)
function makeToken(pw) {
  return crypto.createHmac('sha256', 'cozy-dashboard-salt').update(pw).digest('hex');
}
const VALID_TOKEN = makeToken(DASHBOARD_PASSWORD);

function isAuthenticated(req) {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const token = url.searchParams.get('token') || req.headers['x-dashboard-token'];
  return token === VALID_TOKEN;
}

// ── Telegram topic names auto-refresh ──
let topicNamesMap = {};
const TOPIC_NAMES_FILE = path.join(__dirname, 'topic-names.json');

function loadTopicNamesFromDisk() {
  try {
    topicNamesMap = JSON.parse(fs.readFileSync(TOPIC_NAMES_FILE, 'utf-8'));
  } catch { topicNamesMap = {}; }
}

function getBotToken() {
  try {
    const c = JSON.parse(fs.readFileSync('/root/.openclaw/openclaw.json', 'utf-8'));
    return c.channels?.telegram?.botToken;
  } catch { return null; }
}

function getForumChatIds() {
  // Extract unique group chat IDs from sessions that have topics
  try {
    const raw = fs.readFileSync('/root/.openclaw/agents/main/sessions/sessions.json', 'utf-8');
    const store = JSON.parse(raw);
    const chatIds = new Set();
    for (const key of Object.keys(store.sessions || store)) {
      const m = key.match(/group:(-?\d+):topic:/);
      if (m) chatIds.add(m[1]);
    }
    return [...chatIds];
  } catch { return []; }
}

async function fetchTopicNames() {
  const token = getBotToken();
  if (!token) return;
  const chatIds = getForumChatIds();
  let changed = false;
  for (const chatId of chatIds) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${token}/getForumTopicsByChat?chat_id=${chatId}`);
      const data = await res.json();
      if (data.ok && data.result) {
        for (const topic of data.result) {
          const name = topic.name;
          const id = String(topic.message_thread_id);
          if (topicNamesMap[id] !== name) {
            topicNamesMap[id] = name;
            changed = true;
          }
        }
      }
    } catch (e) {
      console.warn(`Failed to fetch topics for ${chatId}:`, e.message);
    }
  }
  if (changed) {
    try { fs.writeFileSync(TOPIC_NAMES_FILE, JSON.stringify(topicNamesMap, null, 2)); } catch {}
  }
}

// Load from disk immediately, then fetch from Telegram
loadTopicNamesFromDisk();
fetchTopicNames();
// Refresh every 5 minutes
setInterval(fetchTopicNames, 5 * 60 * 1000);

// Read sessions directly from the sessions store
function getSessions() {
  try {
    const raw = fs.readFileSync('/root/.openclaw/agents/main/sessions/sessions.json', 'utf-8');
    const store = JSON.parse(raw);
    // sessions.json is { sessions: { [key]: sessionData } }
    const sessions = Object.entries(store.sessions || store).map(([key, s]) => ({
      key,
      ...s
    }));
    return JSON.stringify({ count: sessions.length, sessions, topicNames: topicNamesMap });
  } catch(e) {
    return JSON.stringify({ count: 0, sessions: [], error: e.message });
  }
}

// Read cron jobs
function getCronJobs() {
  try {
    const raw = fs.readFileSync('/root/.openclaw/cron/jobs.json', 'utf-8');
    return JSON.stringify(JSON.parse(raw));
  } catch(e) {
    return JSON.stringify({ version: 1, jobs: [], error: e.message });
  }
}

// Read cron run history for a specific job
function getCronRuns(jobId) {
  try {
    const filePath = `/root/.openclaw/cron/runs/${jobId}.jsonl`;
    if (!fs.existsSync(filePath)) {
      return JSON.stringify({ runs: [] });
    }
    
    const lines = fs.readFileSync(filePath, 'utf-8').trim().split('\n');
    // Get last 20 lines
    const recentLines = lines.slice(-20);
    const runs = recentLines.filter(line => line.trim()).map(line => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    }).filter(run => run !== null);
    
    return JSON.stringify({ runs });
  } catch(e) {
    return JSON.stringify({ runs: [], error: e.message });
  }
}

// Toggle cron job enabled/disabled
function toggleCronJob(jobId, enabled) {
  try {
    const raw = fs.readFileSync('/root/.openclaw/cron/jobs.json', 'utf-8');
    const data = JSON.parse(raw);
    
    const job = data.jobs.find(j => j.id === jobId);
    if (!job) {
      return JSON.stringify({ error: 'Job not found' });
    }
    
    job.enabled = enabled;
    job.updatedAtMs = Date.now();
    
    fs.writeFileSync('/root/.openclaw/cron/jobs.json', JSON.stringify(data, null, 2));
    return JSON.stringify({ success: true, enabled });
  } catch(e) {
    return JSON.stringify({ error: e.message });
  }
}

// Trigger a cron job run
function runCronJob(jobId) {
  try {
    const result = execSync(`openclaw cron run ${jobId}`, { 
      encoding: 'utf-8', 
      timeout: 5000 
    });
    return JSON.stringify({ success: true, output: result.trim() });
  } catch(e) {
    return JSON.stringify({ 
      error: e.message, 
      output: e.stdout || '', 
      stderr: e.stderr || '' 
    });
  }
}

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json' };

// Login page HTML
const LOGIN_HTML = `<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Dashboard Login</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'JetBrains Mono',monospace;background:#0a0c10;color:#e2e4e9;display:flex;align-items:center;justify-content:center;min-height:100vh}
.login{background:#0e1117;border:1px solid rgba(255,255,255,.06);border-radius:8px;padding:40px;width:340px}
h1{font-size:16px;margin-bottom:24px;color:#e5954a}
input{width:100%;padding:10px 12px;background:#141820;border:1px solid rgba(255,255,255,.09);border-radius:3px;color:#e2e4e9;font-family:inherit;font-size:13px;margin-bottom:16px;outline:none}
input:focus{border-color:rgba(229,149,74,.4)}
button{width:100%;padding:10px;background:#e5954a;color:#0a0c10;border:none;border-radius:3px;font-family:inherit;font-size:13px;font-weight:600;cursor:pointer}
button:hover{opacity:.9}
.err{color:#f87171;font-size:11px;margin-bottom:12px;display:none}
</style></head><body>
<div class="login"><h1>🔒 Dashboard</h1>
<div class="err" id="err">Wrong password</div>
<form id="f"><input type="password" id="pw" placeholder="Password" autofocus>
<button type="submit">Login</button></form>
</div>
<script>
document.getElementById('f').onsubmit=async e=>{
  e.preventDefault();
  const pw=document.getElementById('pw').value;
  const res=await fetch('/api/auth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
  const d=await res.json();
  if(d.token){localStorage.setItem('dashboard-token',d.token);window.location.href='/';}
  else{document.getElementById('err').style.display='block';}
};
// If already logged in, redirect
if(localStorage.getItem('dashboard-token'))window.location.href='/';
</script></body></html>`;

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-Dashboard-Token');
  
  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
    return;
  }
  
  // Login page (always accessible)
  if (url.pathname === '/login') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(LOGIN_HTML);
    return;
  }
  
  // Auth endpoint (always accessible)
  if (url.pathname === '/api/auth' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        const { password } = JSON.parse(body);
        if (password === DASHBOARD_PASSWORD) {
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ token: VALID_TOKEN }));
        } else {
          res.writeHead(401, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ error: 'Invalid password' }));
        }
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid request' }));
      }
    });
    return;
  }
  
  // All other routes require auth
  if (!isAuthenticated(req)) {
    res.writeHead(401, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Unauthorized' }));
    return;
  }
  
  if (url.pathname === '/data/sessions.json') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(getSessions());
    return;
  }
  
  if (url.pathname === '/data/cron-jobs.json') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(getCronJobs());
    return;
  }
  
  if (url.pathname.startsWith('/data/cron-runs/')) {
    const jobId = url.pathname.replace('/data/cron-runs/', '');
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(getCronRuns(jobId));
    return;
  }
  
  if (url.pathname === '/api/cron/toggle' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        const { jobId, enabled } = JSON.parse(body);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(toggleCronJob(jobId, enabled));
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }
  
  if (url.pathname === '/api/cron/run' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        const { jobId } = JSON.parse(body);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(runCronJob(jobId));
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Invalid JSON' }));
      }
    });
    return;
  }
  
  let filePath = path.join(__dirname, url.pathname === '/' ? 'index.html' : url.pathname);
  try {
    const content = fs.readFileSync(filePath);
    res.writeHead(200, { 'Content-Type': MIME[path.extname(filePath)] || 'text/plain' });
    res.end(content);
  } catch {
    res.writeHead(404);
    res.end('Not found');
  }
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`🎛️  Dashboard: http://localhost:${PORT}`);
  console.log(`🌐 Tailscale: http://<tailscale-ip>:${PORT}`);
});
