import * as vscode from 'vscode';
import * as path from 'path';

export async function validateQueryFile(filePath: string): Promise<string | undefined> {
  if (!filePath) {
    return 'select a .sql query file';
  }
  if (!filePath.toLowerCase().endsWith('.sql')) {
    return `expected a .sql file, got: ${path.basename(filePath)}`;
  }
  try {
    const stat = await vscode.workspace.fs.stat(vscode.Uri.file(filePath));
    if (stat.type !== vscode.FileType.File) {
      return `not a file: ${filePath}`;
    }
    if (stat.size === 0) {
      return `query file is empty: ${filePath}`;
    }
  } catch {
    return `query file not found: ${filePath}`;
  }
  return undefined;
}

/**
 * Find the backend script. This used to resolve ONLY against the currently open
 * workspace folder, so opening any folder other than the CETP checkout itself --
 * Desktop, a scratch folder, any other project -- broke prediction with "backend script
 * not found", even though cetp.repoRoot was already set correctly and used successfully
 * by the hardware probe (measure.ts). Now repoRoot is the primary root, exactly like the
 * probe, so prediction works from any open folder once repoRoot points at the checkout.
 */
export function resolveBackendScript(setting: string, repoRoot: string): string {
  const folders = vscode.workspace.workspaceFolders;
  const fallback = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
  const root = repoRoot || fallback;
  if (!setting) {
    return path.join(root, 'gate', 'cetp_predict_cli.py');
  }
  return setting.replace('${workspaceFolder}', root);
}

export async function fileExists(filePath: string): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(vscode.Uri.file(filePath));
    return true;
  } catch {
    return false;
  }
}
