// Cloudflare Worker: принимает отчёты об ошибках SheqelMotion и пересылает их в Telegram.
// Секреты задаются в настройках воркера, а не в коде (см. RELAY_SETUP.md):
//   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — обязательно;
//   RATE_KV (KV-привязка), PER_INSTALL_PER_HOUR, GLOBAL_PER_DAY — необязательно.

const MAX_BODY_BYTES = 16 * 1024;
const TELEGRAM_TEXT_LIMIT = 3900;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const JOB_ID_RE = /^[A-Za-z0-9-]{6,64}$/;
const REQUIRED_FIELDS = {
  app_version: 20,
  os: 120,
  python: 20,
  where: 120,
  exc_type: 80,
  message: 600,
  time: 30,
};
const MAX_LOG_LINES = 30;
const MAX_LOG_LINE = 300;

// Без KV счётчики живут только в памяти экземпляра воркера — лучше, чем ничего.
const memoryCounters = new Map();

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method Not Allowed", { status: 405 });
    }
    if (!(request.headers.get("content-type") || "").includes("application/json")) {
      return new Response("Unsupported Media Type", { status: 415 });
    }
    const declared = Number(request.headers.get("content-length") || 0);
    if (declared > MAX_BODY_BYTES) {
      return new Response("Payload Too Large", { status: 413 });
    }
    const raw = await request.text();
    if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) {
      return new Response("Payload Too Large", { status: 413 });
    }

    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      return new Response("Bad JSON", { status: 400 });
    }
    const report = validate(data);
    if (!report) {
      return new Response("Bad Request", { status: 400 });
    }

    const perInstall = Number(env.PER_INSTALL_PER_HOUR || 10);
    const perDay = Number(env.GLOBAL_PER_DAY || 300);
    const hour = new Date().toISOString().slice(0, 13);
    const day = hour.slice(0, 10);
    if (!(await allow(env, `install:${report.install_id}:${hour}`, perInstall, 3600))) {
      return new Response("Too Many Requests", { status: 429 });
    }
    if (!(await allow(env, `global:${day}`, perDay, 86400))) {
      return new Response("Too Many Requests", { status: 429 });
    }

    if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) {
      return new Response("Relay not configured", { status: 500 });
    }
    const tg = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        chat_id: env.TELEGRAM_CHAT_ID,
        text: formatMessage(report),
        parse_mode: "HTML",
        disable_web_page_preview: true,
      }),
    });
    return new Response(tg.ok ? "ok" : "telegram error", { status: tg.ok ? 200 : 502 });
  },
};

export function validate(data) {
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  if (typeof data.install_id !== "string" || !UUID_RE.test(data.install_id)) return null;

  const report = { install_id: data.install_id };
  for (const [field, limit] of Object.entries(REQUIRED_FIELDS)) {
    if (typeof data[field] !== "string" || data[field].length === 0) return null;
    report[field] = data[field].slice(0, limit);
  }

  const tail = data.log_tail ?? [];
  if (!Array.isArray(tail) || tail.length > MAX_LOG_LINES) return null;
  if (!tail.every((line) => typeof line === "string")) return null;
  report.log_tail = tail.map((line) => line.slice(0, MAX_LOG_LINE));

  if (data.job_id === null || data.job_id === undefined || data.job_id === "") {
    report.job_id = null;
  } else if (typeof data.job_id === "string" && JOB_ID_RE.test(data.job_id)) {
    report.job_id = data.job_id;
  } else {
    return null;
  }
  return report;
}

async function allow(env, key, limit, ttlSeconds) {
  if (env.RATE_KV) {
    const current = Number((await env.RATE_KV.get(key)) || 0);
    if (current >= limit) return false;
    await env.RATE_KV.put(key, String(current + 1), { expirationTtl: Math.max(60, ttlSeconds) });
    return true;
  }
  const now = Date.now();
  const entry = memoryCounters.get(key);
  if (!entry || entry.expires < now) {
    memoryCounters.set(key, { count: 1, expires: now + ttlSeconds * 1000 });
    return true;
  }
  if (entry.count >= limit) return false;
  entry.count += 1;
  return true;
}

function escapeHtml(text) {
  return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

export function formatMessage(r) {
  const head = [
    `🐞 <b>SheqelMotion ${escapeHtml(r.app_version)}</b> — <code>${escapeHtml(r.exc_type)}</code>`,
    `📍 <code>${escapeHtml(r.where)}</code>`,
    `💬 <pre>${escapeHtml(r.message)}</pre>`,
  ];
  if (r.job_id) head.push(`🎬 Sync job: <code>${escapeHtml(r.job_id)}</code>`);
  head.push(
    `💻 ${escapeHtml(r.os)} · Python ${escapeHtml(r.python)}`,
    `🆔 установка <code>${escapeHtml(r.install_id.slice(0, 8))}</code> · 🕒 ${escapeHtml(r.time)}`,
  );
  let text = head.join("\n");

  if (r.log_tail.length) {
    const prefix = "\n📜 Последние строки лога:\n<pre>";
    const suffix = "</pre>";
    let budget = TELEGRAM_TEXT_LIMIT - text.length - prefix.length - suffix.length;
    const kept = [];
    // Берём строки с конца целиком, чтобы не разрезать HTML-сущность.
    for (const line of r.log_tail.map(escapeHtml).reverse()) {
      if (line.length + 1 > budget) break;
      kept.unshift(line);
      budget -= line.length + 1;
    }
    if (kept.length) text += prefix + kept.join("\n") + suffix;
  }
  return text;
}
