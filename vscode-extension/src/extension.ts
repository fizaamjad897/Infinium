/**
 * Infinium VSCode Extension
 *
 * Three commands talk to the Infinium backend's IDE endpoints:
 *   - infinium.askQuestion       → POST /api/ide/query
 *   - infinium.explainSelection  → POST /api/ide/explain
 *   - infinium.improveSelection  → POST /api/ide/improve
 *   - infinium.pickRepo          → GET  /api/ide/repos (sets infinium.repoName)
 *
 * Answers render in a dedicated webview panel with clickable source links.
 */
import * as vscode from "vscode";
import * as http from "http";
import * as https from "https";
import { URL } from "url";

interface IDESource {
  source: string;
  repo: string;
  type: string;
  relevance: number;
}

interface IDEResponse {
  answer: string;
  sources: IDESource[];
  model: string;
  chunks_retrieved: number;
  duration_ms: number;
}

let answerPanel: vscode.WebviewPanel | undefined;

function getConfig() {
  const cfg = vscode.workspace.getConfiguration("infinium");
  return {
    apiUrl: (cfg.get<string>("apiUrl") || "http://localhost:8000").replace(/\/$/, ""),
    repoName: cfg.get<string>("repoName") || "",
    jwtToken: cfg.get<string>("jwtToken") || "",
  };
}

function postJson<T>(url: string, body: any, jwt?: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "https:" ? https : http;
    const payload = Buffer.from(JSON.stringify(body), "utf8");
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "Content-Length": payload.length.toString(),
    };
    if (jwt) headers["Authorization"] = `Bearer ${jwt}`;

    const req = lib.request(
      {
        method: "POST",
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname + u.search,
        headers,
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          if (!res.statusCode || res.statusCode >= 400) {
            reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 300)}`));
            return;
          }
          try {
            resolve(JSON.parse(data) as T);
          } catch (err) {
            reject(err);
          }
        });
      }
    );
    req.on("error", reject);
    req.write(payload);
    req.end();
  });
}

function getJson<T>(url: string, jwt?: string): Promise<T> {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const lib = u.protocol === "https:" ? https : http;
    const headers: Record<string, string> = {};
    if (jwt) headers["Authorization"] = `Bearer ${jwt}`;
    const req = lib.request(
      {
        method: "GET",
        hostname: u.hostname,
        port: u.port || (u.protocol === "https:" ? 443 : 80),
        path: u.pathname + u.search,
        headers,
      },
      (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          if (!res.statusCode || res.statusCode >= 400) {
            reject(new Error(`HTTP ${res.statusCode}: ${data.slice(0, 300)}`));
            return;
          }
          try {
            resolve(JSON.parse(data) as T);
          } catch (err) {
            reject(err);
          }
        });
      }
    );
    req.on("error", reject);
    req.end();
  });
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderHtml(query: string, response: IDEResponse): string {
  const sources = response.sources
    .map(
      (s) =>
        `<li><code>${escapeHtml(s.repo)}</code> — <code>${escapeHtml(
          s.source
        )}</code> <span class="rel">(${s.relevance.toFixed(2)})</span></li>`
    )
    .join("");

  // Very lightweight markdown → HTML (preserves ``` fences and **bold**)
  const answerHtml = escapeHtml(response.answer)
    .replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code}</code></pre>`)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\n/g, "<br>");

  return `<!doctype html>
<html><head><meta charset="utf-8"><style>
  body { font-family: var(--vscode-font-family); padding: 1em; line-height: 1.5; }
  h2 { font-size: 1.1em; border-bottom: 1px solid #555; padding-bottom: .25em; }
  pre { background: var(--vscode-editor-background); padding: .75em; border-radius: 4px; overflow-x: auto; }
  code { font-family: var(--vscode-editor-font-family); }
  ul { padding-left: 1.5em; }
  .meta { font-size: .85em; opacity: .7; }
  .rel { opacity: .6; }
</style></head><body>
  <h2>Question</h2>
  <p>${escapeHtml(query)}</p>
  <h2>Answer</h2>
  <div>${answerHtml}</div>
  <h2>Sources (${response.sources.length})</h2>
  <ul>${sources}</ul>
  <p class="meta">Model: ${escapeHtml(response.model)} • ${response.chunks_retrieved} chunks • ${response.duration_ms} ms</p>
</body></html>`;
}

function showAnswer(query: string, response: IDEResponse) {
  if (!answerPanel) {
    answerPanel = vscode.window.createWebviewPanel(
      "infiniumAnswer",
      "Infinium",
      vscode.ViewColumn.Beside,
      { enableScripts: false }
    );
    answerPanel.onDidDispose(() => (answerPanel = undefined));
  }
  answerPanel.webview.html = renderHtml(query, response);
  answerPanel.reveal(vscode.ViewColumn.Beside, true);
}

async function buildPayload(question: string) {
  const editor = vscode.window.activeTextEditor;
  const cfg = getConfig();
  const file_path = editor?.document.uri.fsPath;
  const language = editor?.document.languageId;
  const selected_code = editor?.document.getText(editor.selection);
  return {
    query: question,
    repo_name: cfg.repoName || undefined,
    file_path,
    language,
    selected_code: selected_code && selected_code.length > 0 ? selected_code : undefined,
  };
}

async function callIDE(path: string, body: any): Promise<IDEResponse> {
  const cfg = getConfig();
  return await postJson<IDEResponse>(`${cfg.apiUrl}${path}`, body, cfg.jwtToken || undefined);
}

async function askQuestion() {
  const question = await vscode.window.showInputBox({
    prompt: "Ask Infinium",
    placeHolder: "Why was JWT replaced? How is auth handled?",
  });
  if (!question) return;

  const payload = await buildPayload(question);
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Infinium is thinking…" },
    async () => {
      try {
        const r = await callIDE("/api/ide/query", payload);
        showAnswer(question, r);
      } catch (err: any) {
        vscode.window.showErrorMessage(`Infinium: ${err.message || err}`);
      }
    }
  );
}

async function explainSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) {
    vscode.window.showWarningMessage("Select some code first.");
    return;
  }
  const cfg = getConfig();
  const body = {
    repo_name: cfg.repoName || undefined,
    file_path: editor.document.uri.fsPath,
    selected_code: editor.document.getText(editor.selection),
    language: editor.document.languageId,
  };
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Infinium: explaining…" },
    async () => {
      try {
        const r = await callIDE("/api/ide/explain", body);
        showAnswer("Explain selected code", r);
      } catch (err: any) {
        vscode.window.showErrorMessage(`Infinium: ${err.message || err}`);
      }
    }
  );
}

async function improveSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor || editor.selection.isEmpty) {
    vscode.window.showWarningMessage("Select some code first.");
    return;
  }
  const cfg = getConfig();
  const body = {
    repo_name: cfg.repoName || undefined,
    file_path: editor.document.uri.fsPath,
    selected_code: editor.document.getText(editor.selection),
    language: editor.document.languageId,
  };
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "Infinium: reviewing…" },
    async () => {
      try {
        const r = await callIDE("/api/ide/improve", body);
        showAnswer("Suggested improvements", r);
      } catch (err: any) {
        vscode.window.showErrorMessage(`Infinium: ${err.message || err}`);
      }
    }
  );
}

async function pickRepo() {
  const cfg = getConfig();
  try {
    const list = await getJson<{ repos: { repo_name: string; chunks: number }[] }>(
      `${cfg.apiUrl}/api/ide/repos`,
      cfg.jwtToken || undefined
    );
    const items = list.repos.map((r) => ({
      label: r.repo_name,
      description: `${r.chunks} chunks`,
    }));
    items.unshift({ label: "(all repos)", description: "Search across every indexed repo" });
    const pick = await vscode.window.showQuickPick(items, { placeHolder: "Select default repo" });
    if (!pick) return;
    const value = pick.label === "(all repos)" ? "" : pick.label;
    await vscode.workspace
      .getConfiguration("infinium")
      .update("repoName", value, vscode.ConfigurationTarget.Global);
    vscode.window.showInformationMessage(`Infinium repo set to: ${value || "(all)"}`);
  } catch (err: any) {
    vscode.window.showErrorMessage(`Infinium: failed to fetch repos — ${err.message || err}`);
  }
}

export function activate(context: vscode.ExtensionContext) {
  context.subscriptions.push(
    vscode.commands.registerCommand("infinium.askQuestion", askQuestion),
    vscode.commands.registerCommand("infinium.explainSelection", explainSelection),
    vscode.commands.registerCommand("infinium.improveSelection", improveSelection),
    vscode.commands.registerCommand("infinium.pickRepo", pickRepo)
  );
}

export function deactivate() {
  /* no-op */
}
