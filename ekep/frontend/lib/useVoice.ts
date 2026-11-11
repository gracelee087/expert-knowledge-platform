"use client";
/**
 * Voice-Schicht (austauschbar über VOICE_ENGINE):
 *
 *  engine = "openai"  → Aufnahme per MediaRecorder, STT/TTS serverseitig (OpenAI/Azure)
 *  engine = "browser" → STT per Web Speech API (Chrome/Edge), TTS per speechSynthesis
 *                       – benötigt nur ein Chat-Modell (z. B. gpt-4o) im Backend
 *
 * Gemeinsam: Voice Activity Detection (RMS, adaptiver Rauschpegel), Barge-in,
 * konfigurierbare Pausentoleranz, Fehlerzustände. Fällt serverseitiges TTS aus,
 * wird automatisch auf die Browser-Sprachausgabe gewechselt.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export type MicState = "off" | "idle" | "listening" | "recording" | "processing" | "ai_speaking" | "error";
export type VoiceEngine = "openai" | "browser";

interface Options {
  /** Spracherkennung: "browser" = Web Speech API, sonst serverseitig (OpenAI/Gemini) */
  engine: VoiceEngine;
  /** Sprachausgabe: "browser" = lokale Systemstimme (offline), "server" = /api/tts */
  tts?: "browser" | "server";
  /** KI darf durch Sprechen unterbrochen werden (nur mit Headset sinnvoll, sonst Echo) */
  bargeIn?: boolean;
  onUtterance: (blob: Blob, durationS: number, ext: string) => void; // engine=openai
  onText: (text: string, durationS: number) => void; // engine=browser
  silenceMs?: number;
}

function pickMime(): { mime: string; ext: string } {
  const cands = [
    { mime: "audio/webm;codecs=opus", ext: "webm" },
    { mime: "audio/webm", ext: "webm" },
    { mime: "audio/mp4", ext: "mp4" },
    { mime: "audio/ogg;codecs=opus", ext: "ogg" },
  ];
  for (const c of cands) if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(c.mime)) return c;
  return { mime: "", ext: "webm" };
}

/** Aufnahme (WebM/Opus) → WAV 16 kHz mono PCM16 – von Gemini und Whisper gleichermaßen unterstützt. */
export async function toWav16k(blob: Blob): Promise<Blob> {
  const buf = await blob.arrayBuffer();
  const ctx = new AudioContext();
  try {
    const decoded = await ctx.decodeAudioData(buf);
    const rate = 16000;
    const off = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
    const src = off.createBufferSource();
    src.buffer = decoded;
    src.connect(off.destination);
    src.start();
    const pcm = (await off.startRendering()).getChannelData(0);
    const out = new DataView(new ArrayBuffer(44 + pcm.length * 2));
    const w = (o: number, s: string) => [...s].forEach((c, i) => out.setUint8(o + i, c.charCodeAt(0)));
    w(0, "RIFF"); out.setUint32(4, 36 + pcm.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
    out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
    out.setUint32(24, rate, true); out.setUint32(28, rate * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
    w(36, "data"); out.setUint32(40, pcm.length * 2, true);
    for (let i = 0; i < pcm.length; i++) out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, pcm[i])) * 0x7fff, true);
    return new Blob([out.buffer], { type: "audio/wav" });
  } finally {
    ctx.close().catch(() => {});
  }
}

export function browserSttSupported() {
  return typeof window !== "undefined" && !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);
}

function germanVoice(): SpeechSynthesisVoice | undefined {
  const vs = window.speechSynthesis?.getVoices() || [];
  return (
    vs.find((v) => v.lang?.startsWith("de") && /natural|online|neural|google/i.test(v.name)) ||
    vs.find((v) => v.lang?.startsWith("de"))
  );
}

export function useVoice({ engine, tts = "server", bargeIn = false, onUtterance, onText, silenceMs = 1800 }: Options) {
  const [state, setState] = useState<MicState>("off");
  const [level, setLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [interim, setInterim] = useState("");

  const stateRef = useRef<MicState>("off");
  const engineRef = useRef(engine);
  engineRef.current = engine;
  const ttsRef = useRef(tts);
  ttsRef.current = tts;
  const bargeInRef = useRef(bargeIn);
  bargeInRef.current = bargeIn;
  /** Bis zu diesem Zeitpunkt wird Mikrofon-Input ignoriert (Nachhall der KI-Stimme). */
  const guardUntilRef = useRef(0);
  const noiseBeforeSpeakRef = useRef(0.01);
  const uttRef = useRef<SpeechSynthesisUtterance | null>(null); // gegen Chrome-GC-Bug
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number>(0);
  const recRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const noiseRef = useRef(0.01);
  const speechSinceRef = useRef<number | null>(null);
  const lastVoiceRef = useRef(0);
  const recStartRef = useRef(0);
  const silenceRef = useRef(silenceMs);
  silenceRef.current = silenceMs;
  const onUttRef = useRef(onUtterance);
  onUttRef.current = onUtterance;
  const onTextRef = useRef(onText);
  onTextRef.current = onText;

  // Web Speech API
  const srRef = useRef<any>(null);
  const srFinalRef = useRef("");
  const srInterimRef = useRef("");
  const srLastResultRef = useRef(0);
  const srWantedRef = useRef(false);

  const loopRef = useRef<() => void>(() => {});
  const set = (s: MicState) => {
    stateRef.current = s;
    setState(s);
  };

  // ---------------------------------------------------------------- Browser-STT
  const stopRecognition = useCallback(() => {
    srWantedRef.current = false;
    try { srRef.current?.abort(); } catch {}
    srRef.current = null;
  }, []);

  const startRecognition = useCallback(() => {
    if (srRef.current) return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setError("Spracherkennung im Browser nicht verfügbar – bitte Chrome oder Edge nutzen oder tippen.");
      return;
    }
    const sr = new SR();
    sr.lang = "de-DE";
    sr.continuous = true;
    sr.interimResults = true;
    sr.onresult = (e: any) => {
      let interimText = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) srFinalRef.current += (srFinalRef.current ? " " : "") + r[0].transcript.trim();
        else interimText += r[0].transcript;
      }
      srInterimRef.current = interimText.trim();
      srLastResultRef.current = performance.now();
      setInterim(`${srFinalRef.current} ${srInterimRef.current}`.trim());
      if (stateRef.current === "listening" || stateRef.current === "ai_speaking") {
        audioRef.current?.pause();
        window.speechSynthesis?.cancel();
        recStartRef.current = performance.now();
        set("recording");
      }
    };
    sr.onerror = (e: any) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setError("Spracherkennung wurde vom Browser blockiert.");
        srWantedRef.current = false;
      } else if (e.error === "network") {
        setError("Spracherkennung offline – bitte Antwort eintippen.");
      }
    };
    sr.onend = () => {
      srRef.current = null;
      // Chrome beendet die Erkennung nach Stille/Zeitlimit → bei Bedarf neu starten
      if (srWantedRef.current && (stateRef.current === "listening" || stateRef.current === "recording")) {
        setTimeout(() => srWantedRef.current && startRecognition(), 150);
      }
    };
    srRef.current = sr;
    srWantedRef.current = true;
    try { sr.start(); } catch {}
  }, []);

  const finalizeBrowserUtterance = useCallback(() => {
    const text = `${srFinalRef.current} ${srInterimRef.current}`.trim();
    const duration = (performance.now() - recStartRef.current) / 1000;
    stopRecognition();
    srFinalRef.current = "";
    srInterimRef.current = "";
    setInterim("");
    if (text.length >= 2) {
      set("processing");
      onTextRef.current(text, Math.max(0.5, duration - silenceRef.current / 1000));
    } else if (stateRef.current !== "off") {
      set("listening");
      startRecognition();
    }
  }, [startRecognition, stopRecognition]);

  // ---------------------------------------------------------------- Server-STT
  const stopRecorder = useCallback((send: boolean) => {
    const rec = recRef.current;
    if (!rec) return;
    recRef.current = null;
    const duration = (performance.now() - recStartRef.current) / 1000;
    rec.onstop = () => {
      const { ext } = pickMime();
      const blob = new Blob(chunksRef.current, { type: rec.mimeType || "audio/webm" });
      chunksRef.current = [];
      if (send && blob.size > 2000) {
        set("processing");
        toWav16k(blob)
          .then((wav) => onUttRef.current(wav, Math.max(0.5, duration), "wav"))
          .catch(() => onUttRef.current(blob, Math.max(0.5, duration), ext));
      } else if (stateRef.current !== "off") set("listening");
    };
    if (rec.state !== "inactive") rec.stop();
  }, []);

  const startRecorder = useCallback(() => {
    if (!streamRef.current || recRef.current) return;
    const { mime } = pickMime();
    const rec = new MediaRecorder(streamRef.current, mime ? { mimeType: mime } : undefined);
    chunksRef.current = [];
    rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
    rec.start(250);
    recRef.current = rec;
    recStartRef.current = performance.now();
    lastVoiceRef.current = performance.now();
    set("recording");
  }, []);

  const stopSpeaking = () => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (typeof window !== "undefined") window.speechSynthesis?.cancel();
  };

  // ---------------------------------------------------------------- VAD-Schleife
  const loop = useCallback(() => {
    const an = analyserRef.current;
    if (!an) return;
    const buf = new Float32Array(an.fftSize);
    an.getFloatTimeDomainData(buf);
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    setLevel(rms);
    const now = performance.now();
    const s = stateRef.current;
    const guarded = now < guardUntilRef.current;
    // Schwelle begrenzt: kann nicht "weglaufen" (sonst wird leise Sprache irgendwann ignoriert)
    const threshold = s === "ai_speaking" ? 0.08 : Math.min(0.045, Math.max(0.014, noiseRef.current * 3));
    const voiced = !guarded && rms > threshold;
    // Rauschpegel: sinkt schnell, steigt nur sehr langsam, gedeckelt; nie während die KI spricht
    if (!voiced && !guarded && (s === "listening" || s === "idle")) {
      const n = noiseRef.current;
      noiseRef.current = Math.min(0.012, rms < n ? n * 0.9 + rms * 0.1 : n * 0.997 + rms * 0.003);
    }
    if (voiced) {
      lastVoiceRef.current = now;
      speechSinceRef.current ??= now;
    } else if (now - lastVoiceRef.current > 200) speechSinceRef.current = null;
    const sustained = speechSinceRef.current !== null && now - speechSinceRef.current > (s === "ai_speaking" ? 350 : 180);
    const browser = engineRef.current === "browser";

    if (s === "ai_speaking" && sustained && bargeInRef.current) {
      stopSpeaking(); // Barge-in (nur wenn erlaubt)
      if (browser) {
        set("listening");
        startRecognition();
      } else startRecorder();
    } else if (s === "listening" && sustained && !browser) {
      startRecorder();
    } else if (s === "recording") {
      if (browser) {
        const lastActivity = Math.max(srLastResultRef.current, lastVoiceRef.current);
        if (now - lastActivity > silenceRef.current) finalizeBrowserUtterance();
      } else if (now - lastVoiceRef.current > silenceRef.current || now - recStartRef.current > 180000) {
        stopRecorder(true);
      }
    }
  }, [startRecorder, stopRecorder, startRecognition, finalizeBrowserUtterance]);

  loopRef.current = loop;

  // ---------------------------------------------------------------- öffentliche API
  const enable = useCallback(async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      streamRef.current = stream;
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      ctxRef.current = ctx;
      analyserRef.current = an;
      stream.getAudioTracks()[0].onended = () => {
        setError("Mikrofon wurde getrennt.");
        set("error");
      };
      window.speechSynthesis?.getVoices(); // Stimmen vorladen
      if (engineRef.current === "browser" && !browserSttSupported()) {
        setError("Dieser Browser unterstützt keine Spracherkennung – bitte Chrome oder Edge verwenden oder tippen.");
      }
      set("idle");
      clearInterval(rafRef.current);
      rafRef.current = window.setInterval(() => loopRef.current(), 50);
      return true;
    } catch (e: any) {
      setError(e?.name === "NotAllowedError" ? "Mikrofonzugriff verweigert. Bitte im Browser erlauben." : "Kein Mikrofon verfügbar.");
      set("error");
      return false;
    }
  }, [loop]);

  const disable = useCallback(() => {
    clearInterval(rafRef.current);
    stopRecorder(false);
    stopRecognition();
    stopSpeaking();
    streamRef.current?.getTracks().forEach((t) => t.stop());
    ctxRef.current?.close().catch(() => {});
    streamRef.current = null;
    analyserRef.current = null;
    setInterim("");
    set("off");
    setLevel(0);
  }, [stopRecorder, stopRecognition]);

  const listen = useCallback(() => {
    if (stateRef.current === "off" || stateRef.current === "error") return;
    speechSinceRef.current = null;
    // Nach KI-Ausgabe: kurze Schutzpause gegen Nachhall, Rauschpegel auf Stand vor der Ausgabe
    if (stateRef.current === "ai_speaking") {
      stopSpeaking(); // auch für "KI überspringen" per Mikrofon-Klick
      guardUntilRef.current = performance.now() + 700;
      noiseRef.current = noiseBeforeSpeakRef.current;
    }
    set("listening");
    if (engineRef.current === "browser") {
      srFinalRef.current = "";
      srInterimRef.current = "";
      setInterim("");
      startRecognition();
    }
  }, [startRecognition]);

  const hold = useCallback(() => {
    if (stateRef.current === "off") return;
    stopRecorder(false);
    stopRecognition();
    stopSpeaking();
    setInterim("");
    set("idle");
  }, [stopRecorder, stopRecognition]);

  /** Aufnahme sofort starten (falls die automatische Erkennung nicht anspringt). */
  const startNow = useCallback(() => {
    if (stateRef.current === "off" || stateRef.current === "error") return;
    stopSpeaking();
    guardUntilRef.current = 0;
    lastVoiceRef.current = performance.now() + 1500; // 1,5 s Schonfrist bis zur Pausenerkennung
    if (engineRef.current === "browser") {
      recStartRef.current = performance.now();
      srLastResultRef.current = performance.now() + 1500;
      set("recording");
      startRecognition();
    } else {
      startRecorder();
      lastVoiceRef.current = performance.now() + 1500;
    }
  }, [startRecorder, startRecognition]);

  const finishNow = useCallback(() => {
    if (engineRef.current === "browser") finalizeBrowserUtterance();
    else stopRecorder(true);
  }, [finalizeBrowserUtterance, stopRecorder]);

  /**
   * Browser-Sprachausgabe mit Schutz gegen bekannte Chrome-Fehler:
   * – Utterance bleibt referenziert (sonst feuert onend nie)
   * – kein cancel() direkt vor speak(), wenn nichts läuft (verwirft sonst die Ausgabe)
   * – pause/resume-Keepalive (Chrome bricht lange Ausgaben nach ~15 s ab)
   * – Watchdog: spätestens nach geschätzter Sprechdauer geht es weiter
   */
  const speakWithBrowser = useCallback(
    (text: string) =>
      new Promise<void>((resolve) => {
        const synth = window.speechSynthesis;
        if (!synth) return resolve();
        let done = false;
        let keepAlive: ReturnType<typeof setInterval> | undefined;
        let watchdog: ReturnType<typeof setTimeout> | undefined;
        const finish = () => {
          if (done) return;
          done = true;
          clearInterval(keepAlive);
          clearTimeout(watchdog);
          uttRef.current = null;
          resolve();
        };
        const start = () => {
          const u = new SpeechSynthesisUtterance(text);
          u.lang = "de-DE";
          const v = germanVoice();
          if (v) u.voice = v;
          u.rate = 1.02;
          u.onend = finish;
          u.onerror = finish;
          uttRef.current = u;
          synth.speak(u);
          keepAlive = setInterval(() => {
            if (!synth.speaking) return;
            synth.pause();
            synth.resume();
          }, 10000);
          const started = performance.now();
          const check = () => {
            // erst weiter, wenn die Stimme wirklich fertig ist (max. 90 s)
            if (synth.speaking && performance.now() - started < 90000) watchdog = setTimeout(check, 500);
            else finish();
          };
          watchdog = setTimeout(check, 4000 + text.length * 85);
        };
        if (synth.speaking || synth.pending) {
          synth.cancel();
          setTimeout(start, 120);
        } else start();
      }),
    []
  )

  const speak = useCallback(
    async (text: string) => {
      if (stateRef.current === "off" || stateRef.current === "error") return;
      stopRecognition(); // KI-Stimme nicht selbst transkribieren
      stopRecorder(false); // keine laufende Aufnahme während die KI spricht
      noiseBeforeSpeakRef.current = noiseRef.current;
      set("ai_speaking");
      if (ttsRef.current === "browser") {
        await speakWithBrowser(text);
        if (stateRef.current === "ai_speaking") listen();
        return;
      }
      try {
        const blob = await api.blob("/api/tts", { text });
        if (stateRef.current !== "ai_speaking") return;
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.onended = () => {
          URL.revokeObjectURL(url);
          if (audioRef.current === audio) {
            audioRef.current = null;
            listen();
          }
        };
        await audio.play();
      } catch {
        // Fallback: Browser-Sprachausgabe
        await speakWithBrowser(text);
        if (stateRef.current === "ai_speaking") listen();
      }
    },
    [listen, speakWithBrowser, stopRecognition]
  );

  useEffect(() => () => disable(), [disable]);

  return { state, level, error, interim, setError, enable, disable, listen, hold, speak, finishNow, startNow };
}
