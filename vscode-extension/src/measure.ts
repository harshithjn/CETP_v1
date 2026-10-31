import * as vscode from 'vscode';
import { execFile } from 'child_process';
import * as path from 'path';
import { ProdProfile } from './types';
import { ProfileStore } from './profiles';

/**
 * Measure this machine's hardware signature automatically.
 *
 * The extension used to ask the user to type a "compute score". Nobody can produce that
 * number by hand -- it comes from a microbenchmark -- so the field was unanswerable and the
 * feature was effectively unusable. This runs the probe instead.
 *
 * It also probes at the ACHIEVABLE degree of parallelism rather than at the core count.
 * A PostgreSQL query can only use 1 + max_parallel_workers_per_gather processes, so a figure
 * measured across every core describes capacity no query can reach: on an 8-core laptop that
 * mistake cost 324% vs 153% MAPE. hw_signature.py reads the setting from the server and
 * probes accordingly.
 */

export interface MeasuredSignature {
  machineId: string;
  bandwidth: number;
  compute: number;
  achievableDop: number;
  nproc: number;
  validated: boolean;
}

function runProbe(
  pythonPath: string,
  scriptPath: string,
  machineId: string,
  database: string,
  cwd: string
): Promise<MeasuredSignature> {
  return new Promise((resolve, reject) => {
    const args = [scriptPath, '--machine-id', machineId, '--db', database];
    // The probe deliberately spends a few seconds per axis to get above the timing noise
    // floor, so allow generous time rather than killing it half way.
    execFile(
      pythonPath,
      args,
      { cwd, timeout: 180_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err) {
          reject(new Error(`probe failed: ${stderr || err.message}`));
          return;
        }
        const registryPath = path.join(cwd, 'models', 'hardware_signatures.json');
        try {
          // eslint-disable-next-line @typescript-eslint/no-var-requires
          const fs = require('fs') as typeof import('fs');
          const reg = JSON.parse(fs.readFileSync(registryPath, 'utf8'));
          const entry = reg.machines?.[machineId];
          if (!entry) {
            reject(new Error(`probe ran but ${machineId} is not in the registry`));
            return;
          }
          const probe = entry.probe ?? {};
          const pg = entry.postgres ?? {};
          const host = entry.host ?? {};
          const compute = probe.fp_vector_gflops_dop ?? probe.fp_vector_gflops_nproc;
          if (!probe.mem_bandwidth_gbs_mt || !compute) {
            reject(new Error('probe produced no usable bandwidth/compute figures'));
            return;
          }
          resolve({
            machineId,
            bandwidth: probe.mem_bandwidth_gbs_mt,
            compute,
            achievableDop: pg.achievable_dop ?? 3,
            nproc: host.nproc ?? 0,
            validated: entry.validation?.ok ?? false
          });
        } catch (e) {
          reject(new Error(`could not read the signature registry: ${String(e)}`));
        }
      }
    );
  });
}

export async function measureThisMachine(
  profileStore: ProfileStore
): Promise<MeasuredSignature | undefined> {
  const config = vscode.workspace.getConfiguration('cetp');
  const pythonPath = config.get<string>('pythonPath') || 'python3';
  const repoRoot =
    config.get<string>('repoRoot') ||
    vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ||
    '';
  if (!repoRoot) {
    vscode.window.showErrorMessage(
      'CETP: set cetp.repoRoot to the CETP checkout so the probe can be found.'
    );
    return undefined;
  }

  const machineId = await vscode.window.showInputBox({
    prompt: 'Name for this machine (used as the signature id)',
    value: 'this-machine',
    validateInput: (v) =>
      /^[A-Za-z0-9._-]{1,40}$/.test(v) ? null : 'letters, digits, dot, dash, underscore'
  });
  if (!machineId) {
    return undefined;
  }
  const database =
    (await vscode.window.showInputBox({
      prompt: 'PostgreSQL database to read max_parallel_workers_per_gather from',
      value: config.get<string>('database') || 'postgres'
    })) || 'postgres';

  const scriptPath = path.join(repoRoot, 'scripts', 'collection', 'hw_signature.py');

  return vscode.window.withProgress(
    {
      location: vscode.ProgressLocation.Notification,
      title: 'CETP: measuring this machine (about 40 seconds)',
      cancellable: false
    },
    async () => {
      try {
        const sig = await runProbe(pythonPath, scriptPath, machineId, database, repoRoot);
        const profile: ProdProfile = {
          name: sig.machineId,
          bandwidth: sig.bandwidth,
          compute: sig.compute
        };
        await profileStore.save(profile);
        const warn = sig.validated ? '' : '  (WARNING: probe validation failed)';
        vscode.window.showInformationMessage(
          `CETP: ${sig.machineId} measured and saved. bandwidth ${sig.bandwidth.toFixed(1)} GB/s, ` +
            `compute ${sig.compute.toFixed(1)} GFLOPS at DOP ${sig.achievableDop} ` +
            `(${sig.nproc} cores present).${warn}`
        );
        return sig;
      } catch (e) {
        vscode.window.showErrorMessage(`CETP: ${String(e instanceof Error ? e.message : e)}`);
        return undefined;
      }
    }
  );
}
