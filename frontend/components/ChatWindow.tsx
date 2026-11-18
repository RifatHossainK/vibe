'use client';
import { useEffect, useRef, useState } from 'react';
import type { Conversation, Message } from '@/lib/api';
import { listMessages, sendText, setDisappearing, uploadFile } from '@/lib/api';
import Avatar from './Avatar';
import MessageBubble from './MessageBubble';
import MediaPanel from './MediaPanel';
import GroupInfoPanel from './GroupInfoPanel';
import VoiceRecorder from './VoiceRecorder';
import type { CallType } from '@/lib/calls';

function inferKind(file: File): 'image' | 'video' | 'audio' {
  if (file.type.startsWith('image/')) return 'image';
  if (file.type.startsWith('video/')) return 'video';
  return 'audio';
}

function formatTimer(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

const TIMER_PRESETS = [0, 10, 30, 60, 300];

export default function ChatWindow({ conversation, currentUserId, messages, setMessages, online, typing, onTyping, onSendRead, onBack, onConversationUpdated, onStartCall, onLeaveGroup }: {
  conversation: Conversation;
  currentUserId: number;
  messages: Message[];
  setMessages: (fn: (prev: Message[]) => Message[]) => void;
  online: boolean;
  typing: boolean;
  onTyping: (typing: boolean) => void;
  onSendRead: () => void;
  onBack: () => void;
  onConversationUpdated?: (conv: Conversation) => void;
  onStartCall?: (type: CallType) => void;
  onLeaveGroup?: () => void;
}) {
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(true);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState('');
  const [uploading, setUploading] = useState(false);
  const [showMedia, setShowMedia] = useState(false);
  const [showGroupInfo, setShowGroupInfo] = useState(false);
  const [sending, setSending] = useState(false);
  const [showTimer, setShowTimer] = useState(false);
  const [timerSeconds, setTimerSeconds] = useState(conversation.disappearing_seconds ?? 0);
  const [customSeconds, setCustomSeconds] = useState('');
  const [timerError, setTimerError] = useState('');
  const [timerBusy, setTimerBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const typingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isGroup = !!conversation.is_group;
  const other = conversation.other_user;
  const members = conversation.participants ?? [];
  const headerTitle = isGroup ? (conversation.name ?? 'Group') : (other?.display_name ?? 'Chat');
  const headerSub = isGroup
    ? `${members.length} member${members.length === 1 ? '' : 's'}${typing ? ' · typing…' : ''}`
    : `${other?.username ? `@${other.username} · ` : ''}${typing ? 'typing…' : online ? 'Online' : 'Offline'}`;

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError('');
    setMessages(() => []);
    listMessages(conversation.id, 50)
      .then((data) => { if (!alive) return; setMessages(() => data); setHasMore(data.length >= 50); })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : 'Failed to load messages'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversation.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages.length, typing]);

  // Disappearing messages: every second, expire any message whose time
  // has run out (both users see it vanish live; the content is also
  // dropped from local state, matching what the server returns).
  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      setMessages((prev) => {
        let changed = false;
        const next = prev.map((m) => {
          if (!m.expired && m.expires_at && new Date(m.expires_at).getTime() <= now) {
            changed = true;
            return { ...m, expired: true, content: null, media_url: null, media_name: null, media_size: null };
          }
          return m;
        });
        return changed ? next : prev;
      });
    }, 1000);
    return () => clearInterval(t);
  }, [setMessages]);

  async function applyTimer(seconds: number) {
    if (seconds < 0 || seconds > 86400) { setTimerError('Timer must be 0 (off) or 1–86400 seconds.'); return; }
    setTimerBusy(true);
    setTimerError('');
    try {
      const updated = await setDisappearing(conversation.id, seconds);
      setTimerSeconds(updated.disappearing_seconds);
      onConversationUpdated?.(updated);
      setShowTimer(false);
    } catch (err) {
      setTimerError(err instanceof Error ? err.message : 'Could not set timer');
    } finally {
      setTimerBusy(false);
    }
  }

  async function loadOlder() {
    if (messages.length === 0) return;
    setLoadingOlder(true);
    try {
      const older = await listMessages(conversation.id, 50, messages[0].id);
      setMessages((prev) => {
        const seen = new Set(prev.map((m) => m.id));
        return [...older.filter((m) => !seen.has(m.id)), ...prev];
      });
      setHasMore(older.length >= 50);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load older messages');
    } finally {
      setLoadingOlder(false);
    }
  }

  function handleInputChange(v: string) {
    setInput(v);
    onTyping(true);
    if (typingTimer.current) clearTimeout(typingTimer.current);
    typingTimer.current = setTimeout(() => onTyping(false), 2000);
  }

  async function handleSend(e?: React.FormEvent) {
    e?.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    onTyping(false);
    if (typingTimer.current) clearTimeout(typingTimer.current);
    setSending(true);
    try {
      const msg = await sendText(conversation.id, text);
      // Dedupe by id: WS echo may also deliver this message.
      setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to send');
      setInput(text);
    } finally {
      setSending(false);
    }
  }

  async function handleFile(file: File) {
    const kind = inferKind(file);
    const limit = kind === 'audio' ? 25 * 1024 * 1024 : 50 * 1024 * 1024;
    if (file.size > limit) { setError(`File too large — max ${kind === 'audio' ? '25' : '50'} MB.`); return; }
    setUploading(true);
    setError('');
    try {
      const msg = await uploadFile(conversation.id, file, kind);
      setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }

  const lastOwn = [...messages].reverse().find((m) => m.sender_id === currentUserId);

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col bg-zinc-950">
      {/* Top bar */}
      <header className="flex items-center gap-3 border-b border-zinc-800 bg-zinc-900 px-3 py-2.5">
        <button onClick={onBack} className="rounded-full px-2 py-1 text-zinc-300 hover:bg-zinc-800 md:hidden" aria-label="Back to chats">←</button>
        <span className="relative shrink-0">
          {isGroup ? (
            <span className="flex h-10 w-10 items-center justify-center rounded-full bg-zinc-700 text-lg">👥</span>
          ) : (
            <>
              <Avatar displayName={other?.display_name ?? ''} avatarUrl={other?.avatar_url} className="h-10 w-10 text-sm" />
              {online && <span className="absolute bottom-0 right-0 h-3 w-3 rounded-full border-2 border-zinc-900 bg-green-500" />}
            </>
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{headerTitle}</p>
          <p className="truncate text-xs text-zinc-400">{headerSub}</p>
        </div>
        <button onClick={() => onStartCall?.('audio')} disabled={isGroup} title={isGroup ? 'Calls are 1-to-1 in Phase 2' : 'Audio call'} aria-label="Audio call"
          className="rounded-full p-2 text-lg hover:bg-zinc-800 disabled:opacity-40">📞</button>
        <button onClick={() => onStartCall?.('video')} disabled={isGroup} title={isGroup ? 'Calls are 1-to-1 in Phase 2' : 'Video call'} aria-label="Video call"
          className="rounded-full p-2 text-lg hover:bg-zinc-800 disabled:opacity-40">📹</button>
        {isGroup && (
          <button onClick={() => { setShowGroupInfo((v) => !v); setShowMedia(false); }} title="Group info" aria-label="Group info"
            className={`rounded-full p-2 text-lg hover:bg-zinc-800 ${showGroupInfo ? 'text-brand-400' : ''}`}>ⓘ</button>
        )}
        {/* Disappearing-message timer */}
        <span className="relative">
          <button onClick={() => { setShowTimer((v) => !v); setTimerError(''); }} title="Disappearing messages" aria-label="Disappearing messages"
            className={`rounded-full px-2 py-1 text-sm hover:bg-zinc-800 ${timerSeconds > 0 ? 'text-brand-400' : 'text-zinc-300'}`}>
            ⏱{timerSeconds > 0 ? ` ${formatTimer(timerSeconds)}` : ''}
          </button>
          {showTimer && (
            <span className="absolute right-0 top-full z-50 mt-1 w-56 rounded-xl border border-zinc-700 bg-zinc-900 p-2 shadow-lg">
              <span className="block px-2 pb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Disappearing messages</span>
              {TIMER_PRESETS.map((sec) => (
                <button key={sec} disabled={timerBusy} onClick={() => applyTimer(sec)}
                  className={`block w-full rounded-lg px-2 py-1.5 text-left text-sm hover:bg-zinc-800 disabled:opacity-50 ${timerSeconds === sec ? 'font-semibold text-brand-400' : 'text-zinc-200'}`}>
                  {sec === 0 ? 'Off' : sec < 60 ? `${sec} seconds` : sec === 60 ? '1 minute' : `${sec / 60} minutes`}
                  {timerSeconds === sec ? ' ✓' : ''}
                </button>
              ))}
              <span className="mt-1 flex items-center gap-2 border-t border-zinc-800 px-2 pt-2">
                <input value={customSeconds} onChange={(e) => setCustomSeconds(e.target.value.replace(/[^0-9]/g, ''))}
                  placeholder="Seconds" inputMode="numeric" maxLength={5}
                  className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1 text-sm outline-none focus:border-brand-500" />
                <button disabled={timerBusy || !customSeconds} onClick={() => applyTimer(parseInt(customSeconds, 10))}
                  className="shrink-0 rounded-full bg-brand-500 px-3 py-1 text-sm font-semibold text-zinc-950 hover:bg-brand-400 disabled:opacity-50">Set</button>
              </span>
              {timerError && <span className="block px-2 pt-1 text-xs text-red-400">{timerError}</span>}
              <span className="block px-2 pt-1 text-[11px] text-zinc-500">Messages sent while on disappear after this long, for both of you.</span>
            </span>
          )}
        </span>
        <button onClick={() => setShowMedia((v) => !v)} title="Media" aria-label="Media panel" className="rounded-full p-2 text-lg hover:bg-zinc-800">⋯</button>
        {/* Close button: desktop only — deselects the conversation and
            returns to the empty state. (On mobile the ← button does this.) */}
        <button onClick={onBack} title="Close chat" aria-label="Close chat" className="hidden rounded-full p-2 text-lg text-zinc-300 hover:bg-zinc-800 md:block">✕</button>
      </header>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-6" onClick={onSendRead}>
            {hasMore && (
              <div className="mb-3 text-center">
                <button onClick={loadOlder} disabled={loadingOlder} className="rounded-full bg-zinc-800 px-4 py-1.5 text-sm text-zinc-300 hover:bg-zinc-700 disabled:opacity-50">
                  {loadingOlder ? 'Loading…' : 'Load older messages'}
                </button>
              </div>
            )}
            {loading && <p className="text-center text-sm text-zinc-500">Loading messages…</p>}
            {error && <p className="mb-2 rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">{error}</p>}
            {!loading && messages.length === 0 && <p className="text-center text-sm text-zinc-500">No messages yet — say hello 👋</p>}
            <div className="space-y-2">
              {messages.map((m) => {
                // Group chats: each message carries its real sender's name + avatar.
                const sender = isGroup ? members.find((p) => p.id === m.sender_id) : undefined;
                return (
                  <MessageBubble key={m.id} message={m} isOwn={m.sender_id === currentUserId} isLastOwn={lastOwn?.id === m.id}
                    otherUserId={other?.id ?? -1} otherDisplayName={other?.display_name} otherAvatarUrl={other?.avatar_url}
                    senderDisplayName={sender?.display_name} senderAvatarUrl={sender?.avatar_url} showSenderName={isGroup} />
                );
              })}
            </div>
            <div ref={bottomRef} />
          </div>

          {/* Input row */}
          <form onSubmit={handleSend} className="flex items-center gap-2 border-t border-zinc-800 bg-zinc-900 px-3 py-2.5">
            <input ref={fileRef} type="file" accept="image/*,video/*,audio/*" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); e.target.value = ''; }} />
            <button type="button" onClick={() => fileRef.current?.click()} disabled={uploading} title="Attach photo, video or audio" aria-label="Attach file"
              className="rounded-full p-2 text-lg text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 disabled:opacity-50">📎</button>
            <VoiceRecorder conversationId={conversation.id}
              onUploaded={(msg) => setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]))}
              onError={(msg) => setError(msg)} />
            <input value={input} onChange={(e) => handleInputChange(e.target.value)} placeholder={`Message ${headerTitle}…`}
              className="min-w-0 flex-1 rounded-full border border-zinc-700 bg-zinc-800 px-4 py-2 text-[15px] outline-none focus:border-brand-500" />
            <button type="submit" disabled={!input.trim() || sending} aria-label="Send message"
              className="shrink-0 rounded-full bg-brand-500 px-4 py-2 font-semibold text-zinc-950 hover:bg-brand-400 disabled:opacity-50">
              {uploading ? 'Uploading…' : 'Send'}
            </button>
          </form>
          {uploading && <p className="bg-zinc-900 px-4 pb-2 text-xs text-zinc-400">Uploading…</p>}
        </div>
        {showMedia && !showGroupInfo && <div className="hidden md:block"><MediaPanel conversationId={conversation.id} onClose={() => setShowMedia(false)} /></div>}
        {showGroupInfo && (
          <div className="hidden md:block">
            <GroupInfoPanel conversation={conversation} currentUserId={currentUserId} onClose={() => setShowGroupInfo(false)}
              onConversationUpdated={(conv) => onConversationUpdated?.(conv)} onLeave={() => { setShowGroupInfo(false); onLeaveGroup?.(); }} />
          </div>
        )}
      </div>
      {showMedia && !showGroupInfo && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/50 md:hidden" onClick={() => setShowMedia(false)}>
          <div onClick={(e) => e.stopPropagation()} className="h-full"><MediaPanel conversationId={conversation.id} onClose={() => setShowMedia(false)} /></div>
        </div>
      )}
      {showGroupInfo && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/50 md:hidden" onClick={() => setShowGroupInfo(false)}>
          <div onClick={(e) => e.stopPropagation()} className="h-full">
            <GroupInfoPanel conversation={conversation} currentUserId={currentUserId} onClose={() => setShowGroupInfo(false)}
              onConversationUpdated={(conv) => onConversationUpdated?.(conv)} onLeave={() => { setShowGroupInfo(false); onLeaveGroup?.(); }} />
          </div>
        </div>
      )}
    </div>
  );
}
