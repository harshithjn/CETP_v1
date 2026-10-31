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

  async remove(name: string): Promise<void> {
    const profiles = this.list().filter((existing) => existing.name !== name);
    await this.context.globalState.update(STORAGE_KEY, profiles);
  }
}

/**
 * The project's 5 original AWS EC2 instance types, used as the "legacy" hardware
 * signature registry throughout the research (see models/hardware_signature.pkl --
 * these are the exact bandwidth/compute values from that file, multi-core aggregate
 * compute, not the stale single-core numbers this extension originally defaulted to).
 *
 * The dropdown had no knowledge of these: ProfileStore starts empty and only ever fills
 * from "Measure This Machine" or a manually-saved entry. Nothing seeded it with the
 * machines the rest of the project actually measured on. Ensured present on every
 * activation by seedLegacyEc2Profiles below (an upsert, not a one-time guard -- a prior
 * version only seeded an empty store, which silently did nothing for a store that
 * already held unrelated test data).
 */
const LEGACY_EC2_PROFILES: ProdProfile[] = [
  { name: 'aws-c5a (c5a.xlarge, 4 vCPU)', bandwidth: 14.9, compute: 40.8071 },
  { name: 'aws-c7i (c7i.large, 2 vCPU)', bandwidth: 8.44, compute: 25.8909 },
  { name: 'aws-m5a (m5a.xlarge, 4 vCPU)', bandwidth: 10.01, compute: 31.5366 },
  { name: 'aws-r5n (r5n.large, 2 vCPU)', bandwidth: 9.39, compute: 24.9681 },
  { name: 'aws-z1d (z1d.large, 2 vCPU)', bandwidth: 9.77, compute: 28.0804 }
];

// Leftover entries from early development/demo testing, found still sitting in a real
// user's globalState: "c5a-demo" (bandwidth 14.9, compute 0.593 -- the OLD, disproven
// SINGLE-CORE score) and "random-machine" (a throwaway test value). Both predate this
// seed existing at all and would silently mislead a prediction if left in place, so they
// are removed by name rather than preserved as if they were real saved profiles.
const STALE_TEST_PROFILE_NAMES = ['c5a-demo', 'random-machine'];

/**
 * Ensure the 5 real AWS machines are present every activation (upsert by name -- ProfileStore.save
 * already de-duplicates on name, so this is safe to call every time and never creates
 * duplicates). This intentionally does NOT gate on "store is empty": a store that already
 * contained only stale test entries from early development was never actually seeded, and
 * the previous empty-check silently left it that way. Any OTHER profile you've added
 * yourself -- from "Measure This Machine" or a manual save -- is left untouched.
 */
export async function seedLegacyEc2Profiles(store: ProfileStore): Promise<void> {
  for (const name of STALE_TEST_PROFILE_NAMES) {
    await store.remove(name);
  }
  for (const profile of LEGACY_EC2_PROFILES) {
    await store.save(profile);
  }
}

