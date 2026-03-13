const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const Redis = require('ioredis');
const path = require('path');

const app = express();
const server = http.createServer(app);
const wss = new WebSocket.Server({ server });

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://orchestrator:8080';
const REDIS_URL = process.env.REDIS_URL || 'redis://redis:6379/0';

const redis = new Redis(REDIS_URL);
const redisSub = new Redis(REDIS_URL);

// Serve static files
app.use(express.static(path.join(__dirname, 'public')));

// Proxy API calls to orchestrator
app.use('/api', async (req, res) => {
  try {
    const url = `${ORCHESTRATOR_URL}${req.originalUrl}`;
    const fetchOpts = {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (req.method === 'POST') {
      let body = '';
      req.on('data', chunk => body += chunk);
      await new Promise(resolve => req.on('end', resolve));
      fetchOpts.body = body;
    }
    const response = await fetch(url, fetchOpts);
    const data = await response.text();
    res.status(response.status).set('Content-Type', 'application/json').send(data);
  } catch (err) {
    res.status(502).json({ error: 'orchestrator unreachable', details: err.message });
  }
});

// Stream monitoring - poll Redis streams for activity
const STREAMS = [
  'tasks:pending_routing',
  'tasks:routed',
  'tasks:pending_spec',
  'tasks:specced',
  'tasks:pending_dev',
  'tasks:developed',
  'tasks:pending_review',
  'tasks:reviewed',
];

function broadcast(data) {
  const msg = JSON.stringify(data);
  wss.clients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(msg);
    }
  });
}

// Poll streams for new messages
async function pollStreams() {
  const lastIds = {};
  STREAMS.forEach(s => lastIds[s] = '$');

  // Get current last IDs
  for (const stream of STREAMS) {
    try {
      const info = await redis.xinfo('STREAM', stream).catch(() => null);
      if (info) {
        lastIds[stream] = '$';
      }
    } catch (e) {
      // Stream doesn't exist yet
    }
  }

  while (true) {
    try {
      const args = [];
      const streamNames = [];
      for (const s of STREAMS) {
        streamNames.push(s);
      }
      for (const s of STREAMS) {
        args.push(lastIds[s]);
      }

      const results = await redis.xread('COUNT', 10, 'BLOCK', 1000, 'STREAMS', ...streamNames, ...args);

      if (results) {
        for (const [stream, messages] of results) {
          for (const [id, fields] of messages) {
            lastIds[stream] = id;
            let taskData = {};
            try {
              taskData = JSON.parse(fields[1]); // fields = [key, value]
            } catch (e) {}

            broadcast({
              type: 'stream_event',
              stream,
              messageId: id,
              taskId: taskData.id || 'unknown',
              taskStatus: taskData.status || 'unknown',
              timestamp: new Date().toISOString(),
              data: taskData,
            });
          }
        }
      }
    } catch (err) {
      if (!err.message.includes('NOGROUP')) {
        console.error('Poll error:', err.message);
      }
      await new Promise(r => setTimeout(r, 1000));
    }
  }
}

wss.on('connection', (ws) => {
  console.log('WebUI client connected');
  ws.send(JSON.stringify({ type: 'connected', streams: STREAMS }));
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`Finit WebUI listening on :${PORT}`);
  pollStreams();
});
