import { test, expect } from "@playwright/test";

/**
 * Navigatie.
 *
 * Deze tests bestaan om een reden die geen andere test opving: de UI had elf
 * werkende routes en NUL links ertussen. Elk scherm deed het prima, alleen
 * kwam er niemand. CIP stond op /equipment en was in de praktijk onvindbaar.
 *
 * Twee dingen worden hier bewaakt:
 *   1. elk scherm is bereikbaar zonder een URL te typen;
 *   2. geen enkel scherm is een doodlopende weg.
 */

const SCHERMEN = [
  { pad: "/", naam: "Plant" },
  { pad: "/management", naam: "Management" },
  { pad: "/site", naam: "3D Site" },
  { pad: "/orders", naam: "Orders" },
  { pad: "/batches", naam: "Batches" },
  { pad: "/equipment", naam: "Equipment" },
  { pad: "/voorraad", naam: "Voorraad" },
  { pad: "/reports", naam: "Rapporten" },
  { pad: "/analyse", naam: "Analyse" },
  { pad: "/line", naam: "Lijn L1" },
  { pad: "/alarms", naam: "Alarmen" },
  { pad: "/shopfloor", naam: "Werkvloer" },
  { pad: "/scada", naam: "SCADA" },
  { pad: "/storingen", naam: "Storingen" },
];

test("elk scherm staat in de zijbalk en is bereikbaar vanaf de landing", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const menu = page.getByRole("navigation", { name: "Hoofdmenu" });
  await expect(menu).toBeVisible();

  for (const scherm of SCHERMEN) {
    const link = menu.getByRole("link", { name: scherm.naam, exact: true });
    await expect(link, `"${scherm.naam}" ontbreekt in de zijbalk`).toHaveAttribute(
      "href",
      scherm.pad,
    );
  }
});

test("de zijbalk klapt in op een telefoon en is met een tik te openen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");

  // Ingeklapt: de vaste kolom is weg, de balk met de hamburger staat er.
  const menuKnop = page.getByRole("button", { name: "Menu" });
  await expect(menuKnop).toBeVisible();

  await menuKnop.click();
  const la = page.locator("#hoofdmenu");
  await expect(la).toBeVisible();
  await expect(la.getByRole("link", { name: "Equipment", exact: true })).toBeVisible();

  // Een routewissel sluit de la; anders blijft hij over het gekozen scherm staan.
  await la.getByRole("link", { name: "Equipment", exact: true }).click();
  await expect(page).toHaveURL(/\/equipment$/);
  await expect(la).toBeHidden();
});

test("de pagina schuift nergens horizontaal, ook niet met de zijbalk erbij", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const scherm of SCHERMEN) {
    await page.goto(scherm.pad);
    await page.waitForLoadState("networkidle").catch(() => {});
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `${scherm.pad} schuift ${overflow} px horizontaal`).toBeLessThanOrEqual(1);
  }
});

test("geen enkel scherm is een doodlopende weg", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });

  for (const scherm of SCHERMEN) {
    await page.goto(scherm.pad);
    await page.waitForLoadState("networkidle").catch(() => {});

    // Links BUITEN de zijbalk: de zijbalk telt niet mee, want die staat overal
    // en zou elke pagina automatisch laten slagen.
    const inhoud = page.locator("main");
    const uitgangen = await inhoud.getByRole("link").count();
    expect(
      uitgangen,
      `${scherm.pad} heeft geen enkele doorklik in de inhoud zelf`,
    ).toBeGreaterThanOrEqual(2);
  }
});

test("de alarmteller in de zijbalk verwijst naar de alarmen", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/line");
  const menu = page.getByRole("navigation", { name: "Hoofdmenu" });
  await menu.getByRole("link", { name: "Alarmen", exact: true }).click();
  await expect(page).toHaveURL(/\/alarms$/);
});

test("het actieve scherm is als zodanig gemarkeerd", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/batches");
  const menu = page.getByRole("navigation", { name: "Hoofdmenu" });
  await expect(menu.getByRole("link", { name: "Batches", exact: true })).toHaveAttribute(
    "aria-current",
    "page",
  );
  // "/" mag NIET actief zijn op een andere route: een prefix-match zou de
  // landing overal laten oplichten.
  await expect(menu.getByRole("link", { name: "Plant", exact: true })).not.toHaveAttribute(
    "aria-current",
    "page",
  );
});
