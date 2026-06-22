/* AssemblyAI Voice Agent — speak scripted text (English TTS via reply.create) */

const ASSEMBLY_WS = "wss://agents.assemblyai.com/v1/ws";
const PCM_SAMPLE_RATE = 24000;

function decodePcmChunks(base64Chunks) {
  const parts = [];
  let total = 0;
  for (const chunk of base64Chunks) {
    const bin = atob(chunk);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    parts.push(bytes);
    total += bytes.length;
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const p of parts) {
    merged.set(p, offset);
    offset += p.length;
  }
  return new Int16Array(merged.buffer, merged.byteOffset, merged.byteLength / 2);
}

async function playPcm16Mono(samples, sampleRate) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate });
  if (ctx.state === "suspended") await ctx.resume();
  const buffer = ctx.createBuffer(1, samples.length, sampleRate);
  const ch = buffer.getChannelData(0);
  for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 32768;
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);
  return new Promise((resolve) => {
    src.onended = () => {
      try { ctx.close(); } catch (_) {}
      resolve();
    };
    src.start(0);
  });
}

/**
 * Speak text using AssemblyAI Voice Agent (ivy voice by default).
 * Returns a promise that resolves when playback finishes.
 */
async function assemblySpeak(text, options) {
  const opts = options || {};
  const voice = opts.voice || "ivy";
  const trimmed = String(text || "").trim().slice(0, 500);
  if (!trimmed) return;

  const tokenRes = await fetch("/api/voice_token");
  const tokenData = await tokenRes.json();
  if (!tokenRes.ok || !tokenData.token) {
    throw new Error(tokenData.error || "Could not get AssemblyAI token");
  }

  const wsUrl = `${ASSEMBLY_WS}?token=${encodeURIComponent(tokenData.token)}`;
  const audioChunks = [];

  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let settled = false;
    const fail = (err) => {
      if (settled) return;
      settled = true;
      try { ws.close(); } catch (_) {}
      reject(err);
    };
    const done = async () => {
      if (settled) return;
      settled = true;
      try {
        if (audioChunks.length) {
          const samples = decodePcmChunks(audioChunks);
          await playPcm16Mono(samples, PCM_SAMPLE_RATE);
        }
        resolve();
      } catch (e) {
        reject(e);
      }
    };

    const timer = setTimeout(() => fail(new Error("AssemblyAI speak timeout")), 50000);

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: "session.update",
          session: {
            system_prompt:
              "You are the voice of a friendly gift shopping assistant. " +
              "When you receive instructions, speak ONLY the exact text requested. " +
              "Do not add greetings, questions, commentary, or extra words.",
            output: { voice },
          },
        })
      );
    };

    ws.onmessage = (ev) => {
      try {
        const event = JSON.parse(ev.data);
        if (event.type === "session.ready") {
          ws.send(
            JSON.stringify({
              type: "reply.create",
              instructions:
                "Read the following text aloud exactly as written, naturally and warmly, " +
                "with no additions before or after:\n\n" + trimmed,
            })
          );
        } else if (event.type === "reply.audio" && event.data) {
          audioChunks.push(event.data);
        } else if (event.type === "reply.done") {
          clearTimeout(timer);
          try {
            ws.send(JSON.stringify({ type: "session.end" }));
          } catch (_) {}
          ws.close();
          done();
        } else if (event.type === "session.error") {
          clearTimeout(timer);
          fail(new Error(event.message || event.error || "AssemblyAI session error"));
        }
      } catch (e) {
        clearTimeout(timer);
        fail(e);
      }
    };

    ws.onerror = () => {
      clearTimeout(timer);
      fail(new Error("AssemblyAI WebSocket error"));
    };

    ws.onclose = () => {
      clearTimeout(timer);
      if (!settled && audioChunks.length) done();
      else if (!settled) fail(new Error("AssemblyAI connection closed"));
    };
  });
}

window.KaprukaAssemblySpeak = { assemblySpeak, PCM_SAMPLE_RATE };
