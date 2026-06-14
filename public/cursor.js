/*
 * Animated pointer for laptop/desktop (fine-pointer) devices.
 * - Skips touch / coarse-pointer devices and users who prefer reduced motion.
 * - Adds a dot at the exact pointer plus a trailing ring that eases behind it,
 *   grows over interactive elements, and pulses on click.
 * - Self-contained: injects its own styles so it works on every page.
 */
(function () {
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!finePointer.matches || reduceMotion.matches) {
    return;
  }

  const style = document.createElement("style");
  style.textContent = `
    html.cursor-active,
    html.cursor-active * { cursor: none !important; }
    html.cursor-active input,
    html.cursor-active textarea,
    html.cursor-active select,
    html.cursor-active [contenteditable="true"] { cursor: auto !important; }

    .cursor-dot,
    .cursor-ring {
      position: fixed;
      top: 0;
      left: 0;
      z-index: 2147483000;
      pointer-events: none;
      border-radius: 50%;
      opacity: 0;
      transform: translate3d(-50%, -50%, 0);
      will-change: transform;
    }
    .cursor-dot {
      width: 7px;
      height: 7px;
      background: var(--cursor-accent, #0f766e);
      transition: opacity 0.25s ease, width 0.15s ease, height 0.15s ease;
    }
    .cursor-ring {
      width: 34px;
      height: 34px;
      border: 2px solid var(--cursor-accent, #0f766e);
      transition: width 0.18s ease, height 0.18s ease, background 0.18s ease,
                  border-color 0.18s ease, opacity 0.25s ease;
    }
    html.cursor-ready .cursor-dot,
    html.cursor-ready .cursor-ring { opacity: 1; }

    .cursor-ring.is-hover {
      width: 54px;
      height: 54px;
      background: color-mix(in srgb, var(--cursor-accent, #0f766e) 16%, transparent);
    }
    .cursor-dot.is-hover { width: 10px; height: 10px; }
    .cursor-ring.is-click { width: 24px; height: 24px; }
    .cursor-dot.is-click { width: 12px; height: 12px; }
    html.cursor-hidden .cursor-dot,
    html.cursor-hidden .cursor-ring { opacity: 0; }
  `;
  document.head.appendChild(style);

  const root = document.documentElement;
  const dot = document.createElement("div");
  dot.className = "cursor-dot";
  const ring = document.createElement("div");
  ring.className = "cursor-ring";
  document.body.appendChild(dot);
  document.body.appendChild(ring);
  root.classList.add("cursor-active");

  let pointerX = window.innerWidth / 2;
  let pointerY = window.innerHeight / 2;
  let ringX = pointerX;
  let ringY = pointerY;
  let started = false;

  const interactiveSelector =
    "a, button, input, select, textarea, label, summary, [role='button']," +
    " [data-monitor-pane-button], [data-oi-period], [data-asset-shortlist-symbol], .tab-button";

  document.addEventListener("mousemove", (event) => {
    pointerX = event.clientX;
    pointerY = event.clientY;
    if (!started) {
      started = true;
      root.classList.add("cursor-ready");
    }
    root.classList.remove("cursor-hidden");
    dot.style.transform = `translate3d(${pointerX}px, ${pointerY}px, 0) translate(-50%, -50%)`;
  }, { passive: true });

  document.addEventListener("mouseover", (event) => {
    if (event.target.closest && event.target.closest(interactiveSelector)) {
      ring.classList.add("is-hover");
      dot.classList.add("is-hover");
    }
  });

  document.addEventListener("mouseout", (event) => {
    const from = event.target.closest && event.target.closest(interactiveSelector);
    const to = event.relatedTarget && event.relatedTarget.closest
      ? event.relatedTarget.closest(interactiveSelector)
      : null;
    if (from && !to) {
      ring.classList.remove("is-hover");
      dot.classList.remove("is-hover");
    }
  });

  document.addEventListener("mousedown", () => {
    ring.classList.add("is-click");
    dot.classList.add("is-click");
  });
  document.addEventListener("mouseup", () => {
    ring.classList.remove("is-click");
    dot.classList.remove("is-click");
  });

  document.addEventListener("mouseleave", () => root.classList.add("cursor-hidden"));
  window.addEventListener("blur", () => root.classList.add("cursor-hidden"));

  function animate() {
    ringX += (pointerX - ringX) * 0.18;
    ringY += (pointerY - ringY) * 0.18;
    ring.style.transform = `translate3d(${ringX}px, ${ringY}px, 0) translate(-50%, -50%)`;
    requestAnimationFrame(animate);
  }
  requestAnimationFrame(animate);
})();
