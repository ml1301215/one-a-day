// SVG → 2x PNG 渲染器（无头 Edge/Chrome + CDP，只用 Node 内置能力）。
//
// 用法：
//   node tools/figures/render.cjs                  // 渲染 docs/assets/architecture/*.svg
//   node tools/figures/render.cjs --scale 2        // 默认 2 倍
//
// 为什么不用 sharp/resvg：本机没有那些依赖，而 Edge 自带、渲染结果就是设计稿的样子
// （字体、mix-blend-mode、feTurbulence 全部真实生效）。

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.resolve(__dirname, "..", "..");
const SVG_DIR = path.join(ROOT, "docs", "assets", "architecture");
const PORT = 9444;

const EDGE_CANDIDATES = [
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function cdp(ws) {
  let id = 0;
  const pending = new Map();
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) {
      const { resolve, reject } = pending.get(m.id);
      pending.delete(m.id);
      m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
    }
  };
  await new Promise((res, rej) => ((ws.onopen = res), (ws.onerror = rej)));
  return (method, params = {}) =>
    new Promise((resolve, reject) => {
      const mid = ++id;
      pending.set(mid, { resolve, reject });
      ws.send(JSON.stringify({ id: mid, method, params }));
    });
}

async function main() {
  const argOf = (name, dflt) => {
    const i = process.argv.indexOf(name);
    return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : dflt;
  };
  const scale = Number(argOf("--scale", "2"));
  // 纸张颗粒（feTurbulence）让 PNG 几乎压不动：3200×2000 单张 2.6–3 MB。
  // JPEG q92 对这套「大字、平色块」的图几乎没有可见损失，体积降到 1/6。
  const format = argOf("--format", "png");
  const quality = Number(argOf("--quality", "92"));
  if (!fs.existsSync(SVG_DIR)) throw new Error("找不到 " + SVG_DIR);
  const svgs = fs.readdirSync(SVG_DIR).filter((f) => f.endsWith(".svg")).sort();
  if (!svgs.length) throw new Error("没有 SVG，先跑 python tools/figures/generate.py");

  const exe = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!exe) throw new Error("找不到 Edge/Chrome");

  const profile = path.join(process.env.TEMP || "/tmp", "figs-render-profile");
  const child = spawn(
    exe,
    [
      "--headless=new",
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      "--no-first-run",
      "--disable-gpu",
      "--hide-scrollbars",
      "--force-device-scale-factor=1",
      "about:blank",
    ],
    { stdio: "ignore", detached: false }
  );

  let targets = [];
  for (let i = 0; i < 60; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (targets.some((t) => t.type === "page")) break;
    } catch {}
    await sleep(250);
  }
  const page = targets.find((t) => t.type === "page");
  if (!page) throw new Error("CDP 没起来");

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  const send = await cdp(ws);
  await send("Page.enable");
  await send("Runtime.enable");

  for (const f of svgs) {
    const file = path.join(SVG_DIR, f);
    const svg = fs.readFileSync(file, "utf8");
    const m = svg.match(/width="(\d+)"\s+height="(\d+)"/);
    const W = Number(m[1]);
    const Hh = Number(m[2]);
    await send("Emulation.setDeviceMetricsOverride", {
      width: W,
      height: Hh,
      deviceScaleFactor: 1,
      mobile: false,
    });
    await send("Page.navigate", { url: "file:///" + file.replace(/\\/g, "/") });
    await sleep(650);
    const shot = await send("Page.captureScreenshot", {
      format,
      ...(format === "jpeg" ? { quality } : {}),
      captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: W, height: Hh, scale },
    });
    const out = file.replace(/\.svg$/, format === "jpeg" ? ".jpg" : ".png");
    fs.writeFileSync(out, Buffer.from(shot.data, "base64"));
    console.log("rendered", path.relative(ROOT, out), `${W * scale}x${Hh * scale}`);
  }

  ws.close();
  child.kill();
}

main().catch((e) => {
  console.error("FAILED:", e.message);
  process.exit(1);
});
