'use client';
import { useState } from 'react';
import { avatarSrc } from '@/lib/api';

export function initials(name: string): string {
  return name.split(' ').map((p) => p[0]).filter(Boolean).slice(0, 2).join('').toUpperCase();
}

/** A user's profile picture, or an initials circle when none is set.
 * `className` controls the size (e.g. "h-10 w-10 text-sm").
 * A broken image URL falls back to initials instead of a broken icon.
 */
export default function Avatar({ displayName, avatarUrl, className }: {
  displayName: string;
  avatarUrl: string | null | undefined;
  className?: string;
}) {
  const [broken, setBroken] = useState(false);
  const src = avatarSrc(avatarUrl);
  const size = className ?? 'h-10 w-10 text-sm';
  if (src && !broken) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={displayName} onError={() => setBroken(true)}
      className={`${size} shrink-0 rounded-full object-cover`} />;
  }
  return (
    <span className={`${size} flex shrink-0 items-center justify-center rounded-full bg-zinc-700 font-bold`}>
      {initials(displayName)}
    </span>
  );
}
