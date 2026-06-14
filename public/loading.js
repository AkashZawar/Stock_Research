/*
 * loading.js - global "data is loading" indicator.
 *
 * Shows a slim animated progress bar at the top of the page whenever any
 * network request (fetch) is in flight, so slow API calls (analyze, search,
 * market monitor, recommendations, asset reports...) give clear feedback
 * instead of looking broken.
 *
 * It wraps window.fetch, so it must run BEFORE the app scripts call fetch -
 * include it as a normal (non-deferred) <script> in <head>.
 */
(function () {
  if (window.__loadingBarInstalled) {
    return;
  }
  window.__loadingBarInstalled = true;

  const style = document.createElement("style");
  style.textContent = `
    #globalLoadingBar {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      height: 3px;
      z-index: 2147483600;
      pointer-events: none;
      overflow: hidden;
      opacity: 0;
      transition: opacity 0.25s ease;
    }
    #globalLoadingBar.is-active { opacity: 1; }
    #globalLoadingBar .glb-fill {
      position: absolute;
      top: 0;
      left: 0;
      height: 100%;
      width: 35%;
      border-radius: 0 3px 3px 0;
      background: linear-gradient(
        90deg,
        transparent,
        var(--loading-accent, #0f766e) 35%,
        var(--loading-accent, #0f766e) 65%,
        transparent
      );
      animation: glb-slide 1.05s cubic-bezier(0.4, 0, 0.2, 1) infinite;
    }
    @keyframes glb-slide {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(330%); }
    }
    @media (prefers-reduced-motion: reduce) {
      #globalLoadingBar .glb-fill { animation-duration: 2.2s; }
    }
  `;

  const bar = document.createElement("div");
  bar.id = "globalLoadingBar";
  bar.setAttribute("role", "status");
  bar.setAttribute("aria-label", "Loading data");
  bar.innerHTML = '<div class="glb-fill"></div>';

  function mount() {
    if (document.head) {
      document.head.appendChild(style);
    }
    if (document.body) {
      document.body.appendChild(bar);
    }
  }
  if (document.body) {
    mount();
  } else {
    document.addEventListener("DOMContentLoaded", mount);
  }

  let pending = 0;
  let showTimer = null;

  function start() {
    pending += 1;
    // Only reveal the bar if the request is slow enough to matter (avoids a
    // flash for fast/cached responses).
    if (!showTimer && !bar.classList.contains("is-active")) {
      showTimer = setTimeout(() => {
        showTimer = null;
        if (pending > 0) {
          bar.classList.add("is-active");
        }
      }, 120);
    }
  }

  function done() {
    pending = Math.max(0, pending - 1);
    if (pending === 0) {
      if (showTimer) {
        clearTimeout(showTimer);
        showTimer = null;
      }
      bar.classList.remove("is-active");
    }
  }

  if (typeof window.fetch === "function") {
    const originalFetch = window.fetch.bind(window);
    window.fetch = function (...args) {
      start();
      let result;
      try {
        result = originalFetch(...args);
      } catch (error) {
        done();
        throw error;
      }
      return Promise.resolve(result).finally(done);
    };
  }
})();
