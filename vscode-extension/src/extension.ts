import * as vscode from 'vscode';
import { SidebarProvider } from './sidebarProvider';
import { ProfileStore } from './profiles';
import { runPredictCommand } from './predictCommand';
import { measureThisMachine } from './measure';

export function activate(context: vscode.ExtensionContext): void {
  const profileStore = new ProfileStore(context);
  const sidebarProvider = new SidebarProvider(context.extensionUri, context, profileStore);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(SidebarProvider.viewType, sidebarProvider)
  );

  // Measuring the machine replaces asking the user to type a compute score, which is a
  // microbenchmark result no human can produce by hand.
  context.subscriptions.push(
    vscode.commands.registerCommand('cetp.measureMachine', async () => {
      try {
        await measureThisMachine(profileStore);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`CETP: could not measure this machine: ${message}`);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('cetp.predict', async (uri?: vscode.Uri) => {
      try {
        await runPredictCommand(sidebarProvider, uri);
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        vscode.window.showErrorMessage(`CETP: unexpected error: ${message}`);
      }
    })
  );
}

export function deactivate(): void {}
