import * as vscode from 'vscode';
import { SidebarProvider } from './sidebarProvider';
import { ProfileStore, seedLegacyEc2Profiles } from './profiles';
import { runPredictCommand } from './predictCommand';
import { measureThisMachine } from './measure';

export async function activate(context: vscode.ExtensionContext): Promise<void> {
  const profileStore = new ProfileStore(context);

  // Awaited BEFORE the webview provider is registered: resolveWebviewView() reads
  // profileStore.list() synchronously on first render, and this extension activates
  // on "onView:cetp.predictionView" -- i.e. activation can be triggered by the user
  // opening the sidebar, so activate() and the view's first render can race. Awaiting
  // here means the dropdown is never seen empty before the seed lands.
  await seedLegacyEc2Profiles(profileStore);

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
