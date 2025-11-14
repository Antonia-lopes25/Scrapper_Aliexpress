import asyncio
import json
import requests
from playwright.async_api import async_playwright

# =======================
# CONFIGURAÇÕES
# =======================

PRODUCT_URL = "https://pt.aliexpress.com/item/1005010000861871.html"  # <-- coloque a URL do produto
WEBHOOK_URL = "https://dropsmart.app.n8n.cloud/webhook/aliexpress-scraper"                 # <-- coloque a URL do seu webhook

# delay extra entre ações (em ms)
CLICK_DELAY = 1200
SCROLL_DELAY = 1200


async def fechar_popups(page):
    """Tenta fechar alguns popups comuns (cookies, modal, etc.)."""
    seletores = [
        'button[aria-label="close"]',
        'button[aria-label="Close"]',
        'button.cookie-banner-accept-button',
        '.btn-close',
        '.close',
        '.close-btn',
    ]
    for sel in seletores:
        try:
            el = await page.query_selector(sel)
            if el:
                print(f"[POPUP] Fechando: {sel}")
                await el.click()
                await page.wait_for_timeout(500)
        except Exception:
            pass


async def clicar_todos_ver_mais(page):
    """
    Clica em TODOS os elementos que tenham texto tipo "Ver mais", "View more", etc.
    Faz algumas passadas porque às vezes aparecem novos após clique / scroll.
    """
    labels_possiveis = [
        "Ver mais",
        "ver mais",
        "VER MAIS",
        "View more",
        "Mehr anzeigen",
        "Voir plus",
    ]

    for rodada in range(3):  # tenta várias vezes
        clicou_algum = False

        for label in labels_possiveis:
            # Playwright: engine de texto
            locator = page.get_by_text(label, exact=False)
            count = await locator.count()

            for i in range(count):
                try:
                    el = locator.nth(i)
                    # checa se é visível
                    if not await el.is_visible():
                        continue
                    print(f"[VER MAIS] Clicando em: '{label}' (rodada {rodada+1})")
                    await el.click()
                    await page.wait_for_timeout(CLICK_DELAY)
                    clicou_algum = True
                except Exception:
                    pass

        if not clicou_algum:
            break  # não tem mais nada pra clicar


async def rolar_ate_fim(page):
    """
    Rola a página até o final.
    Isso ajuda a carregar blocos que só aparecem com scroll.
    """
    ultimo_altura = await page.evaluate("() => document.body.scrollHeight")

    for _ in range(30):
        await page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_DELAY)

        nova_altura = await page.evaluate("() => document.body.scrollHeight")
        if nova_altura == ultimo_altura:
            print("[SCROLL] Chegou ao final da página.")
            break
        ultimo_altura = nova_altura


async def capturar_html_completo(url: str) -> str:
    """
    Abre a página, fecha popups, clica em 'Ver mais', rola até o fim
    e retorna o HTML completo (DOM renderizado).
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1366, "height": 900})

        print("[NAV] Acessando:", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # espera inicial
        await page.wait_for_timeout(3000)

        # fecha popups
        await fechar_popups(page)

        # primeira rodada de "ver mais"
        await clicar_todos_ver_mais(page)

        # rola até o fim (para carregar mais seções)
        await rolar_ate_fim(page)

        # mais uma rodada de "ver mais" depois do scroll
        await clicar_todos_ver_mais(page)

        # pequena espera extra
        await page.wait_for_timeout(2000)

        # HTML final do DOM
        html = await page.content()
        print("[HTML] Tamanho capturado:", len(html), "bytes")

        await browser.close()
        return html


def enviar_para_webhook(url: str, html: str):
    """
    Envia o HTML e a URL para o webhook do n8n.
    """
    payload = {
        "url": url,
        "html": html,
    }

    print("[WEBHOOK] Enviando para:", WEBHOOK_URL)
    resp = requests.post(
        WEBHOOK_URL,
        json=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=60,
    )
    print("[WEBHOOK] Status:", resp.status_code)
    try:
        print("[WEBHOOK] Resposta (início):", resp.text[:400])
    except Exception:
        pass


async def main():
    html = await capturar_html_completo(PRODUCT_URL)
    enviar_para_webhook(PRODUCT_URL, html)


if __name__ == "__main__":
    asyncio.run(main())
