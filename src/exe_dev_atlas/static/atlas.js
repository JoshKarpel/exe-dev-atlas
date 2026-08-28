const list = document.getElementById("ports");
const empty = document.getElementById("empty");
const state = document.getElementById("state");
const workspaces = document.getElementById("workspaces");
const favicon = document.getElementById("favicon");
const emblem = document.getElementById("emblem");
const vm = document.getElementById("vm");
document.getElementById("host").textContent = location.hostname;

// The digits that open a link, which bounds how many rows can carry one. The
// footer in the server-rendered shell names the same range.
const HOTKEYS = "123456789";

let openable = [];
// The first paint is not news, so nothing flashes on it. Afterwards a row that
// had no element is one that just appeared, which is what the flash is for.
let painted = false;

// Built from the host the reader actually used, which is what keeps every link
// on the page correct through the exe.dev proxy and through an SSH tunnel alike.
function urlFor(port, path) {
  return location.protocol + "//" + location.hostname + ":" + port + "/" + path;
}

// Offer a link to the keyboard and answer with the digit that now opens it, or
// "" once the row of digits is used up. The label is read out of HOTKEYS at the
// position the link landed in, which is the same string and the same index the
// keypress handler resolves, so a digit on the page cannot open another row.
function offer(href) {
  openable.push(href);
  return HOTKEYS[openable.length - 1] ?? "";
}

// This page is itself served on the port the bare hostname points at, so its own
// row is where you already are and every other row needs an explicit port.
function linkFor(row, ownPort) {
  if (row.is_http === false || row.port === ownPort) return null;
  // A zellij web server creates a brand new session for anyone arriving without
  // one named in the path, so linking its root would litter the box with an
  // empty session per visit. The chips below are the way in: the existing
  // sessions, and one link that asks for a new one on purpose.
  if (row.sessions) return null;
  return urlFor(row.port, "");
}

// The one link on the page that acts on the box rather than pointing at
// something already on it, since arriving at a zellij web server's root creates
// a session. That is why it is drawn as an action rather than a destination and
// is never handed to `offer`: a session collected by a stray digit or a misclick
// on the row stays on the box until somebody clears it from a shell.
function newSessionLink(port) {
  const link = document.createElement("a");
  link.className = "session create";
  link.href = urlFor(port, "");
  link.textContent = "+ new session";
  return link;
}

// One port number can carry two rows: two processes bound to it on different
// addresses. The element a row updates is found by this, so keying it on the
// port alone would have the second row overwrite the first every paint.
function keyFor(row) {
  return row.port + "/" + (row.pid === null ? "" : row.pid);
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

// What the last payload said about the VM itself, so an unchanged answer costs
// nothing. All three of these come from reflection, read once at startup, so they
// are fixed for the life of the connection: re-encoding the favicon SVG and
// rebuilding the VS Code link once a second would be work for a value that
// cannot have changed.
let identity = null;

function applyIdentity(payload) {
  const current = JSON.stringify([payload.vm_name, payload.vm_emoji, payload.vscode_url]);
  if (current === identity) return;
  identity = current;

  // The VM's own name, which the shell already rendered, or whatever hostname got
  // us here where reflection knew of none: through a tunnel that is `localhost`,
  // which still tells the reader which tab is which.
  document.title = payload.vm_name || location.hostname;

  // The VM's emoji is the heading, which is what makes two of these tabs tell
  // themselves apart at a glance. The shell's "Atlas" stands where there is no
  // emoji to put in its place, off exe.dev or where reflection did not answer.
  if (payload.vm_emoji) emblem.textContent = payload.vm_emoji;

  // The name the VM is known by, which is what the reader is here to identify.
  // Hidden rather than blank where reflection did not answer, so the hostname
  // beside it does not sit behind an empty gap.
  vm.hidden = !payload.vm_name;
  vm.textContent = payload.vm_name;

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

  // Absent where the link was turned off at install, and off exe.dev, where
  // there is no <vm>.exe.xyz for Remote-SSH to reach.
  workspaces.hidden = !payload.vscode_url;
  workspaces.innerHTML = "";
  if (payload.vscode_url) {
    const link = document.createElement("a");
    link.className = "workspace";
    link.href = payload.vscode_url;
    link.textContent = "open in VS Code";
    workspaces.appendChild(link);
  }
}

function render(payload) {
  const rows = payload.rows || [];
  empty.hidden = rows.length > 0;

  applyIdentity(payload);
  openable = [];

  // Stale rows go first, so the position check below compares against a list holding
  // only rows this payload still has. Doing it afterwards would leave departed rows
  // sitting at intermediate positions and force every surviving row to move.
  const keep = new Set(rows.map(keyFor));
  list.querySelectorAll("li").forEach((li) => {
    if (!keep.has(li.dataset.key)) li.remove();
  });

  rows.forEach((row, index) => {
    const key = keyFor(row);
    const href = linkFor(row, payload.own_port);
    const hotkey = href ? offer(href) : "";

    let li = list.querySelector('li[data-key="' + key + '"]');
    const fresh = !li;
    if (fresh) {
      li = document.createElement("li");
      li.dataset.key = key;
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

    li.querySelector(".key").textContent = hotkey;
    li.querySelector(".port").textContent = row.port;

    const title = li.querySelector(".title");
    title.textContent = describe(row);
    title.classList.toggle("unknown", !row.title);

    const headline = li.querySelector(".headline");
    headline.querySelectorAll(".badge").forEach((b) => b.remove());
    const badges = [];
    if (row.port === payload.own_port) badges.push(["this page", "badge here"]);
    if (row.is_http === false) badges.push(["no http", "badge"]);
    if (row.sessions) badges.push(["session server", "badge"]);
    badges.forEach(([text, className]) => {
      const span = document.createElement("span");
      span.className = className;
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

    // Absent on every row but a zellij web server's, where it can still be empty
    // because the server is serving no sessions. That empty list is the case the
    // new-session link exists for, so the presence of the field rather than its
    // length is what decides whether these are drawn at all.
    const sessions = li.querySelector(".sessions");
    sessions.innerHTML = "";
    if (row.sessions) {
      row.sessions.forEach((name) => {
        const link = document.createElement("a");
        link.className = "session";
        link.href = urlFor(row.port, encodeURIComponent(name));
        const key = document.createElement("span");
        key.className = "key";
        key.textContent = offer(link.href);
        link.appendChild(key);
        const label = document.createElement("span");
        label.textContent = name;
        link.appendChild(label);
        sessions.appendChild(link);
      });
      sessions.appendChild(newSessionLink(row.port));
    }

    // Appending an attached node *moves* it, so doing this unconditionally would tear
    // down and rebuild the whole list once a second. The rows above are updated field by
    // field for the same reason: the cadence must not take text selection and focus with
    // it. Touch the list only where a row is new or has actually changed place.
    if (list.children[index] !== li) list.insertBefore(li, list.children[index] ?? null);
  });

  painted = true;
}

function showState(text, stale) {
  state.textContent = text;
  state.classList.toggle("stale", stale);
}

let latest = null;
const source = new EventSource("/events");
source.onopen = () => showState("live", false);
source.onerror = () => showState("reconnecting", true);
source.onmessage = (event) => {
  showState("live", false);
  latest = JSON.parse(event.data);
  render(latest);
};

// Uptimes are rendered from an absolute start time, so they need a local tick to
// stay honest between pushes.
setInterval(() => { if (latest) render(latest); }, 5000);

addEventListener("keydown", (event) => {
  if (event.metaKey || event.ctrlKey || event.altKey) return;
  const index = HOTKEYS.indexOf(event.key);
  if (index >= 0 && openable[index]) location.href = openable[index];
});
