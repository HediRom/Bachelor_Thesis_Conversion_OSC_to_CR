import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";

// Once VS Code installs this extension, it's extracted into its own
// directory (e.g. ~/.vscode-server/extensions/...) — __dirname no longer
// has any relation to the osc2cr_extended checkout, so the pipeline
// location must be configured explicitly, the same way storyboard.pythonPath is.
//
// An empty value means "not configured": the bridge is then run as a module
// (`python -m osc2cr_extended.web.vscode_bridge`), which works whenever the
// configured interpreter can already import the package.
function getStoryboardDir(): string {
  return vscode.workspace
    .getConfiguration("storyboard")
    .get<string>("storyboardParsingPath", "");
}

let outputChannel: vscode.OutputChannel;

export function activate(context: vscode.ExtensionContext) {
  outputChannel = vscode.window.createOutputChannel("Storyboard Parsing");

  const disposable = vscode.commands.registerCommand(
    "storyboard.runOnActiveFile",
    (uri?: vscode.Uri) => runOnActiveFile(uri)
  );
  context.subscriptions.push(disposable, outputChannel);
}

function resolveXoscPath(uri?: vscode.Uri): string | undefined {
  if (uri?.fsPath) {
    return uri.fsPath;
  }
  const activeDoc = vscode.window.activeTextEditor?.document;
  if (activeDoc?.fileName) {
    return activeDoc.fileName;
  }
  return undefined;
}

async function runOnActiveFile(uri?: vscode.Uri): Promise<void> {
  const xoscPath = resolveXoscPath(uri);
  if (!xoscPath || !xoscPath.toLowerCase().endsWith(".xosc")) {
    vscode.window.showErrorMessage(
      "Storyboard: select or open a .xosc file first."
    );
    return;
  }
  // Unconfigured: run the bridge as a module and let the interpreter find it.
  // Configured: run the file directly, for a checkout that is not installed.
  const storyboardDir = getStoryboardDir();
  let bridgeArgs: string[];
  let cwd: string | undefined;

  if (storyboardDir) {
    const bridgeScript = path.join(storyboardDir, "vscode_bridge.py");
    if (!fs.existsSync(bridgeScript)) {
      vscode.window.showErrorMessage(
        `Storyboard: bridge script not found at ${bridgeScript}. ` +
          `Check the "storyboard.storyboardParsingPath" setting.`
      );
      return;
    }
    bridgeArgs = [bridgeScript, xoscPath];
    cwd = storyboardDir;
  } else {
    bridgeArgs = ["-m", "osc2cr_extended.web.vscode_bridge", xoscPath];
    cwd = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  }

  const config = vscode.workspace.getConfiguration("storyboard");
  const pythonPath = config.get<string>("pythonPath", "python3");
  const timeoutSeconds = config.get<number>("timeoutSeconds", 120);

  outputChannel.clear();
  outputChannel.show(true);
  outputChannel.appendLine(`Running pipeline on ${xoscPath}`);

  const child = cp.spawn(pythonPath, bridgeArgs, {
    cwd,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
  });

  let summaryJsonPath: string | undefined;
  let settled = false;
  let panel: vscode.WebviewPanel | undefined;

  // esmini can hang indefinitely on some scenarios (observed with pedestrian.xosc);
  // without this the output channel just sits there with no way to recover.
  const timeoutHandle = setTimeout(() => {
    if (settled) {
      return;
    }
    outputChannel.appendLine(`\nTimed out after ${timeoutSeconds}s — killing process.`);
    child.kill();
    vscode.window.showErrorMessage(
      `Storyboard: pipeline timed out after ${timeoutSeconds}s (esmini may be stuck on this scenario). ` +
        `Adjust "storyboard.timeoutSeconds" if it just needs more time.`
    );
  }, timeoutSeconds * 1000);

  await vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: `Storyboard: parsing ${path.basename(xoscPath)}`,
      cancellable: true,
    },
    (_progress, cancelToken) => {
      cancelToken.onCancellationRequested(() => {
        if (!settled) {
          outputChannel.appendLine("\nCancelled by user.");
          child.kill();
        }
      });
      return new Promise<void>((resolve) => {
        child.stdout.on("data", (chunk: Buffer) => {
          const text = chunk.toString();
          outputChannel.append(text);
          for (const line of text.split("\n")) {
            if (line.startsWith("SUMMARY_JSON:")) {
              summaryJsonPath = line.slice("SUMMARY_JSON:".length).trim();
            } else if (line.startsWith("PREVIEW_PNG:")) {
              const previewPath = line.slice("PREVIEW_PNG:".length).trim();
              panel = getOrCreatePanel(panel, xoscPath, storyboardDir);
              panel.webview.html = renderRunningHtml(
                path.basename(xoscPath),
                toWebviewUri(panel, previewPath)
              );
            }
          }
        });

        child.stderr.on("data", (chunk: Buffer) => {
          outputChannel.append(chunk.toString());
        });

        child.on("error", (err) => {
          settled = true;
          clearTimeout(timeoutHandle);
          vscode.window.showErrorMessage(`Storyboard: failed to launch Python (${err.message})`);
          resolve();
        });

        child.on("close", (code) => {
          settled = true;
          clearTimeout(timeoutHandle);
          if (code !== 0 || !summaryJsonPath) {
            if (code !== null) {
              vscode.window.showErrorMessage(
                `Storyboard pipeline failed (exit code ${code}). See "Storyboard Parsing" output for details.`
              );
            }
            resolve();
            return;
          }
          try {
            const summary = JSON.parse(fs.readFileSync(summaryJsonPath, "utf-8"));
            panel = getOrCreatePanel(panel, xoscPath, storyboardDir);
            panel.webview.html = renderResultsHtml(panel, summary);
            panel.reveal(vscode.ViewColumn.Beside);
          } catch (e) {
            vscode.window.showErrorMessage(
              `Storyboard: could not read summary JSON (${(e as Error).message})`
            );
          }
          resolve();
        });
      });
    }
  );
}

interface StrategySummary {
  xosc: string;
  output_dir: string;
  n_obstacles: number;
  n_events: number;
  n_conditions: number;
  summary_text: string;
  preview_png?: string | null;
  replay_gif?: string | null;
  strategies: {
    transcription: { scenario_file?: string; conditions_file?: string };
    translation: { mapped: number; skipped: number; report_file?: string; scenario_file?: string };
    interpretation: { trace_events: number; trace_file?: string; scenario_file?: string };
  };
}

function getOrCreatePanel(
  existing: vscode.WebviewPanel | undefined,
  xoscPath: string,
  storyboardDir: string
): vscode.WebviewPanel {
  if (existing) {
    return existing;
  }
  const panel = vscode.window.createWebviewPanel(
    "storyboardResults",
    `Storyboard: ${path.basename(xoscPath)}`,
    vscode.ViewColumn.Beside,
    {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.file(storyboardDir)],
    }
  );
  panel.webview.onDidReceiveMessage((message) => {
    if (message.command === "open" && typeof message.path === "string") {
      vscode.workspace.openTextDocument(message.path).then((doc) => {
        vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One });
      });
    }
  });
  return panel;
}

function toWebviewUri(panel: vscode.WebviewPanel, filePath: string): string {
  return panel.webview.asWebviewUri(vscode.Uri.file(filePath)).toString();
}

function renderRunningHtml(xoscName: string, previewUri: string): string {
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>
  body { font-family: var(--vscode-font-family); padding: 1rem; color: var(--vscode-foreground); }
  img { max-width: 100%; border: 1px solid var(--vscode-panel-border); }
  .status { color: var(--vscode-descriptionForeground); margin-top: 0.75rem; }
</style></head>
<body>
  <h2>${escapeHtml(xoscName)}</h2>
  <p>Initial layout (esmini conversion done, Transcription/Translation/Interpretation + replay GIF still running):</p>
  <img src="${previewUri}" alt="initial scenario layout">
  <p class="status">Pipeline running &hellip;</p>
</body>
</html>`;
}

function fileLink(filePath: string | undefined, label: string): string {
  if (!filePath) {
    return `<span class="muted">${label} (not produced)</span>`;
  }
  const safePath = filePath.replace(/"/g, "&quot;");
  return `<a href="#" data-path="${safePath}">${label}</a>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function renderResultsHtml(panel: vscode.WebviewPanel, summary: StrategySummary): string {
  const { transcription, translation, interpretation } = summary.strategies;
  const replayHtml = summary.replay_gif
    ? `<img src="${toWebviewUri(panel, summary.replay_gif)}" alt="replay animation">`
    : `<p class="muted">Replay GIF not produced.</p>`;
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
  body { font-family: var(--vscode-font-family); padding: 1rem; color: var(--vscode-foreground); }
  h2 { margin-top: 0; }
  img { max-width: 100%; border: 1px solid var(--vscode-panel-border); }
  table { border-collapse: collapse; width: 100%; margin: 1rem 0 1.5rem; }
  th, td { border: 1px solid var(--vscode-panel-border); padding: 6px 10px; text-align: left; vertical-align: top; }
  th { background: var(--vscode-editorWidget-background); }
  .muted { color: var(--vscode-disabledForeground); }
  a { color: var(--vscode-textLink-foreground); cursor: pointer; }
  pre { background: var(--vscode-textCodeBlock-background); padding: 0.75rem; overflow-x: auto; white-space: pre-wrap; }
</style>
</head>
<body>
  <h2>${escapeHtml(path.basename(summary.xosc))}</h2>
  <p>${summary.n_obstacles} obstacles &middot; ${summary.n_events} events &middot; ${summary.n_conditions} conditions</p>

  <h3>Replay</h3>
  ${replayHtml}

  <table>
    <tr><th></th><th>Transcription</th><th>Translation</th><th>Interpretation</th></tr>
    <tr>
      <th>Result</th>
      <td>all conditions tagged as metadata</td>
      <td>${translation.mapped} mapped, ${translation.skipped} skipped</td>
      <td>${interpretation.trace_events} events fired on replay</td>
    </tr>
    <tr>
      <th>Files</th>
      <td>
        ${fileLink(transcription.scenario_file, "scenario_transcription.xml")}<br>
        ${fileLink(transcription.conditions_file, "conditions_transcription.json")}
      </td>
      <td>
        ${fileLink(translation.scenario_file, "scenario_translation.xml")}<br>
        ${fileLink(translation.report_file, "report_translation.txt")}
      </td>
      <td>
        ${fileLink(interpretation.scenario_file, "scenario_interpretation.xml")}<br>
        ${fileLink(interpretation.trace_file, "trace_interpretation.json")}
      </td>
    </tr>
  </table>

  <h3>Summary</h3>
  <pre>${escapeHtml(summary.summary_text)}</pre>

  <script>
    const vscode = acquireVsCodeApi();
    document.querySelectorAll('a[data-path]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        vscode.postMessage({ command: 'open', path: el.getAttribute('data-path') });
      });
    });
  </script>
</body>
</html>`;
}

export function deactivate() {}
