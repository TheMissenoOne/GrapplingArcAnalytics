/**
 * Delete an account, for real.
 *
 * Before this existed there was no deletion mechanism anywhere in the four repos — the public
 * page told people to email a personal address and wait. This is the trusted server side of the
 * in-app flow.
 *
 * **Identity comes from the token, never from the body.** There is no `userId` parameter and no
 * request field is read at all, so there is nothing a caller could send that would make this
 * delete someone else's account. The gateway also verifies the JWT (`verify_jwt = true`), and
 * this verifies it again with a user-scoped client, because "the gateway checked it" is a
 * configuration fact and configuration drifts.
 *
 * The service-role key is used only after that identity is established, and only with the uuid
 * that came out of it.
 *
 * ponytail: no server-side "recent authentication" requirement. Supabase puts an `amr` claim in
 * the token, so requiring the last auth event to be recent is possible — but the claim shape has
 * to be verified against a real token before anything can fail closed on it, and failing OPEN on
 * a missing claim would make the check decorative. The baseline is a verified JWT plus the app's
 * destructive confirmation. Upgrade path: parse `amr`, require the newest timestamp within N
 * minutes, and reject when the claim is absent.
 */

import { createClient, type SupabaseClient } from 'https://esm.sh/@supabase/supabase-js@2';
import { deleteAccount, type DeletionEffects, type OwnedFile } from './deletion.ts';

/**
 * Every bucket a migration creates, not just the one this function was written for — public
 * buckets included. `gym-logos` (alembic 0056) is PUBLIC (read is the point), but a bucket
 * being public says nothing about whether its objects should survive an account deletion: an
 * owner's academy logo is still THEIR write, under THEIR group's prefix, and this sweep is
 * about not leaving files behind, not about who can read them.
 *
 * `user-media` (alembic 0042) exists because `session-videos` carries a professor read policy
 * that must not reach a study attachment. `instructional-media` (alembic 0050) is professor-
 * authored teaching material for a whole group. Four buckets means the sweep below has to visit
 * all four: a bucket the deletion path does not know about is files left on the server under a
 * response that says the account is gone.
 *
 * Add a bucket to the schema, add it here in the same change.
 */
const PRIVATE_BUCKETS = ['session-videos', 'user-media', 'instructional-media', 'gym-logos'];

/**
 * `instructional-media` and `gym-logos` objects both live under `{groupId}/...`, not
 * `{ownerId}/...` — the whole group reads them, so the path can't be keyed by one person. An
 * owner who deletes their account still needs those objects swept, so for these buckets the
 * "owner prefix" to walk is each group THEY OWN, not their own uid.
 */
const GROUP_KEYED_BUCKETS = new Set(['instructional-media', 'gym-logos']);
const PAGE = 100;

const ALLOWED_ORIGINS = [
  'https://themissenoone.github.io',
  'http://localhost:8081',
  'http://localhost:19006',
];

function corsHeaders(origin: string): Record<string, string> {
  return {
    'Access-Control-Allow-Origin': ALLOWED_ORIGINS.includes(origin) ? origin : 'null',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Content-Type': 'application/json',
  };
}

/**
 * Every object under `{ownerId}/` in one bucket, walking the per-record folders.
 *
 * Storage `list` is not recursive and is paged, so a user with many sessions needs both loops.
 * Missing the second one would leave files behind while reporting success, which is the failure
 * mode this whole function exists to avoid.
 */
async function listOwnedInBucket(
  client: SupabaseClient,
  bucket: string,
  ownerId: string,
): Promise<OwnedFile[]> {
  const storage = client.storage.from(bucket);
  const paths: OwnedFile[] = [];

  const page = async (prefix: string): Promise<Array<{ name: string; id: string | null }>> => {
    const found: Array<{ name: string; id: string | null }> = [];
    for (let offset = 0; ; offset += PAGE) {
      const { data, error } = await storage.list(prefix, { limit: PAGE, offset });
      if (error) throw new Error(`storage list failed for ${prefix}: ${error.message}`);
      const batch = data ?? [];
      found.push(...batch);
      if (batch.length < PAGE) return found;
    }
  };

  for (const entry of await page(ownerId)) {
    // Storage reports a folder as an entry with a null id; a file has one.
    if (entry.id === null) {
      for (const file of await page(`${ownerId}/${entry.name}`)) {
        if (file.id !== null) paths.push({ bucket, path: `${ownerId}/${entry.name}/${file.name}` });
      }
    } else {
      paths.push({ bucket, path: `${ownerId}/${entry.name}` });
    }
  }

  return paths;
}

/** The same sweep across every private bucket, group-keyed buckets walked one group at a time. */
async function listOwnedFiles(client: SupabaseClient, ownerId: string): Promise<OwnedFile[]> {
  const found: OwnedFile[] = [];
  for (const bucket of PRIVATE_BUCKETS) {
    if (GROUP_KEYED_BUCKETS.has(bucket)) {
      const { data: groups, error } = await client.from('groups').select('id').eq('owner_id', ownerId);
      if (error) throw new Error(`group lookup failed for ${bucket}: ${error.message}`);
      for (const group of groups ?? []) {
        found.push(...(await listOwnedInBucket(client, bucket, group.id)));
      }
      continue;
    }
    found.push(...(await listOwnedInBucket(client, bucket, ownerId)));
  }
  return found;
}

function effectsFor(admin: SupabaseClient): DeletionEffects {
  return {
    listOwnedFiles: (ownerId) => listOwnedFiles(admin, ownerId),
    removeFiles: async (files) => {
      // One `remove` per bucket rather than per file — the API takes a list, and a per-file
      // round trip would turn a heavy account into a timeout.
      for (const bucket of PRIVATE_BUCKETS) {
        const paths = files.filter((f) => f.bucket === bucket).map((f) => f.path);
        if (paths.length === 0) continue;
        const { error } = await admin.storage.from(bucket).remove(paths);
        if (error) throw new Error(`storage remove failed for ${bucket}: ${error.message}`);
      }
    },
    deleteOwnedGraphs: async (ownerId) => {
      // `graphs.owner_id` is polymorphic (athlete id OR profile id), so it has no FK to
      // `profiles` and nothing cascades here. `owner_kind` is pinned as well as the id: an
      // athlete graph must never be reachable from a user deletion even if the two uuids
      // somehow collided.
      const { data, error } = await admin
        .from('graphs')
        .delete()
        .eq('owner_kind', 'user')
        .eq('owner_id', ownerId)
        .select('id');
      if (error) throw new Error(`graph delete failed: ${error.message}`);
      return (data ?? []).length;
    },
    deleteAuthUser: async (ownerId) => {
      const { error } = await admin.auth.admin.deleteUser(ownerId);
      // A retry after a partial run finds no user, and that is success, not failure.
      if (error && !/not found/i.test(error.message)) {
        throw new Error(`auth delete failed: ${error.message}`);
      }
    },
  };
}

Deno.serve(async (req: Request) => {
  const headers = corsHeaders(req.headers.get('origin') || '');

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers });
  if (req.method !== 'POST') {
    return new Response(JSON.stringify({ error: 'Method not allowed' }), { status: 405, headers });
  }

  const url = Deno.env.get('SUPABASE_URL');
  const anonKey = Deno.env.get('SUPABASE_ANON_KEY');
  const serviceKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
  if (!url || !anonKey || !serviceKey) {
    return new Response(JSON.stringify({ error: 'Function is not configured' }), { status: 500, headers });
  }

  const authorization = req.headers.get('Authorization') ?? '';
  if (!authorization.startsWith('Bearer ')) {
    return new Response(JSON.stringify({ error: 'Missing bearer token' }), { status: 401, headers });
  }

  // The whole authority model, in one call: whoever this token belongs to is the only account
  // this request can delete.
  const caller = createClient(url, anonKey, {
    global: { headers: { Authorization: authorization } },
    auth: { persistSession: false },
  });
  const { data: userData, error: userError } = await caller.auth.getUser();
  const ownerId = userData?.user?.id;
  if (userError || !ownerId) {
    return new Response(JSON.stringify({ error: 'Invalid or expired session' }), { status: 401, headers });
  }

  const admin = createClient(url, serviceKey, { auth: { persistSession: false } });
  const report = await deleteAccount(ownerId, effectsFor(admin));

  // A partial deletion is reported as a failure, so the app never tells someone their data is
  // gone when some of it is not.
  return new Response(JSON.stringify(report), { status: report.deleted ? 200 : 500, headers });
});
