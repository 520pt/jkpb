import crypto from 'node:crypto';
import fs from 'node:fs';
import readline from 'node:readline';
import { WSClient, generateReqId } from '@wecom/aibot-node-sdk';

const config = JSON.parse(process.argv[2] || '{}');
if (!config.bot_id || !config.secret) {
  process.stdout.write(`${JSON.stringify({ type: 'error', message: 'Bot ID 或 Secret 未配置' })}\n`);
  process.exit(1);
}

const emit = (payload) => process.stdout.write(`${JSON.stringify(payload)}\n`);
const log = (...args) => process.stderr.write(`${args.map(String).join(' ')}\n`);
const logger = { debug: () => {}, info: log, warn: log, error: log };
const client = new WSClient({
  botId: config.bot_id,
  secret: config.secret,
  maxReconnectAttempts: -1,
  logger,
});

const seenMsgIds = new Set();
const rememberMsgId = (msgid) => {
  if (!msgid || seenMsgIds.has(msgid)) return false;
  seenMsgIds.add(msgid);
  if (seenMsgIds.size > 1000) seenMsgIds.delete(seenMsgIds.values().next().value);
  return true;
};

const emitMessage = (frame, content) => {
  const body = frame.body || {};
  if (!rememberMsgId(body.msgid)) return;
  emit({
    type: 'message',
    headers: frame.headers || {},
    stream_id: generateReqId('duty'),
    text: String(content || '').trim(),
    msgid: body.msgid || '',
    chatid: body.chatid || '',
    chattype: body.chattype || '',
    userid: body.from?.userid || '',
    received_at: new Date().toISOString(),
  });
};

client.on('connected', () => emit({ type: 'status', status: 'connected' }));
client.on('authenticated', () => emit({ type: 'status', status: 'authenticated' }));
client.on('disconnected', (reason) => emit({ type: 'status', status: 'disconnected', message: String(reason || '') }));
client.on('reconnecting', (attempt) => emit({ type: 'status', status: 'reconnecting', message: `第 ${attempt} 次重连` }));
client.on('error', (error) => emit({ type: 'error', message: error?.message || String(error) }));
client.on('message.text', (frame) => emitMessage(frame, frame.body?.text?.content));
client.on('message.voice', (frame) => emitMessage(frame, frame.body?.voice?.content));

const reply = async (command, finish) => {
  const frame = { headers: command.headers || {} };
  const streamId = command.stream_id || generateReqId('duty');
  const content = String(command.content || '');
  let items;
  if (finish && command.image_path) {
    const image = fs.readFileSync(command.image_path);
    items = [{
      msgtype: 'image',
      image: {
        base64: image.toString('base64'),
        md5: crypto.createHash('md5').update(image).digest('hex'),
      },
    }];
  }
  await client.replyStream(frame, streamId, content, finish, items);
};

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async (line) => {
  let command;
  try {
    command = JSON.parse(line);
    if (command.type === 'stop') {
      client.disconnect();
      process.exit(0);
    }
    if (command.type === 'reply_progress') await reply(command, false);
    if (command.type === 'reply_final') await reply(command, true);
    emit({ type: 'reply_result', success: true, command: command.type });
  } catch (error) {
    emit({
      type: 'reply_result',
      success: false,
      command: command?.type || '',
      message: error?.message || String(error),
    });
  }
});

process.on('SIGINT', () => {
  client.disconnect();
  process.exit(0);
});
process.on('SIGTERM', () => {
  client.disconnect();
  process.exit(0);
});

client.connect();
