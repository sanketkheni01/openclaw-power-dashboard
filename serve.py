#!/usr/bin/env python3
import http.server, json, os, socketserver, glob, time, threading, asyncio, websockets, urllib.request, urllib.error, gzip, hashlib, secrets, base64
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urlencode
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

PORT = 3847
WS_PORT = 3850
DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_FILE = '/root/.openclaw/agents/main/sessions/sessions.json'
TOPIC_NAMES_FILE = os.path.join(DIR, 'topic-names.json')

# ── In-memory cache ──
_sessions_cache = {'data': None, 'etag': None, 'mtime': 0}
_sessions_cache_lock = threading.Lock()

# ── OAuth Constants ──
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"
OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
OAUTH_CREDS_FILE = os.path.join(DIR, 'oauth-creds.json')

# Pending PKCE sessions: {sessionId: {verifier, createdAt}}
_pkce_sessions = {}
_pkce_lock = threading.Lock()

def _pkce_cleanup():
    """Remove PKCE sessions older than 10 minutes."""
    now = time.time()
    with _pkce_lock:
        expired = [k for k, v in _pkce_sessions.items() if now - v['createdAt'] > 600]
        for k in expired:
            del _pkce_sessions[k]

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

def _oauth_load_creds():
    try:
        with open(OAUTH_CREDS_FILE) as f:
            return json.load(f)
    except:
        return {"accounts": {}}

def _oauth_save_creds(creds):
    with open(OAUTH_CREDS_FILE, 'w') as f:
        json.dump(creds, f, indent=2)
    os.chmod(OAUTH_CREDS_FILE, 0o600)

def _oauth_token_request(payload):
    """Make a POST to the OAuth token endpoint with browser-like headers."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(OAUTH_TOKEN_URL, data=data, headers={
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Origin': 'https://claude.ai',
        'Referer': 'https://claude.ai/',
    })
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode()
        except:
            pass
        raise ValueError(f'Token exchange failed (HTTP {e.code}): {body}')

def _oauth_refresh_if_needed(account):
    """Refresh token if expiring within 5 minutes. Returns updated account or None on failure."""
    if account.get('expiresAt', 0) > time.time() + 300:
        return account
    try:
        result = _oauth_token_request({
            "grant_type": "refresh_token",
            "client_id": OAUTH_CLIENT_ID,
            "refresh_token": account['refreshToken'],
        })
        account['accessToken'] = result['access_token']
        account['refreshToken'] = result['refresh_token']
        account['expiresAt'] = time.time() + result.get('expires_in', 3600)
        return account
    except Exception as e:
        account['refreshError'] = str(e)
        return None

def _oauth_get_usage(access_token):
    """Fetch usage stats for an OAuth account."""
    req = urllib.request.Request(
        'https://api.anthropic.com/api/oauth/usage',
        headers={
            'Authorization': f'Bearer {access_token}',
            'anthropic-beta': 'oauth-2025-04-20',
        }
    )
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())

def _run_status(cmd, timeout=3, cwd=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except Exception as e:
        return type('Result', (), {'returncode': 1, 'stdout': '', 'stderr': str(e)})()


def _gateway_user_systemctl(*args):
    env = os.environ.copy()
    env.setdefault('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    return subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True, timeout=12, env=env)


def _service_status(name, cmd):
    try:
        result = cmd()
        status = (result.stdout or result.stderr or '').strip().split('\n')[0] or ('active' if result.returncode == 0 else 'inactive')
        active = result.returncode == 0 and status in ('active', 'running')
        return {'name': name, 'status': status, 'active': active}
    except Exception as e:
        return {'name': name, 'status': f'unknown: {e}', 'active': False}


def _tmux_session_active(session_name):
    result = _run_status(['tmux', 'has-session', '-t', session_name])
    return result.returncode == 0


def _start_detached_command(cmd, log_path, cwd=None):
    """Launch a long-running helper detached from the dashboard.

    The helper runs inside its own transient systemd scope so its memory is
    charged to a separate cgroup rather than cozy-dashboard.service's. Heavy
    helpers like `openclaw doctor` (~350MB) would otherwise blow the service's
    MemoryHigh/Max cap and throttle the entire dashboard into unresponsiveness.
    Falls back to a plain detached spawn if systemd-run is unavailable.
    """
    import re
    with open(log_path, 'ab', buffering=0) as log:
        log.write(f"\n--- {datetime.now().isoformat()} ---\n$ {' '.join(cmd)}\n".encode())
        unit = re.sub(r'[^A-Za-z0-9:_.\-]', '_',
                      f"cozy-spawn-{os.path.basename(cmd[0])}-{int(time.time())}-{secrets.token_hex(2)}")
        wrapped = ['systemd-run', '--scope', '--collect', '--expand-environment=no',
                   f'--unit={unit}', '--property=MemoryMax=1G', *cmd]
        try:
            return subprocess.Popen(wrapped, cwd=cwd, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        except FileNotFoundError:
            log.write(b"[cozy] systemd-run not found; running without cgroup isolation\n")
            return subprocess.Popen(cmd, cwd=cwd, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)


def get_system_info():
    info = {}
    try:
        # CPU
        cpu_count = os.cpu_count() or 1
        load1, load5, load15 = os.getloadavg()
        cpu_usage = min(100, round(load1 / cpu_count * 100, 1))
        cpu_model = ''
        try:
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if 'model name' in line:
                        cpu_model = line.split(':')[1].strip()
                        break
        except: pass
        info['cpu'] = {
            'usage_pct': cpu_usage,
            'load_avg': f'{load1:.2f} / {load5:.2f} / {load15:.2f}',
            'cores': cpu_count,
            'model': cpu_model
        }
        
        # Memory
        try:
            with open('/proc/meminfo') as f:
                meminfo = {}
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0])  # kB
                        meminfo[key] = val
            total = meminfo.get('MemTotal', 0)
            avail = meminfo.get('MemAvailable', 0)
            used = total - avail
            swap_total = meminfo.get('SwapTotal', 0)
            swap_free = meminfo.get('SwapFree', 0)
            swap_used = swap_total - swap_free
            def fmt_kb(kb):
                if kb > 1048576: return f'{kb/1048576:.1f}G'
                if kb > 1024: return f'{kb/1024:.0f}M'
                return f'{kb}K'
            info['memory'] = {
                'total': fmt_kb(total), 'used': fmt_kb(used), 'available': fmt_kb(avail),
                'used_pct': round(used/total*100, 1) if total else 0,
                'swap_total': fmt_kb(swap_total), 'swap_used': fmt_kb(swap_used),
                'swap_pct': round(swap_used/swap_total*100, 1) if swap_total else 0
            }
        except: info['memory'] = {}
        
        # Disk
        try:
            df = subprocess.check_output(['df', '-h', '--output=source,fstype,size,used,avail,pcent,target'], text=True)
            disks = []
            for line in df.strip().split('\n')[1:]:
                parts = line.split()
                if len(parts) >= 7 and parts[0].startswith('/'):
                    pct = int(parts[5].replace('%',''))
                    disks.append({'fs': parts[0], 'type': parts[1], 'size': parts[2], 'used': parts[3], 'avail': parts[4], 'used_pct': pct, 'mount': parts[6]})
            info['disks'] = disks
        except: info['disks'] = []
        
        # Network
        try:
            with open('/proc/net/dev') as f:
                lines = f.readlines()[2:]
            rx_total = tx_total = 0
            for line in lines:
                parts = line.split()
                if parts[0].rstrip(':') in ('lo',): continue
                rx_total += int(parts[1])
                tx_total += int(parts[9])
            def fmt_bytes(b):
                if b > 1073741824: return f'{b/1073741824:.1f}G'
                if b > 1048576: return f'{b/1048576:.1f}M'
                if b > 1024: return f'{b/1024:.0f}K'
                return f'{b}B'
            conns = subprocess.check_output(['ss', '-tun'], text=True).count('\n') - 1
            info['network'] = {'rx': fmt_bytes(rx_total), 'tx': fmt_bytes(tx_total), 'connections': str(conns)}
        except: info['network'] = {}
        
        # System
        try:
            hostname = os.uname().nodename
            kernel = os.uname().release
            uptime_s = float(open('/proc/uptime').read().split()[0])
            days = int(uptime_s // 86400)
            hours = int((uptime_s % 86400) // 3600)
            mins = int((uptime_s % 3600) // 60)
            uptime = f'{days}d {hours}h {mins}m' if days else f'{hours}h {mins}m'
            proc_count = len([d for d in os.listdir('/proc') if d.isdigit()])
            info['system'] = {'hostname': hostname, 'kernel': kernel, 'uptime': uptime, 'processes': str(proc_count)}
        except: info['system'] = {}
        
        # Services
        svcs = []
        svcs.append(_service_status('openclaw-gateway', lambda: _gateway_user_systemctl('is-active', 'openclaw-gateway')))
        watch_active = _tmux_session_active('openclaw-gateway-watch-main')
        svcs.append({'name': 'gateway-watch', 'status': 'active' if watch_active else 'inactive', 'active': watch_active})
        for svc in ['cozy-dashboard']:
            svcs.append(_service_status(svc, lambda svc=svc: _run_status(['systemctl', 'is-active', svc])))
        try:
            tailscale = _run_status(['tailscale', 'status', '--json'])
            svcs.append({'name': 'tailscale', 'status': 'active' if tailscale.returncode == 0 else 'inactive', 'active': tailscale.returncode == 0})
        except:
            svcs.append({'name': 'tailscale', 'status': 'unknown', 'active': False})
        info['services'] = svcs
        
        # Top processes
        try:
            ps = subprocess.check_output(['ps', 'aux', '--sort=-pcpu'], text=True, timeout=5)
            procs = []
            for line in ps.strip().split('\n')[1:11]:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    procs.append({'user': parts[0], 'pid': parts[1], 'cpu': parts[2], 'mem': parts[3], 'rss': parts[5], 'cmd': parts[10][:80]})
            info['processes'] = procs
        except: info['processes'] = []
        
    except Exception as e:
        info['error'] = str(e)
    return info

def _get_groq_api_key():
    try:
        with open('/root/.openclaw/openclaw.json') as f:
            cfg = json.load(f)
        return cfg.get('env', {}).get('GROQ_API_KEY') or os.environ.get('GROQ_API_KEY')
    except:
        return os.environ.get('GROQ_API_KEY')

def transcribe_audio(audio_bytes: bytes, mime_type: str = 'audio/webm') -> dict:
    """Transcribe audio using Groq Whisper API."""
    import io, email.generator, email.mime.multipart, email.mime.base, email.encoders
    api_key = _get_groq_api_key()
    if not api_key:
        return {'error': 'GROQ_API_KEY not configured'}
    
    # Determine file extension from mime type
    ext_map = {
        'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'mp4',
        'audio/mpeg': 'mp3', 'audio/wav': 'wav', 'audio/flac': 'flac',
        'audio/m4a': 'm4a', 'audio/x-m4a': 'm4a',
    }
    ext = ext_map.get(mime_type.split(';')[0].strip(), 'webm')
    filename = f'audio.{ext}'
    
    # Build multipart form data manually
    boundary = f'----FormBoundary{secrets.token_hex(16)}'
    body_parts = []
    # model field
    body_parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\nwhisper-large-v3-turbo\r\n')
    # response_format field
    body_parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="response_format"\r\n\r\njson\r\n')
    # file field
    file_header = f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: {mime_type}\r\n\r\n'
    body_end = f'\r\n--{boundary}--\r\n'
    
    full_body = file_header.encode() + audio_bytes + body_end.encode()
    for part in body_parts:
        full_body = part.encode() + full_body
    
    try:
        req = urllib.request.Request(
            'https://api.groq.com/openai/v1/audio/transcriptions',
            data=full_body,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': f'multipart/form-data; boundary={boundary}',
                'User-Agent': 'OpenClaw-Dashboard/1.0',
            },
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        text = data.get('text', '').strip()
        return {'text': text} if text else {'error': 'empty transcription'}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='replace')
        try:
            err_data = json.loads(err_body)
            return {'error': err_data.get('error', {}).get('message', err_body[:200])}
        except:
            return {'error': f'HTTP {e.code}: {err_body[:200]}'}
    except Exception as e:
        return {'error': str(e)}

def _get_gateway_token():
    try:
        with open('/root/.openclaw/openclaw.json') as f:
            cfg = json.load(f)
        return cfg.get('gateway', {}).get('auth', {}).get('token')
    except:
        return None

def _get_gateway_port():
    try:
        with open('/root/.openclaw/openclaw.json') as f:
            cfg = json.load(f)
        return cfg.get('gateway', {}).get('port', 18789)
    except:
        return 18789

async def _gateway_send_message(session_key: str, message: str) -> dict:
    """Send a message to a session via the OpenClaw gateway WebSocket."""
    import uuid as _uuid
    token = _get_gateway_token()
    port = _get_gateway_port()
    if not token:
        return {'error': 'gateway token not found'}
    uri = f'ws://127.0.0.1:{port}'
    try:
        async with websockets.connect(
            uri,
            additional_headers={'Origin': f'http://127.0.0.1:{port}'},
            open_timeout=5
        ) as ws:
            # Receive challenge
            await asyncio.wait_for(ws.recv(), timeout=5.0)
            # Connect as control-ui (bypasses device pairing with allowInsecureAuth=true)
            await ws.send(json.dumps({
                'type': 'req', 'id': '1', 'method': 'connect',
                'params': {
                    'minProtocol': 4, 'maxProtocol': 4,
                    'client': {'id': 'openclaw-control-ui', 'mode': 'ui', 'version': '1.0', 'platform': 'linux'},
                    'scopes': ['operator.admin', 'operator.write'],
                    'auth': {'token': token}
                }
            }))
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
            if not hello.get('ok'):
                return {'error': f"gateway connect failed: {hello.get('error', {}).get('message', 'unknown')}"}
            # Drain any immediate events
            try:
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=0.3)
            except asyncio.TimeoutError:
                pass
            # Send chat.send
            await ws.send(json.dumps({
                'type': 'req', 'id': '2', 'method': 'chat.send',
                'params': {
                    'sessionKey': session_key,
                    'message': message,
                    'idempotencyKey': str(_uuid.uuid4())
                }
            }))
            # Wait for response
            for _ in range(30):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                    if msg.get('type') == 'res' and msg.get('id') == '2':
                        if msg.get('ok'):
                            return {'status': 'sent', 'runId': msg.get('payload', {}).get('runId')}
                        else:
                            return {'error': msg.get('error', {}).get('message', 'send failed')}
                except asyncio.TimeoutError:
                    return {'error': 'timeout waiting for gateway response'}
            return {'error': 'no response from gateway'}
    except Exception as e:
        return {'error': str(e)}

def gateway_send_message_sync(session_key: str, message: str) -> dict:
    """Synchronous wrapper for _gateway_send_message."""
    try:
        loop = asyncio.new_event_loop()
        result = loop.run_until_complete(_gateway_send_message(session_key, message))
        loop.close()
        return result
    except Exception as e:
        return {'error': str(e)}

def load_topic_names():
    try:
        with open(TOPIC_NAMES_FILE) as f:
            return json.load(f)
    except:
        return {}

def _get_bot_token():
    try:
        with open('/root/.openclaw/openclaw.json') as f:
            cfg = json.load(f)
        return cfg.get('channels', {}).get('telegram', {}).get('botToken')
    except:
        return None

def _get_forum_chat_ids():
    """Extract unique group chat IDs from sessions that have topics."""
    try:
        with open(SESSIONS_FILE) as f:
            store = json.load(f)
        ids = set()
        for key in store:
            import re
            m = re.search(r'group:(-?\d+):topic:', key)
            if m:
                ids.add(m.group(1))
        return list(ids)
    except:
        return []

def _refresh_topic_names():
    """Refresh topic names and group names from multiple sources.
    
    Since Telegram Bot API has no 'list all topics' method, we:
    1. Update group chat titles via getChat
    2. Deep-scan transcript files for forum_topic_created events (in API responses,
       tool results, service messages) — the Bot API embeds topic names in
       reply_to_message.forum_topic_created.name for every message sent to a topic
    3. Save everything to topic-names.json
    """
    import re as _re
    token = _get_bot_token()
    
    current = load_topic_names()
    changed = False
    
    try:
        raw = json.load(open(SESSIONS_FILE))
    except:
        return
    
    # Build maps: chatId -> set(topicIds), and topicId -> sessionId for transcript lookup
    chat_ids = set()
    topic_session_map = {}  # (chatId, topicId) -> sessionId
    unknown_topics = set()  # topicIds not yet in current
    
    for key, val in raw.items():
        m = _re.search(r'group:(-?\d+)(?::topic:(\d+))?', key)
        if not m:
            continue
        cid = m.group(1)
        tid = m.group(2)
        chat_ids.add(cid)
        if tid:
            topic_session_map[(cid, tid)] = val.get('sessionId', '')
            if tid not in current:
                unknown_topics.add((cid, tid))
    
    # 1. Fetch group titles via getChat (only if we have a bot token)
    if token:
        for cid in chat_ids:
            try:
                url = f'https://api.telegram.org/bot{token}/getChat?chat_id={cid}'
                resp = urllib.request.urlopen(urllib.request.Request(url), timeout=10)
                data = json.loads(resp.read())
                if data.get('ok') and data['result'].get('title'):
                    group_key = f'_group:{cid}'
                    title = data['result']['title']
                    if current.get(group_key) != title:
                        current[group_key] = title
                        changed = True
            except:
                pass
    
    if not unknown_topics:
        if changed:
            try:
                with open(TOPIC_NAMES_FILE, 'w') as f:
                    json.dump(current, f, indent=2)
            except:
                pass
        return
    
    # 2. Deep-scan transcripts for forum_topic_created patterns
    # The pattern "forum_topic_created":{"name":"..." appears in:
    # - Telegram API responses embedded in tool results (sendMessage, sendVoice, etc.)
    # - Direct forum_topic_created service messages
    # We use a fast regex on raw lines to avoid parsing every JSON entry
    
    _ftc_pattern = _re.compile(r'"forum_topic_created"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)"')
    _thread_id_pattern = _re.compile(r'"message_thread_id"\s*:\s*(\d+)')
    
    # For each unknown topic, scan its transcript file(s)
    still_unknown = set()
    for cid, tid in unknown_topics:
        sid = topic_session_map.get((cid, tid), '')
        if not sid:
            still_unknown.add((cid, tid))
            continue
        
        # Find transcript files for this session (may have suffixes like -topic-XXX)
        import glob as _glob
        patterns = [
            os.path.join(TRANSCRIPTS_DIR, f'{sid}.jsonl'),
            os.path.join(TRANSCRIPTS_DIR, f'{sid}-*.jsonl'),
        ]
        files = []
        for p in patterns:
            files.extend(_glob.glob(p))
        
        if not files:
            still_unknown.add((cid, tid))
            continue
        
        found = False
        for fpath in files:
            if found:
                break
            try:
                with open(fpath, 'r', errors='replace') as f:
                    for line in f:
                        if 'forum_topic_created' not in line:
                            continue
                        m = _ftc_pattern.search(line)
                        if m:
                            name = m.group(1)
                            # Verify this matches our topic by checking message_thread_id
                            tm = _thread_id_pattern.search(line)
                            if tm:
                                found_tid = tm.group(1)
                                if found_tid == tid:
                                    current[tid] = name
                                    changed = True
                                    found = True
                                    break
                                else:
                                    # Different thread in same transcript — store it too
                                    if found_tid not in current:
                                        current[found_tid] = name
                                        changed = True
                            else:
                                # No thread_id in line — trust it since it's this topic's transcript
                                current[tid] = name
                                changed = True
                                found = True
                                break
            except:
                pass
        
        if not found:
            still_unknown.add((cid, tid))
    
    # 3. Broad scan: for remaining unknowns, scan ALL recent transcripts
    # (topic names might appear in OTHER sessions' transcripts, e.g., when a sub-agent
    # sends a message to a different topic)
    if still_unknown:
        wanted_tids = {tid for _, tid in still_unknown}
        
        try:
            import glob as _glob
            all_files = _glob.glob(os.path.join(TRANSCRIPTS_DIR, '*.jsonl'))
            all_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
            
            for fpath in all_files[:100]:
                if not wanted_tids:
                    break
                try:
                    with open(fpath, 'r', errors='replace') as f:
                        for line in f:
                            if 'forum_topic_created' not in line:
                                continue
                            m = _ftc_pattern.search(line)
                            if not m:
                                continue
                            name = m.group(1)
                            tm = _thread_id_pattern.search(line)
                            if tm:
                                found_tid = tm.group(1)
                                if found_tid in wanted_tids:
                                    current[found_tid] = name
                                    changed = True
                                    wanted_tids.discard(found_tid)
                                elif found_tid not in current:
                                    current[found_tid] = name
                                    changed = True
                except:
                    pass
        except:
            pass
        
        still_unknown = {(c, t) for c, t in still_unknown if t not in current}
    
    # 4. Telegram API probe: for topics still unknown AND recently active, send a
    # temporary message, read the topic name from reply_to_message, then delete it.
    if still_unknown and token:  # probe only recently active topics
        import glob as _glob
        import time as _time
        _active_cutoff = _time.time() - 7 * 86400  # 7 days
        active_unknown = set()
        for cid, tid in still_unknown:
            sid = topic_session_map.get((cid, tid), '')
            if not sid:
                continue
            # Check if transcript was modified recently
            for p in [os.path.join(TRANSCRIPTS_DIR, f'{sid}.jsonl'),
                       *_glob.glob(os.path.join(TRANSCRIPTS_DIR, f'{sid}-*.jsonl'))]:
                try:
                    if os.path.getmtime(p) > _active_cutoff:
                        active_unknown.add((cid, tid))
                        break
                except:
                    pass
        
        probed = 0
        for cid, tid in active_unknown:
            if probed >= 200:  # rate limit per refresh cycle
                break
            try:
                # Send a temporary message
                import urllib.parse as _up
                send_url = f'https://api.telegram.org/bot{token}/sendMessage'
                payload = _up.urlencode({
                    'chat_id': cid,
                    'message_thread_id': tid,
                    'text': '.',  # temporary probe message
                    'disable_notification': 'true',
                }).encode()
                req = urllib.request.Request(send_url, data=payload)
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())
                probed += 1
                
                if data.get('ok'):
                    result = data['result']
                    # Extract topic name from reply_to_message.forum_topic_created
                    rtm = result.get('reply_to_message', {})
                    ftc = rtm.get('forum_topic_created', {})
                    name = ftc.get('name', '')
                    if name:
                        current[tid] = name
                        changed = True
                    
                    # Delete the temporary message
                    msg_id = result.get('message_id')
                    if msg_id:
                        try:
                            del_url = f'https://api.telegram.org/bot{token}/deleteMessage'
                            del_payload = _up.urlencode({
                                'chat_id': cid,
                                'message_id': msg_id,
                            }).encode()
                            urllib.request.urlopen(
                                urllib.request.Request(del_url, data=del_payload),
                                timeout=5
                            )
                        except:
                            pass
                time.sleep(0.2)  # avoid Telegram rate limits
            except:
                pass
    
    if changed:
        try:
            with open(TOPIC_NAMES_FILE, 'w') as f:
                json.dump(current, f, indent=2)
        except:
            pass

def _topic_refresh_loop():
    """Background thread: refresh topic names every 5 minutes."""
    while True:
        try:
            _refresh_topic_names()
        except:
            pass
        time.sleep(300)

# Start topic name refresh thread
_topic_thread = threading.Thread(target=_topic_refresh_loop, daemon=True)
_topic_thread.start()

TRANSCRIPTS_DIR = '/root/.openclaw/agents/main/sessions/'
PINNED_FILE = os.path.join(DIR, 'pinned.json')

# WebSocket clients for real-time updates
ws_clients = set()
ws_loop = None

# Gateway live-session bridge state.  The dashboard keeps file reads/watchers as
# source-of-truth + fallback, but prefers OpenClaw Gateway session events when
# the local gateway is reachable.
gateway_live_state = {'connected': False, 'last_error': None, 'updated_at': 0}
_gateway_live_lock = threading.Lock()

# SSE clients for log streaming (list of queue objects)
import queue
sse_log_clients = set()  # set of queue.Queue instances
sse_lock = threading.Lock()

def sse_push_log(entry):
    """Push a log entry to all SSE clients."""
    data = json.dumps(entry)
    dead = []
    with sse_lock:
        for q in sse_log_clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_log_clients.discard(q)

class ReuseServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    allow_reuse_port = True
    daemon_threads = True
    request_queue_size = 64

def load_pinned():
    try:
        with open(PINNED_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_pinned(pinned):
    try:
        with open(PINNED_FILE, 'w') as f:
            json.dump(list(pinned), f)
    except:
        pass



def timestamp_sort_key(ts):
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            return datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() * 1000
        except Exception:
            return 0
    return 0

def find_session_files(session_id):
    """Return transcript files for a session id, including topic-suffixed files.

    Python's glob treats [] in UUID-looking ids as character classes if those
    ever appear, so keep matching explicit and centralised. Prefer the normal
    conversation jsonl over trajectory sidecars when both are present.
    """
    patterns = [
        os.path.join(TRANSCRIPTS_DIR, f'{session_id}.jsonl'),
        os.path.join(TRANSCRIPTS_DIR, f'{session_id}-*.jsonl'),
    ]
    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    return sorted(set(files), key=lambda f: ('.trajectory' in os.path.basename(f), f))

def normalize_content(content):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content}]
    return content if isinstance(content, list) else []

import re as _re
_MEDIA_ATTACH_RE = _re.compile(r'\[media attached:\s*([^\]]+)\]')
_MEDIA_URL_RE = _re.compile(r'media://(\S+?\.[a-zA-Z0-9]+)')
_IMG_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')
_AUDIO_EXT = ('.ogg', '.mp3', '.wav', '.m4a')
_VIDEO_EXT = ('.mp4', '.webm', '.mov')

def _media_url_to_http(u):
    """media://inbound/<file>  ->  /media/inbound/<file> (served by _serve_media)."""
    if not u:
        return ''
    if u.startswith('media://'):
        return '/media/' + u[len('media://'):]
    return u

def _kind_for_ext(path):
    p = path.lower()
    if p.endswith(_IMG_EXT):
        return 'image'
    if p.endswith(_AUDIO_EXT):
        return 'audio'
    if p.endswith(_VIDEO_EXT):
        return 'video'
    return 'file'

def _extract_image_media(c):
    """Normalize an image content block into a renderable attachment.
    Handles inline base64 ({data, mimeType}), {source:{url}}, {url}, {image}."""
    src = c.get('source', {}) if isinstance(c.get('source'), dict) else {}
    url = src.get('url', '') or c.get('url', '') or c.get('image', '')
    data = c.get('data') or src.get('data') or ''
    if url:
        return {'type': 'image', 'url': _media_url_to_http(url)}
    if data:
        mime = c.get('mimeType') or src.get('mimeType') or c.get('mediaType') or src.get('media_type') or 'image/jpeg'
        if not str(data).startswith('data:'):
            data = f'data:{mime};base64,{data}'
        return {'type': 'image', 'url': data, 'inline': True}
    return None

def _extract_media_attachments(txt):
    """Pull [media attached: ...] / media:// refs out of message text into attachments."""
    out = []
    seen = set()
    for m in _MEDIA_URL_RE.findall(txt or ''):
        if m in seen:
            continue
        seen.add(m)
        href = _media_url_to_http('media://' + m)
        out.append({'type': 'attachment', 'kind': _kind_for_ext(m), 'url': href, 'name': m.split('/')[-1]})
    return out

def parse_transcript_entry(entry):
    """Convert a persisted session jsonl event into dashboard transcript entry.

    Supports both classic `message` events and newer trajectory-only events so
    sessions do not render as an unexplained empty transcript.
    """
    msg = entry.get('message') or {}
    role = msg.get('role', '')
    model = msg.get('model', '')
    stop = msg.get('stopReason', '')
    ts = msg.get('timestamp', entry.get('timestamp', entry.get('ts', '')))
    cost = msg.get('usage', {}).get('cost', {}).get('total', 0)
    tokens_in = msg.get('usage', {}).get('input', 0)
    tokens_out = msg.get('usage', {}).get('output', 0)
    cache_read = msg.get('usage', {}).get('cacheRead', 0)
    content = normalize_content(msg.get('content', []))
    parsed = []

    if role == 'toolResult':
        tool_call_id = msg.get('toolCallId', '')
        tool_name = msg.get('toolName', '?')
        result_text = ''
        result_atts = []
        for c in content:
            if c.get('type') == 'text':
                result_text = c.get('text', '')[:4000]
                result_atts = _extract_media_attachments(result_text)
                break
            if c.get('type') == 'image':
                media = _extract_image_media(c)
                if media:
                    result_text = f"[IMAGE: {media.get('url') or 'inline'}]"
                    result_atts = [media]
                    break
        parsed.append({'type': 'result', 'name': tool_name, 'text': result_text, 'id': tool_call_id})
        for a in result_atts:
            parsed.append(a)
    else:
        for c in content:
            t = c.get('type', '')
            if t == 'toolCall':
                args = c.get('arguments', {})
                if isinstance(args, dict):
                    args = {k: (v[:2000] + '…' if isinstance(v, str) and len(v) > 2000 else v) for k, v in args.items()}
                parsed.append({'type': 'tool', 'name': c.get('name','?'), 'args': args, 'id': c.get('id','')})
            elif t == 'image':
                media = _extract_image_media(c)
                if media:
                    parsed.append(media)
            elif t == 'text':
                txt = c.get('text', '')
                if txt.strip():
                    # Extract any [media attached: media://inbound/...] refs into renderable attachments
                    atts = _extract_media_attachments(txt)
                    parsed.append({'type': 'text', 'text': txt[:5000]})
                    for a in atts:
                        parsed.append(a)
            elif t == 'thinking':
                thinking = c.get('thinking', '')
                if thinking:
                    parsed.append({'type': 'thinking', 'text': thinking[:3000]})

    if not parsed and entry.get('traceSchema') == 'openclaw-trajectory':
        event_type = entry.get('type', 'trajectory')
        data = entry.get('data') or {}
        role = 'system'
        model = entry.get('modelId', model)
        lines = [event_type]
        if event_type == 'prompt.submitted' and data.get('prompt'):
            lines = ['prompt submitted', data.get('prompt', '')[:5000]]
        elif event_type == 'model.completed':
            if data.get('text'):
                lines = ['model completed', data.get('text', '')[:5000]]
            elif data.get('truncated'):
                lines = ['model completed', f"output omitted by trajectory log ({data.get('reason', 'truncated')})"]
        elif event_type == 'session.started':
            lines = ['session started', f"provider: {entry.get('provider','?')} · model: {entry.get('modelId','?')}"]
        elif event_type == 'session.ended':
            lines = ['session ended']
        elif event_type == 'context.compiled' and data.get('prompt'):
            lines = ['context compiled', data.get('prompt', '')[:5000]]
        parsed.append({'type': 'text', 'text': '\n'.join(x for x in lines if x)})

    if not parsed:
        return None
    return {
        'role': role, 'model': model, 'stop': stop,
        'ts': ts, 'cost': cost,
        'tokens': {'in': tokens_in, 'out': tokens_out, 'cache': cache_read},
        'content': parsed
    }

def get_recent_activity(session_id, max_lines=10):
    """Read recent parsed transcript entries to get current activity."""
    files = find_session_files(session_id)
    if not files:
        return None

    try:
        entries = []
        # Prefer newest files first but inspect a few so a fresh trajectory sidecar
        # does not hide the real conversation file.
        for f in sorted(files, key=os.path.getmtime, reverse=True)[:3]:
            with open(f, 'rb') as fh:
                fh.seek(0, 2)
                size = fh.tell()
                read_size = min(size, 10240)
                fh.seek(-read_size, 2)
                data = fh.read().decode('utf-8', errors='ignore')
            for line in [l for l in data.strip().split('\n') if l.strip()][-max_lines * 2:]:
                try:
                    parsed = parse_transcript_entry(json.loads(line))
                    if parsed:
                        entries.append(parsed)
                except Exception:
                    continue

        activities = []
        for parsed in sorted(entries, key=lambda e: timestamp_sort_key(e.get('ts')))[-max_lines * 2:]:
            activity = {
                'role': parsed.get('role', ''),
                'model': parsed.get('model', ''),
                'stop': parsed.get('stop', ''),
                'ts': parsed.get('ts', ''),
                'cost': parsed.get('cost', 0),
            }
            for c in parsed.get('content', []):
                t = c.get('type', '')
                if t == 'result':
                    activity['action'] = f"✅ {c.get('name', '?')} done"
                    if c.get('text'):
                        activity['detail'] = c.get('text', '')[:60]
                    break
                if t == 'tool':
                    activity['action'] = f"🔧 {c.get('name', '?')}"
                    args = c.get('args', {})
                    if isinstance(args, dict):
                        if 'command' in args:
                            activity['detail'] = str(args['command'])[:60]
                        elif 'query' in args:
                            activity['detail'] = str(args['query'])[:60]
                        elif 'url' in args:
                            activity['detail'] = str(args['url'])[:60]
                        elif 'action' in args:
                            activity['detail'] = str(args['action'])[:60]
                    break
                if t == 'text':
                    txt = c.get('text', '').strip()
                    if txt and txt != 'NO_REPLY':
                        activity['action'] = '💬 Responding'
                        activity['detail'] = txt[:80]
                        break
                if t == 'thinking':
                    activity['action'] = '🧠 Thinking'
                    break
            if 'action' in activity:
                activities.append(activity)
        return activities[-8:] if activities else None
    except Exception:
        return None

def get_auth_info():
    """Get API key profiles from config."""
    try:
        with open('/root/.openclaw/openclaw.json') as f:
            cfg = json.load(f)
        profiles = cfg.get('auth', {}).get('profiles', {})
        order = cfg.get('auth', {}).get('order', {})
        return {'profiles': profiles, 'order': order}
    except:
        return {'profiles': {}, 'order': {}}

def get_cron_jobs():
    """Read cron jobs from jobs.json"""
    try:
        with open('/root/.openclaw/cron/jobs.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        return {'version': 1, 'jobs': [], 'error': str(e)}

def get_cron_runs(job_id):
    """Read cron run history for a specific job"""
    try:
        file_path = f'/root/.openclaw/cron/runs/{job_id}.jsonl'
        if not os.path.exists(file_path):
            return {'runs': []}
        
        with open(file_path, 'r') as f:
            lines = f.read().strip().split('\n')
        
        # Get last 20 lines
        recent_lines = lines[-20:]
        runs = []
        for line in recent_lines:
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except:
                continue
        
        return {'runs': runs}
    except Exception as e:
        return {'runs': [], 'error': str(e)}

def toggle_cron_job(job_id, enabled):
    """Toggle cron job enabled/disabled"""
    try:
        with open('/root/.openclaw/cron/jobs.json', 'r') as f:
            data = json.load(f)
        
        job = None
        for j in data.get('jobs', []):
            if j.get('id') == job_id:
                job = j
                break
        
        if not job:
            return {'error': 'Job not found'}
        
        job['enabled'] = enabled
        job['updatedAtMs'] = int(time.time() * 1000)
        
        with open('/root/.openclaw/cron/jobs.json', 'w') as f:
            json.dump(data, f, indent=2)
        
        return {'success': True, 'enabled': enabled}
    except Exception as e:
        return {'error': str(e)}

def run_cron_job(job_id):
    """Trigger a cron job run"""
    try:
        result = subprocess.run(
            ['openclaw', 'cron', 'run', job_id],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            return {'success': True, 'output': result.stdout.strip()}
        else:
            return {
                'error': f'Command failed with exit code {result.returncode}',
                'output': result.stdout,
                'stderr': result.stderr
            }
    except subprocess.TimeoutExpired:
        return {'error': 'Command timed out'}
    except Exception as e:
        return {'error': str(e)}

LOG_DIR = '/tmp/openclaw'

def _parse_log_entry(raw_line):
    """Parse a single NDJSON log line into a structured entry dict."""
    try:
        obj = json.loads(raw_line)
    except:
        return None
    meta = obj.get('_meta', {})
    level = meta.get('logLevelName', 'INFO')
    # Subsystem extraction
    raw_name = meta.get('name', '')
    subsystem = raw_name
    try:
        name_obj = json.loads(raw_name)
        if isinstance(name_obj, dict) and 'subsystem' in name_obj:
            subsystem = name_obj['subsystem']
    except:
        pass
    # Clean up: strip leading 'openclaw.' or 'openclaw'
    if isinstance(subsystem, str):
        if subsystem.startswith('openclaw.'):
            subsystem = subsystem[len('openclaw.'):]
        elif subsystem == 'openclaw':
            subsystem = 'core'
        subsystem = subsystem.strip('"').strip()
    # Message extraction: prefer field '1', fall back to '0'
    message = obj.get('1') or obj.get('0') or ''
    if isinstance(message, dict):
        message = json.dumps(message)
    timestamp = obj.get('time', meta.get('date', ''))
    return {
        'time': timestamp,
        'level': level,
        'subsystem': subsystem,
        'message': str(message),
        'raw': raw_line.strip()
    }

def get_log_entries(date=None, level_filter=None, limit=500, offset=-1, subsystem_filter=None):
    """Read and parse log entries from the NDJSON log file."""
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')
    log_path = os.path.join(LOG_DIR, f'openclaw-{date}.log')
    if not os.path.exists(log_path):
        return {'entries': [], 'total': 0, 'hasMore': False}
    entries = []
    try:
        with open(log_path, 'r', errors='replace') as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                entry = _parse_log_entry(raw_line)
                if entry is None:
                    continue
                # Apply server-side filters
                if level_filter and entry['level'] not in level_filter:
                    continue
                if subsystem_filter and entry['subsystem'] != subsystem_filter:
                    continue
                entries.append(entry)
    except Exception as e:
        return {'entries': [], 'total': 0, 'hasMore': False, 'error': str(e)}
    total = len(entries)
    if offset == -1:
        paginated = entries[-limit:] if len(entries) > limit else entries
        actual_offset = max(0, total - limit)
    else:
        paginated = entries[offset:offset + limit]
        actual_offset = offset
    return {
        'entries': paginated,
        'total': total,
        'offset': actual_offset,
        'hasMore': actual_offset > 0 if offset == -1 else (offset + limit) < total
    }

def calculate_session_stats(sessions):
    """Calculate aggregate statistics for dashboard."""
    now = time.time() * 1000
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000
    week_ago = (datetime.now() - timedelta(days=7)).timestamp() * 1000
    month_ago = (datetime.now() - timedelta(days=30)).timestamp() * 1000
    
    stats = {
        'total_cost_today': 0,
        'total_cost_week': 0, 
        'total_cost_month': 0,
        'total_tokens_in': 0,
        'total_tokens_out': 0,
        'total_cache_hits': 0,
        'by_model': {},
        'active_sessions': 0,
        'failed_sessions': 0,
        'completed_sessions': 0
    }
    
    for session in sessions:
        updated = session.get('updatedAt', 0)
        activity = session.get('activity', [])
        
        # Count status
        if now - updated < 300000:  # 5 min = active
            stats['active_sessions'] += 1
        
        # Aggregate costs and tokens from activity
        for act in activity:
            cost = act.get('cost', 0)
            if cost > 0:
                ts = act.get('ts', 0)
                if isinstance(ts, str):
                    try:
                        ts = datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp() * 1000
                    except:
                        continue
                
                if ts >= today:
                    stats['total_cost_today'] += cost
                if ts >= week_ago:
                    stats['total_cost_week'] += cost
                if ts >= month_ago:
                    stats['total_cost_month'] += cost
                
                model = act.get('model', 'unknown')
                if model not in stats['by_model']:
                    stats['by_model'][model] = {'cost': 0, 'tokens_in': 0, 'tokens_out': 0}
                stats['by_model'][model]['cost'] += cost
    
    return stats

def get_sessions_with_activity():
    try:
        with open(SESSIONS_FILE) as f:
            raw = json.load(f)
        
        sessions = []
        now = time.time() * 1000
        pinned = load_pinned()
        
        # Build parent-child map
        children_map = {}
        parent_map = {}
        
        for key, val in raw.items():
            if ':run:' in key:
                parent_key = key.rsplit(':run:', 1)[0]
                if parent_key in raw:
                    parent_map[key] = parent_key
                    children_map.setdefault(parent_key, []).append(key)
            spawned_by = val.get('spawnedBy', '')
            if spawned_by and spawned_by in raw:
                parent_map[key] = spawned_by
                children_map.setdefault(spawned_by, []).append(key)
        
        for key, s in raw.items():
            session = {'key': key, **s}
            sid = s.get('sessionId', '')
            updated = s.get('updatedAt', 0)
            
            # Mark if pinned
            session['pinned'] = key in pinned
            
            # Add parent/children references
            if key in parent_map:
                session['parentKey'] = parent_map[key]
                parent = raw.get(parent_map[key], {})
                session['parentLabel'] = parent.get('label', '') or parent.get('displayName', '') or parent_map[key]
            if key in children_map:
                child_list = children_map[key]
                child_info = []
                for ck in sorted(child_list, key=lambda c: raw.get(c, {}).get('updatedAt', 0), reverse=True):
                    cv = raw[ck]
                    child_info.append({
                        'key': ck,
                        'sessionId': cv.get('sessionId', ''),
                        'updatedAt': cv.get('updatedAt', 0),
                        'label': cv.get('label', ''),
                    })
                session['children'] = child_info
                session['childCount'] = len(child_info)
            
            # Classify session type
            if key == 'agent:main:main':
                session['sessionType'] = 'main'
            elif ':subagent:' in key:
                session['sessionType'] = 'subagent'
            elif ':cron:' in key and ':run:' in key:
                session['sessionType'] = 'cron-run'
            elif ':cron:' in key:
                session['sessionType'] = 'cron'
            elif ':telegram:' in key:
                session['sessionType'] = 'telegram'
            else:
                session['sessionType'] = 'other'
            
            # Determine status
            age_ms = now - updated
            if age_ms < 300000:  # 5 min
                session['status'] = 'running'
            elif age_ms < 3600000:  # 1 hour
                session['status'] = 'idle'
            else:
                session['status'] = 'completed'
            
            # Get activity only for ACTIVE sessions (5 min window, not 2 hours)
            if now - updated < 300000 and sid:
                activity = get_recent_activity(sid)
                if activity:
                    session['activity'] = activity
            
            # Strip heavy fields not needed by frontend
            session.pop('skillsSnapshot', None)
            session.pop('systemPromptReport', None)
            session.pop('origin', None)
            session.pop('deliveryContext', None)
            
            sessions.append(session)
        
        auth = get_auth_info()
        stats = calculate_session_stats(sessions)
        topic_names = load_topic_names()
        
        return json.dumps({
            'count': len(sessions), 
            'sessions': sessions, 
            'auth': auth,
            'stats': stats,
            'timestamp': now,
            'topicNames': topic_names
        })
    except Exception as e:
        return json.dumps({'error': str(e), 'count': 0, 'sessions': []})

def _test_api_key(cred, profile_name='', provider='anthropic'):
    """Test an API key by sending a real LLM request. Supports anthropic, openai, google, groq, openrouter."""
    token = cred.get('token') or cred.get('key') or ''
    if not token:
        return {'ok': False, 'error': 'No token/key found'}
    
    _oauth_prefix = 'sk-' + 'ant-oat'
    is_oauth = token.startswith(_oauth_prefix)
    
    if is_oauth:
        try:
            with open('/root/.openclaw/agents/main/agent/auth-profiles.json') as f:
                store = json.load(f)
            usage = store.get('usageStats', {})
            stats = usage.get(profile_name, {})
            last_ok = stats.get('lastUsed', 0)
            last_err = stats.get('lastFailureAt', 0)
            total_err = stats.get('errorCount', 0)
            last_error_msg = stats.get('lastError', '')
            total_ok = 1 if last_ok > 0 else 0
            
            if last_ok > 0 and last_ok >= last_err:
                from datetime import datetime
                last_ok_str = datetime.fromtimestamp(last_ok / 1000).strftime('%H:%M:%S') if last_ok > 1e12 else datetime.fromtimestamp(last_ok).strftime('%H:%M:%S')
                return {'ok': True, 'oauth': True, 'note': f'Last used at {last_ok_str} ({total_err} errors)'}
            elif last_err > last_ok and last_error_msg:
                return {'ok': False, 'oauth': True, 'error': f'{last_error_msg} ({total_err} errors)'}
            elif total_ok == 0 and total_err == 0:
                return {'ok': True, 'oauth': True, 'note': 'OAuth token present (unused)'}
            else:
                return {'ok': True, 'oauth': True, 'note': f'OAuth token ({total_err} errors)'}
        except:
            return {'ok': True, 'oauth': True, 'note': 'OAuth token present'}
    
    # Real LLM request based on provider
    try:
        if provider in ('openai', 'groq', 'openrouter'):
            base_urls = {
                'openai': 'https://api.openai.com/v1/chat/completions',
                'groq': 'https://api.groq.com/openai/v1/chat/completions',
                'openrouter': 'https://openrouter.ai/api/v1/chat/completions',
            }
            models = {
                'openai': 'gpt-4o-mini',
                'groq': 'llama-3.1-8b-instant',
                'openrouter': 'meta-llama/llama-3.1-8b-instruct:free',
            }
            payload = json.dumps({
                'model': models[provider],
                'max_tokens': 1,
                'messages': [{'role': 'user', 'content': 'hi'}]
            }).encode()
            req = urllib.request.Request(base_urls[provider], data=payload, headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {token}',
            })
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            model = data.get('model', '')
            return {'ok': True, 'model': model}
        
        elif provider == 'google':
            url = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={token}'
            payload = json.dumps({
                'contents': [{'parts': [{'text': 'hi'}]}],
                'generationConfig': {'maxOutputTokens': 1}
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            return {'ok': True, 'model': 'gemini-2.0-flash'}
        
        else:  # anthropic (default)
            payload = json.dumps({
                'model': 'claude-3-5-haiku-20241022',
                'max_tokens': 1,
                'messages': [{'role': 'user', 'content': 'hi'}]
            }).encode()
            req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=payload, headers={
                'anthropic-version': '2023-06-01',
                'content-type': 'application/json',
                'x-api-key': token,
            })
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read())
            return {'ok': True, 'model': data.get('model', '')}
    
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            msg = body.get('error', {}).get('message', '') or body.get('error', str(e))
            if isinstance(msg, dict):
                msg = msg.get('message', str(msg))
        except:
            msg = str(e)
        return {'ok': False, 'error': msg}
    except Exception as e:
        return {'ok': False, 'error': str(e)}

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DIR, **kw)
    
    def do_POST(self):
        content_type = self.headers.get('Content-Type', '')

        # ── Anthropic API Key Settings ──
        if self.path == '/api/settings/anthropic-key':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            new_key = body.get('key', '').strip()
            if not new_key:
                self._json_response(400, {'error': 'key is required'})
                return
            # Validate: test the key against Anthropic API
            try:
                payload = json.dumps({
                    'model': 'claude-3-5-haiku-20241022',
                    'max_tokens': 1,
                    'messages': [{'role': 'user', 'content': 'hi'}]
                }).encode()
                req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=payload, headers={
                    'anthropic-version': '2023-06-01',
                    'content-type': 'application/json',
                    'x-api-key': new_key,
                })
                resp = urllib.request.urlopen(req, timeout=15)
                resp_data = json.loads(resp.read())
                model_used = resp_data.get('model', '')
            except urllib.error.HTTPError as e:
                try:
                    err_body = json.loads(e.read())
                    msg = err_body.get('error', {}).get('message', '') or str(e)
                    if isinstance(msg, dict):
                        msg = msg.get('message', str(msg))
                except:
                    msg = str(e)
                self._json_response(400, {'error': f'Key validation failed: {msg}'})
                return
            except Exception as e:
                self._json_response(400, {'error': f'Key validation failed: {str(e)}'})
                return

            # Key is valid — update both config files
            try:
                # 1. Update openclaw.json auth profile
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                auth = cfg.setdefault('auth', {})
                profiles = auth.setdefault('profiles', {})
                if 'anthropic:default' in profiles:
                    profiles['anthropic:default']['mode'] = 'token'
                else:
                    profiles['anthropic:default'] = {'provider': 'anthropic', 'mode': 'token'}
                with open('/root/.openclaw/openclaw.json', 'w') as f:
                    json.dump(cfg, f, indent=2)

                # 2. Update auth-profiles.json with actual token
                auth_store_path = '/root/.openclaw/agents/main/agent/auth-profiles.json'
                with open(auth_store_path) as f:
                    store = json.load(f)
                store.setdefault('profiles', {})['anthropic:default'] = {
                    'type': 'token',
                    'provider': 'anthropic',
                    'token': new_key,
                }
                with open(auth_store_path, 'w') as f:
                    json.dump(store, f, indent=2)

                masked = '••••••••' + new_key[-4:] if len(new_key) > 4 else '••••'
                self._json_response(200, {'status': 'ok', 'masked': masked, 'model': model_used})
            except Exception as e:
                self._json_response(500, {'error': f'Failed to save key: {str(e)}'})
            return

        if self.path == '/api/keys/add':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            name = body.get('name', '').strip()
            provider = body.get('provider', '').strip()
            mode = body.get('mode', 'token').strip()
            key = body.get('key', '').strip()
            if not name or not provider or not key:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'name, provider, and key are required'}).encode())
                return
            try:
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                auth = cfg.setdefault('auth', {})
                profiles = auth.setdefault('profiles', {})
                if name in profiles:
                    self.send_response(409)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Profile already exists'}).encode())
                    return
                profiles[name] = {'provider': provider, 'mode': mode, 'key': key}
                order = auth.setdefault('order', {})
                provider_order = order.setdefault(provider, [])
                provider_order.append(name)
                with open('/root/.openclaw/openclaw.json', 'w') as f:
                    json.dump(cfg, f, indent=2)
                # Also write to auth-profiles.json so the key is immediately testable
                try:
                    auth_store_path = '/root/.openclaw/agents/main/agent/auth-profiles.json'
                    with open(auth_store_path) as f:
                        store = json.load(f)
                    store.setdefault('profiles', {})[name] = {
                        'provider': provider,
                        'mode': mode,
                        'token': key,
                        'key': key,
                    }
                    with open(auth_store_path, 'w') as f:
                        json.dump(store, f, indent=2)
                except Exception:
                    pass  # Non-fatal: key still added to config
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/delete':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            profile_name = body.get('profileName', '').strip()
            if not profile_name:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'profileName is required'}).encode())
                return
            try:
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                auth = cfg.setdefault('auth', {})
                profiles = auth.setdefault('profiles', {})
                if profile_name not in profiles:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Profile not found'}).encode())
                    return
                provider = profiles[profile_name].get('provider', '')
                del profiles[profile_name]
                order = auth.setdefault('order', {})
                if provider in order and profile_name in order[provider]:
                    order[provider].remove(profile_name)
                with open('/root/.openclaw/openclaw.json', 'w') as f:
                    json.dump(cfg, f, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/toggle':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            profile_name = body.get('profileName', '')
            enabled = body.get('enabled', True)
            try:
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                auth = cfg.setdefault('auth', {})
                profiles = auth.get('profiles', {})
                if profile_name not in profiles:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Profile not found'}).encode())
                    return
                provider = profiles[profile_name].get('provider', '')
                order = auth.setdefault('order', {})
                provider_order = order.setdefault(provider, [])
                if enabled:
                    if profile_name not in provider_order:
                        provider_order.append(profile_name)
                else:
                    if profile_name in provider_order:
                        provider_order.remove(profile_name)
                with open('/root/.openclaw/openclaw.json', 'w') as f:
                    json.dump(cfg, f, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/reorder':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            provider = body.get('provider', '')
            new_order = body.get('order', [])
            try:
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                cfg.setdefault('auth', {}).setdefault('order', {})[provider] = new_order
                with open('/root/.openclaw/openclaw.json', 'w') as f:
                    json.dump(cfg, f, indent=2)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok'}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path in ('/api/keys/test', '/api/keys/test-all'):
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            try:
                auth_store_path = '/root/.openclaw/agents/main/agent/auth-profiles.json'
                with open(auth_store_path) as f:
                    store = json.load(f)
                store_profiles = store.get('profiles', {})

                # Load config for provider info
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                all_profiles = cfg.get('auth', {}).get('profiles', {})

                if self.path == '/api/keys/test':
                    profile_name = body.get('profileName', '')
                    cred = store_profiles.get(profile_name)
                    if not cred:
                        self.send_response(404)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps({'error': 'Profile not found in auth store'}).encode())
                        return
                    provider = all_profiles.get(profile_name, {}).get('provider', cred.get('provider', 'anthropic'))
                    result = _test_api_key(cred, profile_name, provider)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({profile_name: result}).encode())
                else:
                    results = {}
                    for pname in all_profiles:
                        cred = store_profiles.get(pname)
                        if cred:
                            provider = all_profiles[pname].get('provider', cred.get('provider', 'anthropic'))
                            results[pname] = _test_api_key(cred, pname, provider)
                        else:
                            results[pname] = {'ok': False, 'error': 'No credentials in auth store'}
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(results).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/oauth/start':
            try:
                _pkce_cleanup()
                verifier = _b64url(secrets.token_bytes(32))
                challenge = _b64url(hashlib.sha256(verifier.encode('ascii')).digest())
                session_id = secrets.token_hex(16)
                with _pkce_lock:
                    _pkce_sessions[session_id] = {'verifier': verifier, 'createdAt': time.time()}
                params = urlencode({
                    'code': 'true',
                    'client_id': OAUTH_CLIENT_ID,
                    'response_type': 'code',
                    'redirect_uri': OAUTH_REDIRECT_URI,
                    'scope': OAUTH_SCOPES,
                    'code_challenge': challenge,
                    'code_challenge_method': 'S256',
                    'state': verifier,
                })
                auth_url = f"{OAUTH_AUTHORIZE_URL}?{params}"
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'sessionId': session_id, 'authUrl': auth_url}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/oauth/complete':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            session_id = body.get('sessionId', '')
            raw_code = body.get('code', '')
            try:
                with _pkce_lock:
                    pkce = _pkce_sessions.pop(session_id, None)
                if not pkce:
                    raise ValueError('Invalid or expired session. Please start over.')
                if '#' in raw_code:
                    code, state = raw_code.split('#', 1)
                else:
                    code = raw_code
                    state = pkce['verifier']
                result = _oauth_token_request({
                    'grant_type': 'authorization_code',
                    'client_id': OAUTH_CLIENT_ID,
                    'code': code,
                    'state': state,
                    'redirect_uri': OAUTH_REDIRECT_URI,
                    'code_verifier': pkce['verifier'],
                })
                access_token = result['access_token']
                refresh_token = result['refresh_token']
                expires_in = result.get('expires_in', 3600)
                email = f"claude-account-{secrets.token_hex(4)}"
                creds = _oauth_load_creds()
                creds['accounts'][email] = {
                    'accessToken': access_token,
                    'refreshToken': refresh_token,
                    'expiresAt': time.time() + expires_in,
                    'email': email,
                }
                _oauth_save_creds(creds)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True, 'email': email}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/oauth/update':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            account_id = body.get('accountId', '')
            label = body.get('label', '')
            linked_key = body.get('linkedKey', '')
            try:
                creds = _oauth_load_creds()
                if account_id not in creds['accounts']:
                    raise ValueError('Account not found')
                creds['accounts'][account_id]['label'] = label
                creds['accounts'][account_id]['linkedKey'] = linked_key
                _oauth_save_creds(creds)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/keys/oauth/remove':
            cl = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(cl).decode()) if cl else {}
            email = body.get('email', '')
            try:
                creds = _oauth_load_creds()
                creds['accounts'].pop(email, None)
                _oauth_save_creds(creds)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': True}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path == '/api/pin':
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length).decode()
            try:
                data = json.loads(body)
                session_key = data.get('sessionKey', '')
                pin_action = data.get('action', '')  # 'pin' or 'unpin'
                
                pinned = load_pinned()
                if pin_action == 'pin':
                    pinned.add(session_key)
                else:
                    pinned.discard(session_key)
                save_pinned(pinned)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            except:
                self.send_response(400)
                self.end_headers()
                return
        if self.path == '/api/refresh-topics':
            try:
                _refresh_topic_names()
                topics = load_topic_names()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'count': len(topics), 'topicNames': topics}).encode())
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

        if self.path == '/api/restart-gateway':
            try:
                proc = _start_detached_command(
                    ['systemctl', '--user', 'restart', 'openclaw-gateway'],
                    '/tmp/cozy-dashboard-gateway-restart.log'
                )
                self._json_response(200, {'status': 'restarting', 'pid': proc.pid, 'service': 'openclaw-gateway'})
                return
            except Exception as e:
                self._json_response(500, {'error': str(e)})
                return

        if self.path == '/api/fix-gateway':
            try:
                proc = _start_detached_command(
                    ['openclaw', 'doctor', '--fix', '--non-interactive'],
                    '/tmp/cozy-dashboard-gateway-fix.log'
                )
                self._json_response(200, {'status': 'fixing', 'pid': proc.pid, 'log': '/tmp/cozy-dashboard-gateway-fix.log'})
                return
            except Exception as e:
                self._json_response(500, {'error': str(e)})
                return

        if self.path == '/api/start-gateway-watch':
            try:
                proc = _start_detached_command(
                    ['bash', '-lc', 'OPENCLAW_GATEWAY_WATCH_ATTACH=0 pnpm gateway:watch'],
                    '/tmp/cozy-dashboard-gateway-watch.log',
                    cwd='/root/openclaw'
                )
                self._json_response(200, {'status': 'starting', 'pid': proc.pid, 'service': 'gateway-watch', 'log': '/tmp/cozy-dashboard-gateway-watch.log'})
                return
            except Exception as e:
                self._json_response(500, {'error': str(e)})
                return
        
        if self.path == '/api/transcribe':
            content_length = int(self.headers.get('Content-Length', 0))
            if not content_length:
                self.send_response(400)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"error":"no audio data"}')
                return
            audio_bytes = self.rfile.read(content_length)
            mime_type = self.headers.get('X-Audio-Mime', 'audio/webm')
            result = transcribe_audio(audio_bytes, mime_type)
            self.send_response(200 if 'text' in result else 500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())
            return

        if self.path == '/api/send-message':
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length).decode()
            try:
                data = json.loads(body)
                session_id = data.get('sessionId', '')
                message = data.get('message', '')
                if not session_id or not message:
                    self.send_response(400)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error":"sessionId and message required"}')
                    return
                # Look up session key from session ID
                session_key = None
                try:
                    with open(SESSIONS_FILE) as f:
                        sessions_data = json.load(f)
                    for key, sess in sessions_data.items():
                        if sess.get('sessionId') == session_id:
                            session_key = key
                            break
                except:
                    pass
                if not session_key:
                    self.send_response(404)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"error":"Session key not found"}')
                    return
                # Send message via gateway WebSocket
                result = gateway_send_message_sync(session_key, message)
                if 'error' in result:
                    self.send_response(500)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': result['error']}).encode())
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'sent'}).encode())
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

        # Cron job toggle endpoint
        if self.path == '/api/cron/toggle':
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length).decode()
            try:
                data = json.loads(body)
                job_id = data.get('jobId', '')
                enabled = data.get('enabled', True)
                result = toggle_cron_job(job_id, enabled)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

        # Cron job run endpoint
        if self.path == '/api/cron/run':
            content_length = int(self.headers['Content-Length'])
            body = self.rfile.read(content_length).decode()
            try:
                data = json.loads(body)
                job_id = data.get('jobId', '')
                result = run_cron_job(job_id)
                
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

        super().do_POST()
    
    def do_GET(self):
        if self.path == '/api/system-stats':
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=0.1)
                mem = psutil.virtual_memory()
                disk = psutil.disk_usage('/')
                data = {'cpu': round(cpu, 1), 'memory': round(mem.percent, 1), 'disk': round(disk.percent, 1), 'memUsed': round(mem.used / (1024**3), 1), 'memTotal': round(mem.total / (1024**3), 1), 'diskUsed': round(disk.used / (1024**3), 1), 'diskTotal': round(disk.total / (1024**3), 1)}
            except ImportError:
                import subprocess
                cpu_out = subprocess.run(['grep', 'cpu ', '/proc/stat'], capture_output=True, text=True).stdout.split()
                idle = int(cpu_out[4]) if len(cpu_out) > 4 else 0
                total = sum(int(x) for x in cpu_out[1:]) if len(cpu_out) > 1 else 1
                cpu_pct = round(100 * (1 - idle / total), 1) if total else 0
                mem_out = subprocess.run(['free', '-b'], capture_output=True, text=True).stdout.split('\n')
                mem_parts = mem_out[1].split() if len(mem_out) > 1 else []
                mem_total = int(mem_parts[1]) if len(mem_parts) > 1 else 1
                mem_used = int(mem_parts[2]) if len(mem_parts) > 2 else 0
                mem_pct = round(100 * mem_used / mem_total, 1) if mem_total else 0
                disk_out = subprocess.run(['df', '/'], capture_output=True, text=True).stdout.split('\n')
                disk_parts = disk_out[1].split() if len(disk_out) > 1 else []
                disk_pct = int(disk_parts[4].replace('%', '')) if len(disk_parts) > 4 else 0
                data = {'cpu': cpu_pct, 'memory': mem_pct, 'disk': disk_pct, 'memUsed': round(mem_used / (1024**3), 1), 'memTotal': round(mem_total / (1024**3), 1)}
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
            return

        if self.path.startswith('/session/'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.end_headers()
            with open(os.path.join(DIR, 'session.html'), 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/logs' or self.path == '/logs/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            with open(os.path.join(DIR, 'logs.html'), 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/keys' or self.path == '/keys/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            with open(os.path.join(DIR, 'keys.html'), 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/cron' or self.path == '/cron/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            with open(os.path.join(DIR, 'cron.html'), 'rb') as f:
                self.wfile.write(f.read())
            return

        if self.path == '/system' or self.path == '/system/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            with open(os.path.join(DIR, 'system.html'), 'rb') as f:
                self.wfile.write(f.read())
            return

        # ── Anthropic API Key Settings (GET) ──
        if self.path.startswith('/api/settings/anthropic-key'):
            try:
                auth_store_path = '/root/.openclaw/agents/main/agent/auth-profiles.json'
                with open(auth_store_path) as f:
                    store = json.load(f)
                profile = store.get('profiles', {}).get('anthropic:default', {})
                token = profile.get('token', '')
                if token:
                    masked = '••••••••' + token[-4:] if len(token) > 4 else '••••'
                    _oat_pfx = 'sk-' + 'ant-oat'
                    key_type = 'oauth' if token.startswith(_oat_pfx) else 'api-key'
                else:
                    masked = 'not set'
                    key_type = 'none'
                self._json_response(200, {'masked': masked, 'type': key_type, 'hasKey': bool(token)})
            except Exception as e:
                self._json_response(500, {'error': str(e)})
            return

        if self.path.startswith('/api/keys/oauth/usage'):
            try:
                creds = _oauth_load_creds()
                accounts = creds.get('accounts', {})
                result = {}
                for email, account in accounts.items():
                    refreshed = _oauth_refresh_if_needed(account)
                    if not refreshed:
                        result[email] = {'error': 'refresh_failed', 'message': account.get('refreshError', 'Token refresh failed. Please re-login.')}
                        continue
                    # Save refreshed tokens
                    accounts[email] = refreshed
                    try:
                        usage = _oauth_get_usage(refreshed['accessToken'])
                        result[email] = {'ok': True, 'usage': usage, 'email': email, 'subscriptionType': refreshed.get('subscriptionType', ''), 'label': refreshed.get('label', ''), 'linkedKey': refreshed.get('linkedKey', '')}
                    except urllib.error.HTTPError as e:
                        try:
                            body = json.loads(e.read())
                            msg = body.get('error', {}).get('message', str(e))
                        except:
                            msg = str(e)
                        if e.code == 401:
                            result[email] = {'error': 'auth_failed', 'message': 'Re-login needed'}
                        else:
                            result[email] = {'error': 'api_error', 'message': msg}
                    except Exception as e:
                        result[email] = {'error': 'api_error', 'message': str(e)}
                _oauth_save_creds(creds)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path.startswith('/api/keys/usage'):
            try:
                # Load usage stats from auth-profiles.json
                with open('/root/.openclaw/agents/main/agent/auth-profiles.json') as f:
                    store = json.load(f)
                usage_stats = store.get('usageStats', {})

                # Load config for profile list
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                profiles = cfg.get('auth', {}).get('profiles', {})
                order = cfg.get('auth', {}).get('order', {})

                now = time.time() * 1000
                result = {'keys': {}, 'summary': {}}
                active_count = 0
                total_errors = 0

                for name in profiles:
                    stats = usage_stats.get(name, {})
                    last_used = stats.get('lastUsed', 0)
                    error_count = stats.get('errorCount', 0)
                    last_failure = stats.get('lastFailureAt', 0)
                    last_error = stats.get('lastError', '')

                    # Determine status
                    if last_used == 0 and last_failure == 0:
                        status = 'unused'
                    elif error_count > 0 and last_failure > last_used:
                        status = 'error'
                    elif last_used > 0 and (now - last_used) < 600000:  # 10 min
                        status = 'active'
                    elif last_used > 0:
                        status = 'idle'
                    else:
                        status = 'unused'

                    if status == 'active':
                        active_count += 1
                    total_errors += error_count

                    # Check if enabled
                    provider = profiles[name].get('provider', '')
                    provider_order = order.get(provider, [])
                    enabled = name in provider_order

                    result['keys'][name] = {
                        'lastUsed': last_used,
                        'errorCount': error_count,
                        'lastFailureAt': last_failure,
                        'lastError': last_error,
                        'status': status,
                        'enabled': enabled,
                    }

                # Find last rotation (most recent lastUsed across all keys)
                all_last_used = [s.get('lastUsed', 0) for s in usage_stats.values()]
                last_rotation = max(all_last_used) if all_last_used else 0

                result['summary'] = {
                    'activeKeys': active_count,
                    'totalKeys': len(profiles),
                    'totalErrors': total_errors,
                    'lastRotation': last_rotation,
                    'timestamp': now,
                }

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path.startswith('/api/keys'):
            try:
                with open('/root/.openclaw/openclaw.json') as f:
                    cfg = json.load(f)
                auth = cfg.get('auth', {})
                profiles = auth.get('profiles', {})
                order = auth.get('order', {})
                keys_data = []
                for name, prof in profiles.items():
                    provider = prof.get('provider', '')
                    provider_order = order.get(provider, [])
                    enabled = name in provider_order
                    position = provider_order.index(name) if enabled else -1
                    keys_data.append({
                        'name': name,
                        'provider': provider,
                        'mode': prof.get('mode', ''),
                        'enabled': enabled,
                        'position': position
                    })
                keys_data.sort(key=lambda x: (x['provider'], x['position'] if x['enabled'] else 999, x['name']))
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({'keys': keys_data, 'order': order}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        if self.path.startswith('/data/system.json'):
            data = json.dumps(get_system_info())
            self._send_json_gzipped(data)
            return

        # Cron jobs data endpoint
        if self.path == '/data/cron-jobs.json':
            data = json.dumps(get_cron_jobs())
            self._send_json_gzipped(data)
            return

        # Logs data endpoint
        if self.path.startswith('/data/logs.json'):
            parsed_url = urlparse(self.path)
            lparams = parse_qs(parsed_url.query)
            l_date = lparams.get('date', [None])[0]
            l_level_raw = lparams.get('level', [''])[0]
            l_level = set(x.upper() for x in l_level_raw.split(',') if x) or None
            l_limit = int(lparams.get('limit', [500])[0])
            l_offset = int(lparams.get('offset', [-1])[0])
            l_subsystem = lparams.get('subsystem', [''])[0] or None
            data = json.dumps(get_log_entries(l_date, l_level, l_limit, l_offset, l_subsystem))
            self._send_json_gzipped(data)
            return

        # SSE endpoint for live log streaming
        if self.path == '/data/logs/stream' or self.path.startswith('/data/logs/stream?'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'keep-alive')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            q = queue.Queue(maxsize=500)
            with sse_lock:
                sse_log_clients.add(q)
            try:
                # Send initial keepalive
                self.wfile.write(b': connected\n\n')
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f'data: {data}\n\n'.encode())
                        self.wfile.flush()
                    except queue.Empty:
                        # Send keepalive comment every 15s
                        self.wfile.write(b': keepalive\n\n')
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with sse_lock:
                    sse_log_clients.discard(q)
            return

        # Cron runs data endpoint
        if self.path.startswith('/data/cron-runs/'):
            job_id = self.path.replace('/data/cron-runs/', '')
            data = json.dumps(get_cron_runs(job_id))
            self._send_json_gzipped(data)
            return
        
        if self.path.startswith('/data/transcript/'):
            parsed_url = urlparse(self.path)
            sid = parsed_url.path.split('/data/transcript/')[1]
            tparams = parse_qs(parsed_url.query)
            t_limit = int(tparams.get('limit', [100])[0])
            t_offset = int(tparams.get('offset', [-1])[0])  # -1 means "last N"
            files = find_session_files(sid)
            if not files:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"not found"}')
                return

            entries = []
            used_files = []
            try:
                # Merge all matching files. Some Telegram/topic sessions have both
                # conversation jsonl and trajectory sidecar files; reading only the
                # newest file can select the sidecar and make the UI show "no entries".
                for f in sorted(files, key=os.path.getmtime):
                    file_had_entries = False
                    with open(f, 'r', errors='replace') as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                parsed_entry = parse_transcript_entry(json.loads(line))
                            except Exception:
                                continue
                            if parsed_entry:
                                entries.append(parsed_entry)
                                file_had_entries = True
                    if file_had_entries:
                        used_files.append(os.path.basename(f))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
                return

            entries.sort(key=lambda e: timestamp_sort_key(e.get('ts')))
            total = len(entries)
            # Pagination: default returns last 100 entries
            if t_offset == -1:
                # Last N entries
                paginated = entries[-t_limit:] if len(entries) > t_limit else entries
                actual_offset = max(0, total - t_limit)
            else:
                paginated = entries[t_offset:t_offset + t_limit]
                actual_offset = t_offset

            data = json.dumps({
                'file': used_files[-1] if len(used_files) == 1 else used_files,
                'files': used_files,
                'count': len(paginated),
                'total': total,
                'offset': actual_offset,
                'hasMore': actual_offset > 0 if t_offset == -1 else (t_offset + t_limit) < total,
                'entries': paginated
            })
            self._send_json_gzipped(data)
            return

        if self.path.startswith('/data/sessions.json'):
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            since = int(float(params.get('since', [0])[0]))
            
            # Check cache
            with _sessions_cache_lock:
                try:
                    current_mtime = os.path.getmtime(SESSIONS_FILE)
                except:
                    current_mtime = 0
                
                if _sessions_cache['data'] is None or current_mtime != _sessions_cache['mtime']:
                    raw_data = get_sessions_with_activity()
                    _sessions_cache['data'] = raw_data
                    _sessions_cache['mtime'] = current_mtime
                    _sessions_cache['etag'] = hashlib.md5(raw_data.encode()).hexdigest()[:16]
                    _sessions_cache['parsed'] = json.loads(raw_data)
                
                etag = _sessions_cache['etag']
                cached_parsed = _sessions_cache['parsed']
            
            # ETag support
            client_etag = self.headers.get('If-None-Match', '').strip('"')
            if client_etag == etag and not since:
                self.send_response(304)
                self.end_headers()
                return
            
            # If since param, filter to only changed sessions
            if since:
                filtered = [s for s in cached_parsed.get('sessions', []) if s.get('updatedAt', 0) > since]
                response_data = json.dumps({
                    'count': len(filtered),
                    'sessions': filtered,
                    'stats': cached_parsed.get('stats', {}),
                    'timestamp': cached_parsed.get('timestamp', 0),
                    'topicNames': cached_parsed.get('topicNames', {}),
                    'incremental': True
                })
            else:
                response_data = _sessions_cache['data']
            
            self._send_json_gzipped(response_data, etag)
            return

        # ── Serve inbound media (images / files attached in chats) ──
        if self.path.startswith('/media/inbound/'):
            self._serve_media(self.path[len('/media/'):])
            return

        super().do_GET()

    def _serve_media(self, rel):
        """Safely serve a file from /root/.openclaw/media/ (read-only, no traversal)."""
        from urllib.parse import unquote
        MEDIA_ROOT = '/root/.openclaw/media'
        rel = unquote(rel.split('?')[0].split('#')[0]).lstrip('/')
        full = os.path.normpath(os.path.join(MEDIA_ROOT, rel))
        # Path-traversal guard: resolved path must stay inside MEDIA_ROOT
        if not (full == MEDIA_ROOT or full.startswith(MEDIA_ROOT + os.sep)):
            self.send_response(403); self.end_headers(); self.wfile.write(b'forbidden'); return
        if not os.path.isfile(full):
            self.send_response(404); self.end_headers(); self.wfile.write(b'not found'); return
        ext = os.path.splitext(full)[1].lower()
        ctype = {
            '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.gif':'image/gif',
            '.webp':'image/webp', '.svg':'image/svg+xml', '.bmp':'image/bmp',
            '.ogg':'audio/ogg', '.mp3':'audio/mpeg', '.wav':'audio/wav', '.m4a':'audio/mp4',
            '.mp4':'video/mp4', '.webm':'video/webm', '.mov':'video/quicktime',
            '.pdf':'application/pdf', '.txt':'text/plain; charset=utf-8',
            '.md':'text/markdown; charset=utf-8', '.csv':'text/csv; charset=utf-8',
            '.json':'application/json', '.zip':'application/zip',
        }.get(ext, 'application/octet-stream')
        try:
            with open(full, 'rb') as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.send_header('Access-Control-Allow-Origin', '*')
            # Inline display for media; downloadable name for documents
            if ctype in ('application/octet-stream','application/zip','application/pdf'):
                self.send_header('Content-Disposition', f'inline; filename="{os.path.basename(full)}"')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(500); self.end_headers(); self.wfile.write(str(e).encode())

    def _json_response(self, status_code, data):
        """Send a JSON response with given status code."""
        raw = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_json_gzipped(self, data, etag=None):
        """Send JSON response with gzip if client supports it and data > 1KB."""
        raw = data.encode('utf-8') if isinstance(data, str) else data
        accept_enc = self.headers.get('Accept-Encoding', '')
        use_gzip = 'gzip' in accept_enc and len(raw) > 1024
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        if etag:
            self.send_header('ETag', f'"{etag}"')
        if use_gzip:
            compressed = gzip.compress(raw, compresslevel=6)
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Content-Length', str(len(compressed)))
            self.end_headers()
            try:
                self.wfile.write(compressed)
            except (BrokenPipeError, ConnectionResetError):
                pass
        else:
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def log_message(self, *a): pass

# WebSocket server for real-time updates
async def websocket_handler(websocket):
    ws_clients.add(websocket)
    try:
        with _gateway_live_lock:
            state = dict(gateway_live_state)
        await websocket.send(json.dumps({
            'type': 'gateway_live_status',
            'connected': state.get('connected', False),
            'mode': 'live' if state.get('connected') else 'degraded',
            'error': state.get('last_error'),
            'timestamp': state.get('updated_at', 0) * 1000,
        }))
        await websocket.wait_closed()
    finally:
        ws_clients.remove(websocket)

async def broadcast_update(message):
    if ws_clients:
        await asyncio.gather(
            *[ws.send(message) for ws in ws_clients.copy()],
            return_exceptions=True
        )


def _dashboard_broadcast(obj):
    """Thread-safe broadcast into the dashboard websocket server."""
    loop = globals().get('ws_loop')
    if loop is None or not loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(broadcast_update(json.dumps(obj)), loop)
    except Exception:
        pass


def _set_gateway_live_status(connected, error=None):
    with _gateway_live_lock:
        changed = gateway_live_state.get('connected') != connected or gateway_live_state.get('last_error') != error
        gateway_live_state.update({'connected': connected, 'last_error': error, 'updated_at': time.time()})
    if changed:
        _dashboard_broadcast({
            'type': 'gateway_live_status',
            'connected': connected,
            'mode': 'live' if connected else 'degraded',
            'error': error,
            'timestamp': time.time() * 1000,
        })


def _normalize_live_content(content):
    if isinstance(content, str):
        return [{'type': 'text', 'text': content[:5000]}]
    if not isinstance(content, list):
        return []
    parsed = []
    for c in content:
        if not isinstance(c, dict):
            continue
        t = c.get('type', '')
        if t in ('text', 'input_text', 'output_text'):
            txt = c.get('text') or c.get('content') or ''
            if txt:
                parsed.append({'type': 'text', 'text': str(txt)[:5000]})
        elif t in ('thinking', 'reasoning'):
            txt = c.get('thinking') or c.get('text') or ''
            if txt:
                parsed.append({'type': 'thinking', 'text': str(txt)[:3000]})
        elif t in ('toolCall', 'tool_call', 'toolUse', 'tool_use'):
            args = c.get('arguments', c.get('args', {}))
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {'raw': args[:2000]}
            parsed.append({'type': 'tool', 'name': c.get('name', c.get('toolName', '?')), 'args': args if isinstance(args, dict) else {}, 'id': c.get('id', c.get('toolCallId', ''))})
        elif t in ('toolResult', 'tool_result'):
            txt = c.get('text') or c.get('content') or c.get('result') or ''
            if not isinstance(txt, str):
                try:
                    txt = json.dumps(txt, ensure_ascii=False)
                except Exception:
                    txt = str(txt)
            parsed.append({'type': 'result', 'name': c.get('name', c.get('toolName', '?')), 'text': txt[:4000], 'id': c.get('id', c.get('toolCallId', ''))})
        elif t == 'image':
            src = c.get('source', {}) if isinstance(c.get('source'), dict) else {}
            url = src.get('url', '') or c.get('url', '') or c.get('image', '')
            parsed.append({'type': 'image', 'url': url})
    return parsed


def parse_gateway_live_message(payload):
    msg = payload.get('message') if isinstance(payload, dict) else None
    if not isinstance(msg, dict):
        return None
    content = _normalize_live_content(msg.get('content', []))
    if not content:
        text = msg.get('text') or msg.get('message')
        if text:
            content = [{'type': 'text', 'text': str(text)[:5000]}]
    if not content:
        return None
    usage = msg.get('usage', {}) if isinstance(msg.get('usage'), dict) else {}
    cost = usage.get('cost', {}) if isinstance(usage.get('cost'), dict) else {}
    return {
        'role': msg.get('role') or payload.get('role') or 'system',
        'model': msg.get('model', ''),
        'stop': msg.get('stopReason', msg.get('stop', '')),
        'ts': msg.get('timestamp') or payload.get('ts') or time.time() * 1000,
        'cost': cost.get('total', 0) or usage.get('costUsd', 0) or 0,
        'tokens': {
            'in': usage.get('input', usage.get('inputTokens', 0)) or 0,
            'out': usage.get('output', usage.get('outputTokens', 0)) or 0,
            'cache': usage.get('cacheRead', 0) or 0,
        },
        'content': content,
        '_liveMessageId': payload.get('messageId'),
        '_liveMessageSeq': payload.get('messageSeq'),
    }


async def _gateway_live_bridge_once():
    token = _get_gateway_token()
    port = _get_gateway_port()
    if not token:
        raise RuntimeError('gateway token not found')
    uri = f'ws://127.0.0.1:{port}'
    async with websockets.connect(
        uri,
        additional_headers={'Origin': f'http://127.0.0.1:{port}'},
        open_timeout=5,
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        await asyncio.wait_for(ws.recv(), timeout=5.0)  # connect.challenge
        await ws.send(json.dumps({
            'type': 'req', 'id': 'live-connect', 'method': 'connect',
            'params': {
                'minProtocol': 4, 'maxProtocol': 4,
                'client': {'id': 'openclaw-control-ui', 'mode': 'ui', 'version': '1.0', 'platform': 'linux'},
                'scopes': ['operator.read'],
                'auth': {'token': token},
            },
        }))
        connected = False
        while not connected:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
            if frame.get('type') == 'res' and frame.get('id') == 'live-connect':
                if not frame.get('ok'):
                    err = frame.get('error', {}) if isinstance(frame.get('error'), dict) else {}
                    raise RuntimeError(err.get('message') or 'gateway connect failed')
                connected = True
        await ws.send(json.dumps({'type': 'req', 'id': 'sessions-subscribe', 'method': 'sessions.subscribe', 'params': {}}))
        _set_gateway_live_status(True)
        while True:
            frame = json.loads(await ws.recv())
            if frame.get('type') != 'event':
                continue
            event = frame.get('event')
            payload = frame.get('payload') or {}
            if event == 'session.message':
                entry = parse_gateway_live_message(payload)
                _dashboard_broadcast({
                    'type': 'gateway_session_message',
                    'payload': payload,
                    'entry': entry,
                    'sessionKey': payload.get('sessionKey'),
                    'sessionId': payload.get('sessionId') or ((payload.get('session') or {}).get('sessionId') if isinstance(payload.get('session'), dict) else None),
                    'messageId': payload.get('messageId'),
                    'messageSeq': payload.get('messageSeq'),
                    'timestamp': time.time() * 1000,
                })
            elif event == 'sessions.changed':
                with _sessions_cache_lock:
                    _sessions_cache['data'] = None
                _dashboard_broadcast({'type': 'gateway_sessions_changed', 'payload': payload, 'timestamp': time.time() * 1000})


def _gateway_live_bridge_loop():
    while True:
        try:
            asyncio.run(_gateway_live_bridge_once())
        except Exception as e:
            _set_gateway_live_status(False, str(e)[:200])
            time.sleep(5)


def start_gateway_live_bridge():
    t = threading.Thread(target=_gateway_live_bridge_loop, daemon=True, name='gateway-live-bridge')
    t.start()
    return t

# Log file watcher for live tail
class LogFileWatcher(FileSystemEventHandler):
    def __init__(self):
        self.last_pos = {}
        self.last_broadcast = time.time()
        # Seed position to current end of today's log so we only broadcast NEW lines
        today_log = os.path.join(LOG_DIR, f'openclaw-{datetime.now().strftime("%Y-%m-%d")}.log')
        if os.path.exists(today_log):
            self.last_pos[today_log] = os.path.getsize(today_log)

    def on_modified(self, event):
        if event.is_directory:
            return
        src = event.src_path
        # Only watch today's log file
        today_log = os.path.join(LOG_DIR, f'openclaw-{datetime.now().strftime("%Y-%m-%d")}.log')
        if src != today_log:
            return
        self._read_new_lines(src)

    def on_created(self, event):
        if not event.is_directory:
            self.on_modified(event)

    def _read_new_lines(self, path):
        last_pos = self.last_pos.get(path, 0)
        try:
            file_size = os.path.getsize(path)
            if file_size < last_pos:
                # File was rotated/truncated
                last_pos = 0
            if file_size == last_pos:
                return
            with open(path, 'r', errors='replace') as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                self.last_pos[path] = f.tell()
            for raw_line in new_lines:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                entry = _parse_log_entry(raw_line)
                if entry is None:
                    continue
                msg = json.dumps({'type': 'log_line', 'entry': entry})
                asyncio.run_coroutine_threadsafe(
                    broadcast_update(msg),
                    ws_loop
                )
                # Also push to SSE clients
                sse_push_log(entry)
        except Exception:
            pass

# File watcher for sessions updates
class SessionsWatcher(FileSystemEventHandler):
    def __init__(self):
        self.last_update = time.time()
    
    def on_modified(self, event):
        if event.is_directory:
            return
        if event.src_path.endswith('sessions.json') or event.src_path.endswith('.jsonl'):
            # Invalidate cache
            with _sessions_cache_lock:
                _sessions_cache['data'] = None
            # Debounce - only update every 2 seconds
            now = time.time()
            if now - self.last_update > 2:
                self.last_update = now
                asyncio.run_coroutine_threadsafe(
                    broadcast_update(json.dumps({'type': 'sessions_updated', 'timestamp': now * 1000})),
                    ws_loop
                )

def start_file_watcher():
    observer = Observer()
    sessions_handler = SessionsWatcher()
    observer.schedule(sessions_handler, '/root/.openclaw/agents/main/sessions', recursive=True)
    # Watch log directory for live tail
    if os.path.isdir(LOG_DIR):
        log_handler = LogFileWatcher()
        observer.schedule(log_handler, LOG_DIR, recursive=False)
    observer.start()
    return observer

def start_websocket_server():
    global ws_loop
    ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(ws_loop)

    async def _run_ws():
        async with websockets.serve(websocket_handler, "0.0.0.0", WS_PORT) as server:
            print(f'WebSocket server listening on 0.0.0.0:{WS_PORT}')
            await asyncio.Future()  # run forever

    ws_loop.run_until_complete(_run_ws())

if __name__ == "__main__":
    # Start file watcher
    observer = start_file_watcher()
    
    # Start WebSocket server in background thread
    ws_thread = threading.Thread(target=start_websocket_server, daemon=True)
    ws_thread.start()

    # Start the read-only OpenClaw Gateway live-session bridge.  If it cannot
    # connect or auth, the existing file watcher/polling path remains active.
    start_gateway_live_bridge()
    
    try:
        with ReuseServer(('127.0.0.1', PORT), Handler) as s:
            print(f'Dashboard: http://localhost:{PORT}')
            print(f'WebSocket: ws://localhost:{WS_PORT}')
            s.serve_forever()
    except KeyboardInterrupt:
        observer.stop()
        observer.join()