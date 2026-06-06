import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/postcss";

const projectRoot = path.dirname(fileURLToPath(import.meta.url));

export default {
  plugins: [
    tailwindcss({
      base: projectRoot,
    }),
  ],
};
