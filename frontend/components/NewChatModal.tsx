'use client';
import { useEffect, useRef, useState } from 'react';
import { createConversation, searchUsers, type Conversation, type PublicUser } from '@/lib/api';
import Avatar from './Avatar';

const DEBOUNCE_MS = 250;

export default function NewChatModal({ onClose, onCreated }: {
  onClose: () => void;
  onCreated: (conv: Conversation) => void;
}) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<PublicUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestId = useRef(0);

  // Debounced search: typing pauses ~250ms before a request goes out, and
  // a slow older response never overwrites newer results (request id guard).
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

  async function startChat(user: PublicUser) {
    try {
      const conv = await createConversation(user.id);
      onCreated(conv);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start chat');
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-24" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900 p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">New chat</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-full px-2 py-1 text-zinc-400 hover:bg-zinc-800">✕</button>
        </div>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search people by name, username or email…"
          className="mt-3 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 outline-none focus:border-brand-500" />
        {loading && <p className="mt-3 text-sm text-zinc-400">Searching…</p>}
        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        <ul className="mt-2 max-h-72 overflow-y-auto">
          {results.map((u) => (
            <li key={u.id}>
              <button onClick={() => startChat(u)} className="flex w-full items-center gap-3 rounded-lg px-2 py-2 text-left hover:bg-zinc-800">
                <Avatar displayName={u.display_name} avatarUrl={u.avatar_url} className="h-10 w-10 text-sm" />
                <span className="min-w-0">
                  <span className="block truncate font-medium">
                    {u.display_name}
                    {u.username && <span className="ml-1.5 text-xs font-normal text-zinc-500">@{u.username}</span>}
                  </span>
                  <span className="block truncate text-xs text-zinc-400">{u.email}</span>
                </span>
              </button>
            </li>
          ))}
          {!loading && q.trim() && results.length === 0 && <li className="px-2 py-3 text-sm text-zinc-500">No people found.</li>}
        </ul>
      </div>
    </div>
  );
}
