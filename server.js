import express from "express";
import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";
import open from "open";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// 静态文件
app.use(express.static(path.join(__dirname, "public")));

// 配置 API 路由
app.get("/config", (req, res) => {
  res.json({
    GOOGLE_MAPS_API_KEY: process.env.GOOGLE_MAPS_API_KEY,
    OPENWEATHER_API_KEY: process.env.OPENWEATHER_API_KEY,
  });
});

// ✅ 根路径自动跳转
app.get("/", (req, res) => {
  res.redirect("/map.html");
});

app.listen(PORT, async () => {
  console.log(`✅ Server running at http://localhost:${PORT}/map.html`);
  await open(`http://localhost:${PORT}/map.html`);
});
