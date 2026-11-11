"use client";
import { useEffect, useRef, useState } from "react";
import { WS_URL } from "./api";

export type ConnState = "connecting" | "open" | "reconnecting" | "closed";

/** WebSocket mit Heartbeat, exponentiellem Backoff und Zustandssync nach Wiederverbindung. */
export function useInterviewSocket(interviewId: string, onEvent: (event: string, data: any) => void) {
  const [conn, setConn] = useState<ConnState>("connecting");
  const handler = useRef(onEvent);
  handler.current = onEvent;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let attempt = 0;
    let stopped = false;
    let ping: ReturnType<typeof setInterval> | undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      ws = new WebSocket(`${WS_URL}/ws/interviews/${interviewId}`);
      ws.onopen = () => {
        if (attempt > 0) ws?.send("sync");
        attempt = 0;
        setConn("open");
        ping = setInterval(() => ws?.readyState === 1 && ws.send("ping"), 20000);
      };
      ws.onmessage = (m) => {
        try {
          const { event, data } = JSON.parse(m.data);
          if (event !== "pong") handler.current(event, data);
        } catch {}
      };
      ws.onclose = () => {
        clearInterval(ping);
        if (stopped) return setConn("closed");
        setConn("reconnecting");
        attempt += 1;
        retry = setTimeout(connect, Math.min(10000, 500 * 2 ** attempt));
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    const onOnline = () => ws?.readyState !== 1 && connect();
    window.addEventListener("online", onOnline);
    return () => {
      stopped = true;
      clearInterval(ping);
      clearTimeout(retry);
      window.removeEventListener("online", onOnline);
      ws?.close();
    };
  }, [interviewId]);

  return conn;
}
