// Rasterize every top-level SVG in this folder to PNG at 2x for PR/gist embedding.
import { readdirSync, mkdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import sharp from "sharp";

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, "png");
mkdirSync(out, { recursive: true });
for (const name of readdirSync(here).filter((f) => f.endsWith(".svg"))) {
  const target = join(out, name.replace(/\.svg$/, ".png"));
  await sharp(join(here, name), { density: 192 }).png().toFile(target);
  console.log("rendered", target);
}
