'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { getToken } from '@/lib/auth';
import {
  adminExportUrl,
  deleteMessage,
  getAdminActivity,
  getAdminConversationMessages,
  getAdminConversations,
  getAdminMedia,
  getAdminStats,
  getAdminUsers,
  me,
  mediaUrlFromPath,
  type AdminActivityEvent,
  type AdminConversation,
  type AdminMediaItem,
  type AdminMediaKind,
  type AdminMessage,
  type AdminStats,
  type AdminUser,
} from '@/lib/api';
import { canDeleteAdminMedia, deletedForEveryoneLabel } from '@/lib/adminDelete';
import { formatBytes, formatMessageTime, formatTime } from '@/lib/format';
import Avatar from '@/components/Avatar';

type Tab = 'dashboard' | 'media';
type Status = 'loading' | 'denied' | 'ready';

const MEDIA_FILTERS: { key: AdminMediaKind; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'photo', label: 'Photos' },
  { key: 'video', label: 'Videos' },
  { key: 'audio', label: 'Audio' },
];

function isForbiddenError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err);
  return /403|forbidden|admin only|not an admin|admin access/i.test(msg);
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function activityIcon(type: AdminActivityEvent['type']): string {
  if (type === 'register') return '👤';
  if (type === 'media') return '📎';
  return '💬';
}

function kindLabel(kind: string | null): string {
  if (kind === 'image') return '📷 Photo';
  if (kind === 'video') return '🎬 Video';
  if (kind === 'audio' || kind === 'voice') return '🔊 Audio';
  return '💬 Text';
}

export default function AdminPage() {
  const router = useRouter();
  const [status, setStatus] = useState<Status>('loading');
  const [error, setError] = useState('');
  const [tab, setTab] = useState<Tab>('dashboard');
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [activity, setActivity] = useState<AdminActivityEvent[]>([]);
  const [conversations, setConversations] = useState<AdminConversation[]>([]);
  const [media, setMedia] = useState<AdminMediaItem[]>([]);
  const [mediaKind, setMediaKind] = useState<AdminMediaKind>('all');
  const [mediaLoading, setMediaLoading] = useState(false);
  const [selectedConv, setSelectedConv] = useState<AdminConversation | null>(null);
  const [convMessages, setConvMessages] = useState<AdminMessage[]>([]);
  const [convLoading, setConvLoading] = useState(false);
  const [convError, setConvError] = useState('');
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [confirmDeleteMediaId, setConfirmDeleteMediaId] = useState<number | null>(null);
  const [deletingMediaId, setDeletingMediaId] = useState<number | null>(null);
  const [mediaDeleteError, setMediaDeleteError] = useState('');
  const [viewer, setViewer] = useState<AdminMediaItem | null>(null);

  const loadDashboard = useCallback(async () => {
    try {
      const [s, u, a, c] = await Promise.all([
        getAdminStats(),
        getAdminUsers(),
        getAdminActivity(100),
        getAdminConversations(),
      ]);
      setStats(s);
      setUsers(u);
      setActivity([...a].sort((x, y) => y.created_at.localeCompare(x.created_at)));
      setConversations(c);
      setError('');
    } catch (err) {
      if (isForbiddenError(err)) setStatus('denied');
      else setError(err instanceof Error ? err.message : 'Could not load the admin dashboard.');
    }
  }, []);

  const loadMedia = useCallback(async (kind: AdminMediaKind) => {
    setMediaLoading(true);
    try {
      const items = await getAdminMedia(kind, 100);
      setMedia(items);
      setError('');
    } catch (err) {
      if (isForbiddenError(err)) setStatus('denied');
      else setError(err instanceof Error ? err.message : 'Could not load media.');
    } finally {
      setMediaLoading(false);
    }
  }, []);

  // Initial auth gate + data load.
  useEffect(() => {
    if (!getToken()) { router.replace('/login'); return; }
    let cancelled = false;
    (async () => {
      try {
        const current = await me();
        if (cancelled) return;
        if (current.role !== 'admin') { setStatus('denied'); return; }
        setStatus('ready');
        await loadDashboard();
        await loadMedia('all');
      } catch (err) {
        if (cancelled) return;
        if (isForbiddenError(err)) setStatus('denied');
        else setError(err instanceof Error ? err.message : 'Could not load the admin dashboard.');
      }
    })();
    return () => { cancelled = true; };
  }, [router, loadDashboard, loadMedia]);

  // Auto-refresh every 30s: dashboard data on the Dashboard tab, media on the Media tab.
  const tabRef = useRef(tab);
  tabRef.current = tab;
  const mediaKindRef = useRef(mediaKind);
  mediaKindRef.current = mediaKind;
  useEffect(() => {
    if (status !== 'ready') return;
    const id = setInterval(() => {
      if (tabRef.current === 'media') void loadMedia(mediaKindRef.current);
      else void loadDashboard();
    }, 30000);
    return () => clearInterval(id);
  }, [status, loadDashboard, loadMedia]);

  const loadConvMessages = useCallback(async (conv: AdminConversation) => {
    setConvLoading(true);
    setConvError('');
    try {
      const msgs = await getAdminConversationMessages(conv.id, 100);
      setConvMessages([...msgs].sort((a, b) => a.created_at.localeCompare(b.created_at)));
    } catch (err) {
      if (isForbiddenError(err)) setStatus('denied');
      else setConvError(err instanceof Error ? err.message : 'Could not load messages.');
    } finally {
      setConvLoading(false);
    }
  }, []);

  async function openConversation(conv: AdminConversation) {
    setSelectedConv(conv);
    setConvMessages([]);
    setConfirmDeleteId(null);
    setDeleteError('');
    await loadConvMessages(conv);
  }

  // Moderation: delete ANY user's message for everyone, then refresh the
  // list so the deleted label (with the deleter's name) shows at once.
  // The message's full content stays visible to the admin.
  async function handleAdminDelete(m: AdminMessage) {
    if (!selectedConv) return;
    setDeletingId(m.id);
    setDeleteError('');
    try {
      await deleteMessage(selectedConv.id, m.id, 'everyone');
      setConfirmDeleteId(null);
      await loadConvMessages(selectedConv);
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : 'Could not delete message');
    } finally {
      setDeletingId(null);
    }
  }

  // Moderation from the Media Library: same DELETE endpoint, using the
  // conversation id the backend now includes on each media item. The item
  // stays visible to the admin with its "deleted" tag and full download —
  // only the participants lose it.
  async function handleMediaDelete(item: AdminMediaItem) {
    if (item.conversation_id == null) return;
    setDeletingMediaId(item.id);
    setMediaDeleteError('');
    try {
      await deleteMessage(item.conversation_id, item.id, 'everyone');
      setConfirmDeleteMediaId(null);
      // The open viewer keeps showing the item, now marked deleted.
      if (viewer?.id === item.id) setViewer({ ...viewer, deleted: true });
      await loadMedia(mediaKind);
    } catch (err) {
      setMediaDeleteError(err instanceof Error ? err.message : 'Could not delete media');
    } finally {
      setDeletingMediaId(null);
    }
  }

  function changeMediaKind(kind: AdminMediaKind) {
    setMediaKind(kind);
    void loadMedia(kind);
  }

  if (status === 'loading') {
    return (
      <main className="flex min-h-screen items-center justify-center bg-zinc-950">
        <p className="text-zinc-400">Loading admin dashboard…</p>
      </main>
    );
  }

  if (status === 'denied') {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center bg-zinc-950 px-4 text-center">
        <p className="text-5xl">🛡</p>
        <h1 className="mt-4 text-2xl font-bold text-zinc-100">Admin only</h1>
        <p className="mt-2 max-w-sm text-zinc-400">This page is only available to Vibe admins. If you think you should have access, ask an admin.</p>
        <Link href="/chat" className="mt-6 rounded-full bg-brand-500 px-5 py-2 text-sm font-semibold text-zinc-950 hover:bg-brand-400">← Back to chat</Link>
      </main>
    );
  }

  const statCards: { label: string; value: number | undefined }[] = [
    { label: 'Total users', value: stats?.total_users },
    { label: 'Messages', value: stats?.total_messages },
    { label: 'Media files', value: stats?.total_media },
    { label: 'Online now', value: stats?.online_now },
    { label: 'Messages today', value: stats?.messages_today },
  ];

  // Resolve "deleted for me" user ids to display names.
  const userNameById = new Map(users.map((u) => [u.id, u.display_name]));

  return (
    <main className="min-h-screen bg-zinc-950 text-zinc-100">
      <header className="sticky top-0 z-10 border-b border-zinc-800 bg-zinc-950/95 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <div className="flex items-center gap-3">
            <Link href="/chat" className="rounded-full px-2 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">← Back to chat</Link>
            <h1 className="text-xl font-bold text-brand-500">🛡 Admin</h1>
          </div>
          <div className="flex gap-1 rounded-full bg-zinc-900 p-1">
            <button onClick={() => setTab('dashboard')}
              className={`rounded-full px-4 py-1.5 text-sm font-medium ${tab === 'dashboard' ? 'bg-brand-500 text-zinc-950' : 'text-zinc-300 hover:bg-zinc-800'}`}>Dashboard</button>
            <button onClick={() => setTab('media')}
              className={`rounded-full px-4 py-1.5 text-sm font-medium ${tab === 'media' ? 'bg-brand-500 text-zinc-950' : 'text-zinc-300 hover:bg-zinc-800'}`}>Media Library</button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-6xl px-4 py-6">
        {error && (
          <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-red-900 bg-red-950/50 px-4 py-3 text-sm text-red-200">
            <span>{error}</span>
            <button
              onClick={() => { if (tab === 'media') void loadMedia(mediaKind); else void loadDashboard(); }}
              className="shrink-0 rounded-full bg-red-900 px-3 py-1 font-medium hover:bg-red-800">Retry</button>
          </div>
        )}

        {tab === 'dashboard' && (
          <div className="space-y-6">
            {/* Stat cards */}
            <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
              {statCards.map((card) => (
                <div key={card.label} className="rounded-xl bg-zinc-900 p-4">
                  <p className="text-xs uppercase tracking-wide text-zinc-500">{card.label}</p>
                  <p className="mt-1 text-3xl font-bold text-zinc-100">{card.value ?? '—'}</p>
                </div>
              ))}
            </section>

            {/* Users table */}
            <section className="rounded-xl bg-zinc-900 p-4">
              <h2 className="mb-3 text-lg font-semibold">Users <span className="text-sm font-normal text-zinc-500">({users.length})</span></h2>
              {users.length === 0 ? (
                <p className="py-4 text-center text-sm text-zinc-500">No users yet.</p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[820px] text-left text-sm">
                    <thead>
                      <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                        <th className="py-2 pr-3 font-medium">Picture</th>
                        <th className="py-2 pr-3 font-medium">Name</th>
                        <th className="py-2 pr-3 font-medium">Username</th>
                        <th className="py-2 pr-3 font-medium">Email</th>
                        <th className="py-2 pr-3 font-medium">Role</th>
                        <th className="py-2 pr-3 font-medium">Status</th>
                        <th className="py-2 pr-3 font-medium">Last seen</th>
                        <th className="py-2 pr-3 font-medium">Messages</th>
                        <th className="py-2 pr-3 font-medium">Media</th>
                        <th className="py-2 font-medium">Joined</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users.map((u) => (
                        <tr key={u.id} className="border-b border-zinc-800/60 last:border-0">
                          <td className="py-2.5 pr-3"><Avatar displayName={u.display_name} avatarUrl={u.avatar_url} className="h-9 w-9 text-xs" /></td>
                          <td className="py-2.5 pr-3 font-medium">{u.display_name}</td>
                          <td className="py-2.5 pr-3 text-zinc-400">{u.username ? `@${u.username}` : '—'}</td>
                          <td className="py-2.5 pr-3 text-zinc-400">{u.email}</td>
                          <td className="py-2.5 pr-3">
                            <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${u.is_admin || u.role === 'admin' ? 'bg-brand-500/15 text-brand-400' : 'bg-zinc-800 text-zinc-300'}`}>
                              {u.is_admin || u.role === 'admin' ? 'Admin' : 'User'}
                            </span>
                          </td>
                          <td className="py-2.5 pr-3">
                            <span className="flex items-center gap-1.5 text-zinc-300">
                              <span className={`h-2.5 w-2.5 rounded-full ${u.online ? 'bg-green-500' : 'bg-zinc-600'}`} title={u.online ? 'Online' : 'Offline'} />
                              {u.online ? 'Online' : 'Offline'}
                            </span>
                          </td>
                          <td className="py-2.5 pr-3 text-zinc-400">{u.last_seen ? formatTime(u.last_seen) : '—'}</td>
                          <td className="py-2.5 pr-3">{u.message_count}</td>
                          <td className="py-2.5 pr-3">{u.media_count}</td>
                          <td className="py-2.5 text-zinc-400">{formatDate(u.created_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <div className="grid gap-6 lg:grid-cols-2">
              {/* Activity feed */}
              <section className="rounded-xl bg-zinc-900 p-4">
                <h2 className="mb-3 text-lg font-semibold">Recent activity</h2>
                {activity.length === 0 ? (
                  <p className="py-4 text-center text-sm text-zinc-500">No activity yet.</p>
                ) : (
                  <ul className="max-h-96 space-y-1 overflow-y-auto">
                    {activity.map((ev, i) => (
                      <li key={`${ev.created_at}-${i}`} className="flex items-start gap-3 rounded-lg px-2 py-2 hover:bg-zinc-800/60">
                        <span className="mt-0.5 shrink-0">{activityIcon(ev.type)}</span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm"><span className="font-medium">{ev.user_name}</span> <span className="text-zinc-400">— {ev.detail}</span></span>
                          <span className="text-xs text-zinc-500">{ev.user_email}</span>
                        </span>
                        <span className="shrink-0 text-xs text-zinc-500">{formatTime(ev.created_at)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* Conversations */}
              <section className="rounded-xl bg-zinc-900 p-4">
                <h2 className="mb-3 text-lg font-semibold">Conversations <span className="text-sm font-normal text-zinc-500">({conversations.length})</span></h2>
                {conversations.length === 0 ? (
                  <p className="py-4 text-center text-sm text-zinc-500">No conversations yet.</p>
                ) : (
                  <ul className="max-h-96 space-y-1 overflow-y-auto">
                    {conversations.map((conv) => (
                      <li key={conv.id}>
                        <button onClick={() => void openConversation(conv)}
                          className={`flex w-full items-start gap-3 rounded-lg px-2 py-2 text-left hover:bg-zinc-800/60 ${selectedConv?.id === conv.id ? 'bg-zinc-800' : ''}`}>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium">
                              {conv.participants.map((p) => p.username ? `${p.display_name} (@${p.username})` : p.display_name).join(' & ') || 'Conversation'}
                            </span>
                            <span className="block truncate text-sm text-zinc-400">
                              {conv.last_message_kind ? `${kindLabel(conv.last_message_kind)}: ` : ''}{conv.last_message_text ?? 'No messages yet'}
                            </span>
                          </span>
                          <span className="shrink-0 text-right">
                            <span className="block text-xs text-zinc-500">{conv.message_count} msgs</span>
                            <span className="block text-xs text-zinc-500">{formatTime(conv.last_message_at)}</span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            </div>

            {/* Read-only conversation view */}
            {selectedConv && (
              <section className="rounded-xl bg-zinc-900 p-4">
                <div className="mb-1 flex items-center justify-between gap-3">
                  <h2 className="text-lg font-semibold">
                    {selectedConv.participants.map((p) => p.username ? `${p.display_name} (@${p.username})` : p.display_name).join(' & ')}
                  </h2>
                  <button onClick={() => { setSelectedConv(null); setConvMessages([]); }}
                    className="rounded-full px-3 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">✕ Close</button>
                </div>
                <p className="mb-4 inline-block rounded-full bg-brand-500/15 px-3 py-1 text-xs font-semibold text-brand-400">
                  Admin view — you can delete any message for everyone
                </p>
                {deleteError && <p className="mb-3 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">{deleteError}</p>}
                {convLoading && <p className="py-4 text-center text-sm text-zinc-500">Loading messages…</p>}
                {convError && <p className="py-4 text-center text-sm text-red-300">{convError}</p>}
                {!convLoading && !convError && convMessages.length === 0 && (
                  <p className="py-4 text-center text-sm text-zinc-500">No messages in this conversation.</p>
                )}
                <ul className="space-y-3">
                  {convMessages.map((m) => {
                    const src = m.media_url ? mediaUrlFromPath(m.media_url, m.id) : '';
                    // Call history lines can't be deleted; already-deleted
                    // messages have nothing left to delete for everyone.
                    const canDelete = m.kind !== 'call' && !m.deleted_for_everyone;
                    return (
                      <li key={m.id} className="rounded-xl bg-zinc-800/70 px-3 py-2">
                        <div className="flex items-start justify-between gap-2">
                          <p className="min-w-0 text-xs text-zinc-400">
                            <span className="font-medium text-zinc-200">{m.sender_name}</span> · {formatMessageTime(m.created_at)}
                            {m.expired && <span className="ml-2 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-400">⏱ disappeared from chat</span>}
                            {m.deleted_for_everyone && <span className="ml-2 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">{deletedForEveryoneLabel(m, users)}</span>}
                            {!m.deleted_for_everyone && (m.deleted_for?.length ?? 0) > 0 && (
                              <span className="ml-2 rounded-full bg-zinc-700 px-2 py-0.5 text-[11px] font-semibold text-zinc-300">
                                Deleted for me by {(m.deleted_for ?? []).map((id) => userNameById.get(id) ?? `User ${id}`).join(', ')}
                              </span>
                            )}
                            {m.deleted && !m.deleted_for_everyone && (m.deleted_for?.length ?? 0) === 0 && (
                              <span className="ml-2 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">🚫 Deleted</span>
                            )}
                          </p>
                          {canDelete && (
                            confirmDeleteId === m.id ? (
                              <span className="flex shrink-0 items-center gap-1">
                                <span className="text-xs text-zinc-300">Delete for everyone?</span>
                                <button
                                  onClick={() => void handleAdminDelete(m)}
                                  disabled={deletingId === m.id}
                                  className="rounded-full bg-red-500 px-2.5 py-0.5 text-xs font-semibold text-white hover:bg-red-400 disabled:opacity-50"
                                >
                                  {deletingId === m.id ? 'Deleting…' : 'Delete'}
                                </button>
                                <button
                                  onClick={() => setConfirmDeleteId(null)}
                                  disabled={deletingId === m.id}
                                  className="rounded-full px-2.5 py-0.5 text-xs text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
                                >
                                  Cancel
                                </button>
                              </span>
                            ) : (
                              <button
                                onClick={() => { setDeleteError(''); setConfirmDeleteId(m.id); }}
                                title="Delete this message for everyone"
                                className="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium text-red-300 hover:bg-red-500/10 hover:text-red-200"
                              >
                                Delete for everyone
                              </button>
                            )
                          )}
                        </div>
                        {m.kind === 'text' && <p className="mt-1 whitespace-pre-wrap break-words text-[15px]">{m.content}</p>}
                        {m.kind === 'image' && src && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={src} alt={m.media_name ?? 'Image'} className="mt-2 max-h-72 max-w-full rounded-lg" />
                        )}
                        {m.kind === 'video' && src && (
                          <video src={src} controls preload="metadata" className="mt-2 max-h-72 max-w-full rounded-lg" />
                        )}
                        {(m.kind === 'audio' || m.kind === 'voice') && src && (
                          <audio src={src} controls preload="metadata" className="mt-2 w-full max-w-sm" />
                        )}
                        {m.kind !== 'text' && m.content && <p className="mt-1 whitespace-pre-wrap break-words text-sm">{m.content}</p>}
                        {m.media_name && <p className="mt-1 truncate text-xs text-zinc-500">{m.media_name}{m.media_size != null ? ` · ${formatBytes(m.media_size)}` : ''}</p>}
                      </li>
                    );
                  })}
                </ul>
              </section>
            )}
          </div>
        )}

        {tab === 'media' && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex gap-1 rounded-full bg-zinc-900 p-1">
                {MEDIA_FILTERS.map((f) => (
                  <button key={f.key} onClick={() => changeMediaKind(f.key)}
                    className={`rounded-full px-4 py-1.5 text-sm font-medium ${mediaKind === f.key ? 'bg-brand-500 text-zinc-950' : 'text-zinc-300 hover:bg-zinc-800'}`}>
                    {f.label}
                  </button>
                ))}
              </div>
              <a href={adminExportUrl(mediaKind)} download
                className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-zinc-950 hover:bg-brand-400">
                ⬇ Download all (ZIP)
              </a>
            </div>

            {mediaLoading && <p className="py-6 text-center text-sm text-zinc-500">Loading media…</p>}
            {mediaDeleteError && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">{mediaDeleteError}</p>}
            {!mediaLoading && media.length === 0 && (
              <p className="rounded-xl bg-zinc-900 py-10 text-center text-sm text-zinc-500">No media found for this filter.</p>
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {media.map((item) => {
                const src = mediaUrlFromPath(item.download_url, item.id);
                return (
                  <div key={item.id} className="overflow-hidden rounded-xl bg-zinc-900">
                    <button onClick={() => setViewer(item)} className="relative block aspect-square w-full overflow-hidden bg-zinc-800 text-left"
                      aria-label={`Open ${item.filename}`}>
                      {item.kind === 'photo' ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={src} alt={item.filename} className="h-full w-full object-cover" />
                      ) : item.kind === 'video' ? (
                        <>
                          <video src={src} preload="metadata" muted className="h-full w-full object-cover" />
                          <span className="absolute inset-0 flex items-center justify-center bg-black/30 text-4xl text-white">▶</span>
                        </>
                      ) : (
                        <span className="flex h-full w-full flex-col items-center justify-center gap-2 text-zinc-300">
                          <span className="text-4xl">🎵</span>
                          <span className="rounded-full bg-brand-500 px-3 py-1 text-xs font-semibold text-zinc-950">▶ Play</span>
                        </span>
                      )}
                    </button>
                    <div className="p-3">
                      <p className="truncate text-sm font-medium" title={item.filename}>{item.filename}</p>
                      <p className="mt-0.5 truncate text-xs text-zinc-400">From {item.sender_name}</p>
                      <p className="truncate text-xs text-zinc-500" title={item.conversation_participants.join(', ')}>
                        {item.conversation_participants.join(' & ') || '—'}
                      </p>
                      <p className="mt-0.5 text-xs text-zinc-500">{formatDate(item.created_at)} · {formatBytes(item.size_bytes)}
                        {item.expired && <span className="ml-1.5 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-400">⏱ disappeared</span>}
                        {item.deleted && <span className="ml-1.5 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">🚫 deleted</span>}
                      </p>
                      <div className="mt-2 flex items-center justify-between gap-2">
                        <a href={src} download={item.filename} onClick={(e) => e.stopPropagation()}
                          className="inline-block rounded-full bg-zinc-800 px-3 py-1 text-xs font-medium text-zinc-200 hover:bg-zinc-700">
                          ⬇ Download
                        </a>
                        {canDeleteAdminMedia(item) && (
                          confirmDeleteMediaId === item.id ? (
                            <span className="flex items-center gap-1">
                              <span className="text-xs text-zinc-300">Delete for everyone?</span>
                              <button
                                onClick={(e) => { e.stopPropagation(); void handleMediaDelete(item); }}
                                disabled={deletingMediaId === item.id}
                                className="rounded-full bg-red-500 px-2.5 py-0.5 text-xs font-semibold text-white hover:bg-red-400 disabled:opacity-50"
                              >
                                {deletingMediaId === item.id ? 'Deleting…' : 'Delete'}
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); setConfirmDeleteMediaId(null); }}
                                disabled={deletingMediaId === item.id}
                                className="rounded-full px-2.5 py-0.5 text-xs text-zinc-300 hover:bg-zinc-700 disabled:opacity-50"
                              >
                                Cancel
                              </button>
                            </span>
                          ) : (
                            <button
                              onClick={(e) => { e.stopPropagation(); setMediaDeleteError(''); setConfirmDeleteMediaId(item.id); }}
                              title="Delete this message for everyone"
                              className="shrink-0 rounded-full px-2 py-0.5 text-xs font-medium text-red-300 hover:bg-red-500/10 hover:text-red-200"
                            >
                              Delete for everyone
                            </button>
                          )
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* Media viewer modal */}
      {viewer && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4" onClick={() => setViewer(null)}>
          <div className="max-h-full w-full max-w-2xl overflow-y-auto rounded-xl bg-zinc-900 p-4" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="truncate font-semibold">{viewer.filename}</h2>
                <p className="text-xs text-zinc-400">
                  From {viewer.sender_name} · {viewer.conversation_participants.join(' & ') || '—'} · {formatDate(viewer.created_at)} · {formatBytes(viewer.size_bytes)}
                  {viewer.expired && <span className="ml-2 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-semibold text-amber-400">⏱ disappeared from chat</span>}
                  {viewer.deleted && <span className="ml-2 rounded-full bg-red-500/15 px-2 py-0.5 text-[11px] font-semibold text-red-400">🚫 deleted</span>}
                </p>
              </div>
              <button onClick={() => setViewer(null)} aria-label="Close viewer"
                className="shrink-0 rounded-full px-3 py-1 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">✕</button>
            </div>
            {viewer.kind === 'photo' && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={mediaUrlFromPath(viewer.download_url, viewer.id)} alt={viewer.filename} className="max-h-[70vh] w-full rounded-lg object-contain" />
            )}
            {viewer.kind === 'video' && (
              <video src={mediaUrlFromPath(viewer.download_url, viewer.id)} controls autoPlay preload="metadata" className="max-h-[70vh] w-full rounded-lg" />
            )}
            {viewer.kind === 'audio' && (
              <audio src={mediaUrlFromPath(viewer.download_url, viewer.id)} controls autoPlay preload="metadata" className="w-full" />
            )}
            <div className="mt-4 flex items-center justify-between gap-3">
              {canDeleteAdminMedia(viewer) ? (
                confirmDeleteMediaId === viewer.id ? (
                  <span className="flex items-center gap-2">
                    <span className="text-sm text-zinc-300">Delete for everyone?</span>
                    <button
                      onClick={() => void handleMediaDelete(viewer)}
                      disabled={deletingMediaId === viewer.id}
                      className="rounded-full bg-red-500 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-400 disabled:opacity-50"
                    >
                      {deletingMediaId === viewer.id ? 'Deleting…' : 'Delete'}
                    </button>
                    <button
                      onClick={() => setConfirmDeleteMediaId(null)}
                      disabled={deletingMediaId === viewer.id}
                      className="rounded-full px-3 py-1.5 text-sm text-zinc-300 hover:bg-zinc-800 disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  </span>
                ) : (
                  <button
                    onClick={() => { setMediaDeleteError(''); setConfirmDeleteMediaId(viewer.id); }}
                    title="Delete this message for everyone"
                    className="rounded-full px-3 py-1.5 text-sm font-medium text-red-300 hover:bg-red-500/10 hover:text-red-200"
                  >
                    Delete for everyone
                  </button>
                )
              ) : (
                <span />
              )}
              <a href={mediaUrlFromPath(viewer.download_url, viewer.id)} download={viewer.filename}
                className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-zinc-950 hover:bg-brand-400">⬇ Download</a>
            </div>
            {mediaDeleteError && confirmDeleteMediaId === viewer.id && (
              <p className="mt-2 text-sm text-red-400">{mediaDeleteError}</p>
            )}
          </div>
        </div>
      )}
    </main>
  );
}
