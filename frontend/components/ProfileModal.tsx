'use client';
import { useRef, useState } from 'react';
import type { User } from '@/lib/api';
import { removeAvatar, updateProfile, uploadAvatar } from '@/lib/api';
import Avatar from './Avatar';

/** My profile: picture (change / remove) and username / display name.
 * A picked picture is previewed first and only uploaded on Save.
 */
export default function ProfileModal({ user, onClose, onUpdated }: {
  user: User;
  onClose: () => void;
  onUpdated: (user: User) => void;
}) {
  const [displayName, setDisplayName] = useState(user.display_name);
  const [username, setUsername] = useState(user.username ?? '');
  const [picked, setPicked] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);

  function pickFile(f: File | undefined) {
    if (!f) return;
    if (!f.type.startsWith('image/')) { setError('Please pick an image file.'); return; }
    if (f.size > 5 * 1024 * 1024) { setError('Picture too large — max 5 MB.'); return; }
    setError('');
    setPicked(f);
    setPreview(URL.createObjectURL(f));
  }

  async function savePicture() {
    if (!picked) return;
    setBusy(true); setError(''); setSaved('');
    try {
      const updated = await uploadAvatar(picked);
      setPicked(null);
      if (preview) URL.revokeObjectURL(preview);
      setPreview(null);
      setSaved('Profile picture updated.');
      onUpdated(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally { setBusy(false); }
  }

  async function removePicture() {
    setBusy(true); setError(''); setSaved('');
    try {
      const updated = await removeAvatar();
      setSaved('Profile picture removed.');
      onUpdated(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not remove picture');
    } finally { setBusy(false); }
  }

  async function saveDetails() {
    const name = displayName.trim();
    const name2 = username.trim().toLowerCase();
    if (!name) { setError('Display name must not be empty.'); return; }
    if (name2 && !/^[a-z0-9_]{3,20}$/.test(name2)) {
      setError('Username must be 3-20 characters, letters, numbers and underscore only.');
      return;
    }
    setBusy(true); setError(''); setSaved('');
    try {
      const updated = await updateProfile({
        display_name: name,
        username: name2 || undefined,
      });
      setSaved('Profile updated.');
      onUpdated(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save profile');
    } finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-24" onClick={onClose}>
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900 p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">My profile</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-full px-2 py-1 text-zinc-400 hover:bg-zinc-800">✕</button>
        </div>

        <div className="mt-4 flex items-center gap-4">
          {preview ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt="New profile picture preview" className="h-20 w-20 shrink-0 rounded-full object-cover" />
          ) : (
            <Avatar displayName={user.display_name} avatarUrl={user.avatar_url} className="h-20 w-20 text-2xl" />
          )}
          <div className="min-w-0">
            <p className="truncate font-semibold">{user.display_name}</p>
            <p className="truncate text-sm text-zinc-400">{user.username ? `@${user.username}` : 'No username yet'}</p>
            <p className="truncate text-sm text-zinc-400">{user.email}</p>
          </div>
        </div>

        <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/gif,image/webp" className="hidden"
          onChange={(e) => { pickFile(e.target.files?.[0]); e.target.value = ''; }} />
        <div className="mt-4 flex flex-wrap gap-2">
          <button onClick={() => fileRef.current?.click()} disabled={busy}
            className="rounded-full bg-zinc-800 px-3 py-1.5 text-sm text-zinc-200 hover:bg-zinc-700 disabled:opacity-50">
            Change profile picture
          </button>
          {picked && (
            <>
              <button onClick={savePicture} disabled={busy}
                className="rounded-full bg-brand-500 px-3 py-1.5 text-sm font-semibold text-zinc-950 hover:bg-brand-400 disabled:opacity-50">
                {busy ? 'Uploading…' : 'Save picture'}
              </button>
              <button onClick={() => { if (preview) URL.revokeObjectURL(preview); setPicked(null); setPreview(null); }} disabled={busy}
                className="rounded-full px-3 py-1.5 text-sm text-zinc-400 hover:bg-zinc-800 disabled:opacity-50">Cancel</button>
            </>
          )}
          {user.avatar_url && !picked && (
            <button onClick={removePicture} disabled={busy}
              className="rounded-full px-3 py-1.5 text-sm text-red-400 hover:bg-zinc-800 disabled:opacity-50">Remove picture</button>
          )}
        </div>
        {picked && <p className="mt-2 text-xs text-zinc-400">Preview: {picked.name} — press “Save picture” to upload.</p>}

        <div className="mt-5 space-y-3 border-t border-zinc-800 pt-4">
          <label className="block">
            <span className="text-sm text-zinc-400">Display name</span>
            <input value={displayName} onChange={(e) => setDisplayName(e.target.value)}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 outline-none focus:border-brand-500" />
          </label>
          <label className="block">
            <span className="text-sm text-zinc-400">Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))}
              placeholder="username" maxLength={20}
              className="mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 outline-none focus:border-brand-500" />
            <span className="mt-1 block text-xs text-zinc-500">3–20 characters, letters, numbers and underscore only. People can find you by it.</span>
          </label>
          <button onClick={saveDetails} disabled={busy}
            className="rounded-full bg-brand-500 px-4 py-2 text-sm font-semibold text-zinc-950 hover:bg-brand-400 disabled:opacity-50">
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </div>

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}
        {saved && <p className="mt-3 text-sm text-green-400">{saved}</p>}
      </div>
    </div>
  );
}
