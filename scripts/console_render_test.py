import asyncio

from playwright.async_api import async_playwright


VIDEO_URL = "https://example.revenant-walkthroughs.pages.dev/walkthrough.mp4"


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        await page.goto("http://127.0.0.1:8790/console.html", wait_until="domcontentloaded")
        result = await page.evaluate(
            """
            (url) => {
              const body = addMsg('rev', '');
              renderRichMessage(body, 'The Director finished the walkthrough:\\n\\n' + url);
              rememberBuild("The Engineer built and deployed HubSpot's Shroud prototype:\\n\\nhttps://example.revenant-prototypes.pages.dev");
              rememberFilm("The Director finished HubSpot's AI walkthrough:\\n\\n" + url);
              rememberDraft("✉️ HubSpot: sales pitch drafted.\\nSubject: HubSpot's CRM data security, solved\\nDeck: https://example.revenant-decks.pages.dev/hubspot-pitch.pptx\\nEmail draft: /tmp/hubspot-email.md");
              updateChips();
              return {
                videos: document.querySelectorAll('.mediaCard video').length,
                videoSrc: document.querySelector('.mediaCard video')?.src || '',
                chips: [...document.querySelectorAll('.chip')].map(x => x.textContent),
                history: recentHistory().map(x => x.content).join('\\n---\\n'),
              };
            }
            """,
            VIDEO_URL,
        )
        await browser.close()
        assert result["videos"] == 1, result
        assert result["videoSrc"] == VIDEO_URL, result
        assert "Film it" in result["chips"], result
        assert "Draft the outreach email" in result["chips"], result
        assert "Walkthrough URL" in result["history"], result
        assert "Last sales draft" in result["history"], result
        print("console video/draft rendering ok")


asyncio.run(main())
