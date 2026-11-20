import { test, expect } from "@playwright/test";

/**
 * De gedragingen die bij een herbouw altijd sneuvelen.
 *
 * Deze tests bestaan omdat elk van deze dingen er in de oude SPA bewust in
 * zat, na een bug, en bij een port stil verdwijnt zonder dat iets faalt.
 */

test("geen enkele request gaat naar een dubbel /api/v1-prefix", async ({ page }) => {
  // De server geeft in detail.action.path AL het /api/v1-prefix mee. Vergeet je
  // dat eraf te halen voordat je post, dan wordt het /api/v1/api/v1/... en
  // krijg je een 404 die eruitziet als een backendfout.
  const bad: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/v1/api/v1")) bad.push(r.url());
  });

  for (const path of ["/", "/management", "/line", "/alarms", "/scada", "/shopfloor"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => {});
  }
  expect(bad, `dubbel prefix in: ${bad.join(", ")}`).toHaveLength(0);
});

type Batch = { batch_id: string; state: string; created_at?: string };

async function scanCode(page: import("@playwright/test").Page, code: string) {
  await page.goto("/shopfloor");
  await page.getByPlaceholder(/ORD-/).fill(code);
  await page.getByRole("button", { name: "Scannen" }).click();
}

test("de scan-gate: geweigerd, order zonder batch, open, en alleen logistiek", async ({
  page,
  request,
}) => {
  // De vorige versie van deze test eindigde op `telling + 1 > 0` en kon dus
  // nooit zakken. Ondertussen las het scherm `batch_id` uit een antwoord dat
  // `{order, batch}` gaf, en bleef de gate altijd dicht. Elke toestand hier
  // controleert daarom wat er WEL en wat er NIET mag staan.

  // 1. Weigering: een code die niet bestaat.
  await scanCode(page, "BESTAAT-NIET");
  await expect(page.getByText("Geweigerd").first()).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText("Gate open")).toHaveCount(0);

  // 2. Order zonder batch: neutraal, niet groen, en geen doseerpanel.
  const order = await (
    await request.post("/api/v1/orders", {
      data: { recipe_id: "chocolate-vla-1L", target_qty_L: 1000 },
    })
  ).json();
  await scanCode(page, order.order_id);
  await expect(page.getByText("Order zonder batch")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByText(/nog geen batch/i)).toBeVisible();
  await expect(page.getByText("Gate open")).toHaveCount(0);
  await expect(page.getByText(/1\. Doseren/)).toHaveCount(0);

  const batches: Batch[] = await (await request.get("/api/v1/batches?line=L1")).json();
  const newest = (state: (b: Batch) => boolean) =>
    batches
      .filter(state)
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""))
      .at(-1);

  // 3. Afgeronde batch: alleen het logistieke deel, geen weging meer.
  const done = newest((b) => b.state === "COMPLETE");
  expect(done, "geen afgeronde batch op L1 om te scannen").toBeTruthy();
  await scanCode(page, done!.batch_id);
  await expect(page.getByText("Batch afgerond, alleen logistiek")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByText(/4\. Pallets, inslag en verzending/)).toBeVisible();
  await expect(page.getByText(/1\. Doseren/)).toHaveCount(0);

  // 4. Lopende batch: gate open, met de grondstoffen uit de engine. Tijdens
  //    een CIP loopt er geen batch; dan valt er niets te controleren.
  // Only a batch that really runs: a waiting batch is refused (line_busy) while
  // another batch runs on its line, and an aborted one is not a demo case.
  const running = newest((b) => ["DOSING", "COOKING", "COOLING", "FILLING"].includes(b.state));
  test.info().annotations.push({
    type: "lopende batch",
    description: running ? running.batch_id : "geen (CIP of stilstand), stap 4 overgeslagen",
  });
  if (running) {
    await scanCode(page, running.batch_id);
    await expect(page.getByText("Gate open")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/1\. Doseren/)).toBeVisible();
    await expect(page.getByText(/van [\d.,]+ kg/).first()).toBeVisible();
  }
});

test("het venster staat in de URL en overleeft de terugknop", async ({ page }) => {
  await page.goto("/management?window=week");
  await page.getByRole("button", { name: "Dag" }).click();
  await expect(page).toHaveURL(/window=day/);
  await page.goBack();
  await expect(page).toHaveURL(/window=week/);
});

test("een geopend batchdetail bevriest de lijst-loop", async ({ page }) => {
  await page.goto("/batches");
  await page.waitForLoadState("networkidle").catch(() => {});

  // Alleen echte batchrijen. De lege-staat-regel is ook een <tr> en telde
  // daardoor mee, waardoor deze test met een lege database niet oversloeg maar
  // op die regel klikte.
  const rows = page.locator('tbody tr[data-row="batch"]');
  if ((await rows.count()) === 0) test.skip(true, "geen batches in de testdatabase");

  await rows.first().click();
  await expect(page).toHaveURL(/batch=/);

  // Zonder de bevriezing re-rendert het detailpaneel onder de gebruiker weg
  // zodra de lijst ververst.
  let listCalls = 0;
  page.on("request", (r) => {
    if (r.url().endsWith("/api/v1/batches")) listCalls += 1;
  });
  await page.waitForTimeout(6000);
  expect(listCalls, "de lijst pollt door terwijl het detail open staat").toBe(0);
});

test("een KPI zonder waarde toont een reden, geen nul", async ({ page }) => {
  await page.goto("/management?window=day");
  await page.waitForLoadState("networkidle").catch(() => {});

  const unset = page.getByText("Geen norm").first();
  if ((await unset.count()) === 0) test.skip(true, "geen UNSET-KPI in dit venster");

  // "Geen verlies" en "niet gemeten" mogen er niet hetzelfde uitzien.
  const tile = unset.locator("xpath=ancestor::article[1]");
  await expect(tile).not.toContainText(/(^|\s)0(\s|$)/);
});
