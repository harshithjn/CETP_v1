import * as vscode from 'vscode';
import { spawnPrediction } from './backend';
import { PredictionResult } from './types';

export interface PredictionParams {
  pythonPath: string;
  backendScript: string;
  filePath: string;
  database: string;
  devBandwidth: number;
  devCompute: number;
  prodBandwidth: number;
  prodCompute: number;
  slaMs: number;
  queryId: string;
}

export async function executePrediction(params: PredictionParams): Promise<PredictionResult> {
  const args = [
    params.backendScript,
    '--query-file', params.filePath,
    '--db', params.database,
    '--dev-bandwidth', String(params.devBandwidth),
    '--dev-compute', String(params.devCompute),
    '--prod-bandwidth', String(params.prodBandwidth),
    '--prod-compute', String(params.prodCompute),
    '--sla-ms', String(params.slaMs),
    '--query-id', params.queryId,
  ];

  const { terminal, write } = createStreamingTerminal('CETP Predict');
  terminal.show(true);
  write(`cetp predict ${params.queryId}\n`);
  write(`> ${params.pythonPath} ${args.map(quoteArg).join(' ')}\n\n`);

  const outcome = await spawnPrediction(params.pythonPath, args, (text) => write(text));

  if (outcome.spawnError) {
    const message = `could not start python backend: ${outcome.spawnError}`;
    write(`\n${message}\n`);
    return { status: 'error', stage: 'backend', query_id: params.queryId, error: message };
  }

  write(`\nExit code: ${outcome.code}\n`);
  return parseResult(outcome.stdout, params.queryId);
}

function createStreamingTerminal(name: string): { terminal: vscode.Terminal; write: (text: string) => void } {
  const writeEmitter = new vscode.EventEmitter<string>();
  const pty: vscode.Pseudoterminal = {
    onDidWrite: writeEmitter.event,
    open: () => {},
    close: () => {},
  };
  const terminal = vscode.window.createTerminal({ name, pty });
  return {
    terminal,
    write: (text: string) => writeEmitter.fire(text.replace(/\r?\n/g, '\r\n')),
  };
}

function parseResult(stdout: string, fallbackQueryId: string): PredictionResult {
  const trimmed = stdout.trim();
  const lastLine = trimmed.split('\n').pop() || '';
  try {
    return JSON.parse(lastLine) as PredictionResult;
  } catch {
    return {
      status: 'error',
      stage: 'backend',
      query_id: fallbackQueryId,
      error: trimmed.length > 0 ? `unexpected backend output: ${trimmed.slice(0, 500)}` : 'backend produced no output',
    };
  }
}

function quoteArg(value: string): string {
  return /\s/.test(value) ? `"${value}"` : value;
}
