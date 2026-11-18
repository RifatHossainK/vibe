'use client';
import { useEffect, useState } from 'react';
import { listMedia, mediaUrlFromPath, type MediaItem } from '@/lib/api';
import { formatBytes, formatTime } from '@/lib/format';

export default function MediaPanel({ conversationId, onClose }: { conversationId: number; onClose: () => void }) {
  const [items, setItems] = useState<MediaItem[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    listMedia(conversationId)
      .then((data) => { if (alive) setItems(data); })
      .catch((err: unknown) => { if (alive) setError(err instanceof Error ? err.message : 'Failed to load media'); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [conversationId]);

  const photos = items.filter((i) => i.kind === 'image' || i.kind === 'video');
  const audios = items.filter((i) => i.kind === 'audio' || i.kind === 'voice');

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-zinc-800 bg-zinc-900">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
        <h2 className="font-semibold">Media</h2>
        <button onClick={onClose} aria-label="Close media panel" className="rounded-full px-2 py-1 text-zinc-400 hover:bg-zinc-800">✕</button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {loading && <p className="text-sm text-zinc-400">Loading media…</p>}
        {error && <p className="text-sm text-red-400">{error}</p>}
        {!loading && !error && items.length === 0 && <p className="text-sm text-zinc-500">No media shared yet.</p>}
        {photos.length > 0 && (
          <>
            <h3 className="mb-2 text-sm font-medium text-zinc-300">Photos &amp; Videos</h3>
            <div className="mb-4 grid grid-cols-3 gap-2">
              {photos.map((item) => {
                const src = mediaUrlFromPath(item.media_url, item.id);
                return item.kind === 'image' ? (
                  <a key={item.id} href={src} target="_blank" rel="noopener noreferrer" title={item.media_name ?? 'Photo'}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={src} alt={item.media_name ?? 'Photo'} className="h-20 w-full rounded-lg object-cover" />
                  </a>
                ) : (
                  <a key={item.id} href={src} target="_blank" rel="noopener noreferrer" title={item.media_name ?? 'Video'}>
                    <video src={src} preload="metadata" className="h-20 w-full rounded-lg object-cover" />
                  </a>
                );
              })}
            </div>
          </>
        )}
        {audios.length > 0 && (
          <>
            <h3 className="mb-2 text-sm font-medium text-zinc-300">Audio</h3>
            <ul className="space-y-3">
              {audios.map((item) => (
                <li key={item.id} className="rounded-lg bg-zinc-800 p-2">
                  <p className="truncate text-xs text-zinc-300">{item.media_name ?? (item.kind === 'voice' ? 'Voice message' : 'Audio')}</p>
                  <p className="text-[11px] text-zinc-500">{formatBytes(item.media_size)} · {formatTime(item.created_at)}</p>
                  <audio src={mediaUrlFromPath(item.media_url, item.id)} controls preload="none" className="mt-1 w-full" />
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </aside>
  );
}
