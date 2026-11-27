/* Staff home page. The page arrives as a frame of placeholder cards; each part of the dashboard is either
   already in the page or fetched on its own the first time a slide that shows it is opened, and its cards
   replace their placeholders as soon as it is there. Rows of cards are paged to fit the window, so the page
   never scrolls; moving between pages does not touch the address. */
(function () {
  "use strict";

  var panel = document.getElementById("home-panel");
  if (!panel) { return; }
  var SVG = "http://www.w3.org/2000/svg";
  var SERIES = ["home-bg-s1", "home-bg-s2", "home-bg-s3", "home-bg-s4", "home-bg-other"];
  var tip = document.getElementById("home-tip");
  var data = {};
  var asked = {};
  var plots = [];

  function grouped(value) { return Number(value).toLocaleString("en-IN"); }

  function each(list, run) { Array.prototype.forEach.call(list, run); }

  function make(tag, className, text) {
    var el = document.createElement(tag);
    if (className) { el.className = className; }
    if (text !== undefined) { el.textContent = text; }
    return el;
  }

  function shape(tag, attrs, parent, text) {
    var el = document.createElementNS(SVG, tag);
    Object.keys(attrs).forEach(function (name) { el.setAttribute(name, attrs[name]); });
    if (text !== undefined) { el.textContent = text; }
    parent.appendChild(el);
    return el;
  }

  function hover(el, text) {
    el.addEventListener("mousemove", function (event) {
      tip.textContent = text;
      tip.style.opacity = 1;
      tip.style.left = Math.min(event.clientX + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
      tip.style.top = (event.clientY - 32) + "px";
    });
    el.addEventListener("mouseleave", function () { tip.style.opacity = 0; });
  }

  function empty(root) {
    root.appendChild(make("p", "home-empty", "No data for this selection."));
  }

  /* rows: [{label, value, shown, hint}] drawn as a horizontal bar list */
  function bars(id, rows, series, max) {
    var root = document.getElementById(id);
    if (!root) { return; }
    if (!rows.length) { empty(root); return; }
    max = max || Math.max.apply(null, rows.map(function (row) { return row.value; })) || 1;
    root.style.maxHeight = rows.length * 64 + "px";
    rows.forEach(function (row) {
      var line = make("div", "home-row");
      var track = make("div");
      var bar = make("div", "home-bar " + (series || SERIES[0]));
      bar.style.width = Math.max(row.value / max * 100, 1) + "%";
      track.appendChild(bar);
      line.appendChild(make("div", "home-row-label", row.label));
      line.appendChild(track);
      line.appendChild(make("div", "home-row-value", row.shown));
      hover(line, row.label + ": " + (row.hint || row.shown));
      root.appendChild(line);
    });
  }

  /* bars and a line on one axis; a null is a month still to come; redrawn to the box's real size whenever the layout changes */
  function plot(id, chart, fill, lineWord, barWord) {
    function draw() {
      var root = document.getElementById(id);
      if (!root) { return; }
      var width = root.clientWidth, height = root.clientHeight;
      if (!width || !height) { return; }
      root.textContent = "";
      var count = chart.labels.length;
      var top = Math.max.apply(null, chart.line.concat(chart.bars).concat([0]));
      if (!top) { empty(root); return; }
      var step = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000]
        .filter(function (nice) { return nice * 3 >= top; })[0] || top;
      var max = Math.ceil(top / step) * step;
      var left = 38, right = 8, above = 18, below = 20;
      var svg = shape("svg", { viewBox: "0 0 " + width + " " + height, role: "img",
                               "aria-label": barWord + " and " + lineWord + " over time" }, root);
      function x(index) { return left + (width - left - right) * (index + 0.5) / count; }
      function y(value) { return above + (height - above - below) * (1 - value / max); }
      [0, step, step * 2, step * 3].filter(function (value) { return value <= max; }).forEach(function (value) {
        shape("line", { x1: left, x2: width - right, y1: y(value), y2: y(value),
                        "class": value ? "home-grid-line" : "home-base-line" }, svg);
        shape("text", { x: left - 7, y: y(value) + 4, "text-anchor": "end" }, svg, grouped(value));
      });
      var slot = (width - left - right) / count;
      var bar = Math.max(3, Math.min(14, slot * 0.5));
      var every = Math.max(1, Math.ceil(count / Math.max(2, Math.floor((width - left - right) / 64))));
      chart.bars.forEach(function (value, index) {
        var tall = Math.max(y(0) - y(value), value ? 2 : 0), round = Math.min(4, tall, bar / 2);
        if (tall) {
          shape("path", { "class": fill, d: "M" + (x(index) - bar / 2) + "," + y(0) + " v" + -(tall - round) +
            " q0," + -round + " " + round + "," + -round + " h" + (bar - 2 * round) +
            " q" + round + ",0 " + round + "," + round + " v" + (tall - round) + " z" }, svg);
        }
        if (index % every === 0) {
          shape("text", { x: x(index), y: height - 4, "text-anchor": "middle" }, svg, chart.labels[index]);
        }
      });
      shape("polyline", { "class": "home-stroke-s2", points: chart.line.map(function (value, index) {
        return value === null ? "" : x(index) + "," + y(value);
      }).join(" ") }, svg);
      chart.line.forEach(function (value, index) {
        if (value !== null) { shape("circle", { "class": "home-dot-s2", cx: x(index), cy: y(value), r: 3 }, svg); }
      });
      if (chart.peak !== null) {
        var anchor = chart.peak < 2 ? "start" : chart.peak > count - 3 ? "end" : "middle";
        shape("text", { "class": "home-mark-label", x: x(chart.peak), y: y(chart.line[chart.peak]) - 7,
                        "text-anchor": anchor }, svg, grouped(chart.line[chart.peak]));
      }
      chart.months.forEach(function (month, index) {
        var hit = shape("rect", { "class": "home-hit", x: x(index) - slot / 2, y: above, width: slot,
                                  height: height - above - below }, svg);
        hover(hit, chart.line[index] === null ? month + ": still to come" :
          month + ": " + grouped(chart.line[index]) + " " + lineWord + ", " + grouped(chart.bars[index]) + " " + barWord);
      });
    }
    plots.push(draw);
    draw();
  }

  /* a city-by-column grid; columns still to come are left empty */
  function grid(id, chart, word) {
    var root = document.getElementById(id);
    if (!root) { return; }
    if (!chart.rows.length) { empty(root); return; }
    var columns = chart.labels.length;
    root.style.gridTemplateColumns = "96px repeat(" + columns + ", minmax(14px, 1fr)) 64px";
    if (chart.rows.length > 7 || columns > 14) { root.className += " home-heat--many"; }
    root.appendChild(make("div"));
    chart.labels.forEach(function (label) { root.appendChild(make("div", "home-heat-head", label)); });
    root.appendChild(make("div", "home-heat-head", "Total"));
    chart.rows.forEach(function (row) {
      root.appendChild(make("div", "home-heat-city", row.label));
      row.values.forEach(function (value, index) {
        if (value === null) {
          root.appendChild(make("div", "home-heat-cell home-heat-future"));
          return;
        }
        var level = value ? Math.min(7, 1 + Math.floor(Math.sqrt(value / chart.peak) * 7)) : 0;
        var cell = make("div", "home-heat-cell" + (level ? " home-heat-" + level : ""), value ? grouped(value) : "·");
        hover(cell, row.label + ", " + chart.months[index] + ": " + grouped(value) + " " + word);
        root.appendChild(cell);
      });
      root.appendChild(make("div", "home-heat-total", grouped(row.total)));
    });
    /* rows squeezed too thin for their numbers keep the colour only */
    function thin() {
      var cell = root.querySelector(".home-heat-cell");
      if (cell && cell.offsetHeight) { root.classList.toggle("home-heat--thin", cell.offsetHeight < 10); }
    }
    plots.push(thin);
    thin();
  }

  function ladder() {
    var root = document.getElementById("home-ladder");
    var chart = data.survey;
    if (!root) { return; }
    if (!chart.rows.length) { empty(root); return; }
    root.style.maxHeight = chart.rows.length * 60 + "px";
    chart.rows.forEach(function (row) {
      var stack = make("div", "home-stack");
      row.shares.forEach(function (share, index) {
        if (!share) { return; }
        var part = make("span", SERIES[index]);
        part.style.flex = share + " 0 0";
        hover(part, row.label + ": " + chart.labels[index] + " " + share + "%");
        stack.appendChild(part);
      });
      root.appendChild(make("div", "", row.label));
      root.appendChild(stack);
      root.appendChild(make("div", "home-ladder-note", Math.round(row.shares[0]) + "% own · " + grouped(row.open) + " open"));
    });
  }

  /* A small table with a split bar: heads name the columns (the bar and the count after it share the second), as
     [words, hint, colour keys shown in place of a legend];
     each row is {label, parts: [[value, colour class, words]], cells: [text]}, the first cell being the bar's total.
     A row with `whole` fills its bar against that number; the others share one scale. */
  function table(id, heads, rows) {
    var root = document.getElementById(id);
    if (!root) { return; }
    if (!rows.length) { empty(root); return; }
    var max = Math.max.apply(null, rows.map(function (row) {
      return row.parts.reduce(function (sum, part) { return sum + part[0]; }, 0);
    })) || 1;
    root.style.maxHeight = (rows.length + 1) * 52 + "px";
    var head = make("div", "home-progress-row home-progress-row--head");
    heads.forEach(function (pair) {
      var cell = make("div", "", pair[0]);
      if (pair[1]) { cell.title = pair[1]; }
      (pair[2] || []).forEach(function (key) {
        cell.appendChild(make("i", "home-key " + key[0]));
        cell.appendChild(make("span", "", key[1]));
      });
      head.appendChild(cell);
    });
    root.appendChild(head);
    rows.forEach(function (row) {
      var line = make("div", "home-progress-row");
      var bar = make("div", "home-progress-bar");
      var track = make("span");
      row.parts.forEach(function (part) {
        if (!part[0]) { return; }
        var piece = make("i", part[1]);
        piece.style.width = part[0] / (row.whole || max) * 100 + "%";
        hover(piece, row.label + ": " + grouped(part[0]) + " " + part[2]);
        track.appendChild(piece);
      });
      bar.appendChild(track);
      line.appendChild(make("div", "home-progress-city", row.label));
      line.appendChild(bar);
      row.cells.forEach(function (text, index) {
        line.appendChild(make("div", "home-progress-value" + (index ? " home-progress-value--quiet" : ""), text));
      });
      root.appendChild(line);
    });
  }

  function progress() {
    var chart = data.toilets.progress;
    table("home-progress", [["City", ""], ["Open now", "Toilets under construction now"],
                            ["Completed", "Completed in the last " + chart.days + " days"],
                            ["Agreed", "New agreements in the last " + chart.days + " days"]],
      chart.rows.map(function (row) {
        return { label: row.label,
                 parts: [[row.under - row.overdue, "home-bg-s4", "open up to " + chart.overdue_days + " days"],
                         [row.overdue, "home-bg-late", "open longer than " + chart.overdue_days + " days"]],
                 cells: [grouped(row.under), grouped(row.completed), grouped(row.agreed)] };
      }));
  }

  /* vulnerable members reached in the period, beside the count till now (or, for all time, those on record) */
  function people() {
    var figures = data.people;
    table("home-people", [["Members", ""],
                          ["", "Reached by a toilet at their home, counted in the year its agreement was signed, or by joining the cup programme",
                           [["home-bg-s1", "Toilet"], ["home-bg-s3", "Cups"]]],
                          figures.whole ? ["On record", "Members in the member records"] : ["Till now", "Reached in all years"]],
      figures.rows.map(function (row) {
        return { label: row.label,
                 parts: [[row.toilet, "home-bg-s1", "have a toilet at home"], [row.programme, "home-bg-s3", "joined the cup programme"]],
                 cells: [grouped(row.reached), grouped(figures.whole ? row.total : row.ever)] };
      }));
  }

  function scope() {
    table("home-scope", [["City", ""], ["Without a toilet of their own", "Households that use a community toilet or defecate in the open"],
                         ["Of surveyed", "Share of the households surveyed in the city"],
                         ["Built", "Toilets completed with us, all years"]],
      data.survey.scope.rows.map(function (row) {
        return { label: row.label,
                 parts: [[row.community, "home-bg-s3", "use a community toilet"],
                         [row.open, "home-bg-s4", "defecate in the open"]],
                 cells: [grouped(row.need), row.percent + "%", grouped(row.built)] };
      }));
  }

  function days(rows) {
    return rows.filter(function (row) { return row.days !== null; }).map(function (row) {
      return { label: row.label, value: row.days, shown: row.days + " d",
               hint: row.days + " days, from " + grouped(row.count) + " toilets" };
    });
  }

  /* what to draw once a part's cards are in the page */
  var DRAW = {
    toilets: function () {
      var figures = data.toilets;
      grid("home-heat-toilets", figures.heat, "completed");
      plot("home-momentum-toilets", figures.momentum, "home-fill-s1", "agreements", "completed");
      progress();
      bars("home-stages", days(figures.stages.rows));
      bars("home-city-days", days(figures.stages.cities));
      bars("home-after", figures.after.rows.filter(function (row) { return row.count; }).map(function (row, index) {
        return { label: row.label, value: row.count, shown: index ? row.percent + "%" : grouped(row.count),
                 hint: grouped(row.count) + " toilets" };
      }));
      bars("home-cancelled", figures.after.cancelled.map(function (row) {
        return { label: row.label, value: row.percent, shown: row.percent + "%",
                 hint: row.percent + "%, " + grouped(row.count) + " agreements" };
      }), SERIES[1]);
    },
    mhm: function () {
      grid("home-heat-mhm", data.mhm.heat, "enrolled");
      plot("home-momentum-mhm", data.mhm.momentum, "home-fill-s3", "follow-up visits", "cups given");
    },
    mobilisation: function () {
      bars("home-activities", data.mobilisation.activities.map(function (row) {
        return { label: row.label, value: row.count, shown: grouped(row.count), hint: grouped(row.count) + " sessions" };
      }), SERIES[2]);
    },
    survey: function () { ladder(); scope(); },
    people: people,
    funders: function () {
      bars("home-funders", data.funders.rows.map(function (row) {
        return { label: row.label, value: row.count, shown: grouped(row.count), hint: grouped(row.count) + " toilets" };
      }));
    },
    synced: function () {}
  };

  /* Rows of cards: show as many as fit the window and slide to the rest. Small screens show everything and scroll. */
  var page = document.getElementById("home-page");
  var area = document.getElementById("home-bands");
  var bands = Array.prototype.slice.call(area.children);
  var slider = document.getElementById("home-slider");
  var pager = document.getElementById("home-pages");
  var more = document.getElementById("home-more");
  var back = document.getElementById("home-back");
  var filters = document.getElementById("home-filters");
  var roomy = window.matchMedia("(min-width: 1101px) and (min-height: 561px)");
  var GAP = 20, GROW = 1.6;
  var pages = [bands], current = 0;

  function natural() {
    page.classList.add("is-measuring");
    var sizes = bands.map(function (band) { return band.offsetHeight; });
    page.classList.remove("is-measuring");
    return sizes;
  }

  /* For bands[lo..hi): the fewest pages that fit, then the split of that many whose tallest page is shortest. */
  function fit(sizes, room, lo, hi) {
    function tall(start, end) {
      var sum = GAP * (end - start - 1);
      for (var i = start; i < end; i += 1) { sum += sizes[i]; }
      return sum;
    }
    var count = 1, start = lo, best = null;
    for (var index = lo; index < hi; index += 1) {
      if (index > start && tall(start, index + 1) > room) { count += 1; start = index; }
    }
    (function walk(from, left, groups, tallest) {
      if (left === 1) {
        var worst = Math.max(tallest, tall(from, hi));
        if (!best || worst < best.worst) { best = { worst: worst, groups: groups.concat([[from, hi]]) }; }
        return;
      }
      for (var end = from + 1; end <= hi - left + 1; end += 1) {
        walk(end, left - 1, groups.concat([[from, end]]), Math.max(tallest, tall(from, end)));
      }
    })(lo, count, [], 0);
    return best.groups;
  }

  /* the bands up to the one marked data-page-end are always the first screen; the rest are paged to fit */
  function split(sizes, room) {
    var groups = [], from = 0;
    bands.forEach(function (band, index) {
      if (band.hasAttribute("data-page-end")) {
        groups.push([from, index + 1]);
        from = index + 1;
      } else if (index === bands.length - 1) {
        groups = groups.concat(fit(sizes, room, from, index + 1));
      }
    });
    return groups.map(function (group) { return bands.slice(group[0], group[1]); });
  }

  function show(index, direction) {
    current = Math.max(0, Math.min(index, pages.length - 1));
    bands.forEach(function (band) { band.hidden = pages[current].indexOf(band) < 0; });
    each(pager.children, function (dot, at) { dot.setAttribute("aria-pressed", String(at === current)); });
    slider.hidden = pages.length < 2;
    more.textContent = current === pages.length - 1 ? "Back to start" : "More insights →";
    if (direction) {
      area.classList.remove("is-next", "is-back");
      void area.offsetWidth;
      area.classList.add(direction > 0 ? "is-next" : "is-back");
    }
    plots.forEach(function (draw) { draw(); });
    want(pages[current]);
  }

  function go(index, direction) {
    show((index + pages.length) % pages.length, direction);
  }

  function arrange() {
    pager.textContent = "";
    if (!roomy.matches) {
      page.classList.remove("is-paged", "is-tight", "is-tiny");
      pages = [bands];
      show(0);
      return;
    }
    /* The first screen holds every band up to the one marked data-page-end. Try the normal spacing, then the
       compact one, then the smallest, which stays on (and the cards squeeze) if even that is too tall. */
    function measure(level) {
      page.classList.toggle("is-tight", level > 0);
      page.classList.toggle("is-tiny", level > 1);
      GAP = parseFloat(window.getComputedStyle(area).rowGap) || GAP;
      var sizes = natural(), first = 0, need = 0;
      bands.some(function (band, index) { first = index + 1; return band.hasAttribute("data-page-end"); });
      for (var i = 0; i < first; i += 1) { need += sizes[i] + (i ? GAP : 0); }
      return { sizes: sizes, fits: need <= area.clientHeight };
    }
    var measured = measure(0);
    for (var level = 1; level < 3 && !measured.fits; level += 1) { measured = measure(level); }
    var sizes = measured.sizes;
    bands.forEach(function (band, index) {
      band.style.maxHeight = Math.round(sizes[index] * (Number(band.getAttribute("data-grow")) || GROW)) + "px";
    });
    page.classList.add("is-paged");
    pages = split(sizes, area.clientHeight);
    if (pages.length > 1) {
      pages.forEach(function (unused, index) {
        var dot = make("button", "home-dot");
        dot.type = "button";
        dot.setAttribute("aria-label", "Page " + (index + 1) + " of " + pages.length);
        dot.addEventListener("click", function () { go(index, index > current ? 1 : -1); });
        pager.appendChild(dot);
      });
    }
    show(current);
  }

  var pending;
  function arrangeSoon() {
    window.clearTimeout(pending);
    pending = window.setTimeout(arrange, 60);
  }

  more.addEventListener("click", function () { go(current + 1, 1); });
  back.addEventListener("click", function () { go(current - 1, -1); });
  /* reaching for the button is a good sign the next slide is wanted: start fetching what it needs */
  ["mouseenter", "focus"].forEach(function (sign) {
    more.addEventListener(sign, function () { want(pages[(current + 1) % pages.length]); });
    back.addEventListener(sign, function () { want(pages[(current - 1 + pages.length) % pages.length]); });
  });
  document.addEventListener("keydown", function (event) {
    var tag = event.target.tagName;
    if (event.key === "Escape") { closePops(); }
    if (tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA" || event.altKey || event.ctrlKey || event.metaKey) { return; }
    if (event.key === "ArrowRight") { go(current + 1, 1); }
    if (event.key === "ArrowLeft") { go(current - 1, -1); }
  });
  window.addEventListener("resize", arrangeSoon);

  each(filters.querySelectorAll("select"), function (select) {
    select.addEventListener("change", function () { filters.submit(); });
  });

  /* small panels that open from a card (the activity list behind "More details") */
  function closePops() {
    each(panel.querySelectorAll(".home-pop"), function (pop) { pop.hidden = true; });
    each(panel.querySelectorAll("[data-open-pop]"), function (link) { link.setAttribute("aria-expanded", "false"); });
  }
  document.addEventListener("click", function (event) {
    var target = event.target;
    if (!target.closest) { return; }
    var opener = target.closest("[data-open-pop]");
    if (opener) {
      var pop = document.getElementById(opener.getAttribute("data-open-pop"));
      var open = pop.hidden;
      closePops();
      pop.hidden = !open;
      opener.setAttribute("aria-expanded", String(open));
      return;
    }
    var retry = target.closest("[data-retry]");
    if (retry) { load(retry.getAttribute("data-retry")); return; }
    if (target.closest("[data-close-pop]") || !target.closest(".home-pop")) { closePops(); }
  });

  /* ---------- parts: put a part's cards in place, or say why they are missing ---------- */

  function place(name, holder) {
    var script = holder.querySelector("script[type='application/json']");
    data[name] = script ? JSON.parse(script.textContent) : {};
    each(holder.querySelectorAll("[data-slot]"), function (card) {
      var target = panel.querySelector("#home-bands [data-slot='" + card.getAttribute("data-slot") + "']");
      if (target && target !== card) { target.parentNode.replaceChild(card, target); }
    });
    DRAW[name]();
    /* a card the part did not bring (an older page meeting a newer server) says so instead of waiting for ever */
    if (waiting(name).length) { failed(name, false); }
    arrangeSoon();
  }

  function waiting(name) {
    return panel.querySelectorAll("#home-bands [data-part='" + name + "']");
  }

  function failed(name, signedOut) {
    each(waiting(name), function (slot) {
      slot.classList.remove("home-bone");
      slot.classList.add("home-missing");
      slot.textContent = "";
      slot.appendChild(make("p", "", signedOut ? "You have been signed out." : "This could not be loaded."));
      var again = make("button", "home-link", signedOut ? "Sign in again" : "Try again");
      again.type = "button";
      if (signedOut) {
        again.addEventListener("click", function () { window.location.reload(); });
      } else {
        again.setAttribute("data-retry", name);
      }
      slot.appendChild(again);
    });
    arrangeSoon();
  }

  /* fetch the parts these bands still wait for; a part is asked for once, when a slide that shows it is opened */
  function want(shown) {
    (shown || []).forEach(function (band) {
      each(band.querySelectorAll("[data-part]"), function (slot) {
        var name = slot.getAttribute("data-part");
        if (!asked[name]) { load(name); }
      });
    });
  }

  function load(name) {
    asked[name] = true;
    each(waiting(name), function (slot) {
      slot.classList.remove("home-missing");
      slot.classList.add("home-bone");
      slot.textContent = "";
    });
    var request = new XMLHttpRequest();
    request.open("GET", panel.getAttribute("data-part-url").replace("PART", name));
    request.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    request.timeout = 60000;
    request.onload = function () {
      var ours = request.status === 200 && request.responseText.indexOf("data-slot=") >= 0;
      if (!ours) {
        failed(name, request.status === 200 || request.status === 401 || request.status === 403);
        return;
      }
      var holder = make("div");
      holder.innerHTML = request.responseText;
      try { place(name, holder); } catch (error) { failed(name, false); }
    };
    request.onerror = request.ontimeout = function () { failed(name, false); };
    request.send();
  }

  var ready = panel.querySelectorAll(".home-ready");
  each(ready, function (holder) { asked[holder.getAttribute("data-ready")] = true; });
  arrange();
  each(ready, function (holder) {
    try { place(holder.getAttribute("data-ready"), holder); } catch (error) { failed(holder.getAttribute("data-ready"), false); }
  });
})();
