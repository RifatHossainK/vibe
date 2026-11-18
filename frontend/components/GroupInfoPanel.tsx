'use client';
import { useEffect, useRef, useState } from 'react';
import { searchUsers, updateGroupMembers, type Conversation, type PublicUser } from '@/lib/api';
import Avatar from './Avatar';

const DEBOUNCE_MS = 250;

/** Group info side panel: member list, add members, leave group. */
export default function GroupInfoPanel({ conversation, currentUserId, onClose, onConversationUpdated, onLeave }: {
  conversation: Conversation;
  currentUserId: number;
  onClose: () => void;
  onConversationUpdated: (conv: Conversation) => void;
  onLeave: () => void;
}) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<PublicUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestId = useRef(0);
  const members = conversation.participants ?? [];

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const query = q.trim();
    if (query.length < 1) { setResults([]); setLoading(false); return; }
    setLoading(true);
    timer.current = setTimeout(() => {
      const id = ++requestId.current;
      searchUsers(query)
        .then((users) => {
          if (requestId.current !== id) return;
          // Hide people who are already members.
          setResults(users.filter((u) => !members.some((m) => m.id === u.id)));
        })
        .catch((err: unknown) => {
          if (requestId.current === id) setError(err instanceof Error ? err.message : 'Search failed');
        })
        .finally(() => { if (requestId.current === id) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, conversation.id]);

  async function addMember(user: PublicUser) {
    setBusy(true);
    setError('');
    try {
      const updated = await updateGroupMembers(conversation.id, { add: [user.id] });
      onConversationUpdated(updated);
      setQ('');
      setResults([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not add member');
    } finally {
      setBusy(false);
    }
  }

  async function leave() {
    setBusy(true);
    setError('');
    try {
      await updateGroupMembers(conversation.id, { remove: [currentUserId] });
      onLeave();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not leave group');
    } finally {
      setBusy(false);
    }
  }

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-zinc-800 bg-zinc-900">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <h2 className="truncate font-semibold">👥 {conversation.name ?? 'Group'}</h2>
        <button onClick={onClose} aria-label="Close group info" className="rounded-full px-2 py-1 text-zinc-400 hover:bg-zinc-800">✕</button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        <h3 className="mb-2 text-sm font-medium text-zinc-300">Members ({members.length})</h3>
        <ul className="space-y-1">
          {members.map((m) => (
            <li key={m.id} className="flex items-center gap-3 rounded-lg px-2 py-1.5">
              <Avatar displayName={m.display_name} avatarUrl={m.avatar_url} className="h-9 w-9 text-xs" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {m.display_name}
                  {m.id === currentUserId && <span className="ml-1.5 text-xs font-normal text-zinc-500">(you)</span>}
                </span>
                {m.username && <span className="block truncate text-xs text-zinc-400">@{m.username}</span>}
              </span>
            </li>
          ))}
        </ul>

        <h3 className="mb-2 mt-5 text-sm font-medium text-zinc-300">Add members</h3>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search people…"
          className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm outline-none focus:border-brand-500" />
        {loading && <p className="mt-2 text-xs text-zinc-400">Searching…</p>}
        {error && <p className="mt-2 text-xs text-red-400">{error}</p>}
        <ul className="mt-2 max-h-56 overflow-y-auto">
          {results.map((u) => (
            <li key={u.id}>
              <button onClick={() => addMember(u)} disabled={busy}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left hover:bg-zinc-800 disabled:opacity-50">
                <Avatar displayName={u.display_name} avatarUrl={u.avatar_url} className="h-8 w-8 text-[11px]" />
                <span className="min-w-0 flex-1 truncate text-sm">{u.display_name}</span>
                <span className="text-xs text-brand-400">Add</span>
              </button>
            </li>
          ))}
          {!loading && q.trim() && results.length === 0 && <li className="px-2 py-2 text-xs text-zinc-500">No people found.</li>}
        </ul>

        <div className="mt-6 border-t border-zinc-800 pt-4">
          {confirmLeave ? (
            <div>
              <p className="text-sm text-zinc-300">Leave this group? You will stop receiving its messages.</p>
              <div className="mt-2 flex gap-2">
                <button onClick={leave} disabled={busy} className="flex-1 rounded-full bg-red-500 px-3 py-2 text-sm font-semibold text-white hover:bg-red-400 disabled:opacity-50">
                  {busy ? 'Leaving…' : 'Yes, leave'}
                </button>
                <button onClick={() => setConfirmLeave(false)} className="flex-1 rounded-full bg-zinc-800 px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-700">Cancel</button>
              </div>
            </div>
          ) : (
            <button onClick={() => setConfirmLeave(true)} className="w-full rounded-full bg-red-500/10 px-3 py-2 text-sm font-semibold text-red-400 hover:bg-red-500/20">
              Leave group
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
