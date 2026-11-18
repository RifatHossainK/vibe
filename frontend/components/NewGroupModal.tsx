'use client';
import { useEffect, useRef, useState } from 'react';
import { createGroup, searchUsers, type Conversation, type PublicUser } from '@/lib/api';
import Avatar from './Avatar';

const DEBOUNCE_MS = 250;

export default function NewGroupModal({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (conv: Conversation) => void;
}) {
  const [name, setName] = useState('');
  const [q, setQ] = useState('');
  const [results, setResults] = useState<PublicUser[]>([]);
  const [selected, setSelected] = useState<PublicUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [creating, setCreating] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestId = useRef(0);

  // Debounced search with the same stale-response guard as NewChatModal.
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    const query = q.trim();
    if (query.length < 1) { setResults([]); setLoading(false); return; }
    setLoading(true);
    timer.current = setTimeout(() => {
      const id = ++requestId.current;
      searchUsers(query)
        .then((users) => { if (requestId.current === id) setResults(users); })
        .catch((err: unknown) => {
          if (requestId.current === id) setError(err instanceof Error ? err.message : 'Search failed');
        })
        .finally(() => { if (requestId.current === id) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [q]);

  function toggle(user: PublicUser) {
    setSelected((prev) => (prev.some((u) => u.id === user.id) ? prev.filter((u) => u.id !== user.id) : [...prev, user]));
  }

  async function create() {
    const groupName = name.trim();
    if (groupName.length < 1 || groupName.length > 100) { setError('Group name is required (1–100 characters).'); return; }
    if (selected.length === 0) { setError('Pick at least one member.'); return; }
    setCreating(true);
    setError('');
    try {
      const conv = await createGroup(groupName, selected.map((u) => u.id));
      onCreated(conv);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create group');
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-24" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900 p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">New group</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-full px-2 py-1 text-zinc-400 hover:bg-zinc-800">✕</button>
        </div>
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} maxLength={100}
          placeholder="Group name (required)"
          className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 outline-none focus:border-brand-500" />
        {selected.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {selected.map((u) => (
              <button key={u.id} onClick={() => toggle(u)} title={`Remove ${u.display_name}`}
                className="flex items-center gap-1 rounded-full bg-brand-500/15 px-2 py-1 text-xs text-brand-300 hover:bg-brand-500/25">
                {u.display_name} ✕
              </button>
            ))}
          </div>
        )}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search people to add…"
          className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 outline-none focus:border-brand-500" />
        {loading && <p className="mt-3 text-sm text-zinc-400">Searching…</p>}
        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        <ul className="mt-2 max-h-64 overflow-y-auto">
          {results.map((u) => {
            const picked = selected.some((s) => s.id === u.id);
            return (
              <li key={u.id}>
                <button onClick={() => toggle(u)} className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-zinc-800">
                  <Avatar displayName={u.display_name} avatarUrl={u.avatar_url} className="h-10 w-10 text-sm" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">
                      {u.display_name}
                      {u.username && <span className="ml-1.5 text-xs font-normal text-zinc-500">@{u.username}</span>}
                    </span>
                    <span className="block truncate text-xs text-zinc-400">{u.email}</span>
                  </span>
                  <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full border ${picked ? 'border-brand-500 bg-brand-500 text-xs text-zinc-950' : 'border-zinc-600'}`}>
                    {picked ? '✓' : ''}
                  </span>
                </button>
              </li>
            );
          })}
          {!loading && q.trim() && results.length === 0 && <li className="px-2 py-3 text-sm text-zinc-500">No people found.</li>}
        </ul>
        <button onClick={create} disabled={creating || !name.trim() || selected.length === 0}
          className="mt-3 w-full rounded-full bg-brand-500 py-2 font-semibold text-zinc-950 hover:bg-brand-400 disabled:opacity-50">
          {creating ? 'Creating…' : `Create group (${selected.length} member${selected.length === 1 ? '' : 's'})`}
        </button>
      </div>
    </div>
  );
}
