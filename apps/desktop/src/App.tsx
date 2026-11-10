import React, { useEffect, useRef, useState } from "react";
import { API_BASE, rallyApi } from "./api";
import { ApprovalRequest, Mission } from "./types";

function playAudioChime() {
  try {
    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
    if (!AudioCtx) return;
    const ctx = new AudioCtx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.type = "sine";
    osc.frequency.setValueAtTime(659.25, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(1046.5, ctx.currentTime + 0.1);
    gain.gain.setValueAtTime(0.35, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.28);
    osc.start(ctx.currentTime);
    osc.stop(ctx.currentTime + 0.28);
  } catch (e) {}
}

function encodeWAV(samples: Float32Array, sampleRate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  function writeString(offset: number, str: string) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }
  writeString(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, 1, true); // Mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); // Block align
  view.setUint16(34, 16, true); // 16-bit
  writeString(36, "data");
  view.setUint32(40, samples.length * 2, true);
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}

export function App() {
  const isCompanion =
    new URLSearchParams(window.location.search).get("companion") === "1" ||
    new URLSearchParams(window.location.search).get("sidecar") === "1" ||
    window.location.href.includes("sidecar") ||
    window.location.href.includes("companion");
  const [isSidecarCollapsed, setIsSidecarCollapsed] = useState(false);
  const [activeTab, setActiveTab] = useState<"voice" | "missions" | "workspace" | "build" | "approvals" | "registry">("voice");
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedMissionId, setSelectedMissionId] = useState<string | null>(null);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [evidence, setEvidence] = useState<any[]>([]);
  const [missionEvents, setMissionEvents] = useState<any[]>([]);
  const [defects, setDefects] = useState<any[]>([]);
  const [buildReport, setBuildReport] = useState<any>(null);
  const [windows, setWindows] = useState<any[]>([]);
  const [providers, setProviders] = useState<any[]>([]);
  const [codingAgents, setCodingAgents] = useState<any[]>([]);

  // Voice & Audio Meter State
  const [voiceStatus, setVoiceStatus] = useState<any>(null);
  const [microphones, setMicrophones] = useState<Array<{ index: number | null; name: string }>>([]);
  const [wakeWordInput, setWakeWordInput] = useState("hey rally");
  const [voicePhraseInput, setVoicePhraseInput] = useState("");
  const [micTestNote, setMicTestNote] = useState<string | null>(null);
  const [browserMicActive, setBrowserMicActive] = useState(false);
  const [micVolume, setMicVolume] = useState<number>(0);
  const [isListeningLive, setIsListeningLive] = useState<boolean>(false);
  const [liveTypingActive, setLiveTypingActive] = useState<boolean>(false);
  const [voiceStatusNotice, setVoiceStatusNotice] = useState<string | null>(null);
  const [autoExecuteEnabled, setAutoExecuteEnabled] = useState<boolean>(true);
  const recognitionRef = useRef<any>(null);
  const micVolumeRef = useRef<number>(0);
  const isExecutingRef = useRef<boolean>(false);

  const [objectiveInput, setObjectiveInput] = useState("Open Spotify.");
  const [missionTypeInput, setMissionTypeInput] = useState("general");
  const [workspaceInput, setWorkspaceInput] = useState(".");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const selectedMission = missions.find((m) => m.id === selectedMissionId) || missions[0] || null;
  const pendingApprovals = approvals.filter((a) => a.status === "pending");

  async function refreshAll(preferredMissionId?: string) {
    try {
      const [health, misList, aprList, winList, vStat] = await Promise.all([
        rallyApi.getHealth(),
        rallyApi.listMissions(),
        rallyApi.listApprovals(),
        rallyApi.getWindows(),
        rallyApi.getVoiceStatus(),
      ]);
      setProviders(health.providers);
      setCodingAgents(health.coding_agents);
      setMissions(misList);
      setApprovals(aprList);
      setWindows(winList.windows);
      setVoiceStatus(vStat);

      const targetId = preferredMissionId || selectedMissionId || misList[0]?.id;
      if (targetId) {
        setSelectedMissionId(targetId);
        const [evd, evts, defs, rep] = await Promise.all([
          rallyApi.getMissionEvidence(targetId),
          rallyApi.getMissionEvents(targetId),
          rallyApi.getBuildDefects(targetId).catch(() => []),
          rallyApi.getBuildReport(targetId).catch(() => null),
        ]);
        setEvidence(evd);
        setMissionEvents(evts);
        setDefects(defs);
        setBuildReport(rep);
      }
      setErrorBanner(null);
    } catch (err: any) {
      // Don't flash transient background polling errors
    }
  }

  useEffect(() => {
    let es: EventSource | null = null;
    let timer: any = null;

    async function initApp() {
      await rallyApi.initSecurityTokens();
      refreshAll();
      rallyApi.startVoiceListening().catch(() => {});
      rallyApi.getMicrophones().then((r) => setMicrophones(r.microphones)).catch(() => {});

      const streamUrl = rallyApi.getEventStreamUrl();
      es = new EventSource(streamUrl);
      es.onmessage = (e) => {
        try {
          const evt = JSON.parse(e.data);
          if (evt.type === "voice.live.transcript") {
            const text = evt.payload?.text;
            if (text) {
              setVoicePhraseInput(text);
              if (!evt.payload?.is_final) {
                setLiveTypingActive(true);
                setVoiceStatusNotice(`✍️ Listening: "${text}"`);
              } else {
                setLiveTypingActive(false);
                setVoiceStatusNotice(`⚡ Executing: "${text}"`);
                refreshAll();
              }
            }
          } else {
            refreshAll();
          }
        } catch {
          refreshAll();
        }
      };
      timer = setInterval(() => refreshAll(), 2000);
    }

    initApp();

    return () => {
      if (es) es.close();
      if (timer) clearInterval(timer);
    };
  }, [selectedMissionId]);

  // Also wire Browser WebSpeech API as a secondary real-time microphone feed into the backend wake-word engine
  function toggleBrowserSpeechRecognition(enable: boolean) {
    const SpeechRec = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRec) return;

    if (!enable) {
      if (recognitionRef.current) {
        recognitionRef.current.onend = null;
        recognitionRef.current.stop();
        recognitionRef.current = null;
      }
      setBrowserMicActive(false);
      return;
    }

    try {
      const rec = new SpeechRec();
      rec.continuous = true;
      rec.interimResults = false;
      rec.lang = navigator.language?.includes("IN") ? "en-IN" : "en-US";
      rec.onresult = async (event: any) => {
        const last = event.results[event.results.length - 1];
        if (last && last.isFinal) {
          const text = last[0].transcript;
          await rallyApi.sendVoiceUtterance(text, false);
          await refreshAll();
        }
      };
      rec.onend = () => {
        if (recognitionRef.current) {
          try { rec.start(); } catch {}
        }
      };
      rec.start();
      recognitionRef.current = rec;
      setBrowserMicActive(true);
    } catch {}
  }

  // Real-time microphone audio visualizer level tracker & Web Audio VAD with auto-execution
  useEffect(() => {
    let animId: number;
    let stream: MediaStream | null = null;
    let audioCtx: AudioContext | null = null;
    let processor: ScriptProcessorNode | null = null;
    let isSpeaking = false;
    let pcmChunks: Float32Array[] = [];
    let silenceTimer: any = null;
    let speechStartTime = 0;

    async function startMeter() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) return;
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
        audioCtx = new AudioCtx();
        const src = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        analyser.smoothingTimeConstant = 0.4;
        src.connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);

        const tick = () => {
          analyser.getByteFrequencyData(data);
          let sum = 0;
          for (let i = 0; i < data.length; i++) {
            sum += data[i];
          }
          const avg = sum / (data.length || 1);
          const vol = Math.min(100, Math.round((avg / 128) * 100));
          setMicVolume(vol);
          micVolumeRef.current = vol;
          animId = requestAnimationFrame(tick);
        };
        tick();

        // Real-time speech capture & auto-execution via Web Audio
        processor = audioCtx.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (e) => {
          const input = e.inputBuffer.getChannelData(0);
          const output = e.outputBuffer.getChannelData(0);
          output.fill(0); // Zero output to prevent echo through laptop speakers

          const vol = micVolumeRef.current;
          if (vol > 10) {
            if (!isSpeaking) {
              isSpeaking = true;
              speechStartTime = Date.now();
              pcmChunks = [];
              setIsListeningLive(true);
              setVoiceStatusNotice("🎙️ Listening live to your voice...");
            }
            if (silenceTimer) {
              clearTimeout(silenceTimer);
              silenceTimer = null;
            }
            pcmChunks.push(new Float32Array(input));
          } else if (isSpeaking) {
            pcmChunks.push(new Float32Array(input));
            if (!silenceTimer) {
              silenceTimer = setTimeout(async () => {
                isSpeaking = false;
                setIsListeningLive(false);
                silenceTimer = null;
                const duration = Date.now() - speechStartTime;
                if (duration > 400 && pcmChunks.length > 0 && !isExecutingRef.current) {
                  isExecutingRef.current = true;
                  setVoiceStatusNotice("✍️ Transcribing & auto-executing...");
                  const totalLen = pcmChunks.reduce((acc, c) => acc + c.length, 0);
                  const merged = new Float32Array(totalLen);
                  let off = 0;
                  for (const c of pcmChunks) {
                    merged.set(c, off);
                    off += c.length;
                  }
                  try {
                    const wav = encodeWAV(merged, audioCtx?.sampleRate || 44100);
                    const res = await rallyApi.transcribeAudio(wav, autoExecuteEnabled, isListeningLive);
                    if (res.text) {
                      setVoicePhraseInput(res.text);
                      setVoiceStatusNotice(`⚡ Executed: "${res.text}"`);
                      setTimeout(() => setVoiceStatusNotice(null), 4500);
                      await refreshAll(res.execution?.mission_id);
                    }
                  } catch (err: any) {
                    setVoiceStatusNotice(null);
                  } finally {
                    isExecutingRef.current = false;
                  }
                }
                pcmChunks = [];
              }, 700);
            }
          }
        };
        src.connect(processor);
        processor.connect(audioCtx.destination);
      } catch (err) {}
    }

    startMeter();

    return () => {
      if (animId) cancelAnimationFrame(animId);
      if (silenceTimer) clearTimeout(silenceTimer);
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (processor) processor.disconnect();
      if (audioCtx) audioCtx.close().catch(() => {});
    };
  }, [autoExecuteEnabled]);

  async function handleEnableVoice() {
    await rallyApi.startVoiceListening();
    toggleBrowserSpeechRecognition(true);
    await refreshAll();
  }

  async function handleWakeVoice() {
    playAudioChime();
    setIsListeningLive(true);
    setVoiceStatusNotice("🎙️ Listening live... Speak now!");
    try {
      await rallyApi.wakeVoice();
      await refreshAll();
    } catch {}
    setTimeout(() => {
      setIsListeningLive(false);
    }, 7000);
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (
        e.key === "F9" ||
        e.key === "F8" ||
        e.key === "F7" ||
        (e.altKey && e.code === "Space") ||
        (e.ctrlKey && e.code === "Space") ||
        (e.ctrlKey && e.shiftKey && e.code === "Space") ||
        (e.ctrlKey && e.shiftKey && (e.key === "A" || e.key === "a"))
      ) {
        e.preventDefault();
        handleWakeVoice();
      }
    };
    window.addEventListener("keydown", onKey);

    const unbindWake = (window as any).rallyDesktop?.onWake?.(() => {
      handleWakeVoice();
    });

    return () => {
      window.removeEventListener("keydown", onKey);
      if (unbindWake) unbindWake();
    };
  }, []);

  async function handlePauseVoice() {
    await rallyApi.pauseVoiceListening();
    toggleBrowserSpeechRecognition(false);
    await refreshAll();
  }

  async function handleSendSpokenUtterance(e: React.FormEvent) {
    e.preventDefault();
    if (!voicePhraseInput.trim() || isSubmitting) return;
    setIsSubmitting(true);
    setErrorBanner(null);
    try {
      const res = await rallyApi.sendVoiceUtterance(voicePhraseInput.trim(), false);
      if (res.mission_id) {
        setSelectedMissionId(res.mission_id);
      }
      await refreshAll(res.mission_id);
    } catch (err: any) {
      setErrorBanner(err?.message || "Voice command failed");
    } finally {
      setIsSubmitting(false);
    }
  }

  async function handleCreateMission(e: React.FormEvent) {
    e.preventDefault();
    if (!objectiveInput.trim() || isSubmitting) return;
    setErrorBanner(null);
    setIsSubmitting(true);
    try {
      const created = await rallyApi.createMission({
        objective: objectiveInput.trim(),
        mission_type: missionTypeInput,
        workspace_path: missionTypeInput === "build" ? workspaceInput : undefined,
        selected_coding_agent: missionTypeInput === "build" ? "ollama_cli" : undefined,
      });
      setSelectedMissionId(created.id);
      await refreshAll(created.id);
      await rallyApi.startMission(created.id);
      await refreshAll(created.id);
    } catch (err: any) {
      setErrorBanner(err?.message || "Failed to execute mission");
    } finally {
      setIsSubmitting(false);
    }
  }

  function statusBadgeColor(status: string) {
    switch (status) {
      case "COMPLETED":
      case "IDLE_LISTENING":
        return "bg-emerald-950 text-emerald-300 border border-emerald-600";
      case "RUNNING":
      case "PLANNING":
      case "VERIFYING":
      case "WAKE_WORD_DETECTED":
      case "CAPTURING_COMMAND":
      case "TRANSCRIBING":
      case "EXECUTING":
      case "SPEAKING":
        return "bg-blue-950 text-blue-300 border border-blue-500 animate-pulse";
      case "AWAITING_APPROVAL":
      case "WAITING":
      case "PAUSED":
        return "bg-amber-950 text-amber-300 border border-amber-500";
      case "FAILED":
      case "ERROR":
        return "bg-red-950 text-red-300 border border-red-600";
      default:
        return "bg-slate-800 text-slate-300";
    }
  }

  if (isCompanion) {
    const isListening = voiceStatus?.listening_enabled;
    const isSpeaking = voiceStatus?.state === "SPEAKING";
    const isExecuting = voiceStatus?.state === "EXECUTING" || voiceStatus?.state === "PLANNING";
    const isAwake = voiceStatus?.in_conversation_window;
    const pendingTier2 = approvals.filter((a) => a.status === "PENDING" || a.status === "pending");

    // Collapsed Mode: Sleek Floating Avatar Orb on the side of the screen
    if (isSidecarCollapsed) {
      return (
        <div
          onClick={() => {
            (window as any).rallyDesktop?.expandSidecar?.();
            setIsSidecarCollapsed(false);
          }}
          title="RALLY AI Desktop Agent — Click to Expand (Ctrl+Shift+R or F9)"
          className="h-full w-full bg-[#070a12]/95 backdrop-blur-2xl border-2 border-blue-500/80 hover:border-cyan-400 cursor-pointer rounded-2xl flex flex-col items-center justify-center p-2 shadow-2xl group transition-all select-none"
        >
          <div className="relative flex items-center justify-center mb-1">
            <span
              className={`absolute h-10 w-10 rounded-full opacity-60 animate-ping ${
                isSpeaking
                  ? "bg-purple-500"
                  : isExecuting
                  ? "bg-blue-500"
                  : isListening
                  ? "bg-emerald-500"
                  : "bg-cyan-500"
              }`}
            />
            <span
              className={`h-9 w-9 rounded-full shadow-lg border border-white/30 flex items-center justify-center text-xs font-black text-white ${
                isSpeaking
                  ? "bg-gradient-to-tr from-purple-700 to-indigo-500 animate-pulse"
                  : isExecuting
                  ? "bg-gradient-to-tr from-blue-700 to-cyan-500"
                  : isListening
                  ? "bg-gradient-to-tr from-emerald-600 to-teal-400 animate-bounce"
                  : "bg-gradient-to-tr from-slate-800 to-blue-600"
              }`}
            >
              R
            </span>
          </div>
          <span className="text-[9px] font-mono tracking-tighter text-blue-300 font-bold uppercase">
            {isSpeaking ? "SPEAK" : isExecuting ? "EXEC" : isListening ? "LIVE" : "RALLY"}
          </span>
          <span className="text-[11px] text-slate-400 group-hover:text-cyan-300 transition-colors mt-0.5">◀</span>
        </div>
      );
    }

    // Expanded Mode: Full-Featured Always-On Sidecar Agent Dock
    return (
      <div className="h-full w-full bg-[#070a12]/95 backdrop-blur-2xl text-slate-100 flex flex-col justify-between border border-blue-500/60 shadow-[0_0_35px_rgba(59,130,246,0.35)] rounded-2xl select-none overflow-hidden p-3.5">
        {/* Top Header / Draggable Bar */}
        <div style={{ ["WebkitAppRegion" as any]: "drag" }} className="flex justify-between items-center cursor-move pb-2 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <span
              className={`h-3 w-3 rounded-full ${
                isSpeaking
                  ? "bg-purple-400 animate-bounce"
                  : isExecuting
                  ? "bg-blue-400 animate-spin"
                  : isListening
                  ? "bg-emerald-400 animate-ping"
                  : "bg-amber-400"
              }`}
            />
            <div>
              <div className="flex items-center gap-1.5">
                <span className="font-extrabold text-xs tracking-wider bg-gradient-to-r from-blue-400 via-cyan-300 to-indigo-300 bg-clip-text text-transparent">
                  RALLY AGENT
                </span>
                <span className="text-[9px] font-mono px-1.5 py-0.2 rounded bg-slate-900 text-blue-300 border border-blue-900/60">
                  {voiceStatus?.state || "IDLE"}
                </span>
              </div>
            </div>
          </div>

          <div style={{ ["WebkitAppRegion" as any]: "no-drag" }} className="flex items-center gap-1">
            <button
              onClick={() => (window as any).rallyDesktop?.openDashboard?.()}
              title="Open Full Mission Control Dashboard"
              className="bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white px-2 py-0.5 rounded text-[10px] font-medium transition"
            >
              Dashboard ↗
            </button>
            <button
              onClick={() => {
                (window as any).rallyDesktop?.collapseSidecar?.();
                setIsSidecarCollapsed(true);
              }}
              title="Minimize into Floating Orb on Screen Edge"
              className="bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white px-1.5 py-0.5 rounded text-[10px] font-bold"
            >
              ▶
            </button>
            <button
              onClick={() => rallyApi.emergencyStop().then(() => refreshAll())}
              className="bg-red-600 hover:bg-red-500 px-2 py-0.5 rounded text-[10px] font-bold text-white shadow"
              title="Emergency Stop All Tasks (Ctrl+Shift+X)"
            >
              STOP
            </button>
          </div>
        </div>

        {/* Interactive AI Agent Core Visualizer */}
        <div className="py-2.5 flex flex-col items-center justify-center relative">
          <div className="relative flex items-center justify-center">
            {/* Concentric Aura Rings */}
            <div
              className={`absolute h-20 w-20 rounded-full border border-blue-500/30 transition-all duration-700 ${
                isSpeaking
                  ? "scale-125 border-purple-500/50 animate-pulse"
                  : isListening
                  ? "scale-110 border-emerald-500/50 animate-ping"
                  : "scale-100"
              }`}
            />
            <div
              className={`absolute h-16 w-16 rounded-full border border-cyan-400/40 transition-all duration-500 ${
                isExecuting ? "rotate-180 animate-spin border-dashed" : ""
              }`}
            />
            {/* Core Orb */}
            <div
              className={`h-12 w-12 rounded-full shadow-lg border-2 border-white/40 flex items-center justify-center text-sm font-black text-white transition-all ${
                isSpeaking
                  ? "bg-gradient-to-tr from-purple-700 via-indigo-600 to-pink-500 animate-pulse"
                  : isExecuting
                  ? "bg-gradient-to-tr from-blue-700 via-cyan-600 to-indigo-700"
                  : isListening
                  ? "bg-gradient-to-tr from-emerald-600 via-teal-500 to-cyan-400"
                  : "bg-gradient-to-tr from-slate-900 via-slate-800 to-blue-900"
              }`}
            >
              {isSpeaking ? "🔊" : isListening ? "🎙️" : isExecuting ? "⚙️" : "AI"}
            </div>
          </div>

          <div className="mt-2 text-center">
            <p className="text-[11px] font-semibold text-slate-200">
              {isSpeaking
                ? "Speaking verified answer..."
                : isExecuting
                ? `Executing: ${voiceStatus?.current_structured_task?.raw_command || "desktop mission"}`
                : isListening
                ? isAwake
                  ? "Multi-turn listening active..."
                  : `Listening for '${voiceStatus?.wake_word || "hey rally"}'...`
                : "Microphone Standby"}
            </p>
            <p className="text-[9px] text-slate-400 flex items-center justify-center gap-1.5 mt-0.5">
              <span>Wake: <strong className="text-cyan-300">"Hey RALLY"</strong></span>
              <span>•</span>
              <span>Hotkey: <strong className="text-cyan-300 font-mono">F9</strong></span>
            </p>
          </div>
        </div>

        {/* Live Conversation Stream (User Said vs RALLY Replied) */}
        <div className="bg-slate-950/80 border border-slate-800/80 rounded-xl p-2.5 space-y-1.5 min-h-[76px] flex flex-col justify-center">
          <div className="text-[11px] flex items-start gap-1.5">
            <span className="text-slate-500 font-mono text-[10px] pt-0.5 font-bold uppercase">YOU:</span>
            <span className="text-slate-200 font-medium italic break-words flex-1">
              "{voiceStatus?.last_transcript || "Say 'Hey RALLY' or press F9..."}"
            </span>
          </div>
          {voiceStatus?.last_spoken_response && (
            <div className="text-[11px] flex items-start gap-1.5 border-t border-slate-800/60 pt-1.5">
              <span className="text-emerald-400 font-mono text-[10px] pt-0.5 font-bold uppercase">RALLY:</span>
              <span className="text-emerald-300 font-medium break-words flex-1">
                "{voiceStatus.last_spoken_response}"
              </span>
            </div>
          )}
        </div>

        {/* Pending Approval Gate Alert (If Tier 2 Action Triggered) */}
        {pendingTier2.length > 0 && (
          <div className="my-1.5 p-2 bg-amber-950/70 border border-amber-500/70 rounded-xl">
            <div className="flex items-center justify-between text-[11px] font-bold text-amber-300 mb-1">
              <span>⚠️ APPROVAL REQUIRED</span>
              <span className="text-[9px] font-mono px-1 rounded bg-amber-900/60 text-amber-200">
                Tier {pendingTier2[0].risk_tier}
              </span>
            </div>
            <p className="text-[10px] text-amber-100 mb-1.5 line-clamp-2">
              {pendingTier2[0].action_summary}
            </p>
            <div className="flex gap-1.5">
              <button
                onClick={() => rallyApi.approveRequest(pendingTier2[0].id).then(() => refreshAll())}
                className="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-bold py-1 rounded text-[10px]"
              >
                Approve & Execute
              </button>
              <button
                onClick={() => rallyApi.rejectRequest(pendingTier2[0].id).then(() => refreshAll())}
                className="flex-1 bg-red-800 hover:bg-red-700 text-white font-bold py-1 rounded text-[10px]"
              >
                Reject
              </button>
            </div>
          </div>
        )}

        {/* Quick Action Chips Grid */}
        <div className="grid grid-cols-2 gap-1.5 my-1 text-[10px]">
          {[
            ["🎵 Spotify Play", "Hey RALLY, open Spotify and play Starboy."],
            ["👁️ Inspect Screen", "Hey RALLY, what is on my screen?"],
            ["📝 Open Notepad", "Hey RALLY, open notepad."],
            ["🌐 YouTube Search", "Hey RALLY, search YouTube for Starboy."],
          ].map(([label, phrase]) => (
            <button
              key={label}
              onClick={() => {
                setVoicePhraseInput(phrase);
                rallyApi.sendVoiceUtterance(phrase, false).then((res) => {
                  if (res.mission_id) setSelectedMissionId(res.mission_id);
                  refreshAll(res.mission_id);
                });
              }}
              className="bg-slate-900/90 hover:bg-blue-900/50 border border-slate-800 hover:border-cyan-400 p-1.5 rounded-lg text-slate-300 text-left font-medium transition-all"
            >
              {label}
            </button>
          ))}
        </div>

        {/* Live Audio Level & Frequency Visualizer */}
        <div className="my-1 px-2.5 py-1.5 bg-slate-950/80 border border-slate-800/90 rounded-xl flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${micVolume > 15 ? "bg-emerald-400 animate-ping" : "bg-cyan-500/60"}`} />
            <span className="text-[9px] font-mono text-slate-400 font-bold uppercase">MIC LEVEL:</span>
          </div>
          <div className="flex-1 bg-slate-900 rounded-full h-2 overflow-hidden border border-slate-800">
            <div
              className={`h-full transition-all duration-75 rounded-full ${
                micVolume > 25
                  ? "bg-gradient-to-r from-emerald-500 via-teal-400 to-cyan-300"
                  : "bg-blue-600/70"
              }`}
              style={{ width: `${Math.max(6, micVolume)}%` }}
            />
          </div>
          <span className="text-[9px] font-mono text-cyan-300 font-semibold w-8 text-right">
            {micVolume}%
          </span>
        </div>

        {/* Big Push-To-Talk Microphone Trigger */}
        <button
          onClick={handleWakeVoice}
          title="Wake RALLY and start listening immediately (F9, Fn+F9, Ctrl+Space, or Ctrl+Shift+Space)"
          className={`w-full py-2.5 rounded-xl font-bold text-xs shadow-lg transition-all flex items-center justify-center gap-2 ${
            isListening
              ? "bg-gradient-to-r from-emerald-600 via-teal-600 to-cyan-600 text-white hover:brightness-110 animate-pulse"
              : "bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white"
          }`}
        >
          <span className="text-base">{isListening ? "🎙️" : "🔊"}</span>
          <span>{isListening ? "Listening... (Speak Now)" : "TALK NOW (F9 / Ctrl+Space)"}</span>
        </button>

        {/* Live Speech Recognition & Auto-Execution Notice */}
        {voiceStatusNotice && (
          <div className="bg-emerald-950/80 border border-emerald-500/40 rounded-lg px-2.5 py-1 text-[10px] text-emerald-200 flex items-center justify-between animate-pulse">
            <span className="truncate">{voiceStatusNotice}</span>
            <span className="text-[8px] bg-emerald-900/80 text-emerald-300 px-1 py-0.5 rounded font-mono">LIVE</span>
          </div>
        )}

        {/* Text Command Input Bar (Always-Available Alternative to Voice) */}
        <form onSubmit={handleSendSpokenUtterance} className="flex gap-1.5 pt-1">
          <div className="relative flex-1">
            <input
              value={voicePhraseInput}
              onChange={(e) => setVoicePhraseInput(e.target.value)}
              placeholder={isListeningLive ? "Listening live... speak now..." : "Speak command or type prompt..."}
              className={`w-full bg-slate-950 border outline-none rounded-xl px-3 py-1.5 text-xs text-white placeholder:text-slate-500 shadow-inner transition-all ${
                isListeningLive || liveTypingActive
                  ? "border-emerald-400 ring-1 ring-emerald-400/50 shadow-[0_0_12px_rgba(52,211,153,0.35)]"
                  : "border-slate-800 focus:border-cyan-400"
              }`}
            />
            {(isListeningLive || liveTypingActive) && (
              <span className="absolute right-2 top-2 text-[8px] text-emerald-400 font-mono flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-ping" />
                LIVE
              </span>
            )}
          </div>
          <button
            type="submit"
            disabled={isSubmitting}
            className="bg-cyan-600 hover:bg-cyan-500 disabled:bg-slate-800 px-3 py-1.5 rounded-xl text-xs font-bold text-slate-950 shadow transition-all"
          >
            {isSubmitting ? "..." : "Send"}
          </button>
        </form>

        {/* Microphone Selector & Status Footer */}
        <div className="flex items-center justify-between pt-1 text-[9px] text-slate-400 border-t border-slate-900">
          <div className="flex items-center gap-1.5 truncate max-w-[200px]">
            <span className={isListeningLive ? "text-emerald-400 animate-ping" : "text-emerald-400"}>●</span>
            <span className="truncate" title={voiceStatus?.selected_microphone || "Microphone Array"}>
              {voiceStatus?.selected_microphone || "Microphone Array (Realtek Audio)"}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setAutoExecuteEnabled(!autoExecuteEnabled)}
              title="Toggle automatic mission execution after speaking"
              className={`px-1.5 py-0.5 rounded text-[8px] font-mono border ${
                autoExecuteEnabled
                  ? "bg-emerald-950 text-emerald-300 border-emerald-700"
                  : "bg-slate-900 text-slate-400 border-slate-800"
              }`}
            >
              Auto-Exec: {autoExecuteEnabled ? "ON" : "OFF"}
            </button>
            <button
              onClick={() => rallyApi.testMicrophone().then((r) => setMicTestNote(r.message))}
              className="text-cyan-400 hover:underline font-medium"
            >
              {micTestNote ? "✓ Tested" : "Test Mic"}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#0a0d14] text-slate-100 flex flex-col">
      {/* Top Header with Always-On Voice Status Indicator & Emergency Stop */}
      <header className="border-b border-slate-800 bg-[#0f1420] px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold">RALLY <span className="text-blue-400 text-xs font-mono">2.0 VOICE</span></h1>
          <span className={`text-xs font-mono px-2.5 py-1 rounded ${statusBadgeColor(voiceStatus?.state || "PAUSED")}`}>
            Voice: {voiceStatus?.state || "PAUSED"} {voiceStatus?.in_conversation_window ? `(Multi-Turn Active: ${voiceStatus.conversation_Window_remaining_sec}s)` : ""}
          </span>
          <button
            onClick={handleWakeVoice}
            title="Wake RALLY & Talk Now (Ctrl+Shift+Space / F9)"
            className="text-xs px-3 py-1 rounded bg-blue-600 hover:bg-blue-500 text-white font-bold shadow flex items-center gap-1.5 animate-pulse"
          >
            <span className="h-2 w-2 rounded-full bg-emerald-300"></span>
            Talk Now (F9)
          </button>
          {voiceStatus?.listening_enabled ? (
            <button onClick={handlePauseVoice} className="text-xs px-3 py-1 rounded bg-amber-700 hover:bg-amber-600 font-semibold">
              Mute / Pause Mic
            </button>
          ) : (
            <button onClick={handleEnableVoice} className="text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 font-semibold">
              Enable "Hey RALLY" Listening
            </button>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button onClick={() => rallyApi.emergencyStop().then(() => refreshAll())} className="bg-red-600 hover:bg-red-500 text-white font-bold text-xs px-4 py-2 rounded shadow">
            STOP RALLY (Ctrl+Shift+X)
          </button>
        </div>
      </header>

      {errorBanner && (
        <div className="bg-red-950 border-b border-red-700 px-6 py-2 text-xs text-red-200 flex justify-between">
          <span>Error: {errorBanner}</span>
          <button onClick={() => setErrorBanner(null)} className="underline">Dismiss</button>
        </div>
      )}

      {/* Always-On Live Listening & Hotkey Banner */}
      <div className="bg-gradient-to-r from-blue-950/60 via-slate-900 to-indigo-950/60 border-b border-blue-900/40 px-6 py-2 flex flex-wrap items-center justify-between text-xs gap-3">
        <div className="flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full ${voiceStatus?.listening_enabled ? "bg-emerald-400 animate-ping" : "bg-amber-500"}`} />
          <span className="font-semibold text-slate-200">
            {voiceStatus?.listening_enabled ? "RALLY IS LIVE & LISTENING" : "RALLY VOICE PAUSED"}
          </span>
          <span className="text-slate-400">
            — Say <strong className="text-blue-300 font-mono">"Hey RALLY"</strong>, <strong className="text-blue-300 font-mono">"RALLY"</strong>, or <strong className="text-blue-300 font-mono">"Wake up"</strong>
          </span>
        </div>

        <div className="flex items-center gap-3 text-slate-300">
          <span className="text-[11px] text-slate-400 font-mono">
            Global Hotkeys: <kbd className="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-blue-300">Ctrl+Shift+Space</kbd> or <kbd className="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-blue-300">Ctrl+Shift+V</kbd> or <kbd className="px-1.5 py-0.5 rounded bg-slate-800 border border-slate-700 text-blue-300">F9</kbd>
          </span>
          <button
            onClick={() => {
              rallyApi.startVoiceListening().then(() => refreshAll());
              toggleBrowserSpeechRecognition(true);
            }}
            className="bg-blue-600 hover:bg-blue-500 text-white font-semibold px-2.5 py-1 rounded text-xs shadow flex items-center gap-1"
          >
            <span>🎙️</span>
            <span>Talk Now</span>
          </button>
        </div>
      </div>

      <nav className="border-b border-slate-800 bg-[#0d111a] px-6 flex gap-6 text-sm">
        {(
          [
            ["voice", `Voice & Wake-Word Companion (${voiceStatus?.state || "PAUSED"})`],
            ["missions", `A. Mission Control (${missions.length})`],
            ["workspace", `B. Live Workspace (${windows.length} Windows)`],
            ["build", "C. Build Supervisor"],
            ["approvals", `D. Approvals (${pendingApprovals.length})`],
            ["registry", "E. Agent & Model Registry"],
          ] as const
        ).map(([k, label]) => (
          <button key={k} onClick={() => setActiveTab(k)} className={`py-3 border-b-2 ${activeTab === k ? "border-blue-500 text-blue-400 font-semibold" : "border-transparent text-slate-400"}`}>
            {label}
          </button>
        ))}
      </nav>

      <main className="flex-1 p-6 grid grid-cols-12 gap-6">
        <section className="col-span-4 space-y-4">
          {/* Voice Command & Wake-Word Tester */}
          <form onSubmit={handleSendSpokenUtterance} className="bg-[#111726] border border-blue-800/60 rounded-lg p-4 flex flex-col gap-3">
            <div className="flex justify-between items-center">
              <label className="text-xs font-semibold uppercase text-blue-400">Wake-Word & Voice Utterance</label>
              <span className="text-[11px] font-mono text-slate-400">Wake: "{voiceStatus?.wake_word || "hey rally"}"</span>
            </div>
            <textarea
              rows={2}
              value={voicePhraseInput}
              onChange={(e) => setVoicePhraseInput(e.target.value)}
              placeholder={isListeningLive ? "Listening live to your voice... start speaking..." : "Say or type: 'Hey RALLY, search YouTube for Starboy by The Weeknd'"}
              className={`bg-slate-950 border rounded p-2.5 text-sm text-white outline-none transition-all ${
                isListeningLive || liveTypingActive
                  ? "border-emerald-400 ring-1 ring-emerald-400/50 shadow-[0_0_12px_rgba(52,211,153,0.3)]"
                  : "border-slate-800 focus:border-blue-400"
              }`}
            />
            {voiceStatusNotice && (
              <div className="bg-emerald-950/80 border border-emerald-500/40 rounded px-2.5 py-1 text-xs text-emerald-200 flex justify-between items-center">
                <span>{voiceStatusNotice}</span>
                <span className="text-[10px] font-mono text-emerald-400 font-bold">REALTIME</span>
              </div>
            )}
            <button
              type="submit"
              disabled={isSubmitting}
              className="bg-blue-600 hover:bg-blue-500 disabled:bg-blue-900 py-2 rounded text-xs font-semibold text-white"
            >
              {isSubmitting ? "Processing Voice Command..." : "Send Voice Utterance (With TTS Response)"}
            </button>
          </form>

          {/* Direct Mission Input */}
          <form onSubmit={handleCreateMission} className="bg-[#111726] border border-slate-800 rounded-lg p-4 flex flex-col gap-3">
            <label className="text-xs font-semibold uppercase text-slate-400">Direct Outcome Input</label>
            <textarea
              rows={2}
              value={objectiveInput}
              onChange={(e) => setObjectiveInput(e.target.value)}
              placeholder="e.g., Open Spotify."
              className="bg-slate-950 border border-slate-800 rounded p-2.5 text-sm text-white"
            />
            <button
              type="submit"
              disabled={isSubmitting}
              className="bg-emerald-700 hover:bg-emerald-600 py-2 rounded text-xs font-semibold text-white"
            >
              Plan & Execute Direct Mission
            </button>
          </form>

          <div className="bg-[#111726] border border-slate-800 rounded-lg p-4 space-y-2 max-h-[340px] overflow-y-auto">
            <h2 className="text-xs font-semibold uppercase text-slate-400">Mission Ledger ({missions.length})</h2>
            {missions.map((m) => (
              <div
                key={m.id}
                onClick={() => { setSelectedMissionId(m.id); setActiveTab("missions"); }}
                className={`p-3 rounded border cursor-pointer ${selectedMission?.id === m.id ? "border-blue-500 bg-blue-950/30" : "border-slate-800 bg-slate-950"}`}
              >
                <div className="flex justify-between items-center gap-2">
                  <span className="text-sm font-medium truncate">{m.title}</span>
                  <span className={`text-[11px] font-mono px-2 py-0.5 rounded ${statusBadgeColor(m.status)}`}>{m.status}</span>
                </div>
                <p className="text-xs text-slate-400 mt-1 truncate">{m.objective}</p>
              </div>
            ))}
          </div>
        </section>

        <section className="col-span-8 space-y-4">
          {activeTab === "voice" && (
            <div className="bg-[#111726] border border-slate-800 rounded-lg p-5 space-y-5">
              <div className="flex justify-between items-center border-b border-slate-800 pb-4">
                <div>
                  <h2 className="text-lg font-bold">Always-On Voice Companion & Wake-Word Engine</h2>
                  <p className="text-xs text-slate-400">
                    State: <span className="text-blue-400 font-mono font-bold">{voiceStatus?.state}</span> | Action: {voiceStatus?.current_action}
                  </p>
                </div>
                <div className="flex gap-2">
                  <button onClick={handleEnableVoice} className="bg-emerald-600 hover:bg-emerald-500 px-3 py-1.5 rounded text-xs font-bold">
                    Enable / Resume Listening
                  </button>
                  <button onClick={handlePauseVoice} className="bg-amber-600 hover:bg-amber-500 px-3 py-1.5 rounded text-xs font-bold">
                    Pause Listening
                  </button>
                  <button
                    onClick={() => rallyApi.testMicrophone().then((r) => setMicTestNote(r.message))}
                    className="bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded text-xs font-semibold"
                  >
                    Test Microphone
                  </button>
                </div>
              </div>

              {micTestNote && (
                <div className="p-2.5 rounded bg-emerald-950/60 border border-emerald-700 text-xs text-emerald-200">
                  {micTestNote}
                </div>
              )}

              {/* Voice Configuration Controls (Section 8) */}
              <div className="grid grid-cols-3 gap-4 bg-slate-950 border border-slate-800 rounded p-4 text-xs">
                <div>
                  <label className="block text-slate-400 mb-1">Microphone Device</label>
                  <select
                    value={voiceStatus?.selected_mic_index ?? ""}
                    onChange={(e) => {
                      const idx = e.target.value === "" ? null : Number(e.target.value);
                      const found = microphones.find((m) => m.index === idx);
                      rallyApi.updateVoiceConfig({ mic_index: idx, mic_name: found?.name || "Default" }).then(() => refreshAll());
                    }}
                    className="w-full bg-slate-900 border border-slate-700 rounded p-1.5 text-slate-200"
                  >
                    {microphones.map((m, i) => (
                      <option key={i} value={m.index ?? ""}>{m.name}</option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">Wake Phrase</label>
                  <div className="flex gap-1">
                    <input
                      value={wakeWordInput}
                      onChange={(e) => setWakeWordInput(e.target.value)}
                      className="flex-1 bg-slate-900 border border-slate-700 rounded px-2 py-1"
                    />
                    <button
                      onClick={() => rallyApi.updateVoiceConfig({ wake_word: wakeWordInput }).then(() => refreshAll())}
                      className="bg-blue-600 px-2.5 py-1 rounded font-semibold"
                    >
                      Save
                    </button>
                  </div>
                </div>

                <div>
                  <label className="block text-slate-400 mb-1">TTS Voice Volume ({voiceStatus?.volume ?? 85}%)</label>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={voiceStatus?.volume ?? 85}
                    onChange={(e) => rallyApi.updateVoiceConfig({ volume: Number(e.target.value) }).then(() => refreshAll())}
                    className="w-full"
                  />
                  <label className="flex items-center gap-2 mt-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={Boolean(voiceStatus?.windows_startup_enabled)}
                      onChange={(e) => rallyApi.updateVoiceConfig({ windows_startup: e.target.checked }).then(() => refreshAll())}
                    />
                    <span>Start RALLY when Windows starts</span>
                  </label>
                </div>
              </div>

              {/* Current Recognized Instruction & Structured Task (Section 5) */}
              <div className="grid grid-cols-2 gap-4">
                <div className="bg-slate-950 border border-slate-800 rounded p-3 text-xs space-y-1">
                  <div className="text-slate-400 uppercase font-semibold">Latest Recognized Instruction</div>
                  <div className="text-sm font-medium text-white">{voiceStatus?.last_transcript || "None yet"}</div>
                  <div className="text-slate-400 uppercase font-semibold pt-2">Latest Spoken Response (TTS)</div>
                  <div className="text-sm font-medium text-emerald-400">{voiceStatus?.last_spoken_response || "None yet"}</div>
                </div>

                <div className="bg-slate-950 border border-slate-800 rounded p-3 text-xs">
                  <div className="text-slate-400 uppercase font-semibold mb-1">Intelligent Command Routing JSON</div>
                  <pre className="text-[11px] text-blue-300 overflow-x-auto">
                    {JSON.stringify(voiceStatus?.current_structured_task || { status: "waiting_for_command" }, null, 2)}
                  </pre>
                </div>
              </div>

              {/* Voice Session History */}
              <div className="space-y-2">
                <h3 className="text-xs font-semibold uppercase text-slate-400">
                  Conversational Voice Session History ({voiceStatus?.history?.length || 0})
                </h3>
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {(voiceStatus?.history || []).map((h: any) => (
                    <div key={h.id} className="p-3 rounded bg-slate-950 border border-slate-800 text-xs space-y-1">
                      <div className="flex justify-between">
                        <span className="font-bold text-blue-300">USER: "{h.transcript}"</span>
                        <span className="text-slate-500 font-mono">{new Date(h.timestamp).toLocaleTimeString()}</span>
                      </div>
                      <div className="text-emerald-300 font-medium">RALLY: "{h.spoken_response}"</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {activeTab === "missions" && selectedMission && (
            <div className="bg-[#111726] border border-slate-800 rounded-lg p-5 space-y-4">
              <div className="flex justify-between items-center border-b border-slate-800 pb-3">
                <div>
                  <h2 className="text-lg font-bold">{selectedMission.title}</h2>
                  <p className="text-xs text-slate-400">{selectedMission.objective}</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={() => rallyApi.startMission(selectedMission.id).then(() => refreshAll())} className="bg-emerald-700 hover:bg-emerald-600 px-3 py-1.5 rounded text-xs font-semibold">Re-Run / Resume</button>
                  <button onClick={() => rallyApi.pauseMission(selectedMission.id).then(() => refreshAll())} className="bg-amber-700 hover:bg-amber-600 px-3 py-1.5 rounded text-xs font-semibold">Pause</button>
                  <button onClick={() => rallyApi.cancelMission(selectedMission.id).then(() => refreshAll())} className="bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded text-xs font-semibold">Cancel</button>
                </div>
              </div>

              <div className="space-y-2">
                <h3 className="text-xs font-semibold uppercase text-slate-400">Observe → Act → Verify Step Graph</h3>
                {selectedMission.steps.map((s) => (
                  <div key={s.id} className="p-3 rounded bg-slate-950 border border-slate-800 flex justify-between items-center text-xs">
                    <div>
                      <span className="font-bold text-slate-100">#{s.step_order + 1} {s.title}</span>
                      <p className="text-slate-400 mt-0.5">{s.description}</p>
                    </div>
                    <span className={`font-mono px-2.5 py-1 rounded ${statusBadgeColor(s.status)}`}>{s.status}</span>
                  </div>
                ))}
              </div>

              <div className="space-y-2">
                <h3 className="text-xs font-semibold uppercase text-slate-400">Real-Time Execution Logs ({missionEvents.length})</h3>
                <div className="bg-slate-950 border border-slate-800 rounded p-3 max-h-48 overflow-y-auto space-y-1 font-mono text-xs">
                  {missionEvents.map((e) => (
                    <div key={e.event_id} className="text-slate-300">
                      <span className="text-slate-500">[{new Date(e.timestamp).toLocaleTimeString()}]</span>{" "}
                      <span className="text-blue-400">{e.type}</span>:{" "}
                      {e.payload?.message || e.payload?.summary || e.payload?.reason || JSON.stringify(e.payload)}
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-2">
                <h3 className="text-xs font-semibold uppercase text-slate-400">Independent Verification Evidence ({evidence.length})</h3>
                {evidence.map((ev) => (
                  <div key={ev.id} className="p-3 rounded bg-slate-950 border border-slate-800 space-y-2 text-xs">
                    <div className="flex justify-between items-center">
                      <span className="font-mono text-emerald-400 font-semibold">{ev.evidence_type}</span>
                      <span className="text-slate-500 font-mono text-[10px]">{new Date(ev.created_at).toLocaleTimeString()}</span>
                    </div>
                    {ev.local_path && ev.evidence_type === "screenshot" && (
                      <div className="mt-1">
                        <img
                          src={`${API_BASE}/evidence/${ev.id}/artifact`}
                          alt="Verification Screenshot"
                          className="max-h-56 rounded border border-slate-800 hover:border-blue-500 cursor-pointer object-cover shadow"
                          onClick={() => window.open(`${API_BASE}/evidence/${ev.id}/artifact`, "_blank")}
                        />
                      </div>
                    )}
                    <pre className="text-[11px] text-emerald-300 overflow-x-auto bg-slate-900/60 p-2 rounded">
                      {JSON.stringify(ev.metadata || ev, null, 2)}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === "workspace" && (
            <div className="bg-[#111726] border border-slate-800 rounded-lg p-5 space-y-4">
              <h2 className="text-base font-bold">Observed Windows Desktop State ({windows.length} Active Windows)</h2>
              <div className="bg-slate-950 border border-slate-800 rounded p-3 max-h-96 overflow-y-auto space-y-1">
                {windows.map((w) => (
                  <div key={w.window_id} className="text-xs py-1.5 border-b border-slate-900 flex justify-between">
                    <span>[{w.process_id}] {w.title} ({w.process_name})</span>
                    <span className="text-slate-500 font-mono">HWND: {w.window_id}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {activeTab === "build" && selectedMission && (
            <div className="bg-[#111726] border border-slate-800 rounded-lg p-5 space-y-4">
              <div className="flex justify-between">
                <h2 className="text-base font-bold">Build Supervisor</h2>
                <div className="flex gap-2">
                  <button onClick={() => rallyApi.runBuildCheck(selectedMission.id, "test").then(() => refreshAll())} className="bg-blue-600 px-3 py-1 rounded text-xs">Run Test Check</button>
                  <button onClick={() => rallyApi.requestCorrection(selectedMission.id).then(() => refreshAll())} className="bg-amber-600 px-3 py-1 rounded text-xs">Dispatch Correction</button>
                </div>
              </div>
              {defects.map((d) => (
                <pre key={d.id} className="p-3 rounded bg-slate-950 border border-red-800 text-xs">{d.title}\n{d.corrective_prompt}</pre>
              ))}
              {buildReport && <pre className="p-3 rounded bg-slate-950 border border-slate-800 text-xs">{JSON.stringify(buildReport, null, 2)}</pre>}
            </div>
          )}

          {activeTab === "approvals" && (
            <div className="bg-[#111726] border border-slate-800 rounded-lg p-5 space-y-3">
              {approvals.map((apr) => (
                <div key={apr.id} className="p-3 rounded bg-slate-950 border border-slate-800 flex justify-between items-center text-xs">
                  <div>
                    <div className="font-bold text-amber-300">{apr.action_summary}</div>
                    <div>Target: {apr.target_summary} | {apr.consequence_summary}</div>
                  </div>
                  {apr.status === "pending" ? (
                    <div className="flex gap-2">
                      <button onClick={() => rallyApi.approveRequest(apr.id).then(() => refreshAll())} className="bg-emerald-600 px-3 py-1 rounded font-bold">Approve Once</button>
                      <button onClick={() => rallyApi.rejectRequest(apr.id).then(() => refreshAll())} className="bg-red-700 px-3 py-1 rounded font-bold">Reject</button>
                    </div>
                  ) : (
                    <span>{apr.status}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {activeTab === "registry" && (
            <div className="bg-[#111726] border border-slate-800 rounded-lg p-5 space-y-3">
              <h2 className="text-sm font-bold">Reasoning Providers & Coding Agents</h2>
              {providers.map((p) => (
                <div key={p.provider_id} className="p-2 rounded bg-slate-950 border border-slate-800 text-xs flex justify-between">
                  <span>{p.display_name} ({p.model_name})</span>
                  <span>{p.billing_mode} — {p.status_message}</span>
                </div>
              ))}
              {codingAgents.map((a) => (
                <div key={a.agent_id} className="p-2 rounded bg-slate-950 border border-slate-800 text-xs flex justify-between">
                  <span>{a.display_name}</span>
                  <span>{a.allowed_in_zero_cost ? "Permitted (Zero-Cost)" : "Blocked (Paid)"}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}