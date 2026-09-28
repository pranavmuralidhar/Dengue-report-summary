// ---------------------------------------------------------------------
// EDIT THIS: paste the invoke URL printed at the end of
// deploy/step4_api_gateway.py, e.g.
// "https://abc123xyz.execute-api.us-east-1.amazonaws.com/prod"
// ---------------------------------------------------------------------
const API_BASE_URL = "https://hjn0zryblg.execute-api.ap-southeast-2.amazonaws.com/prod";

const els = {
  fileInput: document.getElementById("file-input"),
  uploadTrigger: document.getElementById("upload-trigger"),
  chatLog: document.getElementById("chat-log"),
  chatEmpty: document.getElementById("chat-empty"),
  chatForm: document.getElementById("chat-form"),
  questionInput: document.getElementById("question-input"),
  askBtn: document.getElementById("ask-btn"),
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  ingestionBar: document.getElementById("ingestion-bar"),
  ingestionText: document.getElementById("ingestion-text"),
  ingestionPercent: document.getElementById("ingestion-percent"),
  progressFill: document.getElementById("progress-fill"),
};

let selectedFile = null;
let sessionId = null;
let ingestionTimer = null;

// --- connection check --------------------------------------------------
function setStatus(state, text) {
  els.statusDot.className = "dot " + state;
  els.statusText.textContent = text;
}

if (API_BASE_URL.includes("REPLACE_ME")) {
  setStatus("bad", "API URL not configured (edit app.js)");
} else {
  setStatus("", "Ready");
}

// --- file selection ------------------------------------------------------
els.uploadTrigger.addEventListener("click", () => els.fileInput.click());
els.fileInput.addEventListener("change", (e) => uploadFile(e.target.files[0]));

async function uploadFile(file) {
  if (!file) return;
  selectedFile = file;
  els.uploadTrigger.disabled = true;
  setIngestionProgress(5, `Uploading ${file.name}…`);

  try {
    const base64 = await fileToBase64(file);
    const res = await fetch(`${API_BASE_URL}/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_name: file.name,
        file_content_base64: base64,
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "Upload failed");

    sessionId = null;
    addReportDivider(file.name);
    pollIngestion(data.ingestion_job_id);
  } catch (err) {
    setIngestionProgress(0, `Upload failed: ${err.message}`, true);
  } finally {
    selectedFile = null;
    els.fileInput.value = "";
    els.uploadTrigger.disabled = false;
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function setIngestionProgress(percent, text, isError = false) {
  els.ingestionBar.hidden = false;
  els.ingestionText.textContent = text;
  els.ingestionText.className = isError ? "ingestion-error" : "";
  els.ingestionPercent.textContent = `${percent}%`;
  els.progressFill.style.width = `${percent}%`;
}

function pollIngestion(jobId) {
  if (ingestionTimer) clearInterval(ingestionTimer);
  if (!jobId || jobId === "ALREADY_IN_PROGRESS") {
    setIngestionProgress(50, "Indexing is already in progress…");
    return;
  }

  const checkStatus = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "status", ingestion_job_id: jobId }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Could not read indexing status");

      if (data.status === "COMPLETE") {
        setIngestionProgress(100, "Indexing complete. You can ask a question.");
        clearInterval(ingestionTimer);
      } else if (["FAILED", "STOPPED"].includes(data.status)) {
        setIngestionProgress(0, `Indexing ${data.status.toLowerCase()}.`, true);
        clearInterval(ingestionTimer);
      } else {
        setIngestionProgress(65, "Indexing report…");
      }
    } catch (err) {
      setIngestionProgress(65, `Checking indexing status…`);
    }
  };

  checkStatus();
  ingestionTimer = setInterval(checkStatus, 5000);
}

// --- chat -------------------------------------------------------------
els.chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = els.questionInput.value.trim();
  if (!question) return;

  els.chatEmpty.style.display = "none";
  addMessage("user", question);
  els.questionInput.value = "";
  els.askBtn.disabled = true;

  const typingEl = addTypingIndicator();

  try {
    const res = await fetch(`${API_BASE_URL}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, session_id: sessionId }),
    });
    const data = await res.json();
    typingEl.remove();
    if (!res.ok) throw new Error(data.error || "Request failed");

    sessionId = data.session_id || sessionId;
    addMessage("bot", data.answer || "(no answer returned)", data.evidence || []);
  } catch (err) {
    typingEl.remove();
    addMessage("bot", "Something went wrong: " + err.message, [], true);
  } finally {
    els.askBtn.disabled = false;
  }
});

function addMessage(role, text, evidence = [], isError = false) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}` + (isError ? " error" : "");

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.appendChild(bubble);

  if (evidence.length > 0) {
    const details = document.createElement("details");
    details.className = "evidence";
    const summary = document.createElement("summary");
    summary.textContent = `Evidence from the report (${evidence.length})`;
    details.appendChild(summary);

    evidence.forEach((ev) => {
      const item = document.createElement("div");
      item.className = "evidence-item";
      const src = ev.s3_uri ? ev.s3_uri.split("/").pop() : "source";
      item.innerHTML = `${escapeHtml(ev.text || "")}<span class="src">from ${escapeHtml(src)}</span>`;
      details.appendChild(item);
    });
    wrap.appendChild(details);
  }

  els.chatLog.appendChild(wrap);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function addReportDivider(fileName) {
  const divider = document.createElement("div");
  divider.className = "report-divider";
  divider.innerHTML = `<span>New report: ${escapeHtml(fileName)}</span>`;
  els.chatLog.appendChild(divider);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
}

function addTypingIndicator() {
  const wrap = document.createElement("div");
  wrap.className = "msg bot";
  wrap.innerHTML = `<div class="bubble"><div class="typing"><span></span><span></span><span></span></div></div>`;
  els.chatLog.appendChild(wrap);
  els.chatLog.scrollTop = els.chatLog.scrollHeight;
  return wrap;
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}
