'use client';
import Avatar from './Avatar';
import type { CallInvite } from '@/lib/calls';

export default function IncomingCallModal({ invite, onAccept, onReject }: {
  invite: CallInvite;
  onAccept: () => void;
  onReject: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/70 p-4">
      <div className="w-full max-w-xs rounded-3xl border border-zinc-800 bg-zinc-900 p-6 text-center shadow-2xl">
        {/* Visual ringing pulse — no audio ringtone file. */}
        <span className="relative mx-auto block h-24 w-24">
          <span className="absolute inset-0 animate-ping rounded-full bg-brand-500/30" />
          <span className="absolute inset-2 animate-pulse rounded-full bg-brand-500/20" />
          <span className="relative block h-full w-full">
            <Avatar displayName={invite.from_name} avatarUrl={invite.from_avatar_url} className="h-24 w-24 text-2xl" />
          </span>
        </span>
        <p className="mt-4 truncate text-lg font-semibold">{invite.from_name}</p>
        <p className="mt-1 text-sm text-zinc-400">
          {invite.call_type === 'video' ? '📹 Video call' : '📞 Audio call'}…
        </p>
        <div className="mt-6 flex items-center justify-center gap-6">
          <button onClick={onReject} aria-label="Reject call"
            className="flex h-14 w-14 items-center justify-center rounded-full bg-red-500 text-xl text-white hover:bg-red-400">
            📞
          </button>
          <button onClick={onAccept} aria-label="Accept call"
            className="flex h-14 w-14 items-center justify-center rounded-full bg-green-500 text-xl text-white hover:bg-green-400">
            📞
          </button>
        </div>
        <p className="mt-4 text-xs text-zinc-500">Accept or Reject</p>
      </div>
    </div>
  );
}
