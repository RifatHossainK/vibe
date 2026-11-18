'use client';
import type { Message } from '@/lib/api';
import { mediaUrlFromPath } from '@/lib/api';
import { formatMessageTime } from '@/lib/format';
import Avatar, { initials } from './Avatar';

export { initials };

export default function MessageBubble({ message, isOwn, isLastOwn, otherUserId, otherDisplayName, otherAvatarUrl, senderDisplayName, senderAvatarUrl, showSenderName }: {
  message: Message;
  isOwn: boolean;
  isLastOwn: boolean;
  otherUserId: number;
  otherDisplayName?: string;
  otherAvatarUrl?: string | null;
  /** Group chats: the actual sender's name/avatar (falls back to the 1:1 other user). */
  senderDisplayName?: string;
  senderAvatarUrl?: string | null;
  showSenderName?: boolean;
}) {
  const seen = isOwn && message.seen_by.includes(otherUserId);
  const src = message.media_url ? mediaUrlFromPath(message.media_url, message.id) : '';
  const avatarName = senderDisplayName ?? otherDisplayName ?? '';
  const avatarUrl = senderAvatarUrl !== undefined ? senderAvatarUrl : otherAvatarUrl;
  // A disappearing message whose time ran out: the content is gone for
  // both users — show a small placeholder line instead of the bubble.
  if (message.expired) {
    return (
      <div className={`flex ${isOwn ? 'justify-end' : 'justify-start'}`}>
        <p className="rounded-full bg-zinc-800/60 px-3 py-1 text-xs italic text-zinc-500">⏱ This message disappeared</p>
      </div>
    );
  }
  // Call history is a system line, not a bubble — no avatar, no reply box.
  if (message.kind === 'call') {
    return (
      <div className="flex justify-center">
        <p className="rounded-full bg-zinc-800/60 px-3 py-1 text-xs text-zinc-400">{message.content ?? '📞 Call'}</p>
      </div>
    );
  }
  return (
    <div className={`flex ${isOwn ? 'justify-end' : 'justify-start'}`}>
      {!isOwn && (
        <span className="mr-2 mt-0.5 shrink-0">
          <Avatar displayName={avatarName} avatarUrl={avatarUrl} className="h-7 w-7 text-[11px]" />
        </span>
      )}
      <div className={`flex min-w-0 max-w-[75%] flex-col ${isOwn ? 'items-end' : 'items-start'}`}>
      {!isOwn && showSenderName && avatarName && (
        <span className="mb-0.5 pl-1 text-xs font-semibold text-brand-400">{avatarName}</span>
      )}
      <div className={`max-w-full rounded-2xl px-3 py-2 ${isOwn ? 'rounded-br-sm bg-brand-500 text-zinc-950' : 'rounded-bl-sm bg-zinc-800 text-zinc-100'}`}>
        {message.kind === 'text' && <p className="whitespace-pre-wrap break-words text-[15px]">{message.content}</p>}
        {message.kind === 'image' && src && (
          <a href={src} target="_blank" rel="noopener noreferrer" title="Open full size">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={message.media_name ?? 'Image'} className="max-h-72 max-w-full rounded-lg" />
          </a>
        )}
        {message.kind === 'video' && src && (
          <video src={src} controls preload="metadata" className="max-h-72 max-w-full rounded-lg" />
        )}
        {(message.kind === 'audio' || message.kind === 'voice') && src && (
          <div className="w-64 max-w-full">
            <audio src={src} controls preload="metadata" className="w-full" />
            {message.media_name && <p className="mt-1 truncate text-xs opacity-80">{message.media_name}</p>}
          </div>
        )}
        {message.kind !== 'text' && message.content && <p className="mt-1 whitespace-pre-wrap break-words text-sm">{message.content}</p>}
        <div className={`mt-1 flex items-center justify-end gap-1 text-[11px] ${isOwn ? 'text-zinc-800' : 'text-zinc-400'}`}>
          <span>{formatMessageTime(message.created_at)}</span>
          {isOwn && <span title={seen ? 'Seen' : 'Sent'} className={seen ? 'text-sky-600' : ''}>{seen ? '✓✓' : '✓'}</span>}
        </div>
      </div>
      {isOwn && isLastOwn && seen && <span className="mt-0.5 pr-1 text-[11px] text-zinc-500">Seen</span>}
      </div>
    </div>
  );
}
