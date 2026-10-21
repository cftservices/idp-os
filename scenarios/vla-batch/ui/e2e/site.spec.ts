import { test, expect } from "@playwright/test";

/**
 * De 3D site-twin op /site.
 *
 * De scène zelf is een canvas en dus niet met DOM-asserties te lezen. Alles
 * wat een gebruiker moet kunnen weten, staat daarom óók in de DOM: de
 * objectlijst, het infopaneel en de KPI-strook. Die worden hier getoetst
 * tegen de echte engine.
 */

const LINE_OBJECTS = '[data-twin-object^="equipment:"]';
const STATE_WORDS = /stil|draait|aandacht|alarm|storing|verouderd|geen data|vervuild|reinigen/i;

test.beforeEach(async ({ page }) => {
  await page.goto("/site");
  // Geen networkidle: de twin pollt doorlopend, dus die toestand komt niet,
  // en zonder eigen timeout vreet het wachten de hele testtijd op.
  await expect(page.locator("[data-twin-hud]")).toBeVisible({ timeout: 30_000 });
});

test("de scène tekent een canvas en de lijn staat in de objectlijst", async ({ page }) => {
  await expect(page.locator(".twin-stage canvas")).toBeVisible();
  const items = page.locator(LINE_OBJECTS);
  await expect(items).toHaveCount(5);
  for (const item of await items.all()) {
    await expect(item).toHaveText(STATE_WORDS);
    await expect(item).toHaveAttribute("data-twin-state", /.+/);
  }
});

test("een object kiezen opent het paneel, Escape sluit het", async ({ page }) => {
  const cook = page.locator('[data-twin-object="equipment:cook-unit-01"]');
  await cook.click();
  await expect(cook).toHaveAttribute("aria-pressed", "true");
  const panel = page.locator("[data-twin-panel]");
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute("data-twin-panel", "equipment:cook-unit-01");
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);
});

test("het paneel linkt door naar het bestaande scherm", async ({ page }) => {
  await page.locator('[data-twin-object="equipment:cook-unit-01"]').click();
  await page.locator("[data-twin-panel]").getByRole("link", { name: /Equipment-overzicht/ }).click();
  // Ruim: met software-WebGL kost het afbreken van de scène zelf al seconden.
  await expect(page).toHaveURL(/\/equipment$/, { timeout: 20_000 });
});

test("een KPI zonder waarde toont een streepje, nooit een nul", async ({ page }) => {
  const cells = page.locator("[data-twin-kpi]");
  await expect(cells.first()).toBeVisible();
  for (const cell of await cells.all()) {
    const value = (await cell.locator("dd.num").textContent())?.trim() ?? "";
    expect(value, `${await cell.getAttribute("data-twin-kpi")}`).not.toMatch(/^0([.,]0+)?(\s|$)/);
  }
});

test("op een telefoon blijven scène en paneel binnen het scherm", async ({ page }) => {
  // Laadt de scène twee keer (goto + reload); met software-WebGL krap binnen 60 s.
  test.setTimeout(120_000);
  // scrollWidth zag dit niet: de stage stak uit, maar de pagina verbreedde niet.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.reload();
  await page.getByRole("button", { name: "Objecten" }).click();
  await page.locator('[data-twin-object="equipment:cook-unit-01"]').click();
  for (const sel of [".twin-stage", "[data-twin-panel]"]) {
    const box = await page.locator(sel).boundingBox();
    expect(box, sel).not.toBeNull();
    expect(box!.x + box!.width, `${sel} steekt rechts uit`).toBeLessThanOrEqual(375);
  }
});

test("zoeken met / vindt een object en opent de detailkaart", async ({ page }) => {
  await page.keyboard.press("/");
  const search = page.getByRole("combobox", { name: /Zoek een object/ });
  await expect(search).toBeFocused();
  await search.fill("cook");
  await expect(page.getByRole("option").first()).toBeVisible();
  await search.press("Enter");
  await expect(page.locator("[data-twin-panel]")).toHaveAttribute("data-twin-panel", /^equipment:/);
});

test("tabbladen tonen aantallen en wisselen de lijst", async ({ page }) => {
  const tab = page.getByRole("tab", { name: /Grondstoffen/ });
  await tab.click();
  await expect(tab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator(LINE_OBJECTS)).toHaveCount(0);
  await page.getByRole("tab", { name: /Lijn/ }).click();
  await expect(page.locator(LINE_OBJECTS)).toHaveCount(5);
});

/*
 * Uitzondering op "geen mocks": HU's zijn lokaal niet te seeden, want
 * create_hu eist een APPROVED batch en die vraagt de fabriekssimulatie.
 * Alleen het HU-antwoord wordt vervangen; de rest komt van de echte engine.
 */
test("HU's die op verzending wachten, zetten een truck aan een dock", async ({ page }) => {
  const ts = new Date().toISOString();
  await page.route(/\/hu(\?.*)?$/, (route) =>
    route.fulfill({
      json: [
        { hu_id: "HU-T1", batch_id: "B-TEST-1", packs_count: 40, location: null, status: "awaiting_shipment", ts },
        { hu_id: "HU-T2", batch_id: "B-TEST-1", packs_count: 40, location: null, status: "shipped", ts },
      ],
    }),
  );
  await page.reload();
  await page.getByRole("tab", { name: /Magazijn/ }).click();
  const dock = page.locator('[data-twin-object="dock:B-TEST-1"]');
  await expect(dock).toBeVisible();
  await dock.click();
  const panel = page.locator("[data-twin-panel]");
  await expect(panel).toContainText(/afgeleid uit HU-status/);
  await expect(panel.getByRole("link", { name: /Batchrapport B-TEST-1/ })).toHaveAttribute("href", "/report/B-TEST-1");
});
