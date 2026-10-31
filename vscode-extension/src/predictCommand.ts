import * as vscode from 'vscode';
import { SidebarProvider } from './sidebarProvider';
import { validateQueryFile } from './validation';

export async function runPredictCommand(sidebarProvider: SidebarProvider, uri?: vscode.Uri): Promise<void> {
  const filePath = await resolveQueryFile(uri);
  if (!filePath) {
    return;
  }
  const validationError = await validateQueryFile(filePath);
  if (validationError) {
    vscode.window.showErrorMessage(`CETP: ${validationError}`);
    return;
  }
  await sidebarProvider.revealWithFile(filePath);
}

async function resolveQueryFile(uri?: vscode.Uri): Promise<string | undefined> {
  if (uri && uri.scheme === 'file') {
    return uri.fsPath;
  }
  const activeEditor = vscode.window.activeTextEditor;
  if (
    activeEditor &&
    activeEditor.document.uri.scheme === 'file' &&
    activeEditor.document.fileName.toLowerCase().endsWith('.sql')
  ) {
    return activeEditor.document.fileName;
  }
  const picked = await vscode.window.showOpenDialog({
    canSelectMany: false,
    filters: { 'SQL files': ['sql'] },
    openLabel: 'Select query file',
  });
  return picked && picked.length > 0 ? picked[0].fsPath : undefined;
}
