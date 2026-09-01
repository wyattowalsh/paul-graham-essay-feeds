const LATEST_N = 20;
const RAW =
  "https://raw.githubusercontent.com/wyattowalsh/paul-graham-essay-feeds/main/feeds/";
const CACHE = "public, max-age=300";

const MIME = {
  rss: "application/rss+xml; charset=utf-8",
  atom: "application/atom+xml; charset=utf-8",
  json: "application/feed+json; charset=utf-8",
  html: "text/html; charset=utf-8",
};

const FILES = {
  "/rss.xml": { file: "rss.xml", kind: "rss" },
  "/atom.xml": { file: "atom.xml", kind: "atom" },
  "/feed.json": { file: "feed.json", kind: "json" },
  "/rss.simple.xml": { file: "rss.simple.xml", kind: "rss" },
  "/atom.simple.xml": { file: "atom.simple.xml", kind: "atom" },
  "/feed.simple.json": { file: "feed.simple.json", kind: "json" },
};

const LATEST = {
  "/latest/rss.xml": { file: "rss.xml", kind: "rss" },
  "/latest/atom.xml": { file: "atom.xml", kind: "atom" },
  "/latest/feed.json": { file: "feed.json", kind: "json" },
  "/latest/rss.simple.xml": { file: "rss.simple.xml", kind: "rss" },
  "/latest/atom.simple.xml": { file: "atom.simple.xml", kind: "atom" },
  "/latest/feed.simple.json": { file: "feed.simple.json", kind: "json" },
};

export default {
  async fetch(request, env) {
    try {
      return await handle(request, env);
    } catch (error) {
      console.error(
        JSON.stringify({
          message: "unhandled",
          error: error instanceof Error ? error.message : String(error),
        }),
      );
      return new Response("Internal error\n", { status: 500 });
    }
  },
};

async function handle(request, env) {
  const url = new URL(request.url);
  const path = url.pathname.replace(/\/+$/, "") || "/";
  const method = request.method.toUpperCase();

  if (method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }
  if (method !== "GET" && method !== "HEAD") {
    return new Response("Method not allowed\n", {
      status: 405,
      headers: { allow: "GET, HEAD, OPTIONS" },
    });
  }

  if (path === "/" || path === "/index.html") {
    const body = indexHtml(url.origin);
    return respond(method, body, {
      "content-type": MIME.html,
      "cache-control": CACHE,
    });
  }

  const latest = LATEST[path];
  const spec = latest || FILES[path];
  if (!spec) {
    return new Response("Not found\n", { status: 404 });
  }

  const loaded = await loadFeed(env, request, spec.file);
  if (loaded === null) {
    return new Response("Feed unavailable\n", { status: 502 });
  }

  if (!latest) {
    const headers = feedHeaders(spec.kind, loaded.etag);
    if (notModified(request, headers.etag)) {
      return new Response(null, { status: 304, headers: headers.values });
    }
    return respond(method, loaded.body, headers.values);
  }

  let payload;
  try {
    payload = sliceLatest(loaded.body, spec.kind);
  } catch (error) {
    console.error(
      JSON.stringify({
        message: "latest-slice-failed",
        path,
        error: error instanceof Error ? error.message : String(error),
      }),
    );
    return new Response("Feed unavailable\n", { status: 502 });
  }
  const etag = await hashEtag(payload);
  const headers = feedHeaders(spec.kind, etag);
  if (notModified(request, etag)) {
    return new Response(null, { status: 304, headers: headers.values });
  }
  return respond(method, payload, headers.values);
}

function corsHeaders() {
  return {
    allow: "GET, HEAD, OPTIONS",
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET, HEAD, OPTIONS",
    "access-control-max-age": "86400",
  };
}

function feedHeaders(kind, etag) {
  const values = {
    "content-type": MIME[kind],
    "cache-control": CACHE,
    "x-content-type-options": "nosniff",
    "access-control-allow-origin": "*",
    "referrer-policy": "no-referrer",
  };
  if (etag) {
    values.etag = etag;
  }
  return { etag, values };
}

function respond(method, body, headers) {
  if (method === "HEAD") {
    const length =
      typeof body === "string" ? new TextEncoder().encode(body).byteLength : body.byteLength;
    return new Response(null, {
      headers: { ...headers, "content-length": String(length) },
    });
  }
  return new Response(body, { headers });
}

function notModified(request, etag) {
  if (!etag) {
    return false;
  }
  const incoming = request.headers.get("if-none-match");
  return incoming !== null && incoming.split(",").some((part) => part.trim() === etag);
}

async function loadFeed(env, request, file) {
  if (env.ASSETS) {
    const asset = await env.ASSETS.fetch(new URL(`/${file}`, request.url));
    if (asset.ok) {
      const body = await asset.arrayBuffer();
      return { body, etag: asset.headers.get("etag") };
    }
  }
  const remote = await fetch(RAW + file, { cf: { cacheTtl: 300 } });
  if (!remote.ok) {
    return null;
  }
  const body = await remote.arrayBuffer();
  return { body, etag: remote.headers.get("etag") };
}

async function hashEtag(payload) {
  const bytes = typeof payload === "string" ? new TextEncoder().encode(payload) : payload;
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  const hex = [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return `"${hex}"`;
}

function sliceLatest(buf, kind) {
  const text = new TextDecoder("utf-8").decode(buf);
  if (kind === "json") {
    const data = JSON.parse(text);
    if (Array.isArray(data.items)) {
      data.items = data.items.slice(0, LATEST_N);
    }
    if (typeof data.title === "string" && !data.title.includes("Latest")) {
      data.title = data.title
        .replace("Enriched", "Latest enriched")
        .replace("Simple", "Latest simple");
    }
    return JSON.stringify(data);
  }
  const tag = kind === "atom" ? "entry" : "item";
  const re = new RegExp(`<${tag}\\b[\\s\\S]*?<\\/${tag}>`, "gi");
  const matches = text.match(re) || [];
  if (matches.length <= LATEST_N) {
    return text;
  }
  const keep = matches.slice(0, LATEST_N).join("\n");
  const first = text.indexOf(matches[0]);
  const lastItem = matches[matches.length - 1];
  const last = text.lastIndexOf(lastItem) + lastItem.length;
  return text.slice(0, first) + keep + text.slice(last);
}

function indexHtml(origin) {
  const rows = [
    ["RSS 2.0", "/rss.xml", "/rss.simple.xml", "/latest/rss.xml"],
    ["Atom 1.0", "/atom.xml", "/atom.simple.xml", "/latest/atom.xml"],
    ["JSON Feed 1.1", "/feed.json", "/feed.simple.json", "/latest/feed.json"],
  ];
  const body = rows
    .map(
      ([name, full, simple, latest]) =>
        `<tr><th scope="row">${name}</th><td><a href="${full}">full</a></td><td><a href="${simple}">simple</a></td><td><a href="${latest}">latest 20</a></td></tr>`,
    )
    .join("");
  return `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Paul Graham essay feeds (unofficial)</title>
<link rel="alternate" type="application/rss+xml" title="Simple RSS" href="${origin}/rss.simple.xml">
<link rel="alternate" type="application/atom+xml" title="Simple Atom" href="${origin}/atom.simple.xml">
<link rel="alternate" type="application/feed+json" title="Simple JSON Feed" href="${origin}/feed.simple.json">
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0 auto; max-width: 40rem; padding: 18vh 1.25rem 3rem;
    font: 16px/1.5 ui-sans-serif, system-ui, sans-serif;
    background: light-dark(#f4efe4, #12100e);
    color: light-dark(#1a1714, #f3eee6);
  }
  .kicker {
    font-size: 0.72rem; letter-spacing: 0.16em; text-transform: uppercase;
    color: light-dark(#9b2f12, #ff6b3d); margin: 0 0 0.85rem;
  }
  h1 {
    font: 600 2.15rem/1.1 "Iowan Old Style", Palatino, "Palatino Linotype", Georgia, serif;
    letter-spacing: -0.03em; margin: 0 0 0.75rem;
  }
  p { color: light-dark(#5c564e, #b9b1a6); margin: 0 0 1.5rem; max-width: 34rem; }
  a { color: inherit; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 0.7rem 0; border-bottom: 1px solid light-dark(#e4dccf, #2a2622); }
  th:first-child { font-weight: 650; }
  code { font-size: 0.86em; }
  footer { margin-top: 2rem; font-size: 0.82rem; color: light-dark(#8a8378, #8f877c); }
</style>
<p class="kicker">Unofficial · metadata only</p>
<h1>Paul Graham essay feeds</h1>
<p>Titles, links, and short excerpts — never complete essays. Served with the correct RSS, Atom, and JSON Feed types from <code>${origin}</code>.</p>
<table>
  <thead><tr><th>Format</th><th>Enriched</th><th>Simple</th><th>Latest</th></tr></thead>
  <tbody>${body}</tbody>
</table>
<footer>Same bytes as the GitHub <code>feeds/</code> tree. Simple is title-only; enriched adds short source excerpts.</footer>
</html>
`;
}
