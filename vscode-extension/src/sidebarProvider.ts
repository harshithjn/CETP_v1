import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';
import { ProfileStore } from './profiles';
import { executePrediction } from './runner';
import { validateQueryFile, resolveBackendScript, fileExists } from './validation';
import { PredictionResult } from './types';

interface RunFormPayload {
  filePath: string;
  profileMode: 'saved' | 'manual';
  profileName?: string;
  bandwidth?: string;
  compute?: string;
  saveAsProfile?: boolean;
  newProfileName?: string;
  slaMs: string;
}

interface WebviewMessage {
  type: string;
  payload?: unknown;
}

export class SidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'cetp.predictionView';
  public static readonly containerCommand = 'workbench.view.extension.cetp-sidebar';

  private view?: vscode.WebviewView;
  private lastResult?: PredictionResult;
  private pendingFilePath?: string;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly context: vscode.ExtensionContext,
    private readonly profileStore: ProfileStore
  ) {
    this.lastResult = context.globalState.get<PredictionResult>('cetp.lastResult');
    this.pendingFilePath = context.globalState.get<string>('cetp.lastFilePath');
  }

  resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.view = webviewView;
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, 'media')],
    };
    webviewView.webview.html = this.getHtml(webviewView.webview);
    webviewView.webview.onDidReceiveMessage((message: WebviewMessage) => {
      this.handleMessage(message).catch((err) => {
        const text = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`CETP: unexpected error: ${text}`);
      });
    });
  }

  async revealWithFile(filePath: string): Promise<void> {
    this.pendingFilePath = filePath;
    await this.context.globalState.update('cetp.lastFilePath', filePath);
    await vscode.commands.executeCommand(SidebarProvider.containerCommand);
    if (this.view) {
      this.view.show?.(true);
      this.view.webview.postMessage({ type: 'prefillFile', payload: { path: filePath } });
    }
  }

  private async handleMessage(message: WebviewMessage): Promise<void> {
    if (!message || typeof message.type !== 'string') {
      return;
    }
    if (message.type === 'ready') {
      this.postInit();
      return;
    }
    if (message.type === 'browseFile') {
      const picked = await vscode.window.showOpenDialog({
        canSelectMany: false,
        filters: { 'SQL files': ['sql'] },
        openLabel: 'Select query file',
      });
      if (picked && picked.length > 0 && this.view) {
        this.view.webview.postMessage({ type: 'fileSelected', payload: { path: picked[0].fsPath } });
      }
      return;
    }
    if (message.type === 'run') {
      await this.handleRun(message.payload as RunFormPayload);
      return;
    }
  }

  private postInit(): void {
    if (!this.view) {
      return;
    }
    const config = vscode.workspace.getConfiguration('cetp');
    const activeEditor = vscode.window.activeTextEditor;
    const activeSqlFile =
      activeEditor && activeEditor.document.uri.scheme === 'file' && activeEditor.document.fileName.toLowerCase().endsWith('.sql')
        ? activeEditor.document.fileName
        : undefined;
    this.view.webview.postMessage({
      type: 'init',
      payload: {
        profiles: this.profileStore.list(),
        defaultSlaMs: config.get<number>('defaultSlaMs', 500),
        filePath: this.pendingFilePath || activeSqlFile || '',
        lastResult: this.lastResult,
      },
    });
  }

  private async handleRun(form: RunFormPayload): Promise<void> {
    if (!this.view || !form) {
      return;
    }
    const filePath = (form.filePath || '').trim();
    const validationError = await validateQueryFile(filePath);
    if (validationError) {
      await this.emitResult({
        status: 'error',
        stage: 'validation',
        query_id: filePath ? path.basename(filePath) : 'query',
        error: validationError,
      });
      return;
    }

    let bandwidth: number;
    let compute: number;

    if (form.profileMode === 'saved') {
      const profile = form.profileName ? this.profileStore.find(form.profileName) : undefined;
      if (!profile) {
        await this.emitResult({
          status: 'error',
          stage: 'validation',
          query_id: path.basename(filePath),
          error: 'select a saved profile or switch to manual entry',
        });
        return;
      }
      bandwidth = profile.bandwidth;
      compute = profile.compute;
    } else {
      const parsedBandwidth = parseFloat(form.bandwidth || '');
      const parsedCompute = parseFloat(form.compute || '');
      if (Number.isNaN(parsedBandwidth) || parsedBandwidth <= 0 || Number.isNaN(parsedCompute) || parsedCompute <= 0) {
        await this.emitResult({
          status: 'error',
          stage: 'validation',
          query_id: path.basename(filePath),
          error: 'bandwidth and compute must be positive numbers',
        });
        return;
      }
      bandwidth = parsedBandwidth;
      compute = parsedCompute;
      if (form.saveAsProfile && form.newProfileName && form.newProfileName.trim().length > 0) {
        await this.profileStore.save({ name: form.newProfileName.trim(), bandwidth, compute });
        this.view.webview.postMessage({ type: 'profiles', payload: this.profileStore.list() });
      }
    }

    const slaMs = parseFloat(form.slaMs || '');
    if (Number.isNaN(slaMs) || slaMs <= 0) {
      await this.emitResult({
        status: 'error',
        stage: 'validation',
        query_id: path.basename(filePath),
        error: 'SLA must be a positive number',
      });
      return;
    }

    const config = vscode.workspace.getConfiguration('cetp');
    const pythonPath = config.get<string>('pythonPath', 'python3');
    const database = config.get<string>('database', 'tpch');
    const devBandwidth = config.get<number>('devBandwidth', 8.44);
    const devCompute = config.get<number>('devCompute', 0.807);
    const backendScript = resolveBackendScript(config.get<string>('backendScript', ''));
    const queryId = path.basename(filePath, path.extname(filePath));

    if (!(await fileExists(backendScript))) {
      const message = `backend script not found: ${backendScript}`;
      vscode.window.showErrorMessage(`CETP: ${message}`);
      await this.emitResult({ status: 'error', stage: 'backend', query_id: queryId, error: message });
      return;
    }

    this.pendingFilePath = filePath;
    await this.context.globalState.update('cetp.lastFilePath', filePath);
    this.view.webview.postMessage({ type: 'running', payload: { queryId } });

    const result = await executePrediction({
      pythonPath,
      backendScript,
      filePath,
      database,
      devBandwidth,
      devCompute,
      prodBandwidth: bandwidth,
      prodCompute: compute,
      slaMs,
      queryId,
    });

    await this.emitResult(result);

    if (result.status === 'ok') {
      vscode.window.showInformationMessage(
        `CETP: ${result.verdict} — ${result.query_id} (p50 ${result.predicted_prod_ms.p50.toFixed(1)} ms)`
      );
    } else {
      vscode.window.showErrorMessage(`CETP: ${result.error}`);
    }
  }

  private async emitResult(result: PredictionResult): Promise<void> {
    this.lastResult = result;
    await this.context.globalState.update('cetp.lastResult', result);
    if (this.view) {
      this.view.webview.postMessage({ type: 'result', payload: result });
    }
  }

  private getHtml(webview: vscode.Webview): string {
    const htmlPath = vscode.Uri.joinPath(this.extensionUri, 'media', 'main.html');
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, 'media', 'main.css'));
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, 'media', 'main.js'));
    const nonce = getNonce();
    const template = fs.readFileSync(htmlPath.fsPath, 'utf8');
    return template
      .replace(/{{cspSource}}/g, webview.cspSource)
      .replace(/{{nonce}}/g, nonce)
      .replace(/{{styleUri}}/g, styleUri.toString())
      .replace(/{{scriptUri}}/g, scriptUri.toString());
  }
}

function getNonce(): string {
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let result = '';
  for (let i = 0; i < 32; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}
