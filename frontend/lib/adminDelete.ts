/**
 * Pure helpers for admin message moderation (delete for everyone).
 * Kept in a tiny module with no React/DOM imports so the rules can be
 * unit-tested in plain node.
 */

export type DeleteMode = 'me' | 'everyone';

/**
 * Which delete options the ⋯ menu offers on a message.
 * - Own messages: "Delete for me" + "Delete for everyone" (everyone).
 * - Other users' messages: "Delete for me" only — UNLESS the viewer is an
 *   admin, who may also delete any message for everyone (moderation).
 */
export function menuDeleteOptions(isOwn: boolean, isAdmin: boolean): DeleteMode[] {
  if (isOwn || isAdmin) return ['me', 'everyone'];
  return ['me'];
}

/**
 * Whether the admin Media Library may offer "Delete for everyone" on an
 * item: only when the backend supplied the conversation id the DELETE
 * endpoint needs, and the underlying message is not already deleted.
 * Without a conversation id the action is hidden — never called with a
 * guessed id.
 */
export function canDeleteAdminMedia(item: { conversation_id?: number | null; deleted?: boolean }): boolean {
  return item.conversation_id != null && !item.deleted;
}

/** Minimal shape of a user needed to resolve who deleted a message. */
export interface DeleterUser {
  id: number;
  display_name: string;
  role: string;
  is_admin: boolean;
}

/** Minimal shape of an admin-view message needed for the deleted label. */
export interface DeletedMessageInfo {
  deleted_by?: number | null;
  deleted_by_name?: string | null;
}

/**
 * Label shown in the admin conversation view for a message deleted for
 * everyone. Admins are labelled generically ("by admin"); other deleters
 * are named when the server supplied a name.
 */
export function deletedForEveryoneLabel(msg: DeletedMessageInfo, users: DeleterUser[]): string {
  const deleter = msg.deleted_by != null ? users.find((u) => u.id === msg.deleted_by) : undefined;
  if (deleter && (deleter.is_admin || deleter.role === 'admin')) return '🚫 Deleted for everyone by admin';
  if (msg.deleted_by_name) return `🚫 Deleted for everyone by ${msg.deleted_by_name}`;
  if (deleter) return `🚫 Deleted for everyone by ${deleter.display_name}`;
  return '🚫 Deleted for everyone';
}
