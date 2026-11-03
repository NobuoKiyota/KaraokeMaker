// 公開サイトの Worker。ふつうのファイルは、そのまま静的アセットとして返す。
// 音源(audio/*.mp3)だけは、ここを通して「範囲指定の読み込み」(HTTP Range)に答える。
// 静的アセットはそれに答えないため、そのままだと、ブラウザが曲の途中へ移動(シーク)できない(ジャンプ・ループに必要)。
export default {
  async fetch(request, env) {
    const res = await env.ASSETS.fetch(request);
    const range = request.headers.get("Range");
    const headers = new Headers(res.headers);
    headers.set("Accept-Ranges", "bytes");
    if (!range || res.status !== 200) return new Response(res.body, { status: res.status, headers });

    const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim());
    if (!m || (m[1] === "" && m[2] === "")) return new Response(res.body, { status: 200, headers });
    const buf = await res.arrayBuffer();
    const size = buf.byteLength;
    let start, end;
    if (m[1] === "") { start = Math.max(0, size - Number(m[2])); end = size - 1; } // bytes=-500(終わりから)
    else { start = Number(m[1]); end = m[2] === "" ? size - 1 : Math.min(Number(m[2]), size - 1); }
    if (start >= size || start > end) {
      headers.set("Content-Range", `bytes */${size}`);
      return new Response(null, { status: 416, headers });
    }
    headers.set("Content-Range", `bytes ${start}-${end}/${size}`);
    headers.set("Content-Length", String(end - start + 1));
    return new Response(buf.slice(start, end + 1), { status: 206, headers });
  },
};
