#!/usr/bin/env python3
"""Сборка статического сайта «Канон» в папку docs/ (для GitHub Pages).

Запуск:  python site/build.py
Данные:  site/content/articles.json (статьи), site/content/site.json (настройки, словарь, подписи к фото).
Только стандартная библиотека Python 3.8+. Картинки, шрифты, CSS и JS уже лежат в docs/assets.

Адрес сайта для превью ссылок (Telegram, VK) берётся из поля "url" в site.json;
его можно переопределить:  SITE_URL=https://USERNAME.github.io/REPO python site/build.py
"""
from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "docs"
CONTENT = ROOT / "content"
SITE_URL = ""  # задаётся в main(): переменная окружения SITE_URL или поле "url" в site.json

MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
MET_CREDIT = "Фото: The Metropolitan Museum of Art, Open Access (CC0)."
DASHES = re.compile("[\u2013\u2014]")

# Короткие слова, после которых ставим неразрывный пробел (русская типографика)
SHORT_WORDS = re.compile(r"(?<![\w-])(в|во|к|ко|с|со|у|о|об|и|а|но|на|по|за|из|от|до|не|ни|же|ли|бы|без|для|при|про|что|как|это|его|её|их|все|всё)\s", re.I)


def typo(text: str) -> str:
    text = text.replace("I WANNA MOG YOU", "I\u00a0WANNA\u00a0MOG\u00a0YOU")
    text = SHORT_WORDS.sub(lambda m: m.group(1) + "\u00a0", text)
    text = re.sub(r"(\d)\s(%|мм|см|мин|г\.|гг\.|в\.|балл)", "\\1\u00a0\\2", text)
    return text


def e(text: str) -> str:
    """Экранирование + типографика для видимого текста."""
    return html.escape(typo(text), quote=False)


def attr(text: str) -> str:
    return html.escape(text, quote=True)


def ru_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day}\u00a0{MONTHS[d.month - 1]} {d.year}"


def short_date(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d.day}\u00a0{MONTHS[d.month - 1]}"


def words(article: dict) -> int:
    n = 0
    for b in article["blocks"]:
        n += len((b.get("text") or "").split()) + sum(len(i.split()) for i in b.get("items") or [])
    return n


def read_time(article: dict) -> str:
    return f"{max(1, math.ceil(words(article) / 190))}\u00a0мин чтения"


# ---------------------------------------------------------------- картинки
def picture(slug: str, kind: str, prefix: str, sizes: str, alt: str, eager: bool = False) -> str:
    """kind: p (4:5) или l (3:2). Файлы: assets/img/{slug}-p600/1200.webp, -l750/1500.webp."""
    if kind == "p":
        small, big, w, h = 600, 1200, 1200, 1500
    else:
        small, big, w, h = 750, 1500, 1500, 1000
    base = f"{prefix}assets/img/{slug}-{kind}"
    loading = 'fetchpriority="high"' if eager else 'loading="lazy"'
    return (
        f'<img src="{base}{small}.webp" srcset="{base}{small}.webp {small}w, {base}{big}.webp {big}w" '
        f'sizes="{sizes}" width="{w}" height="{h}" alt="{attr(alt)}" {loading} decoding="async">'
    )


# ---------------------------------------------------------------- каркас страницы
def head(title: str, description: str, prefix: str, path: str, og_image: str | None, og_type: str = "website",
         base_tag: str = "") -> str:
    canonical = f"{SITE_URL}/{path}" if SITE_URL else ""
    og_img = ""
    if og_image:
        og_img_url = f"{SITE_URL}/{og_image}" if SITE_URL else f"{prefix}{og_image}"
        og_img = f'<meta property="og:image" content="{attr(og_img_url)}">\n<meta name="twitter:card" content="summary_large_image">'
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{attr(title)}</title>
<meta name="description" content="{attr(description)}">
<meta name="theme-color" content="#f4f4f1" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#111214" media="(prefers-color-scheme: dark)">
<meta property="og:type" content="{og_type}">
<meta property="og:title" content="{attr(title)}">
<meta property="og:description" content="{attr(description)}">
<meta property="og:locale" content="ru_RU">
{f'<meta property="og:url" content="{attr(canonical)}">' if canonical else ''}
{f'<link rel="canonical" href="{attr(canonical)}">' if canonical else ''}
{og_img}
{base_tag or assets_tags(prefix)}
<script>
(function(){{var r=document.documentElement;r.classList.add("js");try{{var t=localStorage.getItem("kanon-theme");if(t==="light"||t==="dark")r.setAttribute("data-theme",t);}}catch(e){{}}}})();
</script>
</head>"""


def assets_tags(prefix: str) -> str:
    return f"""<link rel="icon" href="{prefix}favicon.svg" type="image/svg+xml">
<link rel="preload" href="{prefix}assets/fonts/source-serif-4-normal-cyrillic.woff2" as="font" type="font/woff2" crossorigin>
<link rel="preload" href="{prefix}assets/fonts/geist-normal-cyrillic.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="{prefix}assets/css/fonts.css">
<link rel="stylesheet" href="{prefix}assets/css/icons.css">
<link rel="stylesheet" href="{prefix}assets/css/style.css">
<script src="{prefix}assets/js/main.js" defer></script>"""


NAV = [("Главное", "index.html#glavnoe"), ("Эксперимент", "index.html#eksperiment"), ("Лента", "index.html#lenta"),
       ("Словарь", "index.html#slovar"), ("О проекте", "about.html")]


def header(prefix: str, current: str = "") -> str:
    current_attr = ' aria-current="page"'
    links = "".join(
        f'<a href="{prefix}{href}"{current_attr if href == current else ""}>{label}</a>' for label, href in NAV
    )
    return f"""<body>
<a class="skip-link" href="#main">К содержанию</a>
<header class="site-header">
  <div class="wrap bar">
    <a class="logo" href="{prefix}index.html" aria-label="Канон, на главную"><span class="logo-mark" aria-hidden="true"></span>Канон</a>
    <nav class="nav" aria-label="Разделы">{links}</nav>
    <div class="bar-actions">
      <button class="icon-btn theme-toggle" type="button" aria-label="Сменить тему"><i class="ph ph-moon" aria-hidden="true"></i><i class="ph ph-sun" aria-hidden="true"></i></button>
      <button class="icon-btn menu-toggle" type="button" aria-expanded="false" aria-controls="mobile-nav" aria-label="Открыть меню"><i class="ph ph-list" aria-hidden="true"></i></button>
    </div>
  </div>
</header>
<nav class="mobile-nav" id="mobile-nav" aria-label="Меню">{links}</nav>"""


def footer(prefix: str, site: dict) -> str:
    year = dt.date.today().year
    return f"""<footer class="site-footer">
  <div class="wrap footer-grid">
    <div>
      <a class="logo" href="{prefix}index.html"><span class="logo-mark" aria-hidden="true"></span>Канон</a>
      <div class="footer-links">
        <a href="{prefix}index.html#lenta">Лента</a>
        <a href="{prefix}index.html#slovar">Словарь</a>
        <a href="{prefix}about.html">О проекте</a>
      </div>
    </div>
    <div class="footer-note">
      <p>{e(site["tagline"])}. Развлекательное издание: герои, компании и события в новостях вымышлены. Исключение: эксперимент с античными головами, его цифры настоящие.</p>
      <p style="margin-top:10px">Фотографии скульптур: <a href="https://www.metmuseum.org/hubs/open-access" rel="noopener">The Met, Open Access</a> (CC0). Шрифты Source Serif 4 и Geist (SIL OFL). © {year} Канон.</p>
    </div>
  </div>
</footer>
</body>
</html>
"""


def byline_meta(a: dict) -> str:
    return (f'<p class="meta"><span>{e(a["author"])}</span><time datetime="{a["date"]}">{ru_date(a["date"])}</time>'
            f'<span>{read_time(a)}</span></p>')


def article_url(slug: str, prefix: str) -> str:
    return f"{prefix}articles/{slug}.html"


# ---------------------------------------------------------------- главная
def render_index(arts: dict, order: list[str], site: dict) -> str:
    p = ""
    lead = arts[site["lead"]]
    ticker_items = "".join(
        f'<li><a href="{article_url(s, p)}">{e(arts[s]["title"])}</a></li>' for s in order
    )
    top = [arts[s] for s in site["top"]]
    main_top, rest_top = top[0], top[1:]

    def top_item(a, with_image):
        img = (f'<span class="media">{picture(a["slug"], "l", p, "(max-width: 680px) 92vw, (max-width: 1000px) 45vw, 26vw", a["image_alt"])}</span>'
               if with_image else "")
        return f"""<article class="top-item{'' if with_image else ' text-only'}" data-reveal>
  <a href="{article_url(a['slug'], p)}">{img}</a>
  <a class="rubric" href="{article_url(a['slug'], p)}">{e(a['category'])}</a>
  <h3><a href="{article_url(a['slug'], p)}"><span class="title-link">{e(a['title'])}</span></a></h3>
  <p class="summary">{e(a['summary'])}</p>
</article>"""

    # две колонки: «картинка + текст» и «текст + картинка», чтобы ритм не повторялся
    top_items = (
        f'<div class="top-col">{top_item(rest_top[0], True)}{top_item(rest_top[1], False)}</div>'
        f'<div class="top-col">{top_item(rest_top[2], False)}{top_item(rest_top[3], True)}</div>'
    )

    feed = "".join(
        f"""<li class="feed-item" data-reveal>
  <time class="feed-date" datetime="{a['date']}">{short_date(a['date'])}</time>
  <div class="feed-body">
    <a class="rubric" href="{article_url(a['slug'], p)}">{e(a['category'])}</a>
    <h3><a href="{article_url(a['slug'], p)}"><span class="title-link">{e(a['title'])}</span></a></h3>
    <p>{e(a['summary'])}</p>
  </div>
  <a href="{article_url(a['slug'], p)}" tabindex="-1" aria-hidden="true"><span class="media">{picture(a['slug'], 'p', p, '(max-width: 640px) 96px, 132px', '')}</span></a>
</li>"""
        for a in (arts[s] for s in order)
    )
    popular = "".join(f'<li><a href="{article_url(s, p)}"><span class="title-link">{e(arts[s]["title"])}</span></a></li>' for s in site["popular"])

    q_art = arts[site["quote_from"]]
    quote = next(b for b in q_art["blocks"] if b["type"] == "quote")

    exp = site["experiment"]
    stats = "".join(f'<div><dt>{e(s["value"])}</dt><dd>{e(s["label"])}</dd></div>' for s in exp["stats"])
    figs = "".join(
        f'<figure><img src="assets/img/{f["src"]}" width="{f["w"]}" height="{f["h"]}" loading="lazy" decoding="async" '
        f'alt="{attr(f["caption"])}"><figcaption>{e(f["caption"])}</figcaption></figure>'
        for f in exp["figures"]
    )
    glossary = "".join(f'<div class="term"><dt>{e(g["term"])}</dt><dd>{e(g["text"])}</dd></div>' for g in site["glossary"])

    body = f"""{header(p)}
<main id="main">
  <section class="wrap lead" aria-label="Главная новость">
    <div class="lead-head">
      <a class="rubric" href="{article_url(lead['slug'], p)}">{e(lead['category'])}</a>
      <h1><a href="{article_url(lead['slug'], p)}"><span class="title-link">{e(lead['title'])}</span></a></h1>
    </div>
    <a class="media" href="{article_url(lead['slug'], p)}">{picture(lead['slug'], 'l', p, '(max-width: 900px) 92vw, 58vw', lead['image_alt'], eager=True)}</a>
    <div class="lead-text">
      <p class="dek">{e(lead['dek'])}</p>
      {byline_meta(lead)}
      <a class="more-link" href="{article_url(lead['slug'], p)}">Читать <i class="ph ph-arrow-right" aria-hidden="true"></i></a>
    </div>
  </section>

  <section class="ticker" aria-label="Коротко">
    <div class="ticker-inner">
      <span class="ticker-label">Коротко</span>
      <div class="ticker-viewport">
        <div class="ticker-track">
          <ul class="ticker-list">{ticker_items}</ul>
          <ul class="ticker-list" aria-hidden="true">{ticker_items.replace('<a ', '<a tabindex="-1" ')}</ul>
        </div>
      </div>
    </div>
  </section>

  <section class="wrap block" id="glavnoe">
    <div class="block-head"><h2 class="block-title">Главное за неделю</h2></div>
    <div class="top-grid">
      <article class="top-main" data-reveal>
        <a href="{article_url(main_top['slug'], p)}"><span class="media">{picture(main_top['slug'], 'p', p, '(max-width: 680px) 92vw, (max-width: 1000px) 45vw, 36vw', main_top['image_alt'])}</span></a>
        <div style="display:flex;flex-direction:column;gap:14px">
          <a class="rubric" href="{article_url(main_top['slug'], p)}">{e(main_top['category'])}</a>
          <h3><a href="{article_url(main_top['slug'], p)}"><span class="title-link">{e(main_top['title'])}</span></a></h3>
          <p class="dek">{e(main_top['dek'])}</p>
          {byline_meta(main_top)}
        </div>
      </article>
      {top_items}
    </div>
  </section>

  <section class="experiment" id="eksperiment">
    <div class="wrap block experiment-grid">
      <div class="experiment-copy" data-reveal>
        <h2>{e(exp['title'])}</h2>
        <p>{e(exp['text'])}</p>
        <dl class="stats">{stats}</dl>
        <a class="more-link" href="{article_url(exp['slug'], p)}">Читать эксперимент <i class="ph ph-arrow-right" aria-hidden="true"></i></a>
      </div>
      <div class="experiment-figures" data-reveal>{figs}</div>
    </div>
  </section>

  <section class="wrap block feed" id="lenta">
    <div>
      <div class="block-head"><h2 class="block-title">Лента</h2></div>
      <ol class="feed-list" reversed>{feed}</ol>
    </div>
    <aside class="popular" aria-labelledby="popular-title">
      <h2 id="popular-title">Самое читаемое</h2>
      <ol>{popular}</ol>
    </aside>
  </section>

  <section class="wrap quote-band" aria-label="Цитата недели" data-reveal>
    <figure>
      <blockquote><p>«{e(quote['text'].strip('«»'))}»</p></blockquote>
      <figcaption><strong>{e(quote.get('cite', ''))}</strong><a class="more-link" href="{article_url(q_art['slug'], p)}">{e(q_art['title'])} <i class="ph ph-arrow-right" aria-hidden="true"></i></a></figcaption>
    </figure>
  </section>

  <section class="glossary" id="slovar" data-scroller>
    <div class="wrap block">
      <div class="block-head">
        <h2 class="block-title">Словарь луксмаксера</h2>
        <div class="scroller-controls">
          <button class="icon-btn" type="button" data-scroll-prev aria-label="Предыдущие термины"><i class="ph ph-arrow-left" aria-hidden="true"></i></button>
          <button class="icon-btn" type="button" data-scroll-next aria-label="Следующие термины"><i class="ph ph-arrow-right" aria-hidden="true"></i></button>
        </div>
      </div>
      <dl class="scroller" tabindex="0" aria-label="Термины">{glossary}</dl>
    </div>
  </section>
</main>
{footer(p, site)}"""
    return head(f"{site['name']}: {site['tagline']}", site["description"], p, "index.html",
                f"assets/og/{lead['slug']}.jpg") + "\n" + body


# ---------------------------------------------------------------- статья
def render_blocks(a: dict, site: dict, p: str) -> str:
    out = []
    h2_seen = 0
    for b in a["blocks"]:
        t = b["type"]
        if t == "h2":
            h2_seen += 1
            if a["slug"] == site["experiment"]["slug"] and h2_seen == 2:
                figs = "".join(
                    f'<figure><img src="{p}assets/img/{f["src"]}" width="{f["w"]}" height="{f["h"]}" loading="lazy" decoding="async" '
                    f'alt="{attr(f["caption"])}"><figcaption>{e(f["caption"])}</figcaption></figure>'
                    for f in site["experiment"]["figures"]
                )
                out.append(f'<div class="figure-pair">{figs}</div>')
            out.append(f"<h2>{e(b['text'])}</h2>")
        elif t == "p":
            out.append(f"<p>{e(b['text'])}</p>")
        elif t == "quote":
            text = b["text"].strip().strip("«»\"")
            out.append(f"<blockquote><p>«{e(text)}»</p><footer>{e(b.get('cite', ''))}</footer></blockquote>")
        elif t == "ul":
            out.append("<ul>" + "".join(f"<li>{e(i)}</li>" for i in b.get("items") or []) + "</ul>")
    return "\n".join(out)


def render_article(a: dict, arts: dict, order: list[str], site: dict) -> str:
    p = "../"
    img_info = site["images"][a["slug"]]
    caption = f"{img_info['caption']} {MET_CREDIT}"
    # «Читайте также»: три соседние по дате статьи другой рубрики, если есть
    others = [arts[s] for s in order if s != a["slug"]]
    others.sort(key=lambda o: (o["category"] == a["category"], abs(dt.date.fromisoformat(o["date"]).toordinal() - dt.date.fromisoformat(a["date"]).toordinal())))
    rel = others[:3]
    big, small = rel[0], rel[1:]
    related = f"""<li><a class="related-big" href="{article_url(big['slug'], p)}">
  <span class="media">{picture(big['slug'], 'p', p, '(max-width: 860px) 120px, 22vw', '')}</span>
  <span><span class="rubric">{e(big['category'])}</span><h3><span class="title-link">{e(big['title'])}</span></h3></span>
</a></li>""" + "".join(
        f"""<li><a class="related-row" href="{article_url(o['slug'], p)}">
  <span class="media">{picture(o['slug'], 'p', p, '120px', '')}</span>
  <span><span class="rubric">{e(o['category'])}</span><h3><span class="title-link">{e(o['title'])}</span></h3></span>
</a></li>""" for o in small)

    body = f"""{header(p)}
<main id="main">
  <article>
    <header class="narrow article-head">
      <a class="rubric" href="{p}index.html#lenta">{e(a['category'])}</a>
      <h1>{e(a['title'])}</h1>
      <p class="dek">{e(a['dek'])}</p>
      <div class="byline"><strong>{e(a['author'])}</strong><time datetime="{a['date']}"><i class="ph ph-clock" aria-hidden="true"></i>{ru_date(a['date'])}</time><span>{read_time(a)}</span></div>
    </header>
    <figure class="article-hero">
      <span class="media">{picture(a['slug'], 'l', p, '(max-width: 1120px) 96vw, 1080px', a['image_alt'], eager=True)}</span>
      <figcaption>{e(caption)}</figcaption>
    </figure>
    <div class="narrow prose">
{render_blocks(a, site, p)}
    </div>
    <div class="narrow">
      <div class="article-foot">
        <button class="btn" type="button" data-copy-link><i class="ph ph-link-simple" aria-hidden="true"></i><span>Скопировать ссылку</span></button>
        <a class="more-link" href="{p}index.html#lenta"><i class="ph ph-arrow-left" aria-hidden="true"></i> Все новости</a>
      </div>
    </div>
  </article>
  <section class="wrap related" aria-labelledby="related-title">
    <div class="block-head"><h2 class="block-title" id="related-title">Читайте также</h2></div>
    <ul class="related-list">{related}</ul>
  </section>
</main>
{footer(p, site)}"""
    return head(f"{a['title']} | {site['name']}", a["dek"], p, f"articles/{a['slug']}.html",
                f"assets/og/{a['slug']}.jpg", "article") + "\n" + body


# ---------------------------------------------------------------- о проекте и 404
def render_about(arts: dict, site: dict) -> str:
    p = ""
    credits = []
    for slug, info in site["images"].items():
        credits.append(f'<li>{e(info["caption"])} <a href="https://www.metmuseum.org/art/collection/search/{info["met"]}" rel="noopener">Карточка в коллекции The Met</a></li>')
    body = f"""{header(p, 'about.html')}
<main id="main" class="wrap page">
  <div class="page-grid">
    <div class="prose" style="padding-top:0">
      <h1 style="font-size:clamp(2.25rem,5vw,3.75rem);line-height:1.04;margin:0">О проекте</h1>
      <p>{e("«Канон»: развлекательное издание о луксмаксинге, трендах, привычках и спорах вокруг внешности.")}</p>
      <p>{e("Новости на сайте придуманы редакцией. Люди, клиники, студии и приложения в них вымышлены, любые совпадения случайны. Единственный материал с настоящими данными: эксперимент, в котором мы оценили 22 античные головы алгоритмом Telegram-бота I WANNA MOG YOU.")}</p>
      <p>{e("Мы не даём медицинских советов. Если вас беспокоит здоровье, кожа или зубы, обратитесь к врачу. Если мысли о внешности мешают жить, поговорите с близкими или психологом.")}</p>
      <h2>Откуда фотографии</h2>
      <p>{e("Все изображения скульптур взяты из открытой коллекции музея Метрополитен (The Met Open Access) и переданы в общественное достояние по лицензии CC0.")}</p>
      <ul class="credits">{''.join(credits)}</ul>
      <h2>Шрифты</h2>
      <p>{e("Source Serif 4 (Adobe) и Geist (Vercel), лицензия SIL Open Font License. Иконки Phosphor (MIT).")}</p>
    </div>
    <figure style="margin:0">
      <span class="media">{picture('about', 'p', p, '(max-width: 860px) 92vw, 40vw', site['images']['about']['caption'], eager=True)}</span>
      <figcaption style="margin-top:10px;font-size:.8125rem;color:var(--muted)">{e(site['images']['about']['caption'] + ' ' + MET_CREDIT)}</figcaption>
    </figure>
  </div>
</main>
{footer(p, site)}"""
    return head(f"О проекте | {site['name']}", "Что такое «Канон», откуда фотографии и почему новости вымышлены.", p,
                "about.html", "assets/og/about.jpg") + "\n" + body


# GitHub Pages отдаёт 404.html по любому неверному адресу, в том числе из вложенных папок,
# поэтому относительные пути на этой странице считаем от корня сайта через <base>.
# Стили и скрипт подключаются из этого же скрипта: иначе браузер заранее запросит их по неверному пути.
BASE_404_SCRIPT = """<script>(function(){var s=location.pathname.split('/'),b='/';
if(/\\.github\\.io$/.test(location.hostname)&&s.length>2&&s[1]&&s[1]!=='articles'){b='/'+s[1]+'/';}
document.write('<base href="'+b+'"><link rel="icon" href="'+b+'favicon.svg" type="image/svg+xml">'
+'<link rel="stylesheet" href="'+b+'assets/css/fonts.css"><link rel="stylesheet" href="'+b+'assets/css/icons.css">'
+'<link rel="stylesheet" href="'+b+'assets/css/style.css"><script src="'+b+'assets/js/main.js" defer><\\/script>');})();</script>"""


def render_404(site: dict) -> str:
    p = ""
    body = f"""{header(p)}
<main id="main" class="wrap not-found">
  <h1>404</h1>
  <p class="prose" style="padding:0">{e("Такой страницы нет. Возможно, новость переехала или ссылка набрана с ошибкой.")}</p>
  <p><a class="btn" href="{p}index.html"><i class="ph ph-arrow-left" aria-hidden="true"></i><span>На главную</span></a></p>
</main>
{footer(p, site)}"""
    base = (f'<base href="{attr(SITE_URL)}/">' + assets_tags("")) if SITE_URL else BASE_404_SCRIPT
    return head(f"Страница не найдена | {site['name']}", site["description"], p, "404.html", None, base_tag=base) + "\n" + body


# ---------------------------------------------------------------- сборка
def check_text(name: str, text: str) -> None:
    visible = text  # проверяем и видимый текст, и атрибуты (alt, description)
    if DASHES.search(visible):
        bad = DASHES.search(visible)
        raise SystemExit(f"{name}: в тексте есть длинное тире рядом с «{visible[max(0, bad.start() - 40):bad.end() + 40]}»")


def main() -> None:
    global SITE_URL
    site = json.loads((CONTENT / "site.json").read_text(encoding="utf-8"))
    SITE_URL = (os.environ.get("SITE_URL") or site.get("url") or "").rstrip("/")
    data = json.loads((CONTENT / "articles.json").read_text(encoding="utf-8"))
    arts = {a["slug"]: a for a in data["articles"]}
    for a in arts.values():
        a.setdefault("image_alt", site["images"][a["slug"]]["caption"])
    order = sorted(arts, key=lambda s: arts[s]["date"], reverse=True)

    (OUT / "articles").mkdir(parents=True, exist_ok=True)
    pages = {"index.html": render_index(arts, order, site), "about.html": render_about(arts, site), "404.html": render_404(site)}
    for slug in order:
        pages[f"articles/{slug}.html"] = render_article(arts[slug], arts, order, site)
    for name, text in pages.items():
        check_text(name, text)
        (OUT / name).write_text(text, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Готово: {len(pages)} страниц в {OUT}")


if __name__ == "__main__":
    main()
