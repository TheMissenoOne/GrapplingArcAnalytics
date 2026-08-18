import { assertEquals } from 'https://deno.land/std@0.208.0/assert/mod.ts';
import { deleteAccount, type DeletionEffects } from '../deletion.ts';

const OWNER = 'owner-1';

function recorder(overrides: Partial<DeletionEffects> = {}) {
  const calls: string[] = [];
  const effects: DeletionEffects = {
    listOwnedFiles: async () => { calls.push('list'); return ['owner-1/s1/v1.mp4']; },
    removeFiles: async () => { calls.push('remove'); },
    deleteOwnedGraphs: async () => { calls.push('graphs'); return 1; },
    deleteAuthUser: async () => { calls.push('auth'); },
    ...overrides,
  };
  return { calls, effects };
}

Deno.test('deletes storage, then the graph, then the identity — in that order', async () => {
  const { calls, effects } = recorder();
  const report = await deleteAccount(OWNER, effects);

  assertEquals(report.deleted, true);
  // Files first because a DB cascade never touches Storage and, once the auth row is gone,
  // there is no owner left to attribute the orphans to.
  assertEquals(calls, ['list', 'remove', 'graphs', 'auth']);
  assertEquals(report.filesRemoved, 1);
  assertEquals(report.graphsRemoved, 1);
});

Deno.test('deletes the graph explicitly, because nothing cascades to it', async () => {
  // `graphs.owner_id` is polymorphic (athlete id OR profile id), so there is no FK to
  // `profiles`. Without this stage the graph, its edges and its private nodes would survive
  // the account, attributed to a uuid that no longer resolves.
  let deletedFor: string | null = null;
  const { effects } = recorder({
    deleteOwnedGraphs: async (ownerId) => { deletedFor = ownerId; return 1; },
  });
  await deleteAccount(OWNER, effects);
  assertEquals(deletedFor, OWNER);
});

Deno.test('skips the storage round trip when the account has no files', async () => {
  const { calls, effects } = recorder({ listOwnedFiles: async () => [] });
  const report = await deleteAccount(OWNER, effects);
  assertEquals(calls.includes('remove'), false);
  assertEquals(report.filesRemoved, 0);
  assertEquals(report.deleted, true);
});

Deno.test('a failure to remove files never reaches the identity', async () => {
  // Deleting the auth user first would strand the videos with no owner to trace them to.
  const { calls, effects } = recorder({
    removeFiles: async () => { throw new Error('storage unavailable'); },
  });
  const report = await deleteAccount(OWNER, effects);

  assertEquals(report.deleted, false);
  assertEquals(report.failedStage, 'storage');
  assertEquals(calls.includes('auth'), false);
  assertEquals(calls.includes('graphs'), false);
});

Deno.test('a failure to delete the graph never reaches the identity', async () => {
  const { calls, effects } = recorder({
    deleteOwnedGraphs: async () => { throw new Error('RLS'); },
  });
  const report = await deleteAccount(OWNER, effects);

  assertEquals(report.deleted, false);
  assertEquals(report.failedStage, 'graphs');
  assertEquals(calls.includes('auth'), false);
});

Deno.test('reports failure, not success, when only the identity survives', async () => {
  // The account is still usable and still signed in, so saying "deleted" would be a lie even
  // though almost everything is gone.
  const { effects } = recorder({
    deleteAuthUser: async () => { throw new Error('service unavailable'); },
  });
  const report = await deleteAccount(OWNER, effects);

  assertEquals(report.deleted, false);
  assertEquals(report.failedStage, 'identity');
  assertEquals(report.error, 'service unavailable');
});

Deno.test('a retry after a partial run completes instead of erroring', async () => {
  // Everything up to the identity already went in the first attempt, so the second finds
  // nothing to remove. That has to read as success, or a transient network failure would leave
  // an account permanently half-deleted with no way to finish.
  let authDeleted = false;
  const { effects } = recorder({
    listOwnedFiles: async () => [],
    deleteOwnedGraphs: async () => 0,
    deleteAuthUser: async () => { authDeleted = true; },
  });
  const report = await deleteAccount(OWNER, effects);

  assertEquals(report.deleted, true);
  assertEquals(report.filesRemoved, 0);
  assertEquals(report.graphsRemoved, 0);
  assertEquals(authDeleted, true);
});
