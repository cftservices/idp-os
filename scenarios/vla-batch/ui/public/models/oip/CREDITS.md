# Bronnen OIP-modellen (MIT)

Uit [Open Industry Project](https://github.com/Open-Industry-Project/Open-Industry-Project),
commit `238668860a34f12e48c4a8f84b6812e38c9053f5`. © Automation Standard LLC and Contributors,
MIT-licentie (zie `LICENSE`, die bij deze bestanden mee moet).

Geoptimaliseerd met `@gltf-transform` (v4): texturen ingebakken in de materialen,
`KHR_materials_specular` verwijderd, prune, dedup, webp op 1024 px, reorder, quantize, meshopt.

| Bestand | Bron in OIP | Wat er bij ons mee gebeurt |
|---|---|---|
| robot.glb | `SixAxisRobot.glb` + `sixaxis_BaseColor/ORM.webp` | Palletiser; gelede arm met de rig uit `rig.json` (`Robot.tsx`) |
| dockdeur.glb | `DockDoor.glb` (alleen node `Door`) | Roldeurpaneel per dock |
| leveller.glb | `AutoLeveler.glb` | Dockleveller; plaat en lip klappen uit bij een truck |
| trailer.glb | `ContainerTrailer.glb` | Trailer van de truck, geschaald naar een 20ft-container |
| tex/pallet-kleur.webp | `PalletWood.webp` | Houttextuur europallet (512 px) |
| tex/pallet-normaal.webp | `PalletWoodNormal.webp` | idem, normal map |
| tex/pallet-ruwheid.webp | `PalletWoodRoughness.webp` | idem, roughness |
