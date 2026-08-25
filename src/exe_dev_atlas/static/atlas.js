const list = document.getElementById("ports");
const empty = document.getElementById("empty");
const state = document.getElementById("state");
const workspaces = document.getElementById("workspaces");
const favicon = document.getElementById("favicon");
document.getElementById("host").textContent = location.hostname;

let openable = [];
// The first paint is not news, so nothing flashes on it. Afterwards a row that
// had no element is one that just appeared, which is what the flash is for.
let painted = false;

// This page is itself served on the port the bare hostname points at, so its own
// row is where you already are and every other row needs an explicit port.
function linkFor(row, ownPort) {
  if (row.is_http === false || row.port === ownPort) return null;
  // A zellij web server creates a brand new session for anyone arriving without
  // one named in the path, so linking its root would litter the box with an
  // empty session per visit. The per-session links below are the way in.
  if (row.is_session_server) return null;
  return location.protocol + "//" + location.hostname + ":" + row.port + "/";
}

function ago(epoch) {
  if (!epoch) return "";
  let s = Math.max(0, Math.floor(Date.now() / 1000) - epoch);
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  if (s < 86400) return Math.floor(s / 3600) + "h";
  return Math.floor(s / 86400) + "d";
}

function describe(row) {
  if (row.title) return row.title;
  if (row.is_http === null) return "probing…";
  if (row.is_http === false) return "not answering HTTP";
  return row.command_name || "no title";
}

function render(payload) {
  const rows = payload.rows || [];
  empty.hidden = rows.length > 0;

  // The VM's own name where reflection knew it, otherwise whatever hostname got
  // us here, which through a tunnel is `localhost` but still tells the reader
  // which tab is which.
  document.title = "Atlas - " + (payload.vm_name || location.hostname);

  // The VM's emoji as the tab icon, drawn as SVG text so there is no image to
  // fetch or generate. dominant-baseline rather than a dy nudge, since the glyph
  // comes from whichever font the reader's system picked and its metrics are not
  // ours to predict.
  if (payload.vm_emoji) {
    const glyph = payload.vm_emoji.replace(/[&<>]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]
    );
    favicon.href =
      "data:image/svg+xml," +
      encodeURIComponent(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">' +
          '<text x="50" y="50" font-size="88" text-anchor="middle" ' +
          'dominant-baseline="central">' +
          glyph +
          "</text></svg>"
      );
  }

  // Absent for anyone but the owner, and off exe.dev, where there is no
  // <vm>.exe.xyz for Remote-SSH to reach.
  workspaces.hidden = !payload.vscode_url;
  workspaces.innerHTML = "";
  if (payload.vscode_url) {
    const link = document.createElement("a");
    link.className = "workspace";
    link.href = payload.vscode_url;
    link.textContent = "open in VS Code";
    workspaces.appendChild(link);
  }
  openable = [];
  const keep = new Set();

  rows.forEach((row) => {
    keep.add(row.port);
    const href = linkFor(row, payload.own_port);
    if (href) openable.push(href);

    let li = list.querySelector('li[data-port="' + row.port + '"]');
    const fresh = !li;
    if (fresh) {
      li = document.createElement("li");
      li.dataset.port = row.port;
      li.innerHTML =
        '<a class="row"><span class="key"></span><span class="port"></span>' +
        '<span class="body"><span class="headline"><span class="title"></span></span>' +
        '<div class="dir"></div><div class="cmd"></div><div class="meta"></div></span></a>' +
        '<div class="sessions"></div>';
      if (painted) {
        li.classList.add("flash");
        li.addEventListener("animationend", () => li.classList.remove("flash"), { once: true });
      }
    }

    const anchor = li.querySelector(".row");
    if (href) anchor.setAttribute("href", href);
    else anchor.removeAttribute("href");
    li.classList.toggle("inert", !href);

    li.querySelector(".key").textContent =
      openable.length && href && openable.length <= 9 ? openable.length : "";
    li.querySelector(".port").textContent = row.port;

    const title = li.querySelector(".title");
    title.textContent = describe(row);
    title.classList.toggle("unknown", !row.title);

    const headline = li.querySelector(".headline");
    headline.querySelectorAll(".badge").forEach((b) => b.remove());
    const badges = [];
    if (row.port === payload.own_port) badges.push(["this page", "here"]);
    if (row.is_http === false) badges.push(["no http", ""]);
    if (row.is_session_server) badges.push(["session server", ""]);
    badges.forEach(([text, kind]) => {
      const span = document.createElement("span");
      span.className = "badge" + (kind === "here" ? " here" : "");
      span.textContent = text;
      headline.appendChild(span);
    });

    const dir = li.querySelector(".dir");
    if (row.directory) {
      const parts = row.directory.split("/");
      const base = parts.pop();
      dir.innerHTML = "";
      dir.append(parts.join("/") + "/");
      const strong = document.createElement("b");
      strong.textContent = base;
      dir.append(strong);
    } else {
      dir.textContent = "";
    }

    li.querySelector(".cmd").textContent = row.command_line;

    const meta = li.querySelector(".meta");
    meta.innerHTML = "";
    const facts = [];
    if (row.command_name) facts.push(row.command_name);
    if (row.pid) facts.push("pid " + row.pid);
    if (row.user) facts.push(row.user);
    if (row.started_at) facts.push("up " + ago(row.started_at));
    if (row.addresses.length) facts.push(row.addresses.join(", "));
    if (row.status) facts.push("HTTP " + row.status);
    if (row.server) facts.push(row.server);
    facts.forEach((fact) => {
      const span = document.createElement("span");
      span.textContent = fact;
      meta.appendChild(span);
    });

    // Sessions arrive only for the VM's owner, so this is absent rather than
    // empty for everyone else and the row renders as any other port would.
    const sessions = li.querySelector(".sessions");
    sessions.innerHTML = "";
    (row.sessions || []).forEach((name) => {
      const link = document.createElement("a");
      link.className = "session";
      link.href = location.protocol + "//" + location.hostname + ":" + row.port + "/" + encodeURIComponent(name);
      openable.push(link.href);
      const key = document.createElement("span");
      key.className = "key";
      key.textContent = openable.length <= 9 ? openable.length : "";
      link.appendChild(key);
      const label = document.createElement("span");
      label.textContent = name;
      link.appendChild(label);
      sessions.appendChild(link);
    });

    list.appendChild(li);
  });

  list.querySelectorAll("li").forEach((li) => {
    if (!keep.has(Number(li.dataset.port))) li.remove();
  });

  painted = true;
}

let latest = null;
const source = new EventSource("/events");
source.onopen = () => { state.textContent = "live"; state.classList.remove("stale"); };
source.onerror = () => { state.textContent = "reconnecting"; state.classList.add("stale"); };
source.onmessage = (event) => {
  state.textContent = "live";
  state.classList.remove("stale");
  latest = JSON.parse(event.data);
  render(latest);
};

// Uptimes are rendered from an absolute start time, so they need a local tick to
// stay honest between pushes.
setInterval(() => { if (latest) render(latest); }, 5000);

addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const index = "123456789".indexOf(event.key);
  if (index >= 0 && openable[index]) location.href = openable[index];
});
