// TaskBoard frontend: talks to the backend through relative /api URLs.
// Nginx (or the Ingress) forwards /api to the backend Service.

const API = "/api";
const STATUSES = ["todo", "in_progress", "done"];
const NEXT = { todo: "in_progress", in_progress: "done" };
const PREV = { in_progress: "todo", done: "in_progress" };

const $ = (id) => document.getElementById(id);

function showError(message) {
  const el = $("error");
  el.textContent = message;
  el.hidden = !message;
}

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function cardHtml(task) {
  const back = PREV[task.status]
    ? `<button data-action="move" data-id="${task.id}" data-to="${PREV[task.status]}" title="Move back">←</button>`
    : "";
  const forward = NEXT[task.status]
    ? `<button data-action="move" data-id="${task.id}" data-to="${NEXT[task.status]}" title="Move forward">→</button>`
    : "";
  return `
    <article class="card ${task.priority} ${task.status === "done" ? "done-card" : ""}">
      <h3>${escapeHtml(task.title)}</h3>
      ${task.description ? `<p>${escapeHtml(task.description)}</p>` : ""}
      <div class="meta">
        <span class="chip ${task.priority}">${task.priority}</span>
        <div class="actions">
          ${back}${forward}
          <button class="delete" data-action="delete" data-id="${task.id}" title="Delete">✕</button>
        </div>
      </div>
    </article>`;
}

function render(tasks) {
  for (const status of STATUSES) {
    const list = tasks.filter((t) => t.status === status);
    $(`count-${status}`).textContent = list.length;
    $(`col-${status}`).innerHTML = list.length
      ? list.map(cardHtml).join("")
      : `<div class="empty">No tasks here yet</div>`;
  }
  $("stat-total").textContent = tasks.length;
  $("stat-progress").textContent = tasks.filter((t) => t.status === "in_progress").length;
  $("stat-done").textContent = tasks.filter((t) => t.status === "done").length;
}

async function loadTasks() {
  try {
    render(await api("/tasks"));
    showError("");
  } catch (err) {
    showError(`Could not load tasks: ${err.message}`);
  }
}

async function loadInfo() {
  const dot = $("health-dot");
  try {
    const info = await api("/info");
    dot.className = "dot ok";
    $("server-info").innerHTML =
      `Served by pod <code class="pill">${escapeHtml(info.pod)}</code> ` +
      `on node <code class="pill">${escapeHtml(info.node)}</code> · ` +
      `version <code class="pill">${escapeHtml(info.version)}</code> · ` +
      `env <code class="pill">${escapeHtml(info.environment)}</code>`;
  } catch (err) {
    dot.className = "dot bad";
    $("server-info").textContent = "API not reachable";
  }
}

$("task-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const title = $("title").value.trim();
  if (!title) return;
  try {
    await api("/tasks", {
      method: "POST",
      body: JSON.stringify({
        title,
        description: $("description").value.trim(),
        priority: $("priority").value,
      }),
    });
    $("title").value = "";
    $("description").value = "";
    $("priority").value = "medium";
    await loadTasks();
  } catch (err) {
    showError(err.message);
  }
});

document.querySelector(".board").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const id = button.dataset.id;
  try {
    if (button.dataset.action === "move") {
      await api(`/tasks/${id}`, { method: "PUT", body: JSON.stringify({ status: button.dataset.to }) });
    } else if (button.dataset.action === "delete") {
      await api(`/tasks/${id}`, { method: "DELETE" });
    }
    await loadTasks();
  } catch (err) {
    showError(err.message);
  }
});

$("refresh-info").addEventListener("click", loadInfo);

loadTasks();
loadInfo();
setInterval(loadTasks, 10000);
