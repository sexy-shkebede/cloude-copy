/* Канон: интерактив без зависимостей. Тема, меню, переход к содержанию, лента «Коротко»,
   появление блоков, копирование ссылки, листание словаря. */
(function () {
  var root = document.documentElement;
  var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function storageGet(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function storageSet(key, value) { try { window.localStorage.setItem(key, value); } catch (e) { /* приватный режим */ } }

  /* ---------- «К содержанию»: фокус на <main> (работает и на 404 с <base>, где #main ведёт на другой адрес) ---------- */
  document.querySelectorAll(".skip-link").forEach(function (link) {
    link.addEventListener("click", function (e) {
      var main = document.getElementById("main");
      if (!main) return;
      e.preventDefault();
      if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
      main.focus();
    });
  });

  /* ---------- тема ---------- */
  var themeBtn = document.querySelector(".theme-toggle");
  function currentTheme() {
    var explicit = root.getAttribute("data-theme");
    if (explicit) return explicit;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function syncThemeLabel() {
    if (!themeBtn) return;
    // подпись называет действие, поэтому aria-pressed здесь не нужен
    themeBtn.setAttribute("aria-label", currentTheme() === "dark" ? "Включить светлую тему" : "Включить тёмную тему");
  }
  if (themeBtn) {
    themeBtn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      storageSet("kanon-theme", next);
      syncThemeLabel();
    });
    syncThemeLabel();
  }

  /* ---------- мобильное меню ---------- */
  var menuBtn = document.querySelector(".menu-toggle");
  var mobileNav = document.getElementById("mobile-nav");
  function setMenu(open) {
    if (!menuBtn || !mobileNav) return;
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
    menuBtn.setAttribute("aria-label", open ? "Закрыть меню" : "Открыть меню");
    menuBtn.querySelector(".ph").className = "ph " + (open ? "ph-x" : "ph-list");
    mobileNav.classList.toggle("is-open", open);
    mobileNav.toggleAttribute("inert", !open);
    document.body.style.overflow = open ? "hidden" : "";
  }
  if (menuBtn && mobileNav) {
    mobileNav.setAttribute("inert", "");
    menuBtn.addEventListener("click", function () { setMenu(menuBtn.getAttribute("aria-expanded") !== "true"); });
    mobileNav.addEventListener("click", function (e) { if (e.target.closest("a")) setMenu(false); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && menuBtn.getAttribute("aria-expanded") === "true") {
        setMenu(false);
        menuBtn.focus();
      }
    });
    window.matchMedia("(min-width: 861px)").addEventListener("change", function (e) { if (e.matches) setMenu(false); });
  }

  /* ---------- лента «Коротко»: пауза, запоминается между визитами ---------- */
  var ticker = document.querySelector(".ticker");
  var tickerBtn = ticker && ticker.querySelector(".ticker-toggle");
  if (ticker && tickerBtn) {
    var setTicker = function (paused, save) {
      ticker.classList.toggle("is-paused", paused);
      tickerBtn.setAttribute("aria-pressed", paused ? "true" : "false");
      if (save) storageSet("kanon-ticker", paused ? "paused" : "running");
    };
    setTicker(storageGet("kanon-ticker") === "paused", false);
    tickerBtn.addEventListener("click", function () { setTicker(tickerBtn.getAttribute("aria-pressed") !== "true", true); });
  }

  /* ---------- появление блоков ---------- */
  var revealEls = document.querySelectorAll("[data-reveal]");
  if (!reduceMotion && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) { entry.target.classList.add("is-in"); io.unobserve(entry.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    revealEls.forEach(function (el) { io.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add("is-in"); });
  }

  /* ---------- копирование ссылки ---------- */
  document.querySelectorAll("[data-copy-link]").forEach(function (btn) {
    var label = btn.querySelector("span");
    var icon = btn.querySelector(".ph");
    var initial = label ? label.textContent : "";
    btn.addEventListener("click", function () {
      var url = window.location.href.split("#")[0];
      var done = function () {
        btn.classList.add("is-done");
        if (label) label.textContent = "Ссылка скопирована";
        if (icon) icon.className = "ph ph-check";
        setTimeout(function () {
          btn.classList.remove("is-done");
          if (label) label.textContent = initial;
          if (icon) icon.className = "ph ph-link-simple";
        }, 2200);
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(url).then(done, function () { window.prompt("Скопируйте ссылку:", url); });
      } else {
        window.prompt("Скопируйте ссылку:", url);
      }
    });
  });

  /* ---------- листание словаря ---------- */
  document.querySelectorAll("[data-scroller]").forEach(function (box) {
    var scroller = box.querySelector(".scroller");
    var prev = box.querySelector("[data-scroll-prev]");
    var next = box.querySelector("[data-scroll-next]");
    if (!scroller || !prev || !next) return;
    var items = scroller.children;
    function step(dir) {
      var first = items[0];
      var amount = first ? first.getBoundingClientRect().width + 16 : 300;
      scroller.scrollBy({ left: dir * amount, behavior: reduceMotion ? "auto" : "smooth" });
    }
    prev.addEventListener("click", function () { step(-1); });
    next.addEventListener("click", function () { step(1); });
    if ("IntersectionObserver" in window && items.length) {
      var edge = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          var btn = entry.target === items[0] ? prev : next;
          var other = btn === prev ? next : prev;
          var off = entry.intersectionRatio > 0.95;
          // отключённая кнопка теряет фокус: заранее переводим его на соседнюю (или на сам список)
          if (off && document.activeElement === btn) (other.disabled ? scroller : other).focus();
          btn.disabled = off;
        });
      }, { root: scroller, threshold: [0, 0.95, 1] });
      edge.observe(items[0]);
      edge.observe(items[items.length - 1]);
    }
  });
})();
