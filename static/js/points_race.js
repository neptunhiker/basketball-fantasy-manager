/* The hover readout of the race charts on Analytics -> Insights.
 *
 * The drawings themselves are server-rendered SVG (apps/analytics/charts.py
 * and templates/analytics/partials/race_card.html); this only moves a guide
 * and one dot per line to the day nearest the pointer, and says what every
 * line had by then:
 *
 *   - in each card's legend, always -- on a phone that is the whole readout,
 *     so nothing ever covers the lines a finger is on;
 *   - in a tooltip beside the guide as well, where there is a mouse.
 *
 * The cards on the page are linked: they share their days, and a day picked
 * on one -- pointer, tap or arrow keys -- shows in every card and every
 * legend. Only the card being pointed at (or focused) shows a tooltip.
 *
 * The arrow keys step through the days while a chart has focus. Without
 * JavaScript every chart is complete and its legend shows the newest day.
 * Names come from the page's JSON and go in with textContent only.
 */
(function () {
  "use strict";

  const NS = "http://www.w3.org/2000/svg";
  const cards = [...document.querySelectorAll("[data-race-chart]")].map(card);
  if (!cards.length) return;

  // The day every card shows, or null for "the newest, nothing picked".
  let current = null;
  const last = cards[0].last;

  function show(day, source, withTooltip) {
    current = day;
    for (const each of cards) each.show(day, withTooltip && each === source);
  }

  function reset() {
    current = null;
    for (const each of cards) each.reset();
  }

  function card(root) {
    const readout = JSON.parse(document.getElementById(root.dataset.readout).textContent);
    const stage = root.querySelector("[data-race-stage]");
    const tooltip = root.querySelector("[data-race-tooltip]");
    const tooltipDay = root.querySelector("[data-race-tooltip-day]");
    const tooltipRows = root.querySelector("[data-race-tooltip-rows]");
    const dayLabel = root.querySelector("[data-race-day]");
    const legend = [...root.querySelectorAll("[data-race-legend]")].map((item) => ({
      item,
      value: item.querySelector("[data-race-value]"),
    }));

    const drawings = [...root.querySelectorAll("[data-race-drawing]")].map((box) => {
      const svg = box.querySelector("[data-race-svg]");
      const geometry = JSON.parse(box.querySelector("script[type='application/json']").textContent);
      return {
        box,
        svg,
        geometry,
        hover: svg.querySelector("[data-race-hover]"),
        guide: svg.querySelector("[data-race-guide]"),
        dots: [...svg.querySelectorAll("[data-race-dot]")].map((dot) => ({
          dot,
          line: Number(dot.dataset.raceDot),
        })),
      };
    });

    const visible = () => drawings.find((drawing) => drawing.box.offsetParent !== null);

    function nearestDay(drawing, clientX) {
      const point = drawing.svg.createSVGPoint();
      point.x = clientX;
      point.y = 0;
      const x = point.matrixTransform(drawing.svg.getScreenCTM().inverse()).x;
      const xs = drawing.geometry.x;
      let best = 0;
      for (let index = 1; index < xs.length; index += 1) {
        if (Math.abs(xs[index] - x) < Math.abs(xs[best] - x)) best = index;
      }
      return best;
    }

    function showDay(day, withTooltip) {
      dayLabel.textContent = readout.days[day];
      readout.lines.forEach((line, index) => {
        legend[index].value.textContent = line.labels[day] ?? "–";
      });
      const drawing = visible();
      if (!drawing) return;
      const x = drawing.geometry.x[day];
      drawing.guide.setAttribute("x1", x);
      drawing.guide.setAttribute("x2", x);
      for (const { dot, line } of drawing.dots) {
        const y = drawing.geometry.y[line][day];
        if (y === null) {
          dot.setAttribute("visibility", "hidden");
        } else {
          dot.setAttribute("visibility", "visible");
          dot.setAttribute("cx", x);
          dot.setAttribute("cy", y);
        }
      }
      drawing.hover.setAttribute("visibility", "visible");

      if (withTooltip) placeTooltip(drawing, day, x);
      else tooltip.hidden = true;
    }

    function placeTooltip(drawing, day, x) {
      tooltipDay.textContent = readout.days[day];
      tooltipRows.replaceChildren(
        ...readout.lines
          .map((line, index) => ({ line, index, value: line.values[day] }))
          .filter((entry) => entry.value !== null)
          .sort((a, b) => b.value - a.value)
          .map(({ line }) => row(line, day)),
      );
      tooltip.hidden = false;
      // Beside the guide, on whichever side has the room.
      const scale = drawing.svg.getBoundingClientRect().width / drawing.svg.viewBox.baseVal.width;
      const left = x * scale;
      const width = tooltip.offsetWidth;
      const gap = 14;
      const stageWidth = stage.clientWidth;
      tooltip.style.left = `${left + gap + width <= stageWidth ? left + gap : Math.max(0, left - gap - width)}px`;
    }

    function hide() {
      tooltip.hidden = true;
      for (const drawing of drawings) drawing.hover.setAttribute("visibility", "hidden");
      dayLabel.textContent = dayLabel.dataset.latest;
      readout.lines.forEach((line, index) => {
        legend[index].value.textContent = line.labels[readout.days.length - 1] ?? "–";
      });
    }

    const self = { show: showDay, reset: hide, last: readout.days.length - 1 };

    for (const drawing of drawings) {
      const pick = (event) => {
        show(nearestDay(drawing, event.clientX), self, event.pointerType === "mouse");
      };
      drawing.svg.addEventListener("pointermove", pick);
      drawing.svg.addEventListener("pointerdown", pick);
      drawing.svg.addEventListener("pointerleave", (event) => {
        // A finger lifting off keeps its day in the legends; a mouse leaving clears it.
        if (event.pointerType === "mouse") reset();
      });
    }

    stage.addEventListener("keydown", (event) => {
      const steps = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity };
      if (event.key === "Escape") {
        reset();
        return;
      }
      if (!(event.key in steps)) return;
      event.preventDefault();
      const from = current ?? last;
      const step = steps[event.key];
      const day = Number.isFinite(step) ? from + step : step < 0 ? 0 : last;
      show(Math.max(0, Math.min(last, day)), self, true);
    });
    stage.addEventListener("blur", (event) => {
      // Moving focus straight to the other chart keeps the day.
      if (!event.relatedTarget?.matches?.("[data-race-stage]")) reset();
    });

    return self;
  }

  function row(line, day) {
    const item = document.createElement("li");
    item.className = "flex items-center gap-2 text-xs";
    item.append(key(line));
    const value = document.createElement("span");
    value.className =
      "min-w-12 shrink-0 text-right text-sm font-semibold tabular-nums text-tertiary-800 dark:text-neutral-50";
    value.textContent = line.labels[day];
    const name = document.createElement("span");
    name.className = `min-w-0 truncate ${line.own || line.dreamTeam ? "font-semibold text-neutral-700 dark:text-neutral-200" : "text-neutral-500 dark:text-neutral-400"}`;
    name.textContent = line.name;
    item.append(value, name);
    return item;
  }

  // The same short stroke as the legend's: dashed for the Dream Team.
  function key(line) {
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 16 8");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", `race-${line.color} h-2 w-4 shrink-0`);
    const stroke = document.createElementNS(NS, "line");
    for (const [name, value] of Object.entries({ x1: 1.5, y1: 4, x2: 14.5, y2: 4 })) {
      stroke.setAttribute(name, value);
    }
    stroke.setAttribute("class", "race-stroke");
    stroke.setAttribute("stroke-width", "3");
    stroke.setAttribute("stroke-linecap", "round");
    if (line.dreamTeam) stroke.setAttribute("stroke-dasharray", "3 3");
    svg.append(stroke);
    return svg;
  }
})();
