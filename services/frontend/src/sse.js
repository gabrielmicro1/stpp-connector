// Minimal SSE parser over a fetch ReadableStream (EventSource can't set the
// Authorization header, so we read the POST /v1/query response body directly).
// Emits every frame, pings included; the caller decides what to do with them.
export async function parseSSEStream(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";

  const emit = (frame) => {
    let event = "message";
    const dataLines = [];
    for (const line of frame.split(/\r?\n/)) {
      if (line.startsWith(":")) continue; // SSE comment
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    const raw = dataLines.join("\n");
    let data = null;
    try {
      data = raw ? JSON.parse(raw) : null;
    } catch {
      // unparseable payload: surfaced to the caller via raw, data stays null
    }
    onEvent({ event, data, raw });
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let m;
    while ((m = buf.match(/\r?\n\r?\n/))) {
      const frame = buf.slice(0, m.index);
      buf = buf.slice(m.index + m[0].length);
      if (frame.trim()) emit(frame);
    }
  }
  buf += decoder.decode();
  if (buf.trim()) emit(buf);
}
