import { spawn } from 'child_process';

export interface SpawnOutcome {
  code: number | null;
  stdout: string;
  stderr: string;
  spawnError: string | null;
}

export type OutputChunkHandler = (text: string, stream: 'stdout' | 'stderr') => void;

export function spawnPrediction(pythonPath: string, args: string[], onChunk: OutputChunkHandler): Promise<SpawnOutcome> {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    let settled = false;

    const finish = (outcome: SpawnOutcome) => {
      if (settled) {
        return;
      }
      settled = true;
      resolve(outcome);
    };

    let child;
    try {
      child = spawn(pythonPath, args, { shell: false });
    } catch (err) {
      finish({ code: null, stdout, stderr, spawnError: err instanceof Error ? err.message : String(err) });
      return;
    }

    child.on('error', (err) => {
      onChunk(`${err.message}\n`, 'stderr');
      finish({ code: null, stdout, stderr, spawnError: err.message });
    });

    child.stdout.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      stdout += text;
      onChunk(text, 'stdout');
    });

    child.stderr.on('data', (chunk: Buffer) => {
      const text = chunk.toString();
      stderr += text;
      onChunk(text, 'stderr');
    });

    child.on('close', (code) => {
      finish({ code, stdout, stderr, spawnError: null });
    });
  });
}
