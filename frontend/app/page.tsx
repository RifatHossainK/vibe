'use client';
import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { getToken } from '@/lib/auth';

export default function HomePage() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getToken() ? '/chat' : '/login');
  }, [router]);
  return (
    <main className="flex min-h-screen items-center justify-center bg-zinc-950">
      <p className="text-zinc-400">Loading Vibe…</p>
    </main>
  );
}
