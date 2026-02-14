import http from 'http';
import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = 3847;

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

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    res.writeHead(200);
    res.end();
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
