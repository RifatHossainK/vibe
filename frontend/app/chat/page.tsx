'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { clearAuth, getToken, getUser, setUser as storeUser, type AuthUser } from '@/lib/auth';
import { getConversation, getOnline, listConversations, markRead, type Conversation, type Message, type PublicUser } from '@/lib/api';
import { WsClient, type WsServerMessage } from '@/lib/ws';
import { CallManager, type CallInvite, type CallState, type CallType } from '@/lib/calls';
import ChatList from '@/components/ChatList';
import ChatWindow from '@/components/ChatWindow';
import CallView from '@/components/CallView';
import IncomingCallModal from '@/components/IncomingCallModal';

interface ActiveCall {
  conversationId: number;
  peer: PublicUser;
  callType: CallType;
  state: 'calling' | 'ringing' | 'connected';
  localStream: MediaStream | null;
  remoteStream: MediaStream | null;
  peerScreenSharing: boolean;
  connectedAt: number | null;
  localSharing: boolean;
}

export default function ChatPage() {
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [messages, setMessagesState] = useState<Message[]>([]);
  const [onlineMap, setOnlineMap] = useState<Record<number, boolean>>({});
  const [typingUser, setTypingUser] = useState<{ conversationId: number; userId: number } | null>(null);
  const [incomingCall, setIncomingCall] = useState<CallInvite | null>(null);
  const [activeCall, setActiveCall] = useState<ActiveCall | null>(null);
  const [callNote, setCallNote] = useState('');
  const wsRef = useRef<WsClient | null>(null);
  const callManagerRef = useRef<CallManager | null>(null);
  const selectedRef = useRef<Conversation | null>(null);
  const messagesRef = useRef<Message[]>([]);
  const activeCallRef = useRef<ActiveCall | null>(null);
  selectedRef.current = selected;
  messagesRef.current = messages;
  activeCallRef.current = activeCall;

  const setMessages = useCallback((fn: (prev: Message[]) => Message[]) => {
    setMessagesState((prev) => fn(prev));
  }, []);

  const appendMessage = useCallback((msg: Message) => {
    setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : [...prev, msg]));
  }, [setMessages]);

  // Brief call notes (busy / ended / errors) auto-clear after a few seconds.
  useEffect(() => {
    if (!callNote) return;
    const t = setTimeout(() => setCallNote(''), 5000);
    return () => clearTimeout(t);
  }, [callNote]);

  // Initial auth + conversations + WS + CallManager
  useEffect(() => {
    const token = getToken();
    const u = getUser();
    if (!token || !u) { router.replace('/login'); return; }
    setUser(u);
    listConversations()
      .then((list) => {
        setConversations(list);
        list.forEach((c) => {
          if (!c.is_group && c.other_user) {
            getOnline(c.other_user.id).then((online) => setOnlineMap((prev) => ({ ...prev, [c.other_user!.id]: online }))).catch(() => undefined);
          }
        });
      })
      .catch(() => undefined);

    const ws = new WsClient();
    wsRef.current = ws;
    ws.connect(token);

    // Global call state: the manager lives for the whole chat page so an
    // incoming call rings no matter which conversation is open (or none).
    const manager = new CallManager(ws, {
      onRemoteStream: (stream) => {
        setActiveCall((prev) => (prev ? { ...prev, remoteStream: stream } : prev));
      },
      onCallStateChange: (state: CallState) => {
        if (state === 'connected') {
          setActiveCall((prev) => (prev ? { ...prev, state: 'connected', connectedAt: prev.connectedAt ?? Date.now(), localStream: manager.currentLocalStream, remoteStream: manager.currentRemoteStream } : prev));
        } else if (state === 'idle') {
          setActiveCall(null);
          setIncomingCall(null);
        }
      },
      onIncomingCall: (invite) => setIncomingCall(invite),
      onBusyInvite: () => setCallNote('Busy — you are already in a call. The new caller was auto-rejected.'),
      onCallEnded: (reason) => {
        setActiveCall(null);
        setIncomingCall(null);
        if (reason && reason !== 'Call ended' && reason !== 'Call declined') setCallNote(reason);
        // Call history lands as a message; refresh the list so it shows.
        listConversations().then(setConversations).catch(() => undefined);
      },
      onError: (message) => setCallNote(message),
      onPeerScreenShare: (sharing) => {
        setActiveCall((prev) => (prev ? { ...prev, peerScreenSharing: sharing } : prev));
      },
      onLocalScreenShare: (sharing) => {
        setActiveCall((prev) => (prev ? { ...prev, localSharing: sharing } : prev));
      },
    });
    callManagerRef.current = manager;
    const offWsClose = ws.onClose(() => manager.handleWsClose());

    const off = ws.onMessage((msg: WsServerMessage) => {
      if (msg.type.startsWith('call:')) {
        void manager.handleMessage(msg);
        return;
      }
      if (msg.type === 'message') {
        const m = msg.message;
        const open = selectedRef.current;
        setConversations((prev) => {
          const idx = prev.findIndex((c) => c.id === m.conversation_id);
          if (idx === -1) {
            // Unknown conversation (e.g. created by the other side) — reload list.
            listConversations().then(setConversations).catch(() => undefined);
            return prev;
          }
          const updated = [...prev];
          const isOpen = open?.id === m.conversation_id;
          const isFromOther = m.sender_id !== u.id;
          updated[idx] = {
            ...updated[idx],
            last_message: { id: m.id, sender_id: m.sender_id, kind: m.kind, content: m.content, created_at: m.created_at },
            last_message_at: m.created_at,
            unread_count: isOpen ? 0 : updated[idx].unread_count + (isFromOther ? 1 : 0),
          };
          return updated.sort((a, b) => (b.last_message_at ?? '').localeCompare(a.last_message_at ?? ''));
        });
        if (open && m.conversation_id === open.id) {
          appendMessage(m);
          if (m.sender_id !== u.id) {
            markRead(m.conversation_id).catch(() => undefined);
            ws.send('read', { conversation_id: m.conversation_id });
          }
        }
      } else if (msg.type === 'message_deleted') {
        // Someone deleted a message for everyone: swap it for the
        // placeholder the server sent — no refresh needed, open chat or not.
        const placeholder = msg.message;
        const open = selectedRef.current;
        if (open && msg.conversation_id === open.id) {
          setMessages((prev) => prev.map((m) => (m.id === msg.message_id ? placeholder : m)));
        }
        setConversations((prev) => prev.map((c) => {
          if (c.id !== msg.conversation_id) return c;
          if (c.last_message?.id !== msg.message_id) return c;
          return {
            ...c,
            last_message: {
              id: placeholder.id,
              sender_id: placeholder.sender_id,
              kind: placeholder.kind,
              content: null,
              created_at: placeholder.created_at,
              deleted: true,
              deleted_for_everyone: true,
            },
          };
        }));
      } else if (msg.type === 'typing') {
        if (selectedRef.current?.id === msg.conversation_id && msg.user_id !== u.id) {
          setTypingUser(msg.typing ? { conversationId: msg.conversation_id, userId: msg.user_id } : null);
        }
      } else if (msg.type === 'presence') {
        setOnlineMap((prev) => ({ ...prev, [msg.user_id]: msg.online }));
      } else if (msg.type === 'read') {
        if (selectedRef.current?.id === msg.conversation_id && msg.user_id !== u.id) {
          setMessages((prev) => prev.map((m) => (m.sender_id === u.id && !m.seen_by.includes(msg.user_id) ? { ...m, seen_by: [...m.seen_by, msg.user_id] } : m)));
        }
      }
    });
    return () => { off(); offWsClose(); ws.disconnect(); callManagerRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const openConversation = useCallback((conv: Conversation) => {
    setSelected(conv);
    setTypingUser(null);
    setConversations((prev) => {
      if (!prev.some((c) => c.id === conv.id)) return [conv, ...prev];
      return prev.map((c) => (c.id === conv.id ? { ...c, unread_count: 0 } : c));
    });
    markRead(conv.id).catch(() => undefined);
    wsRef.current?.send('read', { conversation_id: conv.id });
  }, []);

  // The disappearing timer changed (or any conversation field): keep the
  // open conversation and the list in sync.
  const handleConversationUpdated = useCallback((conv: Conversation) => {
    setSelected((prev) => (prev && prev.id === conv.id ? { ...prev, ...conv } : prev));
    setConversations((prev) => prev.map((c) => (c.id === conv.id ? { ...c, ...conv } : c)));
  }, []);

  // Disappearing messages in the LIST: every second, strip the preview of
  // any last message whose time has run out, so the chat list never leaks
  // an expired message's content.
  useEffect(() => {
    const t = setInterval(() => {
      const now = Date.now();
      setConversations((prev) => {
        let changed = false;
        const next = prev.map((c) => {
          const lm = c.last_message;
          if (lm && !lm.expired && lm.expires_at && new Date(lm.expires_at).getTime() <= now) {
            changed = true;
            return { ...c, last_message: { ...lm, expired: true, content: null } };
          }
          return c;
        });
        return changed ? next : prev;
      });
    }, 1000);
    return () => clearInterval(t);
  }, []);

  const handleConversationCreated = useCallback((conv: Conversation) => {
    setConversations((prev) => {
      if (prev.some((c) => c.id === conv.id)) return prev;
      return [conv, ...prev];
    });
    openConversation(conv);
  }, [openConversation]);

  const handleLeaveGroup = useCallback(() => {
    const conv = selectedRef.current;
    if (!conv) return;
    setConversations((prev) => prev.filter((c) => c.id !== conv.id));
    setSelected(null);
  }, []);

  const handleTyping = useCallback((typing: boolean) => {
    const conv = selectedRef.current;
    if (!conv) return;
    wsRef.current?.send('typing', { conversation_id: conv.id, typing });
  }, []);

  const handleSendRead = useCallback(() => {
    const conv = selectedRef.current;
    if (!conv) return;
    markRead(conv.id).catch(() => undefined);
    wsRef.current?.send('read', { conversation_id: conv.id });
    setConversations((prev) => prev.map((c) => (c.id === conv.id ? { ...c, unread_count: 0 } : c)));
  }, []);

  function logout() {
    callManagerRef.current?.endCall();
    clearAuth();
    wsRef.current?.disconnect();
    router.replace('/login');
  }

  // Profile updated in the sidebar (picture / username / display name):
  // refresh the page state and the stored session user.
  const handleUserUpdated = useCallback((updated: AuthUser) => {
    setUser(updated);
    storeUser(updated);
  }, []);

  // ---- Calls (1-to-1 only) ----
  const handleStartCall = useCallback((type: CallType) => {
    const conv = selectedRef.current;
    const manager = callManagerRef.current;
    if (!conv || !manager || conv.is_group || !conv.other_user) return;
    const peer = conv.other_user;
    setActiveCall({
      conversationId: conv.id,
      peer,
      callType: type,
      state: 'calling',
      localStream: null,
      remoteStream: null,
      peerScreenSharing: false,
      localSharing: false,
      connectedAt: null,
    });
    void manager.startCall(conv.id, type).then(() => {
      // startCall reports failures via onError; if it failed the manager
      // is back to idle, so drop the call overlay too.
      if (manager.state === 'idle') { setActiveCall(null); return; }
      setActiveCall((prev) => (prev ? { ...prev, localStream: manager.currentLocalStream } : prev));
    });
  }, []);

  const handleAcceptCall = useCallback((invite: CallInvite) => {
    const manager = callManagerRef.current;
    if (!manager) return;
    const peer: PublicUser = {
      id: invite.from_user_id,
      display_name: invite.from_name,
      username: null,
      email: '',
      avatar_url: invite.from_avatar_url,
    };
    setIncomingCall(null);
    setActiveCall({
      conversationId: invite.conversation_id,
      peer,
      callType: invite.call_type,
      state: 'ringing',
      localStream: null,
      remoteStream: null,
      peerScreenSharing: false,
      localSharing: false,
      connectedAt: null,
    });
    // Open the conversation (fetch it if it is not in the list yet) so the
    // call history and messages are visible once the call ends.
    const known = conversations.find((c) => c.id === invite.conversation_id);
    if (known) openConversation(known);
    else getConversation(invite.conversation_id).then(openConversation).catch(() => undefined);
    void manager.acceptCall(invite).then(() => {
      if (manager.state === 'idle') { setActiveCall(null); return; }
      setActiveCall((prev) => (prev ? { ...prev, localStream: manager.currentLocalStream } : prev));
    });
  }, [conversations, openConversation]);

  const handleRejectCall = useCallback(() => {
    callManagerRef.current?.rejectCall();
    setIncomingCall(null);
  }, []);

  const handleRecordingUploaded = useCallback((msg: Message) => {
    if (selectedRef.current?.id === msg.conversation_id) appendMessage(msg);
    listConversations().then(setConversations).catch(() => undefined);
  }, [appendMessage]);

  // A message was deleted from the open chat (either mode): the list
  // preview may now point at a message I no longer see — refetch it.
  const handleMessageDeleted = useCallback(() => {
    listConversations().then(setConversations).catch(() => undefined);
  }, []);

  if (!user) {
    return <main className="flex min-h-screen items-center justify-center bg-zinc-950"><p className="text-zinc-400">Loading Vibe…</p></main>;
  }

  const selectedTyping = selected && typingUser?.conversationId === selected.id;
  const selectedOnline = selected && !selected.is_group && selected.other_user ? (onlineMap[selected.other_user.id] ?? false) : false;

  return (
    <main className="flex h-screen overflow-hidden bg-zinc-950">
      {/* List: full screen on mobile until a chat is selected */}
      <div className={`${selected ? 'hidden' : 'flex'} h-full w-full md:flex`}>
        <ChatList conversations={conversations} selectedId={selected?.id ?? null} onlineMap={onlineMap}
          currentUser={user} onSelect={openConversation} isAdmin={user.role === 'admin'}
          onConversationCreated={handleConversationCreated} onLogout={logout} onUserUpdated={handleUserUpdated} />
      </div>
      {/* Conversation: full screen on mobile when selected, side-by-side on desktop */}
      <div className={`${selected ? 'flex' : 'hidden'} h-full flex-1 md:flex`}>
        {selected ? (
          <ChatWindow key={selected.id} conversation={selected} currentUserId={user.id}
            messages={messages} setMessages={setMessages}
            online={selectedOnline}
            typing={!!selectedTyping} onTyping={handleTyping} onSendRead={handleSendRead}
            onBack={() => setSelected(null)} onConversationUpdated={handleConversationUpdated}
            onStartCall={handleStartCall} onLeaveGroup={handleLeaveGroup} onMessageDeleted={handleMessageDeleted}
            isAdmin={user.role === 'admin'} />
        ) : (
          <div className="hidden flex-1 flex-col items-center justify-center bg-zinc-950 md:flex">
            <p className="text-4xl font-bold text-brand-500">Vibe</p>
            <p className="mt-2 text-zinc-400">Select a chat or start a new one.</p>
          </div>
        )}
      </div>

      {/* Brief call status note */}
      {callNote && (
        <div className="fixed bottom-4 left-1/2 z-[65] -translate-x-1/2 rounded-full bg-zinc-800 px-4 py-2 text-sm text-zinc-100 shadow-lg">
          {callNote}
          <button onClick={() => setCallNote('')} aria-label="Dismiss" className="ml-2 text-zinc-400 hover:text-zinc-100">✕</button>
        </div>
      )}

      {/* Incoming call rings globally — whatever is open (or nothing). */}
      {incomingCall && (
        <IncomingCallModal invite={incomingCall} onAccept={() => handleAcceptCall(incomingCall)} onReject={handleRejectCall} />
      )}

      {/* In-call overlay sits above the chat. */}
      {activeCall && (
        <CallView conversationId={activeCall.conversationId} peer={activeCall.peer} callType={activeCall.callType}
          state={activeCall.state} localStream={activeCall.localStream} remoteStream={activeCall.remoteStream}
          peerScreenSharing={activeCall.peerScreenSharing} sharing={activeCall.localSharing} connectedAt={activeCall.connectedAt}
          onMuteToggle={() => callManagerRef.current?.toggleMute() ?? false}
          onCameraToggle={() => callManagerRef.current?.toggleCamera() ?? false}
          onScreenShareToggle={async () => {
            const manager = callManagerRef.current;
            if (!manager) return false;
            if (manager.isScreenSharing) { await manager.stopScreenShare(); return false; }
            return manager.startScreenShare();
          }}
          onEnd={() => callManagerRef.current?.endCall()}
          onRecordingUploaded={handleRecordingUploaded}
          onError={(msg) => setCallNote(msg)} />
      )}
    </main>
  );
}
