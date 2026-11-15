/* The hover readout of the points race on Analytics -> Insights.
 *
 * The drawing itself is server-rendered SVG (apps/analytics/charts.py and
 * templates/analytics/partials/points_race_svg.html); this only moves a guide
 * and one dot per line to the day nearest the pointer, and says what every
 * line had scored by then:
 *
 *   - in the legend under the chart, always -- on a phone that is the whole
 *     readout, so nothing ever covers the lines a finger is on;
 *   - in a tooltip beside the guide as well, where there is a mouse.
 *
 * The arrow keys step through the days while the chart has focus. Without
 * JavaScript the chart is complete and the legend shows the newest day.
 * Names come from the page's JSON and go in with textContent only.
 */
(function () {
  "use strict";

  const root = document.querySelector("[data-points-race]");
  if (!root) return;

  const readout = JSON.parse(document.getElementById("points-race-readout").textContent);
  const stage = root.querySelector("[data-race-stage]");
  const tooltip = root.querySelector("[data-race-tooltip]");
  const tooltipDay = root.querySelector("[data-race-tooltip-day]");
  const tooltipRows = root.querySelector("[data-race-tooltip-rows]");
  const dayLabel = root.querySelector("[data-race-day]");
  const legend = [...root.querySelectorAll("[data-race-legend]")].map((item) => ({
    item,
    value: item.querySelector("[data-race-value]"),
  }));
  const last = readout.days.length - 1;
  const NS = "http://www.w3.org/2000/svg";

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
  let current = null;

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

  function show(day, withTooltip) {
    const drawing = visible();
    if (!drawing) return;
    current = day;
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

    dayLabel.textContent = readout.days[day];
    readout.lines.forEach((line, index) => {
      legend[index].value.textContent = line.labels[day] ?? "–";
    });

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

  function row(line, day) {
    const item = document.createElement("li");
    item.className = "flex items-center gap-2 text-xs";
    item.append(key(line));
    const value = document.createElement("span");
    value.className = "w-12 shrink-0 text-right text-sm font-semibold tabular-nums text-tertiary-800 dark:text-neutral-50";
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

  function reset() {
    current = null;
    tooltip.hidden = true;
    for (const drawing of drawings) drawing.hover.setAttribute("visibility", "hidden");
    dayLabel.textContent = dayLabel.dataset.latest;
    readout.lines.forEach((line, index) => {
      legend[index].value.textContent = line.labels[last] ?? "–";
    });
  }

  for (const drawing of drawings) {
    drawing.svg.addEventListener("pointermove", (event) => {
      show(nearestDay(drawing, event.clientX), event.pointerType === "mouse");
    });
    drawing.svg.addEventListener("pointerdown", (event) => {
      show(nearestDay(drawing, event.clientX), event.pointerType === "mouse");
    });
    drawing.svg.addEventListener("pointerleave", (event) => {
      // A finger lifting off keeps its day in the legend; a mouse leaving clears it.
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
    show(Math.max(0, Math.min(last, day)), true);
  });
  stage.addEventListener("blur", reset);
})();
