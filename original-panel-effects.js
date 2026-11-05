/* Original HALOFLUX panel art. Shown only in explicitly labelled visual mode. */
function drawOriginalDiff() {
  const x = cDiff.x, w = cDiff.w, h = cDiff.h;
  x.clearRect(0, 0, w, h);
  x.font = `600 7px ${FONT}`; x.textBaseline = 'top';
  const gap = 8, mw = (w - gap) / 2, mh = h * 0.68, cols = 22, rows = 13, cw = mw / cols, ch = (mh - 10) / rows;
  const hist = new Array(28).fill(0);
  for (let m = 0; m < 2; m++) {
    const ox = m * (mw + gap);
    x.fillStyle = '#6b6f78'; x.fillText(m ? '原版动效' : '视觉参考', ox, 0);
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      let v = 0.5 + 0.25 * Math.sin(i * 0.45 + T * 0.8) * Math.cos(j * 0.5 - T * 0.5) + 0.2 * Math.sin((i + j) * 0.3 + T);
      let err = 0;
      if (m) { const dx = i - 15 - Math.sin(T * 0.7) * 3, dy = j - 6; err = Math.exp(-(dx * dx + dy * dy) / 6) * (0.5 + 0.5 * Math.sin(T * 2)); v += err * 0.3; hist[Math.min(27, (err * 27) | 0)]++; }
      const g = Math.max(0, Math.min(255, v * 210 + 20)) | 0;
      x.fillStyle = err > 0.35 ? `rgba(240,166,198,${0.5 + err * 0.5})` : `rgb(${g},${g},${g + 4})`;
      x.fillRect(ox + i * cw, 10 + j * ch, cw - 0.6, ch - 0.6);
    }
  }
  const by = mh + 4, bh = h - by, bw = w / hist.length;
  const mx = Math.max(...hist.slice(1), 1);
  hist.forEach((v, i) => {
    const hh = i === 0 ? bh * 0.9 : (v / mx) * bh * 0.8 + 1;
    x.fillStyle = i > 9 ? '#f0a6c6' : '#8a8e97';
    x.fillRect(i * bw, by + bh - hh, bw - 1, hh);
  });
}


const ORIGINAL_PANEL_IDX = Array.from({ length: 11 }, () => Array.from({ length: 28 }, () => rnd()));
function drawOriginalIdx() {
  const x = cIdx.x, w = cIdx.w, h = cIdx.h;
  x.clearRect(0, 0, w, h);
  x.font = `600 6.5px ${FONT}`; x.textBaseline = 'middle';
  const lx = 24, rx = 34, rows = 11, cols = 28, cw = (w - lx - rx) / cols, ch = h / rows;
  const scan = Math.floor((T * 6) % cols);
  if (rnd() < 0.3) { const r = (rnd() * rows) | 0, c = (rnd() * cols) | 0; ORIGINAL_PANEL_IDX[r][c] = rnd(); }
  for (let j = 0; j < rows; j++) {
    x.fillStyle = '#6b6f78'; x.fillText('M' + String(j + 1).padStart(2, '0'), 0, j * ch + ch / 2);
    let match = 0;
    for (let i = 0; i < cols; i++) {
      const v = ORIGINAL_PANEL_IDX[j][i];
      x.fillStyle = v > 0.86 ? '#f0a6c6' : v > 0.7 ? 'rgba(240,166,198,.45)' : v > 0.4 ? '#4a4e56' : '#1c1e23';
      if (i === scan) x.fillStyle = v > 0.4 ? '#ffffff' : '#3a3e46';
      x.fillRect(lx + i * cw, j * ch + 1, cw - 1, ch - 2);
      if (v > 0.7) match++;
    }
    x.fillStyle = match > 7 ? '#f0a6c6' : '#9a9ea7';
    x.fillText(match > 7 ? '漂移' : '扫描', w - rx + 4, j * ch + ch / 2);
  }
}
