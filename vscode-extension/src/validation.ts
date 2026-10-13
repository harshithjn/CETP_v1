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

export function resolveBackendScript(setting: string): string {
  const folders = vscode.workspace.workspaceFolders;
  const root = folders && folders.length > 0 ? folders[0].uri.fsPath : '';
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
