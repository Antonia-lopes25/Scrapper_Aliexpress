import asyncio
import argparse
import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# ---------------- utils ----------------
def unique(seq):
    seen, out = set(), []
    for x in seq:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

def extract_product_id(url: str) -> Optional[str]:
    m = re.search(r"/item/(\d+)\.html", url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
    for k in ("productId", "product_id", "id"):
        if k in qs and qs[k]:
            return qs[k][0]
    return None

def is_product_image(url: str) -> bool:
    """Filtra ícones/sprites/tracking; mantém imagens de produto."""
    if not url or "bat.bing.com" in url:
        return False
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    good_host = ("ae01.alicdn.com", "aliexpress-media.com", "alicdn.com")
    if not any(h in u for h in good_host):
        return False
    small_pat = re.compile(
        r"/(\d{2}x\d{2}|\d{2,3}x\d{2,3})\.(png|jpg|jpeg|avif)(?:[_.].*)?$",
        re.I,
    )
    if small_pat.search(u):
        return False
    if not re.search(r"\.(jpg|jpeg|png|webp|avif)(?:[_.].*)?$", u, re.I):
        return False
    return True

def prettify_color(name: str) -> str:
    s = name.strip().lower()
    replacements = {
        "white": "branca",
        "black": "preta",
        "blue": "azul",
        "red": "vermelha",
        "pink": "rosa",
        "green": "verde",
        "yellow": "amarela",
        "purple": "roxa",
        "orange": "laranja",
        "gray": "cinza",
        "grey": "cinza",
        "silver": "prata",
        "gold": "dourada",
        "beige": "bege",
        "brown": "marrom",
        "transparent": "transparente",
    }
    for k, v in replacements.items():
        if s == k or s.endswith(" " + k) or k in s:
            return v if len(s) <= len(k) + 2 else s
    if re.search(r"(white|branco|branca)\b", s):
        return "branca"
    if re.search(r"(black|preto|preta)\b", s):
        return "preta"
    if re.search(r"(blue|azul)\b", s):
        return "azul"
    if re.search(r"(pink|rosa)\b", s):
        return "rosa"
    if re.search(r"(red|vermelho|vermelha)\b", s):
        return "vermelha"
    return name.strip()

# ---------------- globais do site ----------------
JS_SNIFFERS = [
    "return window.runParams || null;",
    "return (window._d_c_ && window._d_c_.DCData) ? window._d_c_.DCData : null;",
    "return (window.__AER_DATA__ && window.__AER_DATA__.store) ? window.__AER_DATA__ : null;",
]

async def get_global_jsons(page) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for js in JS_SNIFFERS:
        try:
            val = await page.evaluate(js)
            if val:
                data[js] = val
        except Exception:
            pass
    return data

# ---------------- título / imagens (página principal) ----------------
def pick_title(dom_html: str, g: Dict[str, Any]) -> Optional[str]:
    rp = g.get("return window.runParams || null;")
    if isinstance(rp, dict):
        d = rp.get("data") or rp.get("dataLayer") or rp
        if isinstance(d, dict):
            for k in ("title", "subject", "name", "prodName"):
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()
            if isinstance(d.get("pageModule"), dict):
                for k in ("title", "seoTitle"):
                    v = d["pageModule"].get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
    dc = g.get(
        "return (window._d_c_ && window._d_c_.DCData) ? window._d_c_.DCData : null;"
    )
    if isinstance(dc, dict):
        for k in ("title", "name", "subject"):
            v = dc.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

    soup = BeautifulSoup(dom_html, "lxml")
    for sel in [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        'meta[name="title"]',
        "title",
    ]:
        el = soup.select_one(sel)
        if el:
            content = el.get("content") if el.name == "meta" else el.get_text()
            if content and content.strip():
                return content.strip()
    h1 = soup.select_one("h1")
    return h1.get_text(strip=True) if h1 else None

def pick_images(dom_html: str, g: Dict[str, Any]) -> List[str]:
    imgs: List[str] = []
    rp = g.get("return window.runParams || null;")
    if isinstance(rp, dict):
        d = rp.get("data") or rp.get("dataLayer") or rp
        if isinstance(d, dict):
            candidates = []
            for key in (
                "images",
                "imagePathList",
                "imageModule",
                "galleryModule",
                "imageList",
                "summImagePathList",
            ):
                val = d.get(key)
                if isinstance(val, list):
                    candidates.extend(val)
                elif isinstance(val, dict):
                    for k2 in ("images", "imagePathList", "imageList", "summImagePathList"):
                        v2 = val.get(k2)
                        if isinstance(v2, list):
                            candidates.extend(v2)
            if isinstance(d.get("imageModule"), dict):
                for k in ("imagePathList", "images"):
                    v = d["imageModule"].get(k)
                    if isinstance(v, list):
                        for x in v:
                            imgs.append(
                                x["image"]
                                if isinstance(x, dict) and "image" in x
                                else x
                            )
            for c in candidates:
                if isinstance(c, str):
                    imgs.append(c)
                elif isinstance(c, dict):
                    for k in ("url", "image", "src"):
                        if isinstance(c.get(k), str):
                            imgs.append(c[k])

    dc = g.get(
        "return (window._d_c_ && window._d_c_.DCData) ? window._d_c_.DCData : null;"
    )
    if isinstance(dc, dict):
        for k in ("imagePathList", "summImagePathList", "images"):
            v = dc.get(k)
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, str):
                        imgs.append(x)
                    elif isinstance(x, dict) and isinstance(x.get("image"), str):
                        imgs.append(x["image"])

    aer = g.get(
        "return (window.__AER_DATA__ && window.__AER_DATA__.store) ? window.__AER_DATA__ : null;"
    )
    if isinstance(aer, dict):
        store = aer.get("store") or {}
        for path in (
            ("page", "product", "images"),
            ("page", "product", "imageModule", "images"),
            ("page", "product", "imageModule", "imagePathList"),
        ):
            ref: Any = store
            ok = True
            for p in path:
                if isinstance(ref, dict) and p in ref:
                    ref = ref[p]
                else:
                    ok = False
                    break
            if ok and isinstance(ref, list):
                for x in ref:
                    if isinstance(x, str):
                        imgs.append(x)
                    elif isinstance(x, dict):
                        for k in ("url", "image", "src"):
                            if isinstance(x.get(k), str):
                                imgs.append(x[k])

    soup = BeautifulSoup(dom_html, "lxml")
    og = soup.select_one('meta[property="og:image"]')
    if og and og.get("content"):
        imgs.append(og["content"])
    for sel in ["img[src]", "img[data-src]"]:
        for img in soup.select(sel):
            u = img.get("src") or img.get("data-src")
            if u and is_product_image(u):
                if u.startswith("//"):
                    u = "https:" + u
                imgs.append(u)
    imgs = [u for u in unique(imgs) if is_product_image(u)]
    return imgs

# ---------------- descrição + specs ----------------
def try_pick_desc_url_from_globals(globals_data: Dict[str, Any]) -> Optional[str]:
    for key in list(globals_data.keys()):
        blob = globals_data[key]
        if not isinstance(blob, dict):
            continue
        stack = [blob]
        seen = set()
        while stack:
            cur = stack.pop()
            if id(cur) in seen:
                continue
            seen.add(id(cur))
            if not isinstance(cur, dict):
                continue
            for k in ("productDescUrl", "detailDesc", "descUrl", "productDesc", "description"):
                v = cur.get(k)
                if isinstance(v, str) and v.startswith(("http://", "https://")):
                    return v
            for v in cur.values():
                if isinstance(v, dict):
                    stack.append(v)
                elif isinstance(v, list):
                    for it in v:
                        if isinstance(it, dict):
                            stack.append(it)
    return None

def extract_text_and_images_from_html(html: str) -> Tuple[str, List[str]]:
    soup = BeautifulSoup(html, "lxml")
    for bad in soup(["script", "style", "noscript"]):
        bad.extract()
    text = soup.get_text("\n", strip=True)
    imgs: List[str] = []
    for im in soup.select("img[src], img[data-src]"):
        u = im.get("src") or im.get("data-src")
        if u and is_product_image(u):
            if u.startswith("//"):
                u = "https:" + u
            imgs.append(u)
    return text, unique(imgs)

async def fetch_description_via_url(context, url: str) -> Optional[Tuple[str, List[str]]]:
    try:
        resp = await context.request.get(url, headers={"referer": "https://www.aliexpress.com/"})
        if not resp.ok:
            return None
        html = await resp.text()
        # só considera se de fato parece a página de descrição
        if 'id="product-description"' not in html and "detailmodule_html" not in html:
            return None
        return extract_text_and_images_from_html(html)
    except Exception:
        return None

async def collect_specs_from_dom(dom_html: str) -> List[str]:
    soup = BeautifulSoup(dom_html, "lxml")
    spec_root = soup.select_one("#nav-specification")
    if not spec_root:
        return []

    specs: List[str] = []

    # 1ª tentativa: classes com prefixo "specification--title--" / "specification--desc--"
    title_nodes = spec_root.find_all(class_=re.compile(r"^specification--title--"))
    desc_nodes = spec_root.find_all(class_=re.compile(r"^specification--desc--"))

    if title_nodes and desc_nodes:
        for t, d in zip(title_nodes, desc_nodes):
            k = t.get_text(strip=True)
            v = d.get_text(strip=True)
            if k and v:
                specs.append(f"{k}: {v}")
        if specs:
            return specs

    # fallback bem genérico: qualquer <li> dentro da aba
    for li in spec_root.select("li"):
        texts = [
            t.get_text(strip=True)
            for t in li.find_all(["span", "div"])
            if t.get_text(strip=True)
        ]
        if len(texts) >= 2:
            for i in range(0, len(texts) - 1, 2):
                k = texts[i]
                v = texts[i + 1]
                if k and v:
                    specs.append(f"{k}: {v}")

    # dedup
    specs = unique(specs)
    return specs

async def pick_description_and_specs(
    page,
    dom_html: str,
    globals_data: Dict[str, Any],
    desc_hits: List[str],
) -> Tuple[Optional[str], List[str], List[str]]:
    """
    Retorna: (description_text, specs_lines, desc_images)
    - Descrição: HTML de iframes extend--iframe--* ou URLs de rede com 'desc'/'detailmodule'.
    - Especificações: aba #nav-specification.
    """
    soup = BeautifulSoup(dom_html, "lxml")

    # --- montar lista de URLs candidatos para descrição ---
    candidate_urls: List[str] = []

    from_globals = try_pick_desc_url_from_globals(globals_data)
    if from_globals:
        candidate_urls.append(from_globals)

    # iframes "extend--iframe--..."
    for iframe in soup.select('iframe[class*="extend--iframe"]'):
        src = iframe.get("src")
        if src:
            if src.startswith("//"):
                src = "https:" + src
            candidate_urls.append(src)

    # respostas de rede com "desc"/"detailmodule"
    candidate_urls.extend(desc_hits)

    candidate_urls = unique(candidate_urls)

    desc_text: Optional[str] = None
    desc_imgs: List[str] = []

    # tenta baixar cada URL candidato até achar um HTML com #product-description
    for u in candidate_urls:
        got = await fetch_description_via_url(page.context, u)
        if not got:
            continue
        text, imgs = got
        if text and len(text.strip()) > 40:  # evita lixo curtinho
            desc_text = text
            desc_imgs.extend(imgs)
            break

    # fallback: caso, por algum motivo, a descrição esteja inline na própria página
    if not desc_text:
        block = soup.select_one("#product-description.product-description")
        if block:
            for bad in block.select("script,style,noscript"):
                bad.extract()
            t = block.get_text("\n", strip=True)
            if t:
                desc_text = t
            for im in block.select("img[src],img[data-src]"):
                u = im.get("src") or im.get("data-src")
                if u:
                    if u.startswith("//"):
                        u = "https:" + u
                    desc_imgs.append(u)

    # specs sempre lidas do DOM principal (não dependem do HTML externo)
    specs_lines = await collect_specs_from_dom(dom_html)

    # normalização da descrição
    if desc_text:
        parts = [ln.strip() for ln in re.split(r"[\r\n]+", desc_text)]
        parts = [p for p in parts if p and len(p) > 1]
        desc_text = "\n".join(parts[:2000])

    desc_imgs = [u for u in unique(desc_imgs) if is_product_image(u)]
    return desc_text, specs_lines, desc_imgs

# ---------------- variações (clicando e lendo labels humanos) ----------------
async def collect_variations_human_labels(page) -> List[Dict[str, Any]]:
    """
    Para cada grupo de variação, clica nas opções e tenta ler rótulo humano:
    - atributos do item selecionado (title/aria-label/…)
    - resumo de seleção (ex.: "Cor: Azul")
    - miniatura ativa da galeria
    - fallback: nome da imagem
    """
    # tenta expandir "ver mais"
    for sel in [
        'button:has-text("Ver mais")',
        'button:has-text("More")',
        ".sku-expand, .sku-more, .attr-more",
    ]:
        try:
            btns = await page.query_selector_all(sel)
            for b in btns[:5]:
                try:
                    await b.click()
                except Exception:
                    pass
        except Exception:
            pass

    groups = await page.query_selector_all(
        ".sku-item--skus--StEhULs, [data-sku-row], .sku-property, .product-sku"
    )
    results: List[Dict[str, Any]] = []

    async def get_group_name(g):
        for s in [
            ".sku-item--title",
            ".sku-title",
            ".sku-property-name",
            ".sku-name",
            "[data-sku-prop-name]",
        ]:
            el = await g.query_selector(s)
            if el:
                t = (await el.inner_text() or "").strip()
                if t:
                    return re.sub(r"\s+", " ", t)
        return "Opção"

    async def read_selection_summary():
        cand = await page.query_selector(
            'div:has-text("Selecionado"):not(:has(*:has-text("Selecionado"))), '
            '[class*="selected"]:has(span), [class*="Selected"]:has(span)'
        )
        if cand:
            txt = (await cand.inner_text() or "").strip()
            txt = re.sub(r"\s+", " ", txt)
            if txt and len(txt) < 120:
                return txt
        for s in [
            'div:has-text("Cor")',
            'div:has-text("Color")',
            '[class*="sku-summary"]',
            '[class*="sku-selected"]',
        ]:
            el = await page.query_selector(s)
            if el:
                txt = (await el.inner_text() or "").strip()
                txt = re.sub(r"\s+", " ", txt)
                if txt and len(txt) < 200:
                    return txt
        return None

    async def read_active_thumb_label():
        for s in [
            ".images-view-list .item.active img",
            ".image-thumb.active img",
            '[class*="thumbnail"][class*="active"] img',
        ]:
            el = await page.query_selector(s)
            if el:
                for attr in ["alt", "title", "aria-label"]:
                    v = await el.get_attribute(attr)
                    if v and v.strip():
                        return v.strip()
        return None

    for g in groups:
        name = await get_group_name(g)
        values: List[str] = []
        opts = await g.query_selector_all(
            "[data-sku-col], [data-sku-id], li, button, a, [role='radio'], input[type='radio']+label"
        )
        seen_labels = set()
        for o in opts[:60]:
            try:
                await o.scroll_into_view_if_needed()
                await page.wait_for_timeout(60)
                try:
                    await o.click()
                except Exception:
                    try:
                        p = await o.evaluate_handle(
                            "el => el.closest('li,button,a,[role=radio]') || el"
                        )
                        await p.click()
                    except Exception:
                        pass

                await page.wait_for_timeout(120)

                label: Optional[str] = None
                for attr in ["title", "aria-label", "data-title", "data-sku-text"]:
                    v = await o.get_attribute(attr)
                    if v and v.strip():
                        label = v.strip()
                        break
                if not label:
                    txt = (await o.inner_text() or "").strip()
                    if txt:
                        label = txt

                if not label or re.fullmatch(r"MS[\w-]+", label or "", re.I):
                    summary = await read_selection_summary()
                    if summary and ":" in summary:
                        cand = summary.split(":")[-1].strip()
                        if cand:
                            label = cand

                if not label or re.fullmatch(r"MS[\w-]+", label or "", re.I):
                    thumb = await read_active_thumb_label()
                    if thumb:
                        label = thumb

                if not label:
                    img = await o.query_selector("img")
                    if img:
                        for a in ["alt", "title", "aria-label", "src", "data-src"]:
                            v = await img.get_attribute(a)
                            if v and v.strip():
                                label = v.strip()
                                break

                if label:
                    label = re.sub(r"\s+", " ", label)
                    pretty = prettify_color(label)
                    if pretty not in seen_labels:
                        seen_labels.add(pretty)
                        values.append(pretty)
            except Exception:
                continue

        if values:
            results.append({"name": name, "values": values})

    merged: Dict[str, List[str]] = {}
    for v in results:
        n = v.get("name") or "Opção"
        merged.setdefault(n, [])
        for val in v.get("values", []):
            if val not in merged[n]:
                merged[n].append(val)
    return [{"name": n, "values": merged[n]} for n in merged]

# ---------------- core ----------------
async def scrape_text_fields(
    url: str, timeout: int = 60, proxy: Optional[str] = None, delay: float = 0.0
) -> Dict[str, Any]:
    async with async_playwright() as pw:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        launch_kwargs: Dict[str, Any] = dict(headless=True, args=args)
        if proxy:
            u = urlparse(proxy)
            if u.scheme and u.hostname and u.port:
                proxy_cfg: Dict[str, Any] = {
                    "server": f"{u.scheme}://{u.hostname}:{u.port}"
                }
                if u.username:
                    proxy_cfg["username"] = u.username
                if u.password:
                    proxy_cfg["password"] = u.password
                launch_kwargs["proxy"] = proxy_cfg

        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            locale="pt-BR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )

        page = await context.new_page()
        page.set_default_timeout(timeout * 1000)

        # lista mutável pra guardar URLs de descrição vistas na rede
        desc_hits: List[str] = []

        def _on_response(resp):
            try:
                url_l = resp.url.lower()
                ctype = resp.headers.get("content-type", "").lower()
                if (
                    any(k in url_l for k in ("desc", "detailmodule", "productdesc"))
                    and "text/html" in ctype
                ):
                    desc_hits.append(resp.url)
            except Exception:
                pass

        page.on("response", _on_response)

        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        except Exception:
            pass

        # fecha popups
        for sel in [
            'button:has-text("Aceitar")',
            'button:has-text("Accept")',
            'button:has-text("Concordo")',
            'button[aria-label="close"]',
            "[data-role='close']",
            ".close-btn",
            ".btn-close",
        ]:
            try:
                el = await page.query_selector(sel)
                if el:
                    await el.click()
            except Exception:
                pass

        if delay > 0:
            await page.wait_for_timeout(int(delay * 1000))

        # dá uma rolada pra forçar o carregamento de módulos
        try:
            for _ in range(3):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(700)
        except Exception:
            pass

        dom_html = await page.content()
        globals_data = await get_global_jsons(page)

        title = pick_title(dom_html, globals_data)
        images_main = pick_images(dom_html, globals_data)
        description_text, specs_lines, desc_imgs = await pick_description_and_specs(
            page, dom_html, globals_data, desc_hits
        )
        all_images = unique(
            [u for u in images_main + (desc_imgs or []) if is_product_image(u)]
        )
        variations = await collect_variations_human_labels(page)

        result = {
            "url": url,
            "product_id": extract_product_id(url),
            "title": title,
            "images": all_images,
            "description_text": description_text,
            "specs": specs_lines,
            "variations": variations,
        }

        await context.close()
        await browser.close()
        return result

# ---------------- CLI ----------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Extrai TEXTO do AliExpress: título, imagens (filtradas), "
            "descrição/informações gerais (iframe/HTML externo) e especificações + variações "
            "(inclui swatches de cor; sem preço/estoque)."
        )
    )
    parser.add_argument("url", help="URL do produto (ex.: https://pt.aliexpress.com/item/...)")
    parser.add_argument("--out", default="produto_texto.json", help="Arquivo de saída JSON")
    parser.add_argument("--timeout", type=int, default=60, help="Timeout (segundos)")
    parser.add_argument(
        "--proxy",
        default=None,
        help="Proxy http://user:pass@host:port ou socks5://host:port",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="Atraso extra após carregamento (segundos)",
    )
    args = parser.parse_args()

    data = asyncio.run(
        scrape_text_fields(
            args.url, timeout=args.timeout, proxy=args.proxy, delay=args.delay
        )
    )
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✔ Salvo em: {args.out}")
