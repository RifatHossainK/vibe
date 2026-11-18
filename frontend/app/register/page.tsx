'use client';
import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { register } from '@/lib/api';
import { setToken, setUser } from '@/lib/auth';

export default function RegisterPage() {
  const router = useRouter();
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [usernameTouched, setUsernameTouched] = useState(false);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  // Auto-suggest a username from the email prefix until the user edits it.
  function onEmailChange(value: string) {
    setEmail(value);
    if (!usernameTouched) {
      const local = value.split('@')[0].toLowerCase().replace(/[^a-z0-9_]/g, '').slice(0, 20);
      setUsername(local);
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (password !== confirm) { setError('Passwords do not match.'); return; }
    if (password.length < 6) { setError('Password must be at least 6 characters.'); return; }
    const name = username.trim().toLowerCase();
    if (name && !/^[a-z0-9_]{3,20}$/.test(name)) { setError('Username must be 3-20 characters, letters, numbers and underscore only.'); return; }
    setError('');
    setLoading(true);
    try {
      const res = await register(displayName.trim(), email.trim(), password, name || undefined);
      setToken(res.access_token);
      setUser({ id: res.id, display_name: res.display_name, username: res.username, email: res.email, role: res.role, avatar_url: res.avatar_url, created_at: res.created_at });
      router.push('/chat');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setLoading(false);
    }
  }

  const inputCls = 'mt-1 w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-zinc-100 outline-none focus:border-brand-500';
  return (
    <main className="flex min-h-screen items-center justify-center bg-zinc-950 px-4">
      <div className="w-full max-w-sm rounded-2xl border border-zinc-800 bg-zinc-900 p-8">
        <h1 className="text-center text-3xl font-bold text-brand-500">Vibe</h1>
        <p className="mt-1 text-center text-sm text-zinc-400">Create your account.</p>
        <form onSubmit={onSubmit} className="mt-6 space-y-4">
          <div><label htmlFor="display_name" className="text-sm text-zinc-300">Display name</label>
            <input id="display_name" required value={displayName} onChange={(e) => setDisplayName(e.target.value)} className={inputCls} /></div>
          <div><label htmlFor="email" className="text-sm text-zinc-300">Email</label>
            <input id="email" type="email" required value={email} onChange={(e) => onEmailChange(e.target.value)} className={inputCls} /></div>
          <div><label htmlFor="username" className="text-sm text-zinc-300">Username <span className="text-zinc-500">(optional — people can find you by it)</span></label>
            <input id="username" value={username} maxLength={20} onChange={(e) => { setUsernameTouched(true); setUsername(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '')); }} className={inputCls} placeholder="username" /></div>
          <div><label htmlFor="password" className="text-sm text-zinc-300">Password</label>
            <input id="password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} className={inputCls} /></div>
          <div><label htmlFor="confirm" className="text-sm text-zinc-300">Confirm password</label>
            <input id="confirm" type="password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} className={inputCls} /></div>
          {error && <p className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">{error}</p>}
          <button type="submit" disabled={loading} className="w-full rounded-lg bg-brand-500 py-2 font-semibold text-zinc-950 hover:bg-brand-400 disabled:opacity-50">
            {loading ? 'Creating account…' : 'Create account'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-zinc-400">
          Already have an account? <Link href="/login" className="text-brand-500 hover:underline">Log in</Link>
        </p>
      </div>
    </main>
  );
}
