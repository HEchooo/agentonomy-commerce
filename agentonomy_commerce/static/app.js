"use strict";
const el = (id) => document.getElementById(id);
let accessToken = "", activePreview = null, previewKey = null, lockedInput = null, busy = false;
const sample = "transaction_id,date,description,amount,currency,category\n" +
  "demo-001,2026-09-01,Synthetic hosting,-12.50,USD,software\n" +
  "demo-002,2026-09-02,Synthetic invoice,40.00,USD,revenue\n" +
  "demo-001,2026-09-01,Synthetic hosting,-12.50,USD,software\n";
function status(message, error = false) { el("status").textContent = message; el("status").classList.toggle("error", error); }
function controls() {
  for (const id of ["connect", "sample", "csv", "token"]) el(id).disabled = busy;
  for (const id of ["preview", "refresh", "lookup"]) el(id).disabled = busy || !accessToken;
  for (const id of ["purchase", "replay"]) el(id).disabled = busy || !accessToken || !activePreview;
}
async function request(path, body, key) {
  const headers = { Authorization: `Bearer ${accessToken}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  const response = await fetch(path, {method: body === undefined ? "GET" : "POST", headers,
    body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(55000)});
  const data = await response.json();
  if (!response.ok) throw new Error(`HTTP ${response.status} · ${data.error || data.detail || "请求未通过"}`);
  return data;
}
async function action(fn) {
  busy = true; controls();
  try { await fn(); } catch (error) { status(`${error.message}。若已发起购买，请查询原订单或重放当前购买，不要另建订单。`, true); }
  finally { busy = false; controls(); }
}
async function budget() {
  const data = await request("/v1/budget");
  el("remaining").textContent = data.remaining_amount_usdc;
  el("used").textContent = data.used_amount_usdc;
  el("settlements").textContent = data.settlement_submissions;
  el("deliveries").textContent = data.merchant_deliveries;
}
function showOrder(data) {
  el("result").textContent = JSON.stringify(data, null, 2);
  el("order-state").textContent = data.state;
  if (data.purchase_id) el("purchase-id").value = data.purchase_id;
  if (data.preview_id) activePreview = data.preview_id;
  const delivered = data.state === "delivered";
  status(delivered ? "服务已交付。可查询订单，或重放同一笔购买核对预算是否保持不变。" : `订单状态：${data.state}。${data.reason_code || "请保留当前订单并检查结果。"}`, !delivered);
}
el("connect").addEventListener("click", () => action(async () => {
  accessToken = el("token").value.trim(); el("token").value = "";
  try { await budget(); status("已连接。载入示例后锁定报价，不会立即扣款。"); }
  catch (error) { accessToken = ""; throw error; }
}));
function inputChanged() {
  activePreview = null; previewKey = null; lockedInput = null;
  el("preview-note").textContent = "输入已更新，请重新锁定报价。"; controls();
}
el("csv").addEventListener("input", inputChanged);
el("sample").addEventListener("click", () => { el("csv").value = sample; inputChanged(); });
el("preview").addEventListener("click", () => action(async () => {
  const csv = el("csv").value;
  if (!csv.trim()) throw new Error("请先填写 CSV 或载入合成示例");
  if (csv !== lockedInput || !previewKey) { previewKey = crypto.randomUUID(); lockedInput = csv; }
  const data = await request("/v1/previews", {offering_id: "csv-reconciliation-v1", csv_text: csv}, previewKey);
  activePreview = data.preview_id;
  el("preview-note").textContent = `报价已锁定 · ${data.preview_id} · 有效至 ${data.expires_at}`;
  status("报价已锁定，尚未扣款。确认后使用 0.30 模拟 USDC 获取报告。");
}));
async function purchase() {
  if (!activePreview) throw new Error("请先锁定报价");
  status("正在核验预算并请求商家交付…");
  showOrder(await request("/v1/purchases", {preview_id: activePreview})); await budget();
}
el("purchase").addEventListener("click", () => action(purchase));
el("replay").addEventListener("click", () => action(purchase));
el("refresh").addEventListener("click", () => action(async () => { await budget(); status("预算已刷新。"); }));
el("lookup").addEventListener("click", () => action(async () => {
  const id = el("purchase-id").value.trim();
  if (!id) throw new Error("请填写订单 ID");
  showOrder(await request(`/v1/purchases/${encodeURIComponent(id)}`)); await budget();
}));
fetch("/health").then((r) => r.json()).then((data) => {
  el("build").textContent = `服务 ${data.status} · commit ${data.commit.slice(0, 12)}`;
  el("build").title = data.commit;
}).catch(() => { el("build").textContent = "服务暂不可用"; });
controls();
