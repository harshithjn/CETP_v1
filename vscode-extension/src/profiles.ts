import * as vscode from 'vscode';
import { ProdProfile } from './types';

const STORAGE_KEY = 'cetp.prodProfiles';

export class ProfileStore {
  constructor(private context: vscode.ExtensionContext) {}

  list(): ProdProfile[] {
    return this.context.globalState.get<ProdProfile[]>(STORAGE_KEY, []);
  }

  find(name: string): ProdProfile | undefined {
    return this.list().find((profile) => profile.name === name);
  }

  async save(profile: ProdProfile): Promise<void> {
    const profiles = this.list().filter((existing) => existing.name !== profile.name);
    profiles.push(profile);
    await this.context.globalState.update(STORAGE_KEY, profiles);
  }
}
