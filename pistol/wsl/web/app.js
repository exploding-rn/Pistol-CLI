"use strict";
const $ = (id) => document.getElementById(id);
const fragment = new URLSearchParams(location.hash.slice(1));
let token = fragment.get("token") || sessionStorage.getItem("pistol-wsl-token");
if (fragment.has("token")) {
  sessionStorage.setItem("pistol-wsl-token", token);
  history.replaceState(null, "", location.pathname);
}
let activePage = "terminal", home = "/", currentPath = "/", parentPath = "/";
let pathHistory = [], historyIndex = -1, editor = null, socket, term, fit;
const datasets = {}, filters = {};
function notice(message) { $("notice").textContent = message; $("notice").hidden = !message; }
function bytes(value) { return value >= 1073741824 ? (value / 1073741824).toFixed(1) + " GiB" : value >= 1048576 ? (value / 1048576).toFixed(1) + " MiB" : value >= 1024 ? (value / 1024).toFixed(1) + " KiB" : value + " B"; }
function duration(seconds) { return Math.floor(seconds / 3600) + "h " + Math.floor(seconds % 3600 / 60) + "m"; }
async function api(path, options = {}) {
  const response = await fetch("/api/" + path, {...options, headers: {Authorization: "Bearer " + (token || ""), ...(options.headers || {})}});
  if (!response.ok) {
    const body = await response.text();
    let message; try { message = JSON.parse(body).error; } catch { message = body; }
    throw new Error(message || "The console could not complete this request.");
  }
  return options.download ? response.blob() : response.json();
}
const post = (path, body) => api(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
async function perform(fn) { try { notice(""); await fn(); } catch (error) { notice(error.message); } }
function button(label, handler, style = "") {
  const node = document.createElement("button"); node.textContent = label; node.className = style;
  node.onclick = () => perform(async () => { node.disabled = true; try { await handler(); } finally { node.disabled = false; } });
  return node;
}
function table(target, columns, rows) {
  const container = $(target); container.replaceChildren();
  if (!rows.length) { const empty = document.createElement("div"); empty.className = "empty"; empty.textContent = "Nothing to show here."; container.append(empty); return; }
  const grid = document.createElement("table"), head = document.createElement("thead"), body = document.createElement("tbody"), tr = document.createElement("tr");
  for (const [title] of columns) { const th = document.createElement("th"); th.textContent = title; tr.append(th); }
  head.append(tr); grid.append(head, body);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const [, field] of columns) {
      const td = document.createElement("td"), value = typeof field === "function" ? field(row) : row[field];
      if (value instanceof Node) td.append(value); else td.textContent = value ?? "—";
      tr.append(td);
    }
    body.append(tr);
  }
  container.append(grid);
}
function rowActions(...nodes) { const div = document.createElement("div"); div.className = "actions"; div.append(...nodes); return div; }
function filtered(page) { const query = (filters[page] || "").toLowerCase(); return (datasets[page] || []).filter(row => Object.values(row).join(" ").toLowerCase().includes(query)); }
function render(page) {
  const rows = filtered(page);
  if (page === "processes") table("processes-list", [["PID", "pid"], ["User", "user"], ["CPU", r => r.cpu + "%"], ["RAM", r => bytes(r.ram)], ["Command", "command"], ["", r => r.owned ? button("Terminate", async () => {
    if (confirm(`Terminate PID ${r.pid}?\n${r.command}`)) { await post("processes", {pid:r.pid, created:r.created, confirmed:true}); await load("processes"); }
  }, "danger") : ""]], rows);
  if (page === "ports") table("ports-list", [["Port", "port"], ["Protocol", "protocol"], ["Process", "process"], ["PID", "pid"], ["Address", "address"], ["Windows localhost", "forwarding"]], rows);
  if (page === "services") table("services-list", [["Service", "service"], ["State", "state"], ["Description", "description"], ["Actions", r => rowActions(...["start", "stop", "restart"].map(action => button(action, async () => {
    if (confirm(`${action} ${r.service}?`)) { await post("services", {service:r.service, action, scope:$("service-scope").value, confirmed:true}); await load("services"); }
  }))) ]], rows);
  if (page === "chambers") table("chambers-list", [["Name", "name"], ["Status", "status"], ["Port", "port"], ["Entrypoint", "entrypoint"], ["Path", r => button(r.path, async () => { await navigateFiles(r.path); await showPage("files"); }, "link")], ["Registry", "source"]], rows);
}
async function load(page) {
  if (!["processes", "ports", "services", "chambers"].includes(page)) return;
  const result = await api(page + (page === "services" ? "?scope=" + $("service-scope").value : ""));
  datasets[page] = Array.isArray(result) ? result : result[page];
  if ($(page + "-note")) $(page + "-note").textContent = result.message || "";
  render(page);
}
async function showPage(page) {
  activePage = page;
  document.querySelectorAll(".page").forEach(node => node.classList.toggle("active", node.id === page));
  document.querySelectorAll("nav button").forEach(node => node.classList.toggle("active", node.dataset.page === page));
  if (page === "terminal") { fit?.fit(); term?.focus(); }
  else if (page === "files" && !pathHistory.length) await navigateFiles(home);
  else await load(page);
}
document.querySelectorAll("nav button").forEach(node => node.onclick = () => perform(() => showPage(node.dataset.page)));
document.querySelectorAll(".filter").forEach(node => node.oninput = () => { filters[node.dataset.filter] = node.value; render(node.dataset.filter); });
$("service-scope").onchange = () => perform(() => load("services"));
$("refresh-chambers").onclick = () => perform(() => load("chambers"));

function newTerminal() {
  if (socket) socket.close();
  term?.dispose();
  term = new Terminal({cursorBlink:true, fontSize:14, fontFamily:"Cascadia Code, Consolas, monospace", theme:{background:"#0b0d10", foreground:"#d8dfdf", cursor:"#edbd75", selectionBackground:"#54432d"}, scrollback:5000, allowProposedApi:false});
  fit = new FitAddon.FitAddon(); term.loadAddon(fit); term.open($("terminal-host")); fit.fit();
  const currentTerm = term, ws = new WebSocket(`ws://${location.host}/ws`); socket = ws; ws.binaryType = "arraybuffer";
  ws.onopen = () => { ws.send(JSON.stringify({token, rows:currentTerm.rows, cols:currentTerm.cols})); $("terminal-label").textContent = "Live shell"; currentTerm.focus(); };
  ws.onmessage = event => {
    if (event.data instanceof ArrayBuffer) currentTerm.write(new Uint8Array(event.data));
    else { const value = JSON.parse(event.data); if (value.type === "exit") currentTerm.writeln("\r\n\x1b[33m" + value.message + "\x1b[0m"); }
  };
  ws.onerror = () => { if (socket === ws) notice("Terminal connection failed. Reopen with pistol wsl gui, then start a new session."); };
  ws.onclose = () => { if (socket === ws) { $("terminal-label").textContent = "Session ended · New session to reconnect"; currentTerm.writeln("\r\n\x1b[90m[disconnected]\x1b[0m"); } };
  currentTerm.onData(data => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:"input", data})); });
  currentTerm.onResize(({rows, cols}) => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({type:"resize", rows, cols})); });
}
$("new-terminal").onclick = () => perform(() => { if (!socket || socket.readyState !== WebSocket.OPEN || confirm("End this shell and its running programs, then start a new session?")) newTerminal(); });
new ResizeObserver(() => { if (activePage === "terminal") fit?.fit(); }).observe($("terminal-host"));
window.addEventListener("pagehide", () => socket?.close());

function childPath(name) {
  if (!name || name === "." || name === ".." || /[\\/\x00-\x1f]/.test(name)) throw new Error("Enter one file or folder name without slashes.");
  return currentPath.replace(/\/$/, "") + "/" + name;
}
async function navigateFiles(path, addHistory = true) {
  const result = await api("files?path=" + encodeURIComponent(path));
  currentPath = result.path; parentPath = result.parent; $("path").value = currentPath;
  if (addHistory && pathHistory[historyIndex] !== currentPath) { pathHistory = pathHistory.slice(0, historyIndex + 1); pathHistory.push(currentPath); historyIndex++; }
  $("back").disabled = historyIndex <= 0; $("forward").disabled = historyIndex >= pathHistory.length - 1;
  table("file-list", [["Name", r => button((r.type === "directory" ? "▤  " : "") + r.name, async () => {
    const path = childPath(r.name);
    $("file-info").textContent = `${path} · ${r.mode} · uid ${r.uid} / gid ${r.gid} · ${bytes(r.size)} · ${new Date(r.modified * 1000).toLocaleString()}`;
    if (r.type === "directory") await navigateFiles(path);
    else if (r.type === "file") await openEditor(path);
    else notice("Symlinks and special files are not opened by the file browser. Use Terminal to work with them.");
  }, "link")], ["Type", "type"], ["Size", r => bytes(r.size)], ["Modified", r => new Date(r.modified * 1000).toLocaleString()], ["Actions", r => rowActions(
    ...(r.type === "file" ? [button("Download", () => download(childPath(r.name), r.name))] : []),
    button("Rename", async () => { const name = prompt("New name", r.name); if (name && name !== r.name) { await post("files", {action:"rename", path:childPath(r.name), destination:childPath(name)}); await navigateFiles(currentPath, false); } }),
    button("Delete", async () => { const path = childPath(r.name); if (confirm(`Permanently delete ${path}${r.type === "directory" ? " and all of its contents" : ""}?\nThis cannot be undone.`)) { await post("files", {action:"delete", path, confirmed:true}); await navigateFiles(currentPath, false); } }, "danger")
  )]], result.entries);
}
$("path-form").onsubmit = event => { event.preventDefault(); perform(() => navigateFiles($("path").value)); };
$("home").onclick = () => perform(() => navigateFiles(home));
$("parent").onclick = () => perform(() => navigateFiles(parentPath));
for (const [id, delta] of [["back", -1], ["forward", 1]]) $(id).onclick = () => perform(async () => { const previous = historyIndex; historyIndex += delta; try { await navigateFiles(pathHistory[historyIndex], false); } catch (error) { historyIndex = previous; throw error; } });
for (const [id, directory] of [["new-file", false], ["new-folder", true]]) $(id).onclick = () => perform(async () => { const name = prompt(directory ? "Folder name" : "File name"); if (name) { await post("files", {action:"create", path:childPath(name), directory}); await navigateFiles(currentPath, false); } });
$("upload-button").onclick = () => $("upload").click();
$("upload").onchange = () => perform(async () => {
  try { const file = $("upload").files[0]; if (!file) return; if (file.size > 32 * 1024 * 1024) throw new Error("Uploads are limited to 32 MiB.");
    await api("upload?path=" + encodeURIComponent(childPath(file.name)), {method:"POST", body:file}); await navigateFiles(currentPath, false);
  } finally { $("upload").value = ""; }
});
async function download(path, name) {
  const data = await api("download?path=" + encodeURIComponent(path), {download:true});
  const url = URL.createObjectURL(data), link = document.createElement("a"); link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
}
function unsaved() { return editor && $("editor-text").value !== editor.original; }
async function openEditor(path) {
  if (unsaved() && !confirm("Discard unsaved changes?")) return;
  const result = await api("text?path=" + encodeURIComponent(path)); editor = {path, revision:result.revision, original:result.text};
  $("editor-name").textContent = path; $("editor-text").value = result.text; $("editor").hidden = false; $("editor").scrollIntoView({behavior:"smooth", block:"nearest"});
}
$("save-file").onclick = () => perform(async () => {
  if (!editor || !confirm(`Replace the contents of ${editor.path}?`)) return;
  const text = $("editor-text").value;
  const result = await post("files", {action:"save", path:editor.path, text, revision:editor.revision, confirmed:true});
  editor.revision = result.revision; editor.original = text; await navigateFiles(currentPath, false);
});
$("close-editor").onclick = () => { if (!unsaved() || confirm("Discard unsaved changes?")) { editor = null; $("editor").hidden = true; } };
window.addEventListener("beforeunload", event => { if (unsaved()) { event.preventDefault(); event.returnValue = ""; } });

async function refreshSystem() {
  const value = await api("system"); home = value.home;
  $("distro").textContent = value.distro; $("connection").textContent = "● RUNNING"; $("identity").textContent = value.username + "@" + value.hostname;
  $("cpu").textContent = `CPU ${value.cpu.toFixed(1)}%`; $("ram").textContent = `RAM ${bytes(value.ram_used)} / ${bytes(value.ram_total)}`;
  $("disk").textContent = `DISK ${bytes(value.disk_used)}`; $("uptime").textContent = `UPTIME ${duration(value.uptime)}`;
  $("metrics").replaceChildren();
  for (const [label, text, percent] of [["CPU USAGE", value.cpu.toFixed(1) + "%", value.cpu], ["MEMORY", bytes(value.ram_used), value.ram_used / value.ram_total * 100], ["DISK USAGE", bytes(value.disk_used), value.disk_used / value.disk_total * 100]]) {
    const div = document.createElement("div"); div.className = "metric";
    const title = document.createElement("small"), number = document.createElement("strong"), bar = document.createElement("progress"); title.textContent = label; number.textContent = text; bar.max = 100; bar.value = percent;
    div.append(title, number, bar); $("metrics").append(div);
  }
  $("system-details").replaceChildren();
  for (const [label, text] of [["Distribution", value.distro], ["Kernel", value.kernel], ["Hostname", value.hostname], ["Linux user", value.username], ["WSL version", value.wsl_version || "Unknown"], ["Uptime", duration(value.uptime)], ["Memory capacity", bytes(value.ram_total)], ["Disk capacity", bytes(value.disk_total)], ["IP addresses", value.ip_addresses.join(", ")], ["Windows drives", value.windows_drives.join(", ") || "None mounted"]]) {
    const div = document.createElement("div"); div.className = "detail"; const key = document.createElement("span"), content = document.createElement("span"); key.textContent = label; content.textContent = text; div.append(key, content); $("system-details").append(div);
  }
}
async function poll() {
  try { await refreshSystem(); if (["processes", "ports", "services"].includes(activePage)) await load(activePage); }
  catch (error) { $("connection").textContent = "DISCONNECTED"; notice(error.message); }
  finally { setTimeout(poll, 2500); }
}
perform(async () => {
  if (!token) throw new Error("Open this console using pistol wsl gui to authenticate this browser tab.");
  await refreshSystem(); newTerminal(); poll();
});
