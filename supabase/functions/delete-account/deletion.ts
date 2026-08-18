/**
 * The order in which an account comes apart, and why it is that order.
 *
 * Deletion is the one operation where a partial success must never be reported as a success:
 * telling someone their data is gone when it is not is worse than telling them the deletion
 * failed. So the sequence is arranged so that the LAST thing to go is the thing whose absence
 * makes everything else unreachable — the auth identity.
 *
 *   1. Storage objects under the owner's prefix. A database CASCADE does not touch Storage, and
 *      once the auth row is gone there is no owner left to attribute the orphans to. Files first.
 *   2. The user's graph. `graphs.owner_id` is polymorphic — an athlete id or a profile id
 *      depending on `owner_kind` — so a column foreign key is impossible and there is no
 *      cascade. It is NOT unhandled, though: alembic 0023 put a `before delete on auth.users`
 *      trigger (`handle_user_delete`) in place that does exactly this delete, and it is live.
 *
 *      This stage stays anyway, for one specific reason: the trigger only fires when the auth
 *      user is deleted, which is stage 3. If stage 3 fails, the trigger never runs, and without
 *      this stage the graph would still be sitting there after a partial deletion the caller was
 *      told had failed. Doing it here makes each stage's outcome independent of the next one's.
 *      It is also idempotent against the trigger — by the time the trigger fires there is
 *      nothing left to delete.
 *   3. The auth user, last. That cascades `profiles`, which cascades the nine owner-scoped
 *      tables (`user_sessions`, `user_projects`, `user_node_names`, `user_sync_meta`, `groups`,
 *      `group_members`, `group_invites`, `class_sessions`, `user_performance_snapshots`).
 *
 * Every stage is idempotent, because the interesting failure is the one that happens halfway:
 * a retry after a network drop must be able to finish the job rather than error on the parts
 * that already succeeded.
 *
 * The effects are injected rather than imported so the ordering and the failure semantics — the
 * part that is actually easy to get wrong — can be tested without a live project.
 */

export interface DeletionEffects {
  /** Object paths under the owner's prefix, or [] when there are none. */
  listOwnedFiles: (ownerId: string) => Promise<string[]>;
  removeFiles: (paths: string[]) => Promise<void>;
  /** Rows the CASCADE from `profiles` does not reach. Returns how many graphs went. */
  deleteOwnedGraphs: (ownerId: string) => Promise<number>;
  /** Last: cascades `profiles` and everything owner-scoped beneath it. */
  deleteAuthUser: (ownerId: string) => Promise<void>;
}

export type DeletionStage = 'storage' | 'graphs' | 'identity';

export interface DeletionReport {
  deleted: boolean;
  filesRemoved: number;
  graphsRemoved: number;
  /** Set only on failure — which stage stopped, so a retry has somewhere to look. */
  failedStage?: DeletionStage;
  error?: string;
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export async function deleteAccount(
  ownerId: string,
  effects: DeletionEffects,
): Promise<DeletionReport> {
  let filesRemoved = 0;
  let graphsRemoved = 0;

  try {
    const files = await effects.listOwnedFiles(ownerId);
    // `remove([])` is a wasted round trip on the common case of an account with no video.
    if (files.length > 0) {
      await effects.removeFiles(files);
      filesRemoved = files.length;
    }
  } catch (error) {
    return { deleted: false, filesRemoved, graphsRemoved, failedStage: 'storage', error: message(error) };
  }

  try {
    graphsRemoved = await effects.deleteOwnedGraphs(ownerId);
  } catch (error) {
    return { deleted: false, filesRemoved, graphsRemoved, failedStage: 'graphs', error: message(error) };
  }

  try {
    await effects.deleteAuthUser(ownerId);
  } catch (error) {
    // The identity survives, so the account is still usable and still signed in. Everything
    // above is already gone, which is exactly why a retry has to be safe: it will find nothing
    // to remove and go straight to this stage again.
    return { deleted: false, filesRemoved, graphsRemoved, failedStage: 'identity', error: message(error) };
  }

  return { deleted: true, filesRemoved, graphsRemoved };
}
