'use client';
import { useRef, useState } from 'react';
import { uploadFile, type Message } from '@/lib/api';

export default function VoiceRecorder({ conversationId, onUploaded, onError }: {
  conversationId: number;
  onUploaded: (msg: Message) => void;
  onError: (msg: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [uploading, setUploading] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream, { mimeType: MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined });
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        if (blob.size === 0) { onError('Recording was empty.'); return; }
        setUploading(true);
        try {
          const file = new File([blob], 'voice-message.webm', { type: 'audio/webm' });
          const msg = await uploadFile(conversationId, file, 'voice');
          onUploaded(msg);
        } catch (err) {
          onError(err instanceof Error ? err.message : 'Voice upload failed');
        } finally {
          setUploading(false);
        }
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
      setSeconds(0);
      timerRef.current = setInterval(() => setSeconds((s) => s + 1), 1000);
    } catch {
      onError('Microphone access denied. Allow microphone permission in your browser to record voice messages.');
    }
  }

  function stop() {
    if (timerRef.current) clearInterval(timerRef.current);
    setRecording(false);
    recorderRef.current?.stop();
  }

  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  const ss = String(seconds % 60).padStart(2, '0');
  return (
    <div className="flex items-center gap-2">
      {recording && (
        <span className="flex items-center gap-2 rounded-full bg-red-500/10 px-3 py-1 text-sm text-red-400">
          <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
          {mm}:{ss} <span className="text-zinc-400">▂▃▅▇▅▃▂</span>
        </span>
      )}
      <button type="button" onClick={recording ? stop : start} disabled={uploading}
        title={recording ? 'Stop recording' : 'Record voice message'}
        className={`rounded-full p-2 ${recording ? 'bg-red-500 text-white' : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100'} disabled:opacity-50`}>
        {uploading ? '…' : recording ? '■' : '🎙️'}
      </button>
    </div>
  );
}
