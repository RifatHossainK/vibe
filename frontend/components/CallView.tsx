'use client';
import { useEffect, useRef, useState } from 'react';
import { uploadFile, type Message, type PublicUser } from '@/lib/api';
import { formatDuration, type CallType } from '@/lib/calls';
import Avatar from './Avatar';

export default function CallView({ conversationId, peer, callType, state, localStream, remoteStream, peerScreenSharing, sharing, connectedAt, onMuteToggle, onCameraToggle, onScreenShareToggle, onEnd, onRecordingUploaded, onError }: {
  conversationId: number;
  peer: PublicUser;
  callType: CallType;
  /** 'calling' (outgoing, ringing) | 'ringing' (accepted, connecting) | 'connected' */
  state: 'calling' | 'ringing' | 'connected';
  localStream: MediaStream | null;
  remoteStream: MediaStream | null;
  peerScreenSharing: boolean;
  /** This user's own screen-share state (manager-driven, incl. browser stop). */
  sharing: boolean;
  connectedAt: number | null;
  onMuteToggle: () => boolean;
  onCameraToggle: () => boolean;
  onScreenShareToggle: () => Promise<boolean>;
  onEnd: () => void;
  onRecordingUploaded: (msg: Message) => void;
  onError: (msg: string) => void;
}) {
  const remoteVideoRef = useRef<HTMLVideoElement>(null);
  const localVideoRef = useRef<HTMLVideoElement>(null);
  const [muted, setMuted] = useState(false);
  const [cameraOff, setCameraOff] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [recording, setRecording] = useState(false);
  const [uploadingRec, setUploadingRec] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  useEffect(() => {
    if (remoteVideoRef.current && remoteStream) remoteVideoRef.current.srcObject = remoteStream;
  }, [remoteStream]);
  useEffect(() => {
    if (localVideoRef.current && localStream) localVideoRef.current.srcObject = localStream;
  }, [localStream]);

  // Live MM:SS timer once connected.
  useEffect(() => {
    if (!connectedAt) { setElapsed(0); return; }
    const tick = () => setElapsed(Math.floor((Date.now() - connectedAt) / 1000));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [connectedAt]);

  const hasRemoteVideo = !!remoteStream?.getVideoTracks().some((t) => t.enabled && t.readyState === 'live');
  const isVideo = callType === 'video';

  function handleMute() { setMuted(onMuteToggle()); }
  function handleCamera() { setCameraOff(onCameraToggle()); }
  async function handleScreen() {
    // The page asks the manager: stop if sharing, start if not. The
    // manager reports the new state back via onLocalScreenShare.
    await onScreenShareToggle();
  }

  /** SILENT recording: MediaRecorder on the REMOTE stream only. Nothing
   * about recording is sent to the peer — the button state shows only in
   * this user's own UI. On stop the file uploads as a normal media message. */
  async function startRecording() {
    if (!remoteStream || remoteStream.getTracks().length === 0) {
      onError('Nothing to record yet — wait until the call connects.');
      return;
    }
    try {
      const mime = isVideo
        ? (MediaRecorder.isTypeSupported('video/webm') ? 'video/webm' : undefined)
        : (MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm' : undefined);
      const rec = new MediaRecorder(remoteStream, mime ? { mimeType: mime } : undefined);
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data); };
      rec.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: isVideo ? 'video/webm' : 'audio/webm' });
        if (blob.size === 0) return;
        setUploadingRec(true);
        try {
          const file = new File([blob], `call-recording-${Date.now()}.webm`, { type: blob.type });
          const msg = await uploadFile(conversationId, file, isVideo ? 'video' : 'audio');
          onRecordingUploaded(msg);
        } catch (err) {
          onError(err instanceof Error ? err.message : 'Recording upload failed');
        } finally {
          setUploadingRec(false);
        }
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
    } catch {
      onError('Recording is not supported in this browser.');
    }
  }

  function stopRecording() {
    setRecording(false);
    recorderRef.current?.stop();
    recorderRef.current = null;
  }

  const statusText = state === 'connected' ? formatDuration(elapsed) : state === 'calling' ? 'Calling…' : 'Connecting…';

  return (
    <div className="fixed inset-0 z-[60] flex flex-col bg-zinc-950">
      {/* Remote tile */}
      <div className="relative flex min-h-0 flex-1 items-center justify-center bg-zinc-900">
        {isVideo && hasRemoteVideo ? (
          <video ref={remoteVideoRef} autoPlay playsInline className="h-full w-full object-contain" />
        ) : (
          <div className="flex flex-col items-center">
            <span className="relative">
              <Avatar displayName={peer.display_name} avatarUrl={peer.avatar_url} className="h-28 w-28 text-3xl" />
              {state === 'connected' && (
                <span className="absolute -bottom-1 -right-1 flex h-6 w-6 items-center justify-center rounded-full bg-green-500 text-[11px]">●</span>
              )}
            </span>
            <p className="mt-4 text-lg font-semibold">{peer.display_name}</p>
            {!isVideo && state === 'connected' && <p className="mt-1 text-sm text-zinc-400">🎙️ Speaking…</p>}
          </div>
        )}
        {peerScreenSharing && (
          <span className="absolute left-3 top-3 rounded-full bg-zinc-950/80 px-3 py-1 text-xs font-semibold text-zinc-100">🖥 Screen</span>
        )}
        <span className="absolute right-3 top-3 rounded-full bg-zinc-950/80 px-3 py-1 text-sm font-medium text-zinc-100">{statusText}</span>
        {recording && (
          <span className="absolute left-3 top-3 flex items-center gap-1.5 rounded-full bg-red-500/90 px-3 py-1 text-xs font-semibold text-white">
            <span className="h-2 w-2 animate-pulse rounded-full bg-white" /> REC {formatDuration(elapsed)}
          </span>
        )}
        {/* Local preview, picture-in-picture */}
        {isVideo && localStream && (
          <div className="absolute bottom-3 right-3 w-28 overflow-hidden rounded-xl border border-zinc-700 bg-zinc-800 sm:w-40">
            {cameraOff ? (
              <div className="flex h-20 items-center justify-center sm:h-28">
                <Avatar displayName="You" avatarUrl={null} className="h-12 w-12 text-sm" />
              </div>
            ) : (
              <video ref={localVideoRef} autoPlay playsInline muted className="h-20 w-full object-cover sm:h-28" />
            )}
          </div>
        )}
      </div>

      {/* Controls */}
      <div className="flex items-center justify-center gap-3 border-t border-zinc-800 bg-zinc-900 px-4 py-4">
        <button onClick={handleMute} title={muted ? 'Unmute' : 'Mute'} aria-label={muted ? 'Unmute microphone' : 'Mute microphone'}
          className={`flex h-12 w-12 items-center justify-center rounded-full text-lg ${muted ? 'bg-zinc-100 text-zinc-900' : 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700'}`}>
          {muted ? '🔇' : '🎙️'}
        </button>
        {isVideo && (
          <button onClick={handleCamera} title={cameraOff ? 'Turn camera on' : 'Turn camera off'} aria-label="Toggle camera"
            className={`flex h-12 w-12 items-center justify-center rounded-full text-lg ${cameraOff ? 'bg-zinc-100 text-zinc-900' : 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700'}`}>
            {cameraOff ? '📷' : '📹'}
          </button>
        )}
        {isVideo && (
          <button onClick={handleScreen} title={sharing ? 'Stop sharing screen' : 'Share screen'} aria-label="Share screen"
            className={`flex h-12 w-12 items-center justify-center rounded-full text-lg ${sharing ? 'bg-brand-500 text-zinc-950' : 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700'}`}>
            🖥
          </button>
        )}
        <button onClick={recording ? stopRecording : startRecording} disabled={uploadingRec} title={recording ? 'Stop recording' : 'Record call'}
          aria-label={recording ? 'Stop recording' : 'Record call'}
          className={`flex h-12 w-12 items-center justify-center rounded-full text-lg disabled:opacity-50 ${recording ? 'bg-red-500 text-white' : 'bg-zinc-800 text-zinc-100 hover:bg-zinc-700'}`}>
          {uploadingRec ? '…' : recording ? '■' : '●'}
        </button>
        <button onClick={onEnd} title="End call" aria-label="End call"
          className="flex h-12 w-12 items-center justify-center rounded-full bg-red-500 text-lg text-white hover:bg-red-400">
          📞
        </button>
      </div>
      <p className="bg-zinc-900 pb-3 text-center text-xs text-zinc-500">
        {isVideo ? 'Video call' : 'Audio call'} with {peer.display_name}
        {recording ? ' · Recording (only you can see this)' : ''}
      </p>
    </div>
  );
}
