/* Taster deck · 幻灯片翻页逻辑（零依赖）
   ←/→/空格 翻页，Home/End 跳首尾，触屏左右滑。进度点可点。URL #/n 直达。 */
(function () {
  var D = window.DECK, E = document.getElementById("decks");
  if (!E) return;
  var slides = [].slice.call(E.children), cur = 0, hashHandled = false;

  function go(i) {
    cur = Math.max(0, Math.min(slides.length - 1, i));
    slides.forEach(function (s, k) { s.classList.toggle("on", k === cur); });
    [].forEach.call(document.querySelectorAll("#deck-dots button"), function (b, k) {
      b.classList.toggle("on", k === cur); b.setAttribute("aria-current", k === cur ? "true" : "false");
    });
    document.getElementById("deck-count").textContent = (cur + 1) + " / " + slides.length;
    if (!hashHandled) location.hash = "/" + (cur + 1);
    hashHandled = false;
    var h = slides[cur].querySelector("h2");
    if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
  }

  /* 进度点 */
  var dots = document.getElementById("deck-dots");
  slides.forEach(function (_, k) {
    var b = document.createElement("button");
    b.type = "button"; b.textContent = k + 1;
    b.setAttribute("aria-label", "第 " + (k + 1) + " 页");
    b.addEventListener("click", function () { go(k); });
    dots.appendChild(b);
  });

  /* 键盘 */
  document.addEventListener("keydown", function (e) {
    if (e.target.closest("input, textarea, select")) return;
    if (e.key === "ArrowRight" || e.key === " " || e.key === "PageDown") { e.preventDefault(); go(cur + 1); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); go(cur - 1); }
    else if (e.key === "Home") { e.preventDefault(); go(0); }
    else if (e.key === "End") { e.preventDefault(); go(slides.length - 1); }
  });

  /* 触屏滑动 */
  var x0 = null;
  document.addEventListener("touchstart", function (e) { x0 = e.touches[0].clientX; }, { passive: true });
  document.addEventListener("touchend", function (e) {
    if (x0 === null) return;
    var dx = e.changedTouches[0].clientX - x0; x0 = null;
    if (Math.abs(dx) > 48) go(cur + (dx < 0 ? 1 : -1));
  });

  /* URL hash */
  function fromHash() {
    var m = /^#\/(\d+)$/.exec(location.hash);
    if (m) { hashHandled = true; go(parseInt(m[1], 10) - 1); return true; }
    return false;
  }
  window.addEventListener("hashchange", fromHash);
  if (!fromHash()) go(0);

  /* 读数：有数据画图（心电图 + 感受色带），没数据保持「待读数」占位 */
  function drawCharts() {
    if (!D || !D.readings || !D.readings.length) {
      [].forEach.call(document.querySelectorAll("[data-slot]"), function (el) {
        el.textContent = "待读数";
      });
      return;
    }
    var FEELS = ["紧张", "好笑", "揪心", "好奇", "痛快", "平淡", "困惑"];

    /* 心电图：横轴 61 章，纵轴 0–100%，0.65/0.35 判定线（同 app.js PASS/FAIL），点=章 */
    var ecg = document.getElementById("ecg");
    if (ecg) {
      var W = 640, H = 200, L = 34, R = 8, T = 12, B = 26;
      var total = D.units_total || D.readings.length;
      var xs = function (n) { return L + (W - L - R) * ((n - 1) / Math.max(1, total - 1)); };
      var ys = function (p) { return T + (H - T - B) * (1 - p); };
      var s = ['<svg viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="想看下一章逐章概率图">',
        '<title>想看下一章 · 逐章原始概率</title>'];
      [0.35, 0.65].forEach(function (v) {
        s.push('<line x1="' + L + '" y1="' + ys(v) + '" x2="' + (W - R) + '" y2="' + ys(v) +
          '" stroke="var(--axis)" stroke-width="0.6" stroke-dasharray="3 4"/>');
        s.push('<text x="' + (L - 6) + '" y="' + (ys(v) + 4) + '" text-anchor="end" font-size="10" fill="var(--ink-3)">' +
          Math.round(v * 100) + '</text>');
      });
      var pts = D.readings.filter(function (r) { return r.want_next != null; });
      var d = pts.map(function (r, i) { return (i ? "L" : "M") + xs(r.n).toFixed(1) + " " + ys(r.want_next).toFixed(1); }).join(" ");
      s.push('<path d="' + d + '" fill="none" stroke="var(--s1)" stroke-width="1.6"/>');
      pts.forEach(function (r) {
        var hi = r.want_next >= 0.65, lo = r.want_next <= 0.35;
        s.push('<circle cx="' + xs(r.n).toFixed(1) + '" cy="' + ys(r.want_next).toFixed(1) +
          '" r="3" fill="' + (hi ? "var(--good)" : lo ? "var(--bad)" : "var(--unsure)") + '"><title>Ch.' + r.n +
          " · " + Math.round(r.want_next * 100) + "%</title></circle>");
      });
      [1, Math.round(total / 2), total].forEach(function (n) {
        s.push('<text x="' + xs(n) + '" y="' + (H - 6) + '" text-anchor="middle" font-size="10" fill="var(--ink-3)">Ch.' + n + '</text>');
      });
      s.push('</svg>');
      ecg.innerHTML = s.join("");
    }

    /* 感受色带：61 格，每格一章；颜色=该章主要感受；判定线同 app.js reading()——
       选择题最高概率 < 0.4 记「拿不准」（灰），不用 confidence 字段 */
    var band = document.getElementById("feeling-band");
    if (band) {
      var byN = {}; D.readings.forEach(function (r) { byN[r.n] = r; });
      var counts = {};
      var cells = "";
      for (var n = 1; n <= (D.units_total || 61); n++) {
        var r = byN[n];
        var cls = "cell-u", label = "Ch." + n + " · 未读";
        if (r && r.feel && r.feel_top != null && r.feel_top >= 0.4) {
          var idx = FEELS.indexOf(r.feel);
          if (idx >= 0) { cls = "cell-e" + (idx + 1); }
          label = "Ch." + n + " · " + r.feel + " " + Math.round(r.feel_top * 100) + "%";
          counts[r.feel] = (counts[r.feel] || 0) + 1;
        } else if (r && r.feel) {
          label = "Ch." + n + " · 拿不准";
        }
        cells += '<span class="fcell ' + cls + '" title="' + label + '">' + label + '</span>';
      }
      var legend = FEELS.map(function (f, i) {
        var c = counts[f] || 0;
        return '<span class="fl"><i class="cell-e' + (i + 1) + '"></i>' + f + (c ? " " + c : "") + '</span>';
      }).join("");
      band.innerHTML = '<div class="fband" role="img" aria-label="逐章主要感受色带">' + cells + '</div>' +
        '<div class="legend">' + legend + '<span class="fl"><i class="cell-u"></i>拿不准/未读</span></div>';
    }
  }
  drawCharts();

  var mv = document.getElementById("model-version"), mv06 = document.getElementById("mv-06");
  if (D && D.model_version) {
    if (mv) mv.textContent = D.model_version;
    if (mv06) mv06.textContent = D.model_version;
  }
})();
