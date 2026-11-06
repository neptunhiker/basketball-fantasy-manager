/* The roster timeline on the history page.
 *
 * Draws templates/fantasy/partials/roster_timeline.html's data as an SVG:
 * fifteen rows, one per roster spot, each cut wherever a trade handed the
 * spot to the next player. The player holding a spot today is drawn in full
 * colour with his name right-aligned against the "Today" line; everyone who
 * held it before is washed out.
 *
 * Two layouts, switched in the card header and remembered per browser:
 *   fit     the season squeezed into the card. Below 500px that gets too
 *           tight for days, so trades are counted per week and the position
 *           mix strip turns into one line of text.
 *   scroll  a fixed scale that scrolls sideways, opening at today. The
 *           current names stay pinned to the visible right edge and the strip
 *           labels to the left edge, wherever the chart is scrolled.
 * Without a remembered choice, the card scrolls whenever fitting would leave
 * less than MIN_FIT px per day.
 *
 * Details: a tooltip where there is a mouse (a click opens the player), a
 * panel under the chart where there is only touch. The bars are not tab
 * stops: the drawing is one image to assistive technology, and the moves
 * table below is the accessible version of the same history. Colours are all CSS classes (assets/input.css), so the
 * theme switch needs nothing from here.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-roster-timeline]");
  if (!root) return;

  const data = JSON.parse(document.getElementById("roster-timeline-data").textContent);
  const text = JSON.parse(document.getElementById("roster-timeline-text").textContent);
  const lang = document.documentElement.lang || "en";

  const SCROLL_PX_PER_DAY = 6;
  const MIN_FIT = 5;
  const COMPACT_BELOW = 500;
  const STORAGE_KEY = "roster-timeline-mode";
  const NS = "http://www.w3.org/2000/svg";
  const DAY = 864e5;

  // ---- dates, as whole days from the chart's first day ----------------------
  const utc = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const first = utc(data.startsOn);
  const dayOf = (iso) => Math.round((utc(iso) - first) / DAY);
  const END = dayOf(data.endsOn) + 1;
  // The end of today, so a player who arrived today still has a sliver.
  const TODAY = Math.max(1, Math.min(dayOf(data.today) + 1, END));
  const dateAt = (day) => new Date(first + day * DAY);
  const monthName = (day, style) =>
    new Intl.DateTimeFormat(lang, { month: style, timeZone: "UTC" }).format(dateAt(day));

  const spots = data.spots.map((spot) => ({
    stints: spot.stints.map((s) => ({
      ...s,
      a: s.start ? dayOf(s.start) : 0,
      b: s.end ? dayOf(s.end) : TODAY,
      current: !s.end,
    })),
  }));
  const allStints = spots.flatMap((spot) => spot.stints);

  const tradesPerDay = new Map();
  for (const s of allStints) if (s.start) tradesPerDay.set(s.a, (tradesPerDay.get(s.a) || 0) + 1);

  const mixAt = (day) => {
    const mix = { G: 0, F: 0, C: 0 };
    for (const s of allStints) if (s.a <= day && day < s.b) mix[s.pos] = (mix[s.pos] || 0) + 1;
    return mix;
  };
  const mixLabel = (mix) => `${mix.G} G · ${mix.F} F · ${mix.C} C`;

  // ---- small helpers --------------------------------------------------------
  const fill = (template, values) => template.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
  const escape = (value) =>
    String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  function el(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    if (parent) parent.appendChild(node);
    return node;
  }
  function label(parent, x, y, value, cls, anchor) {
    const node = el("text", { x, y, class: cls }, parent);
    if (anchor) node.setAttribute("text-anchor", anchor);
    node.textContent = value;
    return node;
  }

  function describe(s, withLink) {
    const name = withLink && s.url
      ? `<a href="${escape(s.url)}" class="font-semibold text-primary-700 underline-offset-2 hover:underline dark:text-primary-300">${escape(s.name)}</a>`
      : `<strong class="font-semibold">${escape(s.name)}</strong>`;
    const came = s.came
      ? fill(escape(text.tradedIn), { date: escape(s.came.dateLabel), partner: escape(s.came.partner) }) +
        (s.came.price ? ` · ${escape(s.came.price)}` : "")
      : escape(text.startingSquad);
    const left = s.left
      ? fill(escape(text.tradedOut), { date: escape(s.left.dateLabel), partner: escape(s.left.partner) }) +
        (s.left.price ? ` · ${escape(s.left.price)}` : "")
      : escape(text.onRoster);
    const days = fill(escape(text.days), { days: Math.max(0, s.b - s.a) });
    const points = s.points !== null
      ? `<strong class="font-semibold">${fill(escape(text.points), { points: escape(s.points) })}</strong>`
      : escape(text.noPoints);
    return `${name} · ${escape(text.positions[s.pos] || s.pos)}<br>${came}<br>${left}<br>${days} · ${points}`;
  }

  // ---- tooltip (mouse) and details panel (touch) -----------------------------
  const canHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const chartBox = root.querySelector("[data-chart]");
  const detail = root.querySelector("[data-detail]");
  const hint = root.querySelector(".timeline-hint");
  const mixLine = root.querySelector(".timeline-mix");
  const tip = document.createElement("div");
  tip.className = "timeline-tip";
  tip.hidden = true;
  tip.setAttribute("role", "tooltip");
  document.body.appendChild(tip);

  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    const box = tip.getBoundingClientRect();
    let left = x + 14;
    let top = y + 14;
    if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
    if (top + box.height > window.innerHeight - 8) top = y - box.height - 14;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${Math.max(8, top)}px`;
  }
  const hideTip = () => {
    tip.hidden = true;
  };
  window.addEventListener("scroll", hideTip, { passive: true });

  let selected = [];
  function select(s, rects) {
    for (const r of selected) r.classList.remove("is-selected");
    selected = rects;
    for (const r of rects) r.classList.add("is-selected");
    detail.innerHTML = describe(s, true);
  }

  function wire(rect, s, rects) {
    if (canHover) {
      const tipHtml = () => describe(s, false) + (s.url ? `<br><span class="opacity-70">${escape(text.clickHint)}</span>` : "");
      rect.addEventListener("pointerenter", (e) => showTip(tipHtml(), e.clientX, e.clientY));
      rect.addEventListener("pointermove", (e) => showTip(tipHtml(), e.clientX, e.clientY));
      rect.addEventListener("pointerleave", hideTip);
      rect.addEventListener("click", () => {
        if (s.url) window.location.href = s.url;
      });
    } else {
      rect.addEventListener("click", () => select(s, rects));
    }
  }

  // ---- drawing --------------------------------------------------------------
  let hatchCount = 0;

  function render(mode) {
    hideTip();
    chartBox.innerHTML = "";
    selected = [];
    const scroll = mode === "scroll";
    chartBox.classList.toggle("is-scroll", scroll);
    chartBox.classList.remove("is-scrolled");
    if (scroll) {
      chartBox.tabIndex = 0;
      chartBox.setAttribute("aria-label", text.scrollLabel);
    } else {
      chartBox.removeAttribute("tabindex");
      chartBox.removeAttribute("aria-label");
    }

    const viewWidth = chartBox.clientWidth;
    const compact = !scroll && viewWidth < COMPACT_BELOW;
    const LEFT = 1;
    const RIGHT = scroll ? 16 : 8;
    const width = scroll ? LEFT + END * SCROLL_PX_PER_DAY + RIGHT : viewWidth;
    const X = (day) => LEFT + (day / END) * (width - LEFT - RIGHT);
    const RH = compact ? 26 : 30;
    const STRIP = 40;
    const TOP = STRIP + (compact ? 44 : 82);
    const height = TOP + spots.length * RH + 4;

    const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": text.chartLabel }, chartBox);
    const hatchId = `tl-hatch-${++hatchCount}`;
    const pattern = el("pattern", { id: hatchId, width: 6, height: 6, patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" }, el("defs", {}, svg));
    el("rect", { width: 6, height: 6, class: "tl-hatch-bg" }, pattern);
    el("line", { x1: 0, y1: 0, x2: 0, y2: 6, class: "tl-hatch-line" }, pattern);

    // The season itself: trade-free phase, the rest still to come, Sundays,
    // month starts, today.
    if (data.tradeFreeUntil) {
      const until = Math.min(dayOf(data.tradeFreeUntil), END);
      el("rect", { x: X(0), y: 34, width: Math.max(0, X(until) - X(0)), height: height - 34, fill: `url(#${hatchId})` }, svg);
    }
    if (TODAY < END) el("rect", { x: X(TODAY), y: 34, width: X(END) - X(TODAY), height: height - 34, class: "tl-future" }, svg);
    for (let day = 0; day <= END; day++) {
      const date = dateAt(day);
      if (!compact && date.getUTCDay() === 0) el("line", { x1: X(day), x2: X(day), y1: 34, y2: height, class: "tl-grid" }, svg);
      if (date.getUTCDate() === 1 || day === 0) {
        if (day) el("line", { x1: X(day), x2: X(day), y1: 0, y2: height, class: "tl-month" }, svg);
        const room = X(Math.min(END, day + 28)) - X(day);
        if (room > 24) label(svg, X(day) + 4, 12, monthName(day, scroll ? "long" : "short"), "tl-label");
      }
    }
    el("line", { x1: X(TODAY), x2: X(TODAY), y1: 18, y2: height, class: "tl-today" }, svg);
    label(svg, X(TODAY) - 4, 28, text.today, "tl-today-label", "end");

    // Strip 1: trades over time. Strip 2: the position mix (not when compact).
    const pinnedLeft = [];
    if (compact) {
      const offset = (dateAt(0).getUTCDay() + 6) % 7; // back to Monday
      const perWeek = new Map();
      for (const [day, count] of tradesPerDay) {
        const week = Math.floor((day + offset) / 7);
        perWeek.set(week, (perWeek.get(week) || 0) + count);
      }
      const most = Math.max(1, ...perWeek.values());
      for (const [week, count] of perWeek) {
        const x0 = X(week * 7 - offset) + 1;
        const x1 = X(week * 7 - offset + 7) - 1;
        const h = 3 + (count / most) * 15;
        el("rect", { x: x0, y: STRIP + 30 - h, width: Math.max(1.5, x1 - x0), height: h, rx: 1, class: "tl-dot" }, svg);
      }
      pinnedLeft.push(label(svg, X(0) + 4, STRIP + 4, text.tradesPerWeek.toUpperCase(), "tl-label tl-caps"));
    } else {
      for (const [day, count] of tradesPerDay) {
        for (let k = 0; k < count; k++) el("circle", { cx: X(day) + 1, cy: STRIP + 26 - k * 6, r: 2.4, class: "tl-dot" }, svg);
      }
      const mixTop = STRIP + 50;
      pinnedLeft.push(label(svg, X(0) + 4, mixTop - 3, text.positionMix.toUpperCase(), "tl-label tl-caps"));
      const days = [...new Set([0, ...allStints.flatMap((s) => [s.a, s.b])])].filter((d) => d < TODAY).sort((p, q) => p - q);
      let previous = null;
      let since = 0;
      const segments = [];
      for (const day of days) {
        const key = mixLabel(mixAt(day));
        if (key !== previous) {
          if (previous !== null) segments.push([since, day, previous]);
          previous = key;
          since = day;
        }
      }
      if (previous !== null) segments.push([since, TODAY, previous]);
      // One band, divided where the mix changed, labelled where there is room.
      el("rect", { x: X(0), y: mixTop + 1, width: Math.max(1, X(TODAY) - X(0)), height: 18, rx: 4, class: "tl-mix" }, svg);
      segments.forEach(([a, b, key], index) => {
        if (index) el("line", { x1: X(a), x2: X(a), y1: mixTop + 4, y2: mixTop + 16, class: "tl-mix-divider" }, svg);
        const tag = label(svg, X(a) + 6, mixTop + 14, key, "tl-label");
        if (tag.getComputedTextLength() > X(b) - X(a) - 10) tag.remove();
      });
      pinnedLeft.push(label(svg, X(0) + 4, mixTop - 4, text.positionMix.toUpperCase(), "tl-label tl-caps"));
      pinnedLeft.push(label(svg, X(0) + 4, STRIP + 4, text.tradesPerDay.toUpperCase(), "tl-label tl-caps"));
    }
    mixLine.hidden = !compact;
    mixLine.textContent = compact ? fill(text.mixNow, { mix: mixLabel(mixAt(TODAY - 1)) }) : "";

    // The spots. Names of current players go on a layer above every bar, so a
    // chip that has to grow leftwards covers the faded segments beneath it.
    const top = el("g", {}, null);
    const chips = [];
    const pastNames = [];
    spots.forEach((spot, row) => {
      const y = TOP + row * RH;
      for (const s of spot.stints) {
        const x0 = X(s.a) + (s.a ? 1 : 0);
        const x1 = X(s.b) - 1;
        const rects = [];
        const bar = el("rect", {
          x: x0,
          y: y + (s.current ? 3 : 6),
          width: Math.max(1.5, x1 - x0),
          height: RH - (s.current ? 6 : 12),
          rx: compact ? 3 : 4,
          class: `tl-bar ${s.current ? "timeline-pos-" : "timeline-past-"}${s.pos}`,
        }, svg);
        rects.push(bar);
        if (s.current) {
          el("circle", { cx: X(TODAY), cy: y + RH / 2, r: compact ? 3.5 : 4, class: `tl-now timeline-pos-${s.pos}` }, top);
          const chip = el("rect", { y: y + 3, height: RH - 6, rx: compact ? 3 : 4, class: `tl-bar timeline-pos-${s.pos}` }, top);
          const name = label(top, 0, y + RH / 2 + 3.5, s.short, "tl-name", "end");
          rects.push(chip);
          chips.push({ chip, name, barStart: x0, row });
        } else {
          const tag = label(svg, x0 + 6, y + RH / 2 + 3.5, s.short, "tl-past-name");
          const room = x1 - x0 - 12;
          if (tag.getComputedTextLength() > room) {
            tag.textContent = s.short.split(" ").slice(1).join(" ") || s.short;
          }
          if (tag.getComputedTextLength() > room) tag.remove();
          else pastNames.push({ tag, row, from: x0, to: x1, width: tag.getComputedTextLength() });
        }
        for (const r of rects) wire(r, s, rects);
      }
    });
    svg.appendChild(top);
    for (const c of chips) c.width = c.name.getComputedTextLength();

    // Pin the current names to the visible right edge (or to today, when it
    // is in view) and the strip labels to the visible left edge.
    const place = () => {
      const left = chartBox.scrollLeft;
      const edge = Math.min(X(TODAY), left + viewWidth - 4);
      const atToday = edge >= X(TODAY) - 0.5;
      for (const c of chips) {
        const end = atToday ? X(TODAY) - 1 : edge;
        const textX = end - 9;
        const x0 = Math.max(left + 1, Math.min(c.barStart, textX - c.width - 8));
        c.chip.setAttribute("x", x0);
        c.chip.setAttribute("width", Math.max(0, end - x0));
        c.name.setAttribute("x", textX);
        c.from = x0;
      }
      // Faded names slide along with the left edge while their segment is
      // in view, and hide rather than show half a name under a chip or past
      // the edge -- half a name reads as a typo.
      const chipFrom = new Map(chips.map((c) => [c.row, c.from]));
      for (const p of pastNames) {
        const x = Math.max(p.from + 6, left + 8);
        const limit = Math.min(p.to, chipFrom.get(p.row) ?? Infinity) - 6;
        p.tag.setAttribute("x", x);
        p.tag.style.visibility = x + p.width > limit ? "hidden" : "";
      }
      for (const t of pinnedLeft) t.setAttribute("x", left + X(0) + 4);
      chartBox.classList.toggle("is-scrolled", left > 2);
    };
    if (scroll) {
      chartBox.scrollLeft = Math.max(0, X(TODAY) - viewWidth + 72);
      chartBox.onscroll = () => window.requestAnimationFrame(place);
    } else {
      chartBox.onscroll = null;
    }
    place();

    // Hints and the details panel.
    const hintText = scroll ? (canHover ? text.scrollHint : text.swipeHint) : canHover ? "" : text.tapHint;
    hint.textContent = hintText;
    hint.hidden = !hintText || (!scroll && !canHover);
    detail.hidden = canHover;
    if (!canHover) detail.textContent = text.tapHint;
  }

  // ---- the layout switch -----------------------------------------------------
  const buttons = root.querySelectorAll("[data-mode]");
  const stored = () => {
    try {
      return window.localStorage.getItem(STORAGE_KEY);
    } catch (e) {
      return null;
    }
  };
  const store = (mode) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, mode);
    } catch (e) {
      /* private mode: the choice simply is not remembered */
    }
  };
  const automatic = () => (chartBox.clientWidth / END < MIN_FIT ? "scroll" : "fit");
  let mode = stored() === "fit" || stored() === "scroll" ? stored() : automatic();

  function show() {
    for (const button of buttons) button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    render(mode);
  }
  for (const button of buttons) {
    button.addEventListener("click", () => {
      mode = button.dataset.mode;
      store(mode);
      show();
    });
  }

  let lastWidth = chartBox.clientWidth;
  if ("ResizeObserver" in window) {
    let pending = null;
    new ResizeObserver(() => {
      const now = chartBox.clientWidth;
      if (Math.abs(now - lastWidth) < 2) return;
      lastWidth = now;
      window.clearTimeout(pending);
      pending = window.setTimeout(show, 120);
    }).observe(chartBox);
  }
  show();
})();
