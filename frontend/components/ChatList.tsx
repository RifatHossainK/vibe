'use client';
import { useState } from 'react';
import Link from 'next/link';
import type { Conversation, User } from '@/lib/api';
import { formatTime } from '@/lib/format';
import Avatar from './Avatar';
import NewChatModal from './NewChatModal';
import NewGroupModal from './NewGroupModal';
import ProfileModal from './ProfileModal';

export default function ChatList({ conversations, selectedId, onlineMap, currentUser, onSelect, onConversationCreated, onLogout, onUserUpdated, isAdmin }: {
  conversations: Conversation[];
  selectedId: number | null;
  onlineMap: Record<number, boolean>;
  currentUser: User;
  onSelect: (conv: Conversation) => void;
  onConversationCreated: (conv: Conversation) => void;
  onLogout: () => void;
  onUserUpdated: (user: User) => void;
  isAdmin?: boolean;
}) {
  const [showNew, setShowNew] = useState(false);
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [showNewMenu, setShowNewMenu] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  const [filter, setFilter] = useState('');
  const filtered = conversations.filter((c) => {
    const q = filter.toLowerCase();
    if (c.is_group) return (c.name ?? 'Group').toLowerCase().includes(q);
    return c.other_user!.display_name.toLowerCase().includes(q) ||
      (c.other_user!.username ?? '').toLowerCase().includes(q) ||
      c.other_user!.email.toLowerCase().includes(q);
  });

  return (
    <div className="flex h-full w-full flex-col bg-zinc-900 md:w-80 md:shrink-0 md:border-r md:border-zinc-800">
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        {/* Own profile: click to open the Profile section (picture + username). */}
        <button onClick={() => setShowProfile(true)} title="My profile" aria-label="My profile" className="flex min-w-0 items-center gap-2 rounded-lg text-left hover:opacity-90">
          <Avatar displayName={currentUser.display_name} avatarUrl={currentUser.avatar_url} className="h-9 w-9 text-sm" />
          <span className="min-w-0">
            <span className="block text-xl font-bold leading-tight text-brand-500">Vibe</span>
            <span className="block max-w-36 truncate text-xs text-zinc-400">
              {currentUser.display_name}{currentUser.username ? ` · @${currentUser.username}` : ''}
            </span>
          </span>
        </button>
        <div className="flex items-center gap-1">
          {isAdmin && (
            <Link href="/admin" title="Admin dashboard" aria-label="Admin dashboard"
              className="rounded-full bg-brand-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-brand-400">🛡 Admin</Link>
          )}
          <span className="relative">
            <button onClick={() => setShowNewMenu((v) => !v)} title="New chat or group" aria-label="New chat or group"
              className="rounded-full bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-700">＋ New</button>
            {showNewMenu && (
              <span className="absolute right-0 top-full z-50 mt-1 w-40 overflow-hidden rounded-xl border border-zinc-700 bg-zinc-900 shadow-lg">
                <button onClick={() => { setShowNewMenu(false); setShowNew(true); }}
                  className="block w-full px-3 py-2 text-left text-sm text-zinc-200 hover:bg-zinc-800">💬 New chat</button>
                <button onClick={() => { setShowNewMenu(false); setShowNewGroup(true); }}
                  className="block w-full px-3 py-2 text-left text-sm text-zinc-200 hover:bg-zinc-800">👥 New group</button>
              </span>
            )}
          </span>
          <button onClick={onLogout} title="Log out" aria-label="Log out"
            className="rounded-full px-2 py-1.5 text-sm text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100">Log out</button>
        </div>
      </div>
      <div className="px-4 pb-2">
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Search chats…"
          className="w-full rounded-full border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm outline-none focus:border-brand-500" />
      </div>
      <ul className="flex-1 overflow-y-auto px-2 pb-2">
        {filtered.length === 0 && <li className="px-2 py-6 text-center text-sm text-zinc-500">No chats yet — tap “＋ New” to start one.</li>}
        {filtered.map((c) => {
          const isGroup = !!c.is_group;
          const online = !isGroup && (onlineMap[c.other_user!.id] ?? false);
          const last = c.last_message;
          let snippet = !last ? 'No messages yet'
            : last.deleted ? '🚫 This message was deleted'
            : last.expired ? '⏱ This message disappeared'
            : last.kind === 'text' ? (last.content ?? '')
            : last.kind === 'image' ? '📷 Photo' : last.kind === 'video' ? '🎬 Video' : last.kind === 'voice' ? '🎙️ Voice message' : last.kind === 'call' ? (last.content ?? '📞 Call') : '🔊 Audio';
          // Group preview: prefix the sender's display name.
          if (isGroup && last && !last.expired && !last.deleted) {
            const sender = (c.participants ?? []).find((p) => p.id === last.sender_id);
            if (sender) snippet = `${sender.display_name}: ${snippet}`;
          }
          const title = isGroup ? (c.name ?? 'Group') : c.other_user!.display_name;
          return (
            <li key={c.id}>
              <button onClick={() => onSelect(c)}
                className={`flex w-full items-center gap-3 rounded-xl px-2 py-2.5 text-left ${selectedId === c.id ? 'bg-zinc-800' : 'hover:bg-zinc-800/60'}`}>
                <span className="relative shrink-0">
                  {isGroup ? (
                    <span className="flex h-11 w-11 items-center justify-center rounded-full bg-zinc-700 text-lg">👥</span>
                  ) : (
                    <Avatar displayName={c.other_user!.display_name} avatarUrl={c.other_user!.avatar_url} className="h-11 w-11 text-sm" />
                  )}
                  {online && <span className="absolute bottom-0 right-0 h-3 w-3 rounded-full border-2 border-zinc-900 bg-green-500" title="Online" />}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline justify-between gap-2">
                    <span className="truncate font-medium">
                      {title}
                      {isGroup ? (
                        <span className="ml-1.5 text-xs font-normal text-zinc-500">{(c.participants ?? []).length} members</span>
                      ) : c.other_user!.username ? (
                        <span className="ml-1.5 text-xs font-normal text-zinc-500">@{c.other_user!.username}</span>
                      ) : null}
                    </span>
                    <span className="shrink-0 text-[11px] text-zinc-500">{formatTime(c.last_message_at)}</span>
                  </span>
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm text-zinc-400">{snippet}</span>
                    {c.unread_count > 0 && (
                      <span className="flex h-5 min-w-5 shrink-0 items-center justify-center rounded-full bg-brand-500 px-1.5 text-[11px] font-bold text-zinc-950">{c.unread_count}</span>
                    )}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {showNew && <NewChatModal onClose={() => setShowNew(false)} onCreated={onConversationCreated} />}
      {showNewGroup && <NewGroupModal onClose={() => setShowNewGroup(false)} onCreated={onConversationCreated} />}
      {showProfile && <ProfileModal user={currentUser} onClose={() => setShowProfile(false)} onUpdated={(u) => { onUserUpdated(u); setShowProfile(false); }} />}
    </div>
  );
}
