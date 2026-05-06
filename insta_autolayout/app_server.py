from __future__ import annotations

import json
import mimetypes
import posixpath
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from uuid import uuid4
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .local_settings import load_local_settings, save_local_settings
from .promo_exporter import refresh_review_assets


_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def serve_local_app(open_browser: bool = True, port: int = 0) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), _LocalAppHandler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"local app: {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nlocal app stopped")
    finally:
        server.server_close()


class _LocalAppHandler(BaseHTTPRequestHandler):
    server_version = "InstaAutolayoutLocalApp/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(_render_app_shell(load_local_settings()))
        elif parsed.path == "/api/health":
            self._send_json({"ok": True})
        elif parsed.path == "/api/startup-check":
            self._send_json(_startup_check(load_local_settings()))
        elif parsed.path == "/api/settings":
            self._send_json(load_local_settings())
        elif parsed.path.startswith("/api/jobs/"):
            self._send_json(_job_snapshot(parsed.path.removeprefix("/api/jobs/")))
        elif parsed.path == "/review/latest":
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/review/latest/")
            self.end_headers()
        elif parsed.path.startswith("/review/latest/"):
            self._serve_latest_review(parsed.path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/settings":
            try:
                payload = self._read_json_body()
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._send_json(save_local_settings(payload))
        elif parsed.path == "/api/warm-cache":
            self._send_json(_start_generation_job(warm_cache_only=True))
        elif parsed.path == "/api/generate-batch":
            self._send_json(_start_generation_job(warm_cache_only=False))
        elif parsed.path == "/api/choose-path":
            try:
                self._send_json(_choose_path(self._read_json_body()))
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        elif parsed.path == "/api/reveal-path":
            try:
                self._send_json(_reveal_path(self._read_json_body()))
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        elif parsed.path == "/api/review/events":
            try:
                payload = self._read_json_body()
                self._send_json(_record_review_event(payload))
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            raise ValueError("Request body must be JSON")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object")
        return payload

    def _serve_latest_review(self, request_path: str) -> None:
        settings = load_local_settings()
        latest = settings.get("latest_batch_dir", "")
        if not latest:
            self.send_error(HTTPStatus.NOT_FOUND, "No latest batch configured")
            return
        latest_dir = Path(latest).expanduser().resolve()
        if not latest_dir.exists() or not latest_dir.is_dir():
            self.send_error(HTTPStatus.NOT_FOUND, "Latest batch directory does not exist")
            return
        refresh_review_assets(latest_dir)
        relative = request_path.removeprefix("/review/latest").lstrip("/")
        if not relative:
            relative = "index.html"
        self._send_file(latest_dir, relative)

    def _send_file(self, root: Path, relative: str) -> None:
        clean = posixpath.normpath(unquote(relative)).lstrip("/")
        if clean == "." or clean.startswith("../"):
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid path")
            return
        target = (root / clean).resolve()
        if root not in target.parents and target != root:
            self.send_error(HTTPStatus.FORBIDDEN, "Invalid path")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Review asset not found")
            return
        body = target.read_bytes()
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _render_app_shell(settings: dict[str, str]) -> str:
    latest_exists = _path_exists(settings.get("latest_batch_dir", ""))
    settings_json = json.dumps(settings).replace("</", "<\\/")
    presets_json = json.dumps(_preset_records()).replace("</", "<\\/")
    startup_check_json = json.dumps(_startup_check(settings)).replace("</", "<\\/")
    latest_href = "/review/latest/" if latest_exists else "#"
    latest_disabled = "" if latest_exists else "disabled"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Insta Autolayout</title>
  <style>
    :root {{
      color-scheme: light;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #f6f7f9;
      color: #1f2933;
    }}
    body {{
      margin: 0;
      min-height: 100vh;
    }}
    main {{
      max-width: 1080px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    h1 {{
      margin: 0 0 24px;
      font-size: 28px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    form {{
      display: grid;
      gap: 20px;
    }}
    section {{
      display: grid;
      gap: 14px;
      padding: 20px;
      border: 1px solid #d9dee7;
      border-radius: 8px;
      background: #fff;
    }}
    h2 {{
      margin: 0;
      font-size: 15px;
      font-weight: 700;
      color: #334155;
    }}
    details {{
      border: 1px solid #d9dee7;
      border-radius: 8px;
      background: #fff;
      padding: 0;
    }}
    summary {{
      cursor: pointer;
      padding: 16px 20px;
      font-weight: 750;
      color: #334155;
    }}
    .help-body {{
      display: grid;
      gap: 12px;
      padding: 0 20px 18px;
      color: #475569;
      font-size: 14px;
      line-height: 1.5;
    }}
    .help-body ul {{
      margin: 0;
      padding-left: 20px;
    }}
    .choices {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .choice {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 36px;
      padding: 0 12px;
      border: 1px solid #c9d2df;
      border-radius: 8px;
      background: #f8fafc;
      cursor: pointer;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    label {{
      display: grid;
      gap: 6px;
      font-size: 13px;
      font-weight: 600;
      color: #475569;
    }}
    select {{
      box-sizing: border-box;
      width: 100%;
      min-height: 38px;
      border: 1px solid #c9d2df;
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 14px;
      color: #111827;
      background: #fff;
    }}
    .path-field {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 6px;
      align-items: end;
    }}
    .path-field label {{
      min-width: 0;
    }}
    input[type="text"] {{
      box-sizing: border-box;
      width: 100%;
      min-height: 38px;
      border: 1px solid #c9d2df;
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 14px;
      color: #111827;
      background: #fff;
    }}
    input[type="number"] {{
      box-sizing: border-box;
      width: 100%;
      min-height: 38px;
      border: 1px solid #c9d2df;
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
      font-size: 14px;
      color: #111827;
      background: #fff;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
    }}
    button, .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 14px;
      border: 1px solid #1f2937;
      border-radius: 6px;
      background: #1f2937;
      color: #fff;
      font: inherit;
      font-size: 14px;
      font-weight: 650;
      text-decoration: none;
      cursor: pointer;
    }}
    button.secondary, .button.secondary {{
      border-color: #c9d2df;
      background: #fff;
      color: #1f2937;
    }}
    .button.disabled {{
      pointer-events: none;
      opacity: .45;
    }}
    pre {{
      margin: 0;
      padding: 12px;
      overflow: auto;
      border-radius: 6px;
      background: #0f172a;
      color: #e2e8f0;
      font-size: 12px;
      line-height: 1.45;
      max-height: 360px;
      white-space: pre-wrap;
    }}
    .status-row {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: #475569;
      font-size: 13px;
      font-weight: 650;
    }}
    .progress-shell {{
      display: grid;
      gap: 8px;
    }}
    .progress-track {{
      width: 100%;
      height: 10px;
      border-radius: 999px;
      background: #e5e7eb;
      overflow: hidden;
    }}
    .progress-fill {{
      width: 0%;
      height: 100%;
      background: linear-gradient(90deg, #1f6feb, #22c55e);
      transition: width .2s ease;
    }}
    .progress-meta {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      color: #475569;
      font-size: 13px;
      font-weight: 650;
    }}
    .muted {{
      color: #64748b;
      font-size: 13px;
      line-height: 1.45;
    }}
    .diag-list {{
      margin: 0;
      padding-left: 18px;
      color: #334155;
      font-size: 13px;
      line-height: 1.5;
    }}
    .diag-ok {{
      color: #166534;
      font-size: 13px;
      font-weight: 650;
    }}
    .diag-info {{
      color: #1d4ed8;
      font-size: 13px;
      font-weight: 650;
      line-height: 1.5;
    }}
    .diag-error {{
      color: #b91c1c;
    }}
    .diag-warning {{
      color: #92400e;
    }}
    @media (max-width: 720px) {{
      main {{
        padding: 22px 14px 36px;
      }}
      .grid {{
        grid-template-columns: 1fr;
      }}
      .path-field {{
        grid-template-columns: 1fr;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <h1>Trybe Video Review</h1>
    <details>
      <summary>How to use this app</summary>
      <div class="help-body">
        <p>This local app is the control panel for generating Trybe batches and reviewing them. It runs on this computer and writes shared review data to OneDrive when the shared state directory is set.</p>
        <ul>
          <li><strong>Reviewer:</strong> choose Rami or Max before saving feedback.</li>
          <li><strong>Input directory:</strong> the source media folder.</li>
          <li><strong>Output directory:</strong> where the next generated batch is written.</li>
          <li><strong>Archive directory:</strong> optional OneDrive archive destination for completed batches.</li>
          <li><strong>Shared state directory:</strong> the OneDrive root that will contain <code>review_state</code>.</li>
          <li><strong>Config path:</strong> optional JSON config. If set, it supplies generation defaults.</li>
          <li><strong>Review batch directory:</strong> any exported batch folder you want to open in the review UI, including older batches.</li>
        </ul>
        <p>Use <strong>Warm Cache</strong> before a larger run, then <strong>Generate Batch</strong>. Both jobs now run in the background and stream logs below.</p>
      </div>
    </details>
    <form id="settings-form">
      <section>
        <h2>Profile</h2>
        <div class="choices">
          <label class="choice"><input type="radio" name="reviewer_id" value="rami"> Rami</label>
          <label class="choice"><input type="radio" name="reviewer_id" value="max"> Max</label>
        </div>
        <input type="hidden" name="project_id" value="trybe">
        <p class="muted">Project is fixed to Trybe for now.</p>
      </section>
      <section>
        <h2>Generation Preset</h2>
        <label>Run type
          <select name="preset_id" id="preset-select"></select>
        </label>
        <p id="preset-description" class="muted"></p>
      </section>
      <section>
        <h2>Startup Checks</h2>
        <div id="startup-checks"></div>
      </section>
      <section>
        <h2>Learned Feedback</h2>
        <div id="learned-feedback-status"></div>
      </section>
      <section>
        <h2>Paths</h2>
        <div class="grid">
          <div class="path-field">
            <label>Input directory<input type="text" name="input_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="input_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="input_dir">Open</button>
          </div>
          <div class="path-field">
            <label>Output directory<input type="text" name="output_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="output_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="output_dir">Open</button>
          </div>
          <div class="path-field">
            <label>Archive directory<input type="text" name="archive_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="archive_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="archive_dir">Open</button>
          </div>
          <div class="path-field">
            <label>Shared state directory<input type="text" name="shared_state_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="shared_state_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="shared_state_dir">Open</button>
          </div>
          <div class="path-field">
            <label>Cache directory<input type="text" name="cache_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="cache_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="cache_dir">Open</button>
          </div>
          <div class="path-field">
            <label>Music directory<input type="text" name="music_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="music_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="music_dir">Open</button>
          </div>
          <div class="path-field">
            <label>Music manifest<input type="text" name="music_manifest" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="music_manifest" data-kind="file">Choose</button>
            <button type="button" class="secondary" data-reveal="music_manifest">Open</button>
          </div>
          <div class="path-field">
            <label>Config path<input type="text" name="config_path" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="config_path" data-kind="file">Choose</button>
            <button type="button" class="secondary" data-reveal="config_path">Open</button>
          </div>
          <div class="path-field">
            <label>Review batch directory<input type="text" name="latest_batch_dir" autocomplete="off"></label>
            <button type="button" class="secondary" data-choose="latest_batch_dir" data-kind="directory">Choose</button>
            <button type="button" class="secondary" data-reveal="latest_batch_dir">Open</button>
          </div>
        </div>
        <p class="muted">Set this to any exported batch folder when you want to review an older batch instead of the last one generated from this screen.</p>
      </section>
      <section>
        <h2>Generation Controls</h2>
        <p class="muted">These are real generator settings. They override the selected preset when filled. Exact clip count is currently planned automatically from target length, style, and punchiness.</p>
        <div class="grid">
          <label>Videos to generate<input type="number" name="count" min="1" max="200" step="1"></label>
          <label>Duration min seconds<input type="number" name="duration_min" min="4" max="60" step="0.5"></label>
          <label>Duration max seconds<input type="number" name="duration_max" min="4" max="90" step="0.5"></label>
          <label>Style
            <select name="style">
              <option value="">Preset default</option>
              <option value="fast_punchy">Fast punchy</option>
              <option value="clean_product_demo">Clean branded/event</option>
              <option value="founder_personal_brand">Founder personal brand</option>
            </select>
          </label>
          <label>Scan depth
            <select name="scan_depth">
              <option value="">Preset default</option>
              <option value="quick">Quick</option>
              <option value="balanced">Balanced</option>
              <option value="deep">Deep</option>
            </select>
          </label>
          <label>Punchiness
            <select name="punchiness">
              <option value="">Preset default</option>
              <option value="normal">Normal</option>
              <option value="fast">Fast</option>
              <option value="hyper">Hyper</option>
            </select>
          </label>
          <label>Minimum BPM<input type="number" name="min_bpm" min="60" max="220" step="1"></label>
          <label>Diversity strength<input type="number" name="diversity_strength" min="0" max="4" step="0.1"></label>
          <label>Audio variants<input type="text" name="audio_variants" autocomplete="off" placeholder="silent,auto,bpm128"></label>
          <label>Seed<input type="text" name="seed" autocomplete="off" placeholder="default"></label>
        </div>
      </section>
      <section>
        <div class="actions">
          <button type="submit">Save Settings</button>
          <button type="button" class="secondary" data-action="/api/warm-cache">Warm Cache</button>
          <button type="button" class="secondary" data-action="/api/generate-batch">Generate Batch</button>
          <a id="open-latest-review" class="button secondary {latest_disabled}" href="{latest_href}">Open Selected Review</a>
        </div>
        <div class="status-row">
          <span id="job-state">Ready.</span>
          <span id="job-id"></span>
        </div>
        <div class="progress-shell">
          <div class="progress-track"><div id="job-progress-fill" class="progress-fill"></div></div>
          <div class="progress-meta">
            <span id="job-progress-label">Waiting to start.</span>
            <span id="job-progress-value">0%</span>
          </div>
        </div>
        <pre id="status">No job running.</pre>
      </section>
    </form>
  </main>
  <script>
    const initialSettings = {settings_json};
    const presets = {presets_json};
    const initialStartupCheck = {startup_check_json};
    const form = document.querySelector("#settings-form");
    const presetSelect = document.querySelector("#preset-select");
    const presetDescription = document.querySelector("#preset-description");
    const startupChecks = document.querySelector("#startup-checks");
    const learnedFeedbackStatus = document.querySelector("#learned-feedback-status");
    const statusBox = document.querySelector("#status");
    const jobState = document.querySelector("#job-state");
    const jobIdNode = document.querySelector("#job-id");
    const openLatestReviewLink = document.querySelector("#open-latest-review");
    const jobProgressFill = document.querySelector("#job-progress-fill");
    const jobProgressLabel = document.querySelector("#job-progress-label");
    const jobProgressValue = document.querySelector("#job-progress-value");
    let pollTimer = null;

    function setStatus(payload) {{
      statusBox.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
    }}

    function setJobState(text, jobId) {{
      jobState.textContent = text;
      jobIdNode.textContent = jobId ? `Job ${{jobId}}` : "";
    }}

    function renderJobProgress(progress, status) {{
      const percent = Number(progress?.percent || (status === "completed" ? 100 : 0));
      const label = progress?.label || (status === "completed" ? "Completed." : status === "failed" ? "Failed." : "Waiting to start.");
      const current = progress?.current;
      const total = progress?.total;
      jobProgressFill.style.width = `${{Math.max(0, Math.min(percent, 100))}}%`;
      jobProgressValue.textContent = `${{Math.max(0, Math.min(percent, 100))}}%`;
      jobProgressLabel.textContent = Number.isFinite(Number(current)) && Number.isFinite(Number(total)) && Number(total) > 0
        ? `${{label}} (${{current}}/${{total}})`
        : label;
    }}

    function renderStartupChecks(payload) {{
      const issues = payload.issues || [];
      const warnings = payload.warnings || [];
      if (!issues.length && !warnings.length) {{
        startupChecks.innerHTML = '<p class="diag-ok">Ready. No obvious startup problems were found for the current settings.</p>';
        return;
      }}
      const rows = [];
      for (const issue of issues) {{
        rows.push(`<li class="diag-error"><strong>Error:</strong> ${{issue}}</li>`);
      }}
      for (const warning of warnings) {{
        rows.push(`<li class="diag-warning"><strong>Warning:</strong> ${{warning}}</li>`);
      }}
      startupChecks.innerHTML = `<ul class="diag-list">${{rows.join("")}}</ul>`;
    }}

    function renderLearnedFeedbackStatus(payload) {{
      const generatedPath = payload.generated_feedback_path || "";
      if (payload.using_generated_feedback && generatedPath) {{
        learnedFeedbackStatus.innerHTML = `<p class="diag-info">Next generation will use learned feedback from:<br><code>${{generatedPath}}</code></p>`;
        return;
      }}
      if (generatedPath) {{
        learnedFeedbackStatus.innerHTML = `<p class="muted">Shared state is configured, but no generated feedback file exists yet.<br><code>${{generatedPath}}</code></p>`;
        return;
      }}
      learnedFeedbackStatus.innerHTML = '<p class="muted">Set `Shared state directory` to enable automatic learned feedback on future generations.</p>';
    }}

    function updateLatestReviewLink(pathValue) {{
      const hasPath = Boolean(String(pathValue || "").trim());
      openLatestReviewLink.classList.toggle("disabled", !hasPath);
      openLatestReviewLink.href = hasPath ? "/review/latest/" : "#";
      openLatestReviewLink.setAttribute("aria-disabled", hasPath ? "false" : "true");
    }}

    function readForm() {{
      const data = Object.fromEntries(new FormData(form).entries());
      for (const key of ["preset_id", "input_dir", "output_dir", "archive_dir", "shared_state_dir", "cache_dir", "music_dir", "music_manifest", "config_path", "latest_batch_dir", "count", "duration_min", "duration_max", "style", "scan_depth", "punchiness", "min_bpm", "diversity_strength", "audio_variants", "seed"]) {{
        data[key] = form.elements[key].value;
      }}
      return data;
    }}

    function writeForm(settings) {{
      for (const [key, value] of Object.entries(settings)) {{
        const field = form.elements[key];
        if (!field) continue;
        if (field instanceof RadioNodeList) {{
          field.value = value;
        }} else {{
          field.value = value || "";
        }}
      }}
      if (settings.reviewer_id) localStorage.setItem("insta_autolayout.reviewer_id", settings.reviewer_id);
      if (settings.project_id) localStorage.setItem("insta_autolayout.project_id", settings.project_id);
      if (settings.preset_id && selectedPreset()) applyPreset(selectedPreset());
      updatePresetDescription();
      updateLatestReviewLink(settings.latest_batch_dir || "");
    }}

    function renderPresets() {{
      presetSelect.innerHTML = '<option value="">Custom paths / manual config</option>' + presets.map((preset) => (
        `<option value="${{preset.id}}">${{preset.label}}</option>`
      )).join("");
    }}

    function selectedPreset() {{
      return presets.find((preset) => preset.id === presetSelect.value);
    }}

    function updatePresetDescription() {{
      const preset = selectedPreset();
      presetDescription.textContent = preset ? preset.description : "Use this when you want to fill paths manually or use a one-off config.";
    }}

    function applyPreset(preset) {{
      if (!preset) return;
      form.elements.config_path.value = preset.config_path || "";
      form.elements.input_dir.value = preset.input_dir || "";
      form.elements.output_dir.value = preset.output_dir || "";
      form.elements.archive_dir.value = preset.archive_dir || "";
      form.elements.cache_dir.value = preset.cache_dir || "";
      form.elements.music_dir.value = preset.music_dir || "";
      form.elements.music_manifest.value = preset.music_manifest || "";
      form.elements.latest_batch_dir.value = preset.output_dir || "";
      form.elements.count.value = preset.count || "";
      form.elements.duration_min.value = preset.duration_min || "";
      form.elements.duration_max.value = preset.duration_max || "";
      form.elements.style.value = preset.style || "";
      form.elements.scan_depth.value = preset.scan_depth || "";
      form.elements.punchiness.value = preset.punchiness || "";
      form.elements.min_bpm.value = preset.min_bpm || "";
      form.elements.diversity_strength.value = preset.diversity_strength || "";
      form.elements.audio_variants.value = preset.audio_variants || "";
      form.elements.seed.value = preset.seed || "";
      updatePresetDescription();
    }}

    presetSelect.addEventListener("change", async () => {{
      const preset = selectedPreset();
      if (preset) {{
        applyPreset(preset);
        setStatus({{ ok: true, selected_preset: preset.label, config_path: preset.config_path }});
      }} else {{
        updatePresetDescription();
      }}
      await saveSettings();
    }});

    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      const result = await saveSettings();
      setStatus(result);
    }});

    async function saveSettings() {{
      const settings = readForm();
      settings.project_id = "trybe";
      if (settings.reviewer_id) localStorage.setItem("insta_autolayout.reviewer_id", settings.reviewer_id);
      localStorage.setItem("insta_autolayout.project_id", "trybe");
      const response = await fetch("/api/settings", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(settings)
      }});
      const payload = await response.json();
      writeForm(payload);
      await refreshStartupChecks();
      return payload;
    }}

    async function refreshStartupChecks() {{
      const response = await fetch("/api/startup-check");
      const payload = await response.json();
      renderStartupChecks(payload);
      renderLearnedFeedbackStatus(payload);
    }}

    for (const button of document.querySelectorAll("[data-action]")) {{
      button.addEventListener("click", async () => {{
        await saveSettings();
        const response = await fetch(button.dataset.action, {{ method: "POST" }});
        const payload = await response.json();
        if (payload.job_id) {{
          setStatus("Job started. Waiting for logs...");
          pollJob(payload.job_id);
        }} else {{
          setStatus(payload);
        }}
      }});
    }}

    for (const button of document.querySelectorAll("[data-choose]")) {{
      button.addEventListener("click", async () => {{
        await saveSettings();
        setJobState("Opening file picker...", "");
        const field = button.dataset.choose;
        const response = await fetch("/api/choose-path", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            field,
            kind: button.dataset.kind,
            current: form.elements[field].value
          }})
        }});
        const payload = await response.json();
        if (payload.ok && payload.path) {{
          form.elements[field].value = payload.path;
          await saveSettings();
          setJobState("Path selected.", "");
          setStatus(payload);
        }} else {{
          setJobState("Path selection cancelled or failed.", "");
          setStatus(payload);
        }}
      }});
    }}

    for (const button of document.querySelectorAll("[data-reveal]")) {{
      button.addEventListener("click", async () => {{
        const field = button.dataset.reveal;
        const response = await fetch("/api/reveal-path", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{ path: form.elements[field].value }})
        }});
        setStatus(await response.json());
      }});
    }}

    async function pollJob(jobId) {{
      if (pollTimer) clearTimeout(pollTimer);
      const response = await fetch(`/api/jobs/${{encodeURIComponent(jobId)}}`);
      const job = await response.json();
      setJobState(job.status || "unknown", jobId);
      renderJobProgress(job.progress || null, job.status || "");
      setStatus((job.logs || []).join("\\n") || JSON.stringify(job, null, 2));
      if (job.status === "running" || job.status === "queued") {{
        pollTimer = setTimeout(() => pollJob(jobId), 1000);
      }} else {{
        if (job.latest_batch_dir) {{
          form.elements.latest_batch_dir.value = job.latest_batch_dir;
          updateLatestReviewLink(job.latest_batch_dir);
          await saveSettings();
        }}
      }}
    }}

    renderPresets();
    writeForm(initialSettings);
    renderStartupChecks(initialStartupCheck);
    renderLearnedFeedbackStatus(initialStartupCheck);
  </script>
</body>
</html>
"""


def _path_exists(value: str) -> bool:
    if not value:
        return False
    return Path(value).expanduser().exists()


def _preset_records() -> list[dict[str, str]]:
    specs = [
        (
            "event_football",
            "Event invite - Pickup football",
            "Clean branded Trybe event invite for pickup football. Use when the goal is a specific event promo.",
            "promo_config_trybe_event_football_onedrive.json",
        ),
        (
            "event_board_games",
            "Event invite - Board game night",
            "Clean branded Trybe event invite for board games. Use when the goal is a specific event promo.",
            "promo_config_trybe_event_board_games_onedrive.json",
        ),
        (
            "event_climbing",
            "Event invite - Climbing night",
            "Clean branded Trybe event invite for climbing. Use when the goal is a specific event promo.",
            "promo_config_trybe_event_climbing_onedrive.json",
        ),
        (
            "generic_sports_invite",
            "Generic Trybe sports invite",
            "Clean branded Trybe invite for the broader sports/activity positioning.",
            "promo_config_trybe_generic_sports_invite_onedrive.json",
        ),
        (
            "punchy_sports_curated",
            "Punchy sports batch - curated",
            "Fast punchy royalty-free sports exploration. Use when the goal is to find energetic social edits.",
            "promo_config_royalty_free_sports_first_pass.json",
        ),
        (
            "punchy_hyper_multibatch",
            "Punchy hyper exploration - multi-batch",
            "Fast hyper multi-batch exploration with multiple diversity settings.",
            "promo_config_hyper_multibatch.json",
        ),
        (
            "punchy_single50",
            "Punchy hyper exploration - single 50",
            "Large single-batch fast punchy exploration with strong diversity pressure.",
            "promo_config_single50.json",
        ),
    ]
    records = []
    for preset_id, label, description, filename in specs:
        path = PROJECT_ROOT / filename
        raw = _load_config_payload(path)
        records.append(
            {
                "id": preset_id,
                "label": label,
                "description": description,
                "config_path": str(path),
                "input_dir": str(raw.get("input") or ""),
                "output_dir": str(raw.get("output") or ""),
                "archive_dir": str(raw.get("archive_output") or raw.get("archive-output") or ""),
                "cache_dir": str(raw.get("cache_dir") or raw.get("cache-dir") or ""),
                "music_dir": str(raw.get("music_dir") or raw.get("music-dir") or ""),
                "music_manifest": str(raw.get("music_manifest") or raw.get("music-manifest") or ""),
                "count": str(raw.get("count") or ""),
                "duration_min": str(raw.get("duration_min") or raw.get("duration-min") or ""),
                "duration_max": str(raw.get("duration_max") or raw.get("duration-max") or ""),
                "style": str(raw.get("style") or ""),
                "scan_depth": str(raw.get("scan_depth") or raw.get("scan-depth") or ""),
                "punchiness": str(raw.get("punchiness") or ""),
                "min_bpm": str(raw.get("min_bpm") or raw.get("min-bpm") or ""),
                "diversity_strength": str(raw.get("diversity_strength") or raw.get("diversity-strength") or ""),
                "audio_variants": _stringify_variants(raw.get("audio_variants") or raw.get("audio-variants")),
                "seed": str(raw.get("seed") or ""),
            }
        )
    return records


def _load_config_payload(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _stringify_variants(value: object) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value if str(item).strip())
    return str(value or "")


def _startup_check(settings: dict[str, str]) -> dict[str, object]:
    issues: list[str] = []
    warnings: list[str] = []
    generated_feedback_path = ""
    using_generated_feedback = False

    if not shutil.which("ffmpeg"):
        warnings.append("`ffmpeg` is not on PATH. Rendering may still work through bundled helpers on some machines, but install ffmpeg for predictable setup.")

    if sys.platform != "darwin":
        try:
            import tkinter  # noqa: F401
        except Exception:
            warnings.append("`tkinter` is not available, so the Choose buttons may not work on this machine. You can still paste paths manually.")

    reviewer_id = (settings.get("reviewer_id") or "").strip()
    if reviewer_id not in {"rami", "max"}:
        issues.append("Reviewer must be set to `rami` or `max`.")

    input_dir = _existing_path(settings.get("input_dir", ""))
    if settings.get("input_dir") and input_dir is None:
        issues.append("Input directory does not exist.")

    shared_state_dir = settings.get("shared_state_dir", "").strip()
    if shared_state_dir:
        shared_path = Path(shared_state_dir).expanduser()
        generated_feedback_file = _generated_feedback_path(shared_path)
        generated_feedback_path = str(generated_feedback_file)
        if not shared_path.exists():
            warnings.append("Shared state directory does not exist yet. The app can create `review_state`, but make sure this path is really the synced OneDrive folder on this machine.")
        else:
            using_generated_feedback = generated_feedback_file.exists()
    else:
        warnings.append("Shared state directory is empty. Review events will not save until it is set.")

    config_path = _existing_path(settings.get("config_path", ""))
    if settings.get("config_path") and config_path is None:
        issues.append("Config path does not exist.")

    music_dir = _existing_path(settings.get("music_dir", ""))
    if settings.get("music_dir") and music_dir is None:
        issues.append("Music directory does not exist.")

    music_manifest = _existing_path(settings.get("music_manifest", ""))
    if settings.get("music_manifest") and music_manifest is None:
        issues.append("Music manifest does not exist.")

    output_dir_raw = settings.get("output_dir", "").strip()
    if output_dir_raw:
        output_dir = Path(output_dir_raw).expanduser()
        parent = output_dir if output_dir.exists() else output_dir.parent
        if not parent.exists():
            warnings.append("Output directory parent does not exist yet. Create it first or choose a path under an existing folder.")
    else:
        warnings.append("Output directory is empty.")

    latest_batch_dir = _existing_path(settings.get("latest_batch_dir", ""))
    if settings.get("latest_batch_dir") and latest_batch_dir is None:
        warnings.append("Latest batch directory does not exist yet, so Open Latest Review will stay disabled.")

    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "platform": sys.platform,
        "generated_feedback_path": generated_feedback_path,
        "using_generated_feedback": using_generated_feedback,
    }


def _existing_path(value: str) -> Path | None:
    raw = value.strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.exists() else None


def _generated_feedback_path(shared_state_root: Path) -> Path:
    review_state_dir = shared_state_root if shared_state_root.name == "review_state" else shared_state_root / "review_state"
    return review_state_dir / "derived" / "manual-overrides.generated.json"


def _start_generation_job(warm_cache_only: bool) -> dict:
    job_id = uuid4().hex[:10]
    command = _generation_command(load_local_settings(), warm_cache_only)
    job = {
        "job_id": job_id,
        "action": "warm_cache" if warm_cache_only else "generate_batch",
        "status": "queued",
        "command": command,
        "logs": [f"[{_now()}] queued {'warm cache' if warm_cache_only else 'batch generation'}"],
        "returncode": None,
        "started_at": None,
        "completed_at": None,
        "progress": {"stage": "queued", "label": "Queued", "percent": 0},
    }
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    thread = threading.Thread(target=_run_generation_job, args=(job_id, warm_cache_only), daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id, "job": _public_job(job)}


def _job_snapshot(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return {"ok": False, "status": "missing", "job_id": job_id, "logs": ["Unknown job."]}
        return _public_job(job)


def _run_generation_job(job_id: str, warm_cache_only: bool) -> None:
    settings = load_local_settings()
    command = _generation_command(settings, warm_cache_only)
    _update_job(
        job_id,
        status="running",
        started_at=_now(),
        progress={"stage": "running", "label": "Starting job", "percent": 1},
        logs=[f"[{_now()}] running: {_command_text(command)}"],
    )
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    except OSError as exc:
        _update_job(job_id, status="failed", completed_at=_now(), returncode=-1, logs=[f"[{_now()}] failed to start: {exc}"])
        return

    assert process.stdout is not None
    for line in process.stdout:
        stripped = line.rstrip()
        if not _maybe_update_job_progress(job_id, stripped):
            _append_job_log(job_id, stripped)
    returncode = process.wait()
    completed_at = _now()
    latest_batch_dir = None
    if returncode == 0 and not warm_cache_only and settings.get("output_dir"):
        settings["latest_batch_dir"] = settings["output_dir"]
        save_local_settings(settings)
        latest_batch_dir = settings["latest_batch_dir"]
    status = "completed" if returncode == 0 else "failed"
    extra = [f"[{completed_at}] {status} with return code {returncode}"]
    if latest_batch_dir:
        extra.append(f"[{completed_at}] latest batch: {latest_batch_dir}")
    _update_job(
        job_id,
        status=status,
        completed_at=completed_at,
        returncode=returncode,
        latest_batch_dir=latest_batch_dir,
        progress={"stage": status, "label": "Completed" if status == "completed" else "Failed", "percent": 100 if status == "completed" else 0},
        logs=extra,
    )


def _generation_command(settings: dict[str, str], warm_cache_only: bool) -> list[str]:
    command = [sys.executable, "-m", "insta_autolayout"]
    config_path = settings.get("config_path") or ""
    if config_path:
        command.extend(["--config", config_path])
    for setting_key, flag in (
        ("input_dir", "--input"),
        ("output_dir", "--output"),
        ("archive_dir", "--archive-output"),
        ("shared_state_dir", "--shared-state"),
        ("cache_dir", "--cache-dir"),
        ("count", "--count"),
        ("duration_min", "--duration-min"),
        ("duration_max", "--duration-max"),
        ("style", "--style"),
        ("scan_depth", "--scan-depth"),
        ("punchiness", "--punchiness"),
        ("min_bpm", "--min-bpm"),
        ("diversity_strength", "--diversity-strength"),
        ("audio_variants", "--audio-variants"),
        ("music_dir", "--music-dir"),
        ("music_manifest", "--music-manifest"),
        ("seed", "--seed"),
    ):
        value = settings.get(setting_key) or ""
        if value:
            command.extend([flag, value])
    if warm_cache_only:
        command.append("--warm-cache-only")
    return command


def _update_job(job_id: str, **updates: object) -> None:
    logs = updates.pop("logs", None)
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job.update({key: value for key, value in updates.items() if value is not None})
        if logs:
            job.setdefault("logs", []).extend(str(line) for line in logs if str(line))
            job["logs"] = job["logs"][-400:]


def _append_job_log(job_id: str, line: str) -> None:
    if not line:
        return
    _update_job(job_id, logs=[line])


def _public_job(job: dict) -> dict:
    return {
        "ok": job.get("status") != "missing",
        "job_id": job.get("job_id"),
        "action": job.get("action"),
        "status": job.get("status"),
        "command": job.get("command"),
        "logs": list(job.get("logs", [])),
        "returncode": job.get("returncode"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("completed_at"),
        "latest_batch_dir": job.get("latest_batch_dir"),
        "progress": dict(job.get("progress", {})),
    }


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _command_text(command: list[str]) -> str:
    return " ".join(command)


def _tail(value: str, limit: int = 12000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _maybe_update_job_progress(job_id: str, line: str) -> bool:
    prefix = "__PROGRESS__ "
    if not line.startswith(prefix):
        return False
    try:
        payload = json.loads(line[len(prefix) :])
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    progress = {
        "stage": str(payload.get("stage") or ""),
        "label": str(payload.get("label") or ""),
        "percent": int(payload.get("percent") or 0),
    }
    if payload.get("current") is not None:
        progress["current"] = int(payload["current"])
    if payload.get("total") is not None:
        progress["total"] = int(payload["total"])
    _update_job(job_id, progress=progress)
    return True


def _choose_path(payload: dict) -> dict:
    kind = str(payload.get("kind") or "directory")
    field = str(payload.get("field") or "path")
    current = str(payload.get("current") or "").strip()
    prompt = f"Choose {field.replace('_', ' ')}"
    if sys.platform != "darwin":
        return _choose_path_with_tk(kind=kind, field=field, current=current, prompt=prompt)
    default_path = _default_picker_location(current, kind)
    if kind == "file":
        script = f'choose file with prompt {_as_applescript_string(prompt)}{default_path}'
    elif kind == "directory":
        script = f'choose folder with prompt {_as_applescript_string(prompt)}{default_path}'
    else:
        raise ValueError("kind must be file or directory")
    script = f"set chosenItem to {script}\nreturn POSIX path of chosenItem"
    try:
        result = subprocess.run(["osascript", "-e", script], check=False, capture_output=True, text=True)
    except OSError as exc:
        return {"ok": False, "field": field, "error": str(exc)}
    if result.returncode != 0:
        return {"ok": False, "field": field, "cancelled": True, "stderr": result.stderr.strip()}
    return {"ok": True, "field": field, "path": result.stdout.strip()}


def _choose_path_with_tk(*, kind: str, field: str, current: str, prompt: str) -> dict:
    if kind not in {"file", "directory"}:
        raise ValueError("kind must be file or directory")
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        return {"ok": False, "field": field, "error": f"Local file picker unavailable: {exc}"}
    root = None
    initial_dir = str(Path(current).expanduser()) if current else ""
    if initial_dir and not Path(initial_dir).exists():
        initial_dir = str(Path(initial_dir).parent)
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if kind == "file":
            chosen = filedialog.askopenfilename(title=prompt, initialdir=initial_dir or None)
        else:
            chosen = filedialog.askdirectory(title=prompt, initialdir=initial_dir or None, mustexist=False)
    except Exception as exc:
        return {"ok": False, "field": field, "error": f"File picker failed: {exc}"}
    finally:
        if root is not None:
            root.destroy()
    if not chosen:
        return {"ok": False, "field": field, "cancelled": True}
    return {"ok": True, "field": field, "path": str(Path(chosen).expanduser())}


def _reveal_path(payload: dict) -> dict:
    raw = str(payload.get("path") or "").strip()
    if not raw:
        raise ValueError("path is required")
    path = Path(raw).expanduser()
    if not path.exists():
        return {"ok": False, "path": str(path), "error": "Path does not exist yet."}
    command = _reveal_command(path)
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True)
    except OSError as exc:
        return {"ok": False, "path": str(path), "error": str(exc)}
    return {
        "ok": result.returncode == 0,
        "path": str(path),
        "returncode": result.returncode,
        "stderr": result.stderr.strip(),
    }


def _reveal_command(path: Path) -> list[str]:
    if sys.platform == "darwin":
        return ["open", str(path)] if path.is_dir() else ["open", "-R", str(path)]
    if sys.platform.startswith("win"):
        return ["explorer", str(path)] if path.is_dir() else ["explorer", f"/select,{path}"]
    target = path if path.is_dir() else path.parent
    return ["xdg-open", str(target)]


def _default_picker_location(current: str, kind: str) -> str:
    if not current:
        return ""
    path = Path(current).expanduser()
    if kind == "file" and path.is_file():
        path = path.parent
    if not path.exists():
        path = path.parent
    if not path.exists():
        return ""
    return f" default location POSIX file {_as_applescript_string(str(path))}"


def _as_applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _record_review_event(payload: dict) -> dict:
    settings = load_local_settings()
    latest_dir = Path(settings.get("latest_batch_dir") or "").expanduser().resolve()
    if not latest_dir.exists():
        raise ValueError("latest_batch_dir is required before saving review events")
    shared_state_dir = settings.get("shared_state_dir") or ""
    if not shared_state_dir:
        raise ValueError("shared_state_dir is required before saving review events")
    payload = dict(payload)
    payload["batch_id"] = payload.get("batch_id") or latest_dir.name
    payload["reviewer_id"] = settings.get("reviewer_id") or payload.get("reviewer_id") or "rami"
    payload["project_id"] = settings.get("project_id") or payload.get("project_id") or "trybe"

    from .shared_state import SharedReviewState

    state = SharedReviewState(shared_state_dir)
    event = state.append_event(payload)
    summary = state.rebuild_summary(str(event["batch_id"]))
    state.rebuild_manual_overrides()
    return {"ok": True, "event": event, "summary": summary}
