import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";

const root = "dist";
const port = Number.parseInt(process.env.PORT || "5173", 10);
const mimeTypes = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"]
]);

const server = createServer(async (request, response) => {
  const url = new URL(request.url || "/", `http://${request.headers.host || "localhost"}`);
  const cleanPath = normalize(url.pathname).replace(/^(\.\.[/\\])+/, "");
  const filePath = join(root, cleanPath === "/" ? "index.html" : cleanPath);
  try {
    const body = await readFile(filePath);
    response.writeHead(200, {
      "Content-Type": mimeTypes.get(extname(filePath)) || "application/octet-stream"
    });
    response.end(body);
  } catch {
    const body = await readFile(join(root, "index.html"));
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(body);
  }
});

server.listen(port, "127.0.0.1", () => {
  console.log(`StudyLens web UI: http://127.0.0.1:${port}`);
});

